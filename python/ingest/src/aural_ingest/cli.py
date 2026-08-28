from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import hashlib
import importlib
import importlib.metadata
import importlib.util
import io
import inspect
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from collections.abc import Iterable
from typing import Any

from aural_ingest.drum_benchmark import (
    BENCHMARK_CLASS_ORDER,
    benchmark_algorithms,
    format_benchmark_summary,
    load_drum_reference,
)
from aural_ingest.device import select_device
from aural_ingest.feedpak_writer import write_feedpak
from aural_ingest.guitar_split import split_lead_rhythm_guitar_stem
from aural_ingest.mt3_compat import ensure_mt3_transformers_compat
from aural_ingest.progress import ProgressEvent, emit, log
from aural_ingest.transcription import (
    DEFAULT_DRUM_FILTER,
    DEFAULT_MELODIC_METHOD,
    DEFAULT_TRANSCRIPTION_PROFILE,
    KNOWN_DRUM_FILTERS,
    KNOWN_MELODIC_METHODS,
    KNOWN_MT3_DRUM_ENGINES,
    KNOWN_TRANSCRIPTION_PROFILES,
    _default_basic_pitch_model_roots,
    available_mt3_modelpacks,
    build_default_drum_algorithm_registry,
    build_default_melodic_algorithm_registry,
    drum_engines_for_profile,
    drum_engine_metadata,
    is_mt3_drum_engine,
    resolve_drum_engine,
    resolve_basic_pitch_model_path,
    DrumTranscriptionResult,
    InstrumentTranscriptionResult,
    transcribe_all_melodic_stems,
    transcribe_melodic,
    transcribe_drums,
    transcribe_drums_with_profile,
    validate_drum_events_against_stem_silence,
    validate_melodic_method,
    validate_transcription_profile,
    resolve_rmvpe_checkpoint_path,
    DEFAULT_DRUM_SILENCE_GATE_DBFS,
    DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS,
)


@dataclass(frozen=True)
class StageModelRequirement:
    model_id: str
    modelpack_id: str
    version: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Stage:
    id: str
    version: str
    outputs: list[str]
    required_models: list[StageModelRequirement] = field(default_factory=list)


PIPELINE_ID = "aural_ingest"
PIPELINE_VERSION = "0.1.0"
# Stage 6a of the native-pack migration: import builds its working
# ``.auralsong`` layout exactly as before, then converts it in place to a
# ``.feedpak`` as the durable artifact. Tests that assert the intermediate
# ``.auralsong`` layout set this False to opt out of the final conversion.
IMPORT_EMIT_FEEDPAK = True
SCHEMA_VERSION = "1.0.0"
DEMUCS_MODELPACK_ID = "demucs_6"
DEMUCS_MODELPACK_FILENAME = "demucs_6.zip"
DEMUCS_FT_DRUMS_MODELPACK_ID = "demucs_ft_drums"
DEMUCS_FT_DRUMS_MODELPACK_FILENAME = "demucs_ft_drums.zip"
SUPPORTED_DEMUCS_MODELPACK_FILENAMES: dict[str, str] = {
    DEMUCS_MODELPACK_ID: DEMUCS_MODELPACK_FILENAME,
    DEMUCS_FT_DRUMS_MODELPACK_ID: DEMUCS_FT_DRUMS_MODELPACK_FILENAME,
}
SUPPORTED_DEMUCS_MODELPACK_IDS: tuple[str, ...] = tuple(SUPPORTED_DEMUCS_MODELPACK_FILENAMES)
DEMUCS_MODELPACK_ID_ALIASES: dict[str, str] = {
    "htdemucs_6s": DEMUCS_MODELPACK_ID,
    "htdemucs_ft": DEMUCS_FT_DRUMS_MODELPACK_ID,
    "htdemucs_ft_drums": DEMUCS_FT_DRUMS_MODELPACK_ID,
}
DEMUCS_PROVIDER = "demucs"
ROFORMER_PROVIDER = "roformer"
DEFAULT_STEM_SEPARATION_PROVIDER = "auto"
DEFAULT_BEAT_ANALYSIS_MODE = "high_accuracy"
KNOWN_BEAT_ANALYSIS_MODES: tuple[str, ...] = ("standard", "high_accuracy")
DEMUCS_STEM_ROLE_ALIASES: dict[str, str] = {"piano": "keys"}
DEMUCS_PRIMARY_STEM_ROLES: tuple[str, ...] = ("drums", "bass", "guitar", "keys", "vocals")
BEAT_TEMPO_PRODUCTION_POLICY: dict[str, Any] = {
    "default_mode": DEFAULT_BEAT_ANALYSIS_MODE,
    "production_default": "librosa_first",
    "preferred_backend": "librosa.beat_track",
    "fallback_mode": "standard",
    "fallback_backend": "energy_autocorrelation_uniform_grid",
    "essentia_status": "research_candidate_not_default",
    "reason": "best transcription/import quality is prioritized over fastest deterministic import throughput",
}
STEM_SEPARATION_PROVIDER_POLICY: dict[str, Any] = {
    "default_provider": DEFAULT_STEM_SEPARATION_PROVIDER,
    "demucs_support_status": "optional_experimental",
    "roformer_support_status": "research_external_command",
    "demucs_supported_modelpacks": list(SUPPORTED_DEMUCS_MODELPACK_IDS),
    "demucs_default_modelpack_id": DEMUCS_MODELPACK_ID,
    "absence_behavior": "skip_separation_and_continue_with_mix_or_provided_stems",
    "portable_requirement": "normal import must not require Demucs runtime or modelpack",
    "gpu_policy": "supported_when_available_with_cpu_fallback_required",
}
BENCHMARK_THRESHOLD_POLICY: dict[str, Any] = {
    "mode": "warn",
    "strict_pr_blocking": False,
    "minimum_warn_only_period": "at least 14 days after representative baselines are frozen",
    "strict_requires": [
        "versioned baselines",
        "role-specific thresholds",
        "representative local hardware profile",
        "reviewed synthetic/private quality fixtures",
    ],
}
RUNTIME_DEPENDENCY_POLICIES: dict[str, dict[str, Any]] = {
    "librosa": {
        "required": False,
        "role": "quality_default_beat_tempo",
        "missing_behavior": "high_accuracy beat analysis degrades to standard",
    },
    "torch": {
        "required": False,
        "role": "optional_model_runtime",
        "missing_behavior": "model-backed adapters are unavailable",
    },
    "torchaudio": {
        "required": False,
        "role": "optional_model_runtime",
        "missing_behavior": "model-backed adapters are unavailable",
    },
    "mt3_infer": {
        "required": False,
        "role": "optional_learned_drum_runtime",
        "missing_behavior": "MT3/YourMT3 engines are unavailable",
    },
    "demucs": {
        "required": False,
        "role": "optional_experimental_separator",
        "missing_behavior": "Demucs separation is skipped",
    },
    "basic_pitch": {
        "required": True,
        "distribution": "basic-pitch",
        "role": "basic_pitch_default_transcription_runtime",
        "missing_behavior": "runtime-check fails; auto transcription profiles can still fall back at transcription time",
    },
    "basic_pitch.inference": {
        "required": True,
        "distribution": "basic-pitch",
        "role": "basic_pitch_inference_entrypoint",
        "missing_behavior": "runtime-check fails; auto transcription profiles can still fall back at transcription time",
    },
    "onnxruntime": {
        "required": False,
        "role": "basic_pitch_onnx_backend",
        "missing_behavior": "Basic Pitch ONNX models cannot run",
    },
    "tensorflow": {
        "required": False,
        "role": "basic_pitch_tensorflow_backend",
        "missing_behavior": "Basic Pitch TensorFlow SavedModel backend is unavailable; ONNX remains usable when onnxruntime is present",
    },
}
INPUT_STEM_ROLE_ALIASES: dict[str, str] = {
    "piano": "keys",
    "keyboard": "keys",
    "synth": "keys",
    "lead": "lead_guitar",
    "rhythm": "rhythm_guitar",
    "voice": "vocals",
}
KNOWN_INPUT_STEM_ROLES: tuple[str, ...] = (
    "drums",
    "bass",
    "guitar",
    "lead_guitar",
    "rhythm_guitar",
    "keys",
    "vocals",
    "other",
)


STAGES: list[Stage] = [
    Stage(id="init_auralsong", version="0.1.0", outputs=["manifest.json"]),
    # We always produce mix.wav. Compressed assets are optional (only produced when ffmpeg is available).
    Stage(id="decode_audio", version="0.2.0", outputs=["audio/mix.wav", "audio/mix.mp3", "audio/mix.ogg"]),
    Stage(id="beats_tempo", version="0.3.0", outputs=["features/beats.json", "features/tempo_map.json"]),
    Stage(id="sections", version="0.3.0", outputs=["features/sections.json"]),
    Stage(
        id="separate_stems",
        version="0.1.0",
        outputs=[
            "audio/stems/drums.wav",
            "audio/stems/bass.wav",
            "audio/stems/guitar.wav",
            "audio/stems/keys.wav",
            "audio/stems/vocals.wav",
            "audio/stems/other.wav",
        ],
        required_models=[
            StageModelRequirement(
                model_id=DEMUCS_MODELPACK_ID,
                modelpack_id=DEMUCS_MODELPACK_ID,
                reason="Demucs stem separation weights",
            )
        ],
    ),
    Stage(
        id="split_guitar_stems",
        version="0.1.0",
        outputs=["audio/stems/lead_guitar.wav", "audio/stems/rhythm_guitar.wav"],
    ),
    Stage(id="transcribe_drums", version="0.2.0", outputs=["features/notes.mid"]),
    Stage(id="midi_finalize", version="0.1.0", outputs=["features/notes.mid"]),
    Stage(id="spectrogram", version="0.1.0", outputs=["features/spectrogram/"]),
]


def _serialize_declared_requirement(req: StageModelRequirement) -> dict[str, Any]:
    return {
        "model_id": req.model_id,
        "modelpack_id": req.modelpack_id,
        "version": req.version,
        "reason": req.reason,
    }


def _declared_mt3_stage_variants() -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        info = drum_engine_metadata(engine_id)
        variants[engine_id] = {
            "engine": engine_id,
            "backend": info.get("backend"),
            "description": info.get("description"),
            "required_models": [
                {
                    "model_id": info.get("model_id"),
                    "modelpack_id": info.get("modelpack_id"),
                    "version": None,
                    "reason": info.get("description"),
                }
            ],
        }
    return variants


def _serialize_stage_declaration(stage: Stage) -> dict[str, Any]:
    payload = {
        "id": stage.id,
        "version": stage.version,
        "outputs": stage.outputs,
    }
    if stage.id == "beats_tempo":
        payload["policy"] = BEAT_TEMPO_PRODUCTION_POLICY
    if stage.id == "separate_stems":
        payload["policy"] = STEM_SEPARATION_PROVIDER_POLICY
    if stage.required_models:
        payload["required_models"] = [_serialize_declared_requirement(req) for req in stage.required_models]
    if stage.id == "transcribe_drums":
        payload["variants"] = _declared_mt3_stage_variants()
    return payload


def _resolved_demucs_stage_requirement(
    modelpack_path: Path | None,
    modelpack_manifest: dict[str, Any] | None,
    modelpack_error: str | None,
) -> dict[str, Any]:
    modelpack_id = (
        _demucs_modelpack_id_from_manifest(modelpack_manifest)
        if isinstance(modelpack_manifest, dict)
        else DEMUCS_MODELPACK_ID
    )
    return {
        "model_id": modelpack_id,
        "modelpack_id": modelpack_id,
        "version": (
            str(modelpack_manifest.get("version", "")).strip() or None
            if isinstance(modelpack_manifest, dict)
            else None
        ),
        "resolved_path": str(modelpack_path) if modelpack_path is not None else None,
        "installed": modelpack_path is not None and modelpack_manifest is not None,
        "reason": "Demucs stem separation weights",
        "error": modelpack_error,
    }


def _resolved_mt3_stage_variants(drum_engines: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        info = drum_engine_metadata(engine_id)
        engine_info = drum_engines.get(engine_id, {})
        enabled = bool(engine_info.get("ok"))
        if "loadable" in engine_info or "transcribe_smoke_ok" in engine_info:
            enabled = enabled and bool(engine_info.get("loadable")) and bool(engine_info.get("transcribe_smoke_ok"))
        variants[engine_id] = {
            "engine": engine_id,
            "backend": info.get("backend"),
            "description": info.get("description"),
            "enabled": enabled,
            "required_models": [
                {
                    "model_id": engine_info.get("model_id", info.get("model_id")),
                    "modelpack_id": engine_info.get("modelpack_id", info.get("modelpack_id")),
                    "version": engine_info.get("modelpack_version"),
                    "resolved_path": engine_info.get("modelpack_root"),
                    "installed": bool(engine_info.get("ok")),
                    "reason": info.get("description"),
                    "error": engine_info.get("error"),
                }
            ],
        }
    return variants


def _runtime_stage_snapshot(
    drum_engines: dict[str, dict[str, Any]],
    demucs_modelpack_path: Path | None,
    demucs_modelpack_manifest: dict[str, Any] | None,
    demucs_modelpack_error: str | None,
) -> dict[str, dict[str, Any]]:
    stages: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        payload = _serialize_stage_declaration(stage)
        if stage.id == "separate_stems":
            payload["required_models"] = [
                _resolved_demucs_stage_requirement(
                    demucs_modelpack_path,
                    demucs_modelpack_manifest,
                    demucs_modelpack_error,
                )
            ]
            payload["enabled"] = payload["required_models"][0]["installed"]
        elif stage.id == "transcribe_drums":
            payload["variants"] = _resolved_mt3_stage_variants(drum_engines)
            payload["enabled"] = True
        else:
            payload["enabled"] = True
        stages[stage.id] = payload
    return stages


def _parse_config_arg(raw: str | None) -> dict[str, Any]:
    """Parse --config.

    Contract (docs/ingest-pipeline.md): --config <json>

    For convenience we accept either:
    - a JSON string
    - a path to a JSON file
    """

    if not raw:
        return {}

    p = Path(raw)
    if p.is_file():
        return json.loads(p.read_text("utf-8-sig"))

    return json.loads(raw)


def _resolve_ffmpeg_path() -> str | None:
    """Return the absolute path to an ffmpeg binary, or *None* if unavailable.

    Lookup order:
    1. Next to ``sys.executable`` (portable / PyInstaller sidecar layout)
    2. System PATH via ``shutil.which``
    """
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if candidate.is_file():
            return str(candidate)
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _have_ffmpeg() -> bool:
    return _resolve_ffmpeg_path() is not None


def _decode_to_wav(src: Path, dst_wav: Path, *, target_sr: int = 48_000) -> tuple[float, int]:
    """Create a deterministic PCM mono WAV.

    Returns (duration_sec, sample_rate).

    Inputs:
    - PCM .wav: copied through (no resample, original sample rate preserved)
    - non-PCM .wav (IEEE float, ADPCM, etc.) or any non-wav: re-encoded via ffmpeg
      to PCM16 mono at ``target_sr``. Requires ffmpeg next to the executable or on PATH.
    """

    import wave

    if src.suffix.lower() == ".wav":
        # Cheap copy-through path for PCM WAVs. Python's stdlib wave module rejects
        # non-PCM WAVs (e.g. IEEE 32-bit float, code 3) with wave.Error; fall through
        # to ffmpeg re-encode in that case so the pipeline can still produce a
        # deterministic PCM mix.wav. DAW master bounces are routinely 32-bit float.
        shutil.copyfile(src, dst_wav)
        try:
            return _wav_duration_sec(dst_wav)
        except wave.Error:
            pass  # Non-PCM container; ffmpeg below will overwrite dst_wav.

    ffmpeg_bin = _resolve_ffmpeg_path()
    if ffmpeg_bin is None:
        raise RuntimeError(
            "ffmpeg not found next to the sidecar or on PATH; non-PCM WAV and non-wav "
            "inputs require ffmpeg for decode. Provide a PCM .wav input or install ffmpeg."
        )

    # Force deterministic output:
    # - PCM16 mono
    # - normalized sample rate
    # Note: ffmpeg can still include encoder metadata in non-wav outputs; we only require wav here.
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(target_sr),
        "-c:a",
        "pcm_s16le",
        str(dst_wav),
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {cp.stderr.strip()}")

    duration, sr = _wav_duration_sec(dst_wav)
    return duration, sr


def _wav_duration_sec(p: Path) -> tuple[float, int]:
    import wave

    with wave.open(str(p), "rb") as w:
        frames = w.getnframes()
        sr = w.getframerate()
        if sr <= 0:
            return 0.0, sr
        return float(frames) / float(sr), sr


def _estimate_bpm_from_wav(wav_path: Path) -> float:
    """Very simple, fully-deterministic BPM estimator.

    This is intentionally naive (RMS onset autocorrelation), but it is *real analysis* and works
    well for click-tracks / synthetic fixtures.
    """

    import wave
    from array import array

    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        if sampwidth != 2:
            raise RuntimeError(f"unsupported wav sample width: {sampwidth} (only PCM16 supported)")

        # Smaller hop improves tempo resolution for the simple autocorrelation approach.
        # (e.g., at 48kHz with hop=1024, 120bpm maps to a non-integer lag and can be off by ~2bpm.)
        hop = 256
        rms: list[float] = []

        while True:
            frames = w.readframes(hop)
            if not frames:
                break
            a = array("h")
            a.frombytes(frames)

            # Convert to mono float [-1,1] in a streaming-friendly way.
            if channels == 1:
                mono_iter = a
                n = len(a)
                if n == 0:
                    continue
                ss = 0.0
                for x in mono_iter:
                    ss += float(x) * float(x)
                rms.append(math.sqrt(ss / float(n)) / 32768.0)
            else:
                # average channels (supports 2+ channels)
                n_frames = len(a) // channels
                if n_frames <= 0:
                    continue
                ss = 0.0
                idx = 0
                for _ in range(n_frames):
                    s = 0.0
                    for _c in range(channels):
                        s += float(a[idx])
                        idx += 1
                    m = s / float(channels)
                    ss += m * m
                rms.append(math.sqrt(ss / float(n_frames)) / 32768.0)

    # Onset envelope (half-wave rectified energy derivative)
    if len(rms) < 8:
        return 120.0

    onset: list[float] = [0.0]
    for i in range(1, len(rms)):
        d = rms[i] - rms[i - 1]
        onset.append(d if d > 0 else 0.0)

    # Autocorrelation over plausible tempos.
    min_bpm = 60.0
    max_bpm = 180.0

    # Lag in frames (each RMS entry is one hop)
    min_lag = max(1, int((60.0 / max_bpm) * float(sr) / float(hop)))
    max_lag = max(min_lag + 1, int((60.0 / min_bpm) * float(sr) / float(hop)))
    max_lag = min(max_lag, len(onset) - 1)

    best_lag = min_lag
    best_score = -1.0

    # Light smoothing by down-weighting very small values.
    eps = 1e-9
    for lag in range(min_lag, max_lag + 1):
        s = 0.0
        for i in range(lag, len(onset)):
            a = onset[i]
            b = onset[i - lag]
            if a > eps and b > eps:
                s += a * b
        if s > best_score:
            best_score = s
            best_lag = lag

    bpm = 60.0 * float(sr) / (float(hop) * float(best_lag))
    if not (min_bpm <= bpm <= max_bpm):
        return 120.0
    return float(round(bpm, 3))


def _quantize(t: float, q: float = 1e-6) -> float:
    return float(round(t / q) * q)


def _generate_beats(duration_sec: float, bpm: float, *, beats_per_bar: int = 4) -> list[dict[str, Any]]:
    if bpm <= 0:
        bpm = 120.0
    period = 60.0 / bpm
    beats: list[dict[str, Any]] = []
    bar = 0
    beat_in_bar = 0
    t = 0.0
    # Include beat at t=0.
    while t <= duration_sec + 1e-9:
        strength = 1.0 if beat_in_bar == 0 else 0.5
        beats.append(
            {
                "t": _quantize(t),
                "bar": bar,
                "beat": beat_in_bar,
                "strength": strength,
            }
        )

        beat_in_bar += 1
        if beat_in_bar >= beats_per_bar:
            beat_in_bar = 0
            bar += 1
        t += period

    return beats


def _generate_sections(duration_sec: float, bpm: float, *, bars_per_section: int = 8) -> list[dict[str, Any]]:
    if bpm <= 0:
        bpm = 120.0

    sec_per_bar = (60.0 / bpm) * 4.0
    sec_per_section = sec_per_bar * float(bars_per_section)
    if sec_per_section <= 0:
        sec_per_section = 8.0

    sections: list[dict[str, Any]] = []
    t0 = 0.0
    idx = 0
    while t0 < duration_sec - 1e-9:
        t1 = min(duration_sec, t0 + sec_per_section)
        sections.append({"t0": _quantize(t0), "t1": _quantize(t1), "label": f"section_{idx}"})
        t0 = t1
        idx += 1

    if not sections:
        sections.append({"t0": 0.0, "t1": _quantize(duration_sec), "label": "section_0"})
    return sections


def _generate_tempo_map(
    duration_sec: float,
    bpm: float,
    *,
    time_signature: str = "4/4",
) -> dict[str, Any]:
    bpm_safe = float(round(bpm if bpm > 0 else 120.0, 3))
    return {
        "tempo_version": "1.0.0",
        "segments": [
            {
                "t0": 0.0,
                "t1": _quantize(max(0.0, duration_sec)),
                "bpm": bpm_safe,
                "time_signature": time_signature,
            }
        ],
    }


def _coerce_scalar_float(value: Any, default: float) -> float:
    try:
        if hasattr(value, "item"):
            return float(value.item())
        if isinstance(value, (list, tuple)):
            if not value:
                return float(default)
            return float(value[0])
        return float(value)
    except Exception:
        return float(default)


def _refine_beat_times_with_onsets(
    beat_times: list[float],
    onset_times: list[float],
    *,
    max_adjust_sec: float,
) -> list[float]:
    if not beat_times or not onset_times:
        return beat_times[:]

    out: list[float] = []
    onset_idx = 0
    for beat in beat_times:
        best = beat
        best_delta = max_adjust_sec + 1e-9
        while onset_idx < len(onset_times) and onset_times[onset_idx] < beat - max_adjust_sec:
            onset_idx += 1
        probe = onset_idx
        while probe < len(onset_times):
            delta = onset_times[probe] - beat
            if delta > max_adjust_sec:
                break
            abs_delta = abs(delta)
            if abs_delta < best_delta:
                best = onset_times[probe]
                best_delta = abs_delta
            probe += 1
        out.append(best)
    return out


def _assign_bar_positions(beat_times: list[float], *, beats_per_bar: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    bar = 0
    beat_in_bar = 0
    for beat_time in beat_times:
        out.append(
            {
                "t": _quantize(max(0.0, beat_time)),
                "bar": int(bar),
                "beat": int(beat_in_bar),
                "strength": 1.0 if beat_in_bar == 0 else 0.5,
            }
        )
        beat_in_bar += 1
        if beat_in_bar >= beats_per_bar:
            beat_in_bar = 0
            bar += 1
    return out


def _analyze_beats_tempo_standard(
    wav_path: Path,
    *,
    duration_sec: float,
    bpm_hint: float | None,
) -> tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if bpm_hint is not None and bpm_hint > 0:
        bpm = float(round(bpm_hint, 3))
        source = "hint"
    else:
        bpm = _estimate_bpm_from_wav(wav_path)
        source = "energy_autocorrelation"
    beats = {"beats_version": "1.0.0", "beats": _generate_beats(duration_sec, bpm)}
    tempo_map = _generate_tempo_map(duration_sec, bpm)
    meta = {
        "mode": "standard",
        "tempo_source": source,
        "beat_source": "uniform_grid",
        "estimated_bpm": bpm,
    }
    return bpm, beats, tempo_map, meta


def _analyze_beats_tempo_high_accuracy(
    wav_path: Path,
    *,
    duration_sec: float,
    bpm_hint: float | None,
) -> tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        import numpy as np
        import librosa
    except Exception:
        bpm, beats, tempo_map, meta = _analyze_beats_tempo_standard(
            wav_path,
            duration_sec=duration_sec,
            bpm_hint=bpm_hint,
        )
        meta["mode"] = "high_accuracy"
        meta["fallback_reason"] = "librosa runtime unavailable"
        meta["degraded_to"] = "standard"
        return bpm, beats, tempo_map, meta

    try:
        audio, sr = librosa.load(str(wav_path), sr=None, mono=True)
    except Exception as exc:
        bpm, beats, tempo_map, meta = _analyze_beats_tempo_standard(
            wav_path,
            duration_sec=duration_sec,
            bpm_hint=bpm_hint,
        )
        meta["mode"] = "high_accuracy"
        meta["fallback_reason"] = f"audio decode failed: {exc}"
        meta["degraded_to"] = "standard"
        return bpm, beats, tempo_map, meta

    if sr <= 0 or len(audio) == 0:
        bpm, beats, tempo_map, meta = _analyze_beats_tempo_standard(
            wav_path,
            duration_sec=duration_sec,
            bpm_hint=bpm_hint,
        )
        meta["mode"] = "high_accuracy"
        meta["fallback_reason"] = "empty audio"
        meta["degraded_to"] = "standard"
        return bpm, beats, tempo_map, meta

    onset_env = librosa.onset.onset_strength(y=np.asarray(audio, dtype=np.float32), sr=int(sr))
    beat_track_kwargs: dict[str, Any] = {
        "y": np.asarray(audio, dtype=np.float32),
        "sr": int(sr),
        "trim": False,
        "units": "time",
    }
    if bpm_hint is not None and bpm_hint > 0:
        beat_track_kwargs["start_bpm"] = float(bpm_hint)

    tempo_estimate, beat_times_raw = librosa.beat.beat_track(**beat_track_kwargs)
    bpm = float(round(_coerce_scalar_float(tempo_estimate, bpm_hint or 120.0), 3))
    beat_times = sorted(float(t) for t in beat_times_raw.tolist()) if hasattr(beat_times_raw, "tolist") else []
    onset_times = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=int(sr),
        units="time",
        backtrack=True,
    )
    onset_times_list = sorted(float(t) for t in onset_times.tolist()) if hasattr(onset_times, "tolist") else []

    if not beat_times:
        if bpm <= 0:
            bpm = float(round(bpm_hint if bpm_hint and bpm_hint > 0 else 120.0, 3))
        beat_times = [float(item["t"]) for item in _generate_beats(duration_sec, bpm)]
        beat_source = "uniform_grid_fallback"
    else:
        max_adjust_sec = min(0.08, max(0.025, (60.0 / max(bpm, 1.0)) * 0.18))
        beat_times = _refine_beat_times_with_onsets(beat_times, onset_times_list, max_adjust_sec=max_adjust_sec)
        if beat_times[0] > 0.125:
            beat_times.insert(0, 0.0)
        if beat_times[-1] < duration_sec - 0.25:
            period = 60.0 / max(bpm, 1.0)
            t = beat_times[-1] + period
            while t <= duration_sec + 1e-9:
                beat_times.append(t)
                t += period
        beat_source = "librosa_beat_track"

    beat_times = sorted({round(max(0.0, min(duration_sec, t)), 6) for t in beat_times if t <= duration_sec + 1e-9})
    if not beat_times:
        beat_times = [0.0]

    beats = {"beats_version": "1.0.0", "beats": _assign_bar_positions(beat_times)}
    tempo_map = _generate_tempo_map(duration_sec, bpm)
    meta = {
        "mode": "high_accuracy",
        "tempo_source": "librosa.beat_track",
        "beat_source": beat_source,
        "estimated_bpm": bpm,
        "onset_refinement": True,
        "detected_beat_count": len(beat_times),
    }
    return bpm, beats, tempo_map, meta


def _analyze_beats_tempo(
    wav_path: Path,
    *,
    duration_sec: float,
    config: dict[str, Any],
    beat_analysis_mode: str,
) -> tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]]:
    bpm_hint = float(config.get("bpm_hint")) if "bpm_hint" in config else None
    if beat_analysis_mode == "high_accuracy":
        # Real beat+downbeat+meter via Beat This! (modelpack-gated, MIT). Returns
        # None when the checkpoint/package is absent or on any error -> we keep
        # the librosa path. Opt out with AURALPRIMER_DISABLE_METER_MODEL=1.
        if os.getenv("AURALPRIMER_DISABLE_METER_MODEL", "").strip().lower() not in {"1", "true", "yes", "on"}:
            try:
                # Absolute import: under PyInstaller, cli runs as a TOP-LEVEL
                # script (no parent package), so a relative import raises
                # ImportError -- which this try swallows, silently disabling
                # Beat This! in every frozen build.
                from aural_ingest import meter_tracker

                model_result = meter_tracker.track_meter(
                    wav_path, duration_sec=duration_sec, config=config
                )
                if model_result is not None:
                    return model_result
            except Exception:
                pass  # any failure -> librosa fallback below
        return _analyze_beats_tempo_high_accuracy(
            wav_path,
            duration_sec=duration_sec,
            bpm_hint=bpm_hint,
        )
    return _analyze_beats_tempo_standard(
        wav_path,
        duration_sec=duration_sec,
        bpm_hint=bpm_hint,
    )


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_song_id(source_sha256: str, profile: str, transcription_options: dict[str, Any]) -> str:
    # Stable id for identical source+pipeline configuration.
    h = hashlib.sha256()
    fingerprint = {
        "profile": profile,
        "pipeline_version": PIPELINE_VERSION,
        "drum_filter_requested": transcription_options.get("drum_filter_requested"),
        "drum_filter": transcription_options.get("drum_filter"),
        "drum_source_kind": transcription_options.get("drum_source_kind"),
        "drum_source_sha256": transcription_options.get("drum_source_sha256"),
        "stem_separation_provider": transcription_options.get("stem_separation_provider"),
        "stem_separation_modelpack_id": transcription_options.get("stem_separation_modelpack_id"),
        "stem_separation_modelpack_version": transcription_options.get("stem_separation_modelpack_version"),
        "melodic_method": transcription_options.get("melodic_method"),
        "transcription_profile": transcription_options.get("transcription_profile"),
        "shifts": transcription_options.get("shifts"),
        "multi_filter": bool(transcription_options.get("multi_filter", False)),
    }
    h.update(source_sha256.encode("utf-8"))
    h.update(b"|")
    h.update(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()[:32]


def _recognition_manifest_block(tr_opts: dict[str, Any]) -> dict[str, Any]:
    drum_requested = tr_opts.get("drum_filter_requested") or tr_opts.get("drum_filter")
    melodic_requested = tr_opts.get("melodic_method")
    return {
        "summary": {
            "drums": {
                "requested_engine": drum_requested,
                "used_engine": None,
            },
            "melodic": {
                "requested_engine": melodic_requested,
                "used_engine": None,
            },
        },
        "drums": {
            "requested_engine": drum_requested,
            "normalized_engine": tr_opts.get("drum_filter"),
            "used_engine": None,
            "source_kind": tr_opts.get("drum_source_kind"),
            "source_path": tr_opts.get("drum_source_path"),
            "attempted_engines": [],
            "warnings": list(tr_opts.get("warnings", [])),
        },
        "melodic": {
            "requested_engine": melodic_requested,
            "used_engine": None,
            "attempted_engines": [],
            "warnings": [],
        },
        "profile": tr_opts.get("transcription_profile"),
    }


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _bounded_int(value: Any, *, lo: int, hi: int) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if lo <= out <= hi:
        return out
    return None


_FRETTED_ROLE_TUNINGS: dict[str, list[int]] = {
    "bass": [28, 33, 38, 43],
    "guitar": [40, 45, 50, 55, 59, 64],
    "lead_guitar": [40, 45, 50, 55, 59, 64],
    "rhythm_guitar": [40, 45, 50, 55, 59, 64],
}


def _candidate_fretted_positions(
    role: str,
    pitch: int,
    *,
    max_fret: int = 24,
) -> list[tuple[int, int]]:
    tuning = _FRETTED_ROLE_TUNINGS.get(role.lower())
    if not tuning:
        return []

    candidates: list[tuple[int, int]] = []
    for string_idx, open_pitch in enumerate(tuning):
        fret = pitch - open_pitch
        if fret < 0 or fret > max_fret:
            continue
        candidates.append((string_idx, fret))
    return candidates


def _fingering_position_cost(
    role: str,
    position: tuple[int, int],
    previous: tuple[int, int] | None = None,
) -> float:
    string_idx, fret = position
    cost = float(fret) * 0.25
    if fret == 0:
        cost -= 0.4
    if role.lower() == "bass":
        cost += float(string_idx) * 0.05
    if previous is not None:
        prev_string, prev_fret = previous
        cost += abs(string_idx - prev_string) * 0.6
        cost += abs(fret - prev_fret) * 0.15
    return cost


def _infer_fretted_position(
    role: str,
    pitch: int,
    *,
    max_fret: int = 24,
    previous: tuple[int, int] | None = None,
    occupied_strings: set[int] | None = None,
) -> tuple[int, int] | None:
    candidates = _candidate_fretted_positions(role, pitch, max_fret=max_fret)
    if not candidates:
        return None
    occupied = occupied_strings or set()
    return min(
        candidates,
        key=lambda position: (
            100.0 if position[0] in occupied else 0.0,
            _fingering_position_cost(role, position, previous),
            position[1],
            position[0],
        ),
    )


def _note_fingering(note: Any) -> tuple[int, int] | None:
    string_idx = _bounded_int(getattr(note, "string", None), lo=0, hi=8)
    fret = _bounded_int(getattr(note, "fret", None), lo=0, hi=36)
    if string_idx is None or fret is None:
        return None
    return string_idx, fret


def _group_assignment_cost(
    role: str,
    group: list[dict[str, Any]],
    positions: list[tuple[int, int]],
    *,
    previous: tuple[int, int] | None,
    occupied_strings: set[int],
) -> float:
    cost = sum(_fingering_position_cost(role, position, previous) for position in positions)
    used: dict[int, int] = {}
    for position in positions:
        used[position[0]] = used.get(position[0], 0) + 1
        if position[0] in occupied_strings:
            cost += 100.0
    for count in used.values():
        if count > 1:
            cost += 100.0 * float(count - 1)

    for left_idx, left in enumerate(group):
        for right_idx in range(left_idx + 1, len(group)):
            right = group[right_idx]
            if int(left["pitch"]) > int(right["pitch"]):
                continue
            left_string = positions[left_idx][0]
            right_string = positions[right_idx][0]
            if left_string > right_string:
                cost += 8.0 * float(left_string - right_string + 1)
    if positions:
        frets = [position[1] for position in positions]
        cost += float(max(frets) - min(frets)) * 0.1
    return cost


def _infer_fretted_group(
    role: str,
    group: list[dict[str, Any]],
    *,
    previous: tuple[int, int] | None,
) -> list[tuple[int, int] | None]:
    assigned: list[tuple[int, int] | None] = [None] * len(group)
    occupied_strings: set[int] = set()
    inferred_indices: list[int] = []
    candidate_lists: list[list[tuple[int, int]]] = []

    for idx, item in enumerate(group):
        explicit = _note_fingering(item["note"])
        if explicit is not None:
            assigned[idx] = explicit
            occupied_strings.add(explicit[0])
            continue
        candidates = _candidate_fretted_positions(role, int(item["pitch"]))
        if not candidates:
            continue
        inferred_indices.append(idx)
        candidate_lists.append(candidates)

    if not inferred_indices:
        return assigned

    if len(inferred_indices) > 6:
        local_occupied = set(occupied_strings)
        local_previous = previous
        for idx in inferred_indices:
            position = _infer_fretted_position(
                role,
                int(group[idx]["pitch"]),
                previous=local_previous,
                occupied_strings=local_occupied,
            )
            assigned[idx] = position
            if position is not None:
                local_occupied.add(position[0])
                local_previous = position
        return assigned

    from itertools import product

    best_combo: tuple[tuple[int, int], ...] | None = None
    best_cost: float | None = None
    inferred_group = [group[idx] for idx in inferred_indices]
    for combo in product(*candidate_lists):
        cost = _group_assignment_cost(
            role,
            inferred_group,
            list(combo),
            previous=previous,
            occupied_strings=occupied_strings,
        )
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_combo = combo

    if best_combo is not None:
        for idx, position in zip(inferred_indices, best_combo, strict=False):
            assigned[idx] = position
    return assigned


def _build_fingering_doc(role: str, notes: Iterable[Any]) -> dict[str, Any] | None:
    parsed: list[dict[str, Any]] = []
    for note in notes:
        try:
            t_on = round(float(getattr(note, "t_on")), 6)
            t_off = round(float(getattr(note, "t_off")), 6)
            pitch = int(round(float(getattr(note, "pitch"))))
            velocity = int(round(float(getattr(note, "velocity", 100))))
        except (TypeError, ValueError):
            continue
        if t_off <= t_on or not 0 <= pitch <= 127:
            continue
        parsed.append(
            {
                "note": note,
                "t_on": t_on,
                "t_off": t_off,
                "pitch": pitch,
                "velocity": max(1, min(127, velocity)),
            }
        )

    parsed.sort(key=lambda item: (item["t_on"], item["pitch"], item["t_off"]))
    entries: list[dict[str, Any]] = []
    previous: tuple[int, int] | None = None
    idx = 0
    while idx < len(parsed):
        group = [parsed[idx]]
        idx += 1
        group_start = float(group[0]["t_on"])
        while idx < len(parsed) and float(parsed[idx]["t_on"]) - group_start <= 0.03:
            group.append(parsed[idx])
            idx += 1

        positions = _infer_fretted_group(role, group, previous=previous)
        for item, fingering in zip(group, positions, strict=False):
            if fingering is None:
                continue
            string_idx, fret = fingering
            previous = fingering
            entries.append(
                {
                    "t_on": item["t_on"],
                    "t_off": item["t_off"],
                    "pitch": item["pitch"],
                    "velocity": item["velocity"],
                    "string": string_idx,
                    "fret": fret,
                }
            )

    if not entries:
        return None
    entries.sort(key=lambda item: (item["t_on"], item["pitch"], item["string"], item["fret"], item["t_off"]))
    out: dict[str, Any] = {"version": "1.0.0", "instrument": role, "notes": entries}
    tuning = _FRETTED_ROLE_TUNINGS.get(role.lower())
    if tuning:
        out["tuning"] = tuning
    return out


def _write_fingering_sidecars(root: Path, instrument_tracks: dict[str, Iterable[Any]]) -> dict[str, str]:
    features_dir = root / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    for stale in features_dir.glob("fingering.*.json"):
        stale.unlink()

    paths: dict[str, str] = {}
    for role, notes in sorted(instrument_tracks.items()):
        doc = _build_fingering_doc(role, notes)
        if doc is None:
            continue
        rel_path = f"features/fingering.{role}.json"
        _write_json(root / rel_path, doc)
        paths[role] = rel_path
    return paths


def _write_vocal_pitch_contour_sidecar(root: Path, instrument_results: Iterable[Any]) -> str | None:
    features_dir = root / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    out_path = features_dir / "vocal_pitch_contour.json"
    if out_path.exists():
        out_path.unlink()

    for result in instrument_results:
        if getattr(result, "instrument", None) not in {"vocal", "vocals"}:
            continue
        meta = getattr(result, "meta", {})
        if not isinstance(meta, dict):
            continue
        contour = meta.get("vocal_pitch_contour")
        if not isinstance(contour, dict):
            continue
        samples = contour.get("samples")
        if not isinstance(samples, list) or not samples:
            continue
        doc = {
            "version": int(contour.get("version") or 1),
            "samples": samples,
        }
        _write_json(out_path, doc)
        return "features/vocal_pitch_contour.json"
    return None


def _write_vocal_pitch_sidecar(root: Path, instrument_results: Iterable[Any]) -> str | None:
    features_dir = root / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    out_path = features_dir / "vocal_pitch.json"
    if out_path.exists():
        out_path.unlink()

    for result in instrument_results:
        if getattr(result, "instrument", None) not in {"vocal", "vocals"}:
            continue
        notes: list[dict[str, Any]] = []
        for note in sorted(getattr(result, "notes", []) or [], key=lambda n: (float(n.t_on), int(n.pitch))):
            start = float(note.t_on)
            duration = max(0.0, float(note.t_off) - start)
            if duration <= 0:
                continue
            notes.append(
                {
                    "t": round(start, 6),
                    "d": round(duration, 6),
                    "midi": int(note.pitch),
                }
            )
        if not notes:
            continue
        _write_json(out_path, {"version": 1, "notes": notes})
        return "features/vocal_pitch.json"
    return None


def _safe_slug(value: str) -> str:
    out = []
    prev_sep = False
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_sep = False
            continue
        if not prev_sep:
            out.append("_")
            prev_sep = True
    return "".join(out).strip("_") or "x"


def _canonical_demucs_modelpack_id(raw: object) -> str:
    value = str(raw or "").strip().lower()
    return DEMUCS_MODELPACK_ID_ALIASES.get(value, value)


def _requested_demucs_modelpack_id(config: dict[str, Any]) -> str | None:
    for key in ("demucs_modelpack_id", "stem_separation_modelpack_id"):
        raw = config.get(key)
        if raw is None:
            continue
        value = _canonical_demucs_modelpack_id(raw)
        if value and value not in {"auto", "default"}:
            return value
    return None


def _demucs_modelpack_id_from_manifest(modelpack_manifest: dict[str, Any]) -> str:
    modelpack_id = _canonical_demucs_modelpack_id(modelpack_manifest.get("id"))
    return modelpack_id or DEMUCS_MODELPACK_ID


def _demucs_modelpack_ids_for_config(config: dict[str, Any]) -> tuple[tuple[str, ...], str | None]:
    requested_id = _requested_demucs_modelpack_id(config)
    if requested_id is not None:
        if requested_id not in SUPPORTED_DEMUCS_MODELPACK_IDS:
            return (), (
                f"unsupported demucs modelpack id {requested_id!r}; "
                f"supported: {', '.join(SUPPORTED_DEMUCS_MODELPACK_IDS)}"
            )
        return (requested_id,), None

    configured = config.get("demucs_modelpack_zip_path")
    if isinstance(configured, str) and configured.strip():
        return SUPPORTED_DEMUCS_MODELPACK_IDS, None

    return (DEMUCS_MODELPACK_ID,), None


def _config_with_cli_demucs_modelpack_options(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    out = config
    modelpack_id = getattr(args, "stem_separation_modelpack_id", None)
    if isinstance(modelpack_id, str) and modelpack_id.strip():
        out = {**out, "stem_separation_modelpack_id": modelpack_id}
    zip_path = getattr(args, "demucs_modelpack_zip_path", None)
    if isinstance(zip_path, str) and zip_path.strip():
        out = {**out, "demucs_modelpack_zip_path": zip_path}
    return out


def _default_demucs_modelpack_candidates(modelpack_ids: Iterable[str] | None = None) -> list[Path]:
    selected_ids = tuple(modelpack_ids) if modelpack_ids is not None else SUPPORTED_DEMUCS_MODELPACK_IDS
    roots: list[Path] = []
    seen_roots: set[str] = set()

    def add_root(root: Path | None) -> None:
        if root is None:
            return
        key = str(root)
        if key in seen_roots:
            return
        seen_roots.add(key)
        roots.append(root)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        add_root(Path(str(meipass)))

    try:
        exe_dir = Path(sys.executable).resolve().parent
        add_root(exe_dir)
        add_root(exe_dir.parent)
        add_root(exe_dir.parent.parent)
    except Exception:
        pass

    try:
        cwd = Path.cwd()
        add_root(cwd)
        add_root(cwd.parent)
    except Exception:
        pass

    try:
        this_file = Path(__file__).resolve()
        add_root(this_file.parent)
        add_root(this_file.parents[2])
        add_root(this_file.parents[4])
    except Exception:
        pass

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for modelpack_id in selected_ids:
            filename = SUPPORTED_DEMUCS_MODELPACK_FILENAMES.get(modelpack_id)
            if not filename:
                continue
            for candidate in (
                root / "modelpacks" / filename,
                root / "AuralPrimerPortable" / "modelpacks" / filename,
                root / "dist" / "modelpacks" / filename,
                root / filename,
            ):
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
    return candidates


def _read_zip_json(zip_path: Path, entry_name: str) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        raw = zf.read(entry_name).decode("utf-8-sig")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"{entry_name} in {zip_path} must be a JSON object")
    return data


def _is_safe_zip_rel_path(rel_path: str) -> bool:
    value = rel_path.strip()
    if not value or "\\" in value or ":" in value or value.startswith("/") or "//" in value:
        return False
    return not any(part in {"", ".", ".."} for part in PurePosixPath(value).parts)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value)


def _primary_demucs_weight_info(modelpack_zip: Path, modelpack_manifest: dict[str, Any]) -> dict[str, Any]:
    weights = modelpack_manifest.get("weights")
    if not isinstance(weights, list) or not weights:
        raise RuntimeError(f"{modelpack_zip} modelpack.json missing weights[]")
    if len(weights) != 1:
        raise RuntimeError(f"{modelpack_zip} modelpack.json must declare exactly one Demucs weight")

    weight_info = weights[0]
    if not isinstance(weight_info, dict):
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0] must be an object")

    rel_path = str(weight_info.get("path", "")).strip()
    if not rel_path:
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].path missing")
    if not _is_safe_zip_rel_path(rel_path):
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].path is not a safe zip-relative path")

    checksum = str(weight_info.get("sha256", "")).strip().lower()
    if not checksum:
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].sha256 missing")
    if not _is_sha256_hex(checksum):
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].sha256 must be 64 hex characters")

    source_url = str(weight_info.get("source_url", "")).strip()
    if not source_url:
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].source_url missing")

    return weight_info


def _demucs_modelpack_required_stems(modelpack_id: str) -> tuple[str, ...]:
    if modelpack_id == DEMUCS_FT_DRUMS_MODELPACK_ID:
        return ("drums",)
    return DEMUCS_PRIMARY_STEM_ROLES


def _declared_demucs_stems(modelpack_manifest: dict[str, Any]) -> set[str]:
    stems = modelpack_manifest.get("stems")
    if not isinstance(stems, list):
        return set()
    out: set[str] = set()
    for stem in stems:
        key = str(stem).strip().lower().replace(" ", "_")
        if not key:
            continue
        out.add(DEMUCS_STEM_ROLE_ALIASES.get(key, key))
    return out


def _demucs_modelpack_has_license_file(zf: zipfile.ZipFile) -> bool:
    return any(
        PurePosixPath(info.filename).name.lower() in {"license", "license.txt", "license.md"}
        for info in zf.infolist()
        if not info.is_dir()
    )


def _validate_demucs_modelpack_archive(
    modelpack_zip: Path,
    *,
    expected_ids: set[str],
) -> dict[str, Any]:
    manifest = _read_zip_json(modelpack_zip, "modelpack.json")
    modelpack_id = _demucs_modelpack_id_from_manifest(manifest)
    if modelpack_id not in SUPPORTED_DEMUCS_MODELPACK_IDS:
        raise RuntimeError(f"unsupported demucs modelpack id: {manifest.get('id')!r}")
    if modelpack_id not in expected_ids:
        raise RuntimeError(f"unexpected demucs modelpack id: {manifest.get('id')!r}")

    version = str(manifest.get("version", "")).strip()
    if not version:
        raise RuntimeError("modelpack.json missing version")
    provider = str(manifest.get("provider", "")).strip().lower()
    if provider and provider != DEMUCS_PROVIDER:
        raise RuntimeError(f"modelpack.json provider must be '{DEMUCS_PROVIDER}'")
    architecture = str(manifest.get("architecture", "")).strip()
    if not architecture:
        raise RuntimeError("modelpack.json missing architecture")

    declared_stems = _declared_demucs_stems(manifest)
    missing_stems = sorted(set(_demucs_modelpack_required_stems(modelpack_id)) - declared_stems)
    if missing_stems:
        raise RuntimeError(f"modelpack.json missing required stem roles: {', '.join(missing_stems)}")

    weight_info = _primary_demucs_weight_info(modelpack_zip, manifest)
    rel_path = str(weight_info["path"]).strip()
    expected_sha = str(weight_info["sha256"]).strip().lower()

    with zipfile.ZipFile(modelpack_zip) as zf:
        try:
            weight_bytes = zf.read(rel_path)
        except KeyError as exc:
            raise RuntimeError(f"modelpack weight missing from zip: {rel_path}") from exc
        actual_sha = hashlib.sha256(weight_bytes).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"modelpack weight sha256 mismatch for {rel_path}: expected {expected_sha} got {actual_sha}"
            )

        if modelpack_id == DEMUCS_FT_DRUMS_MODELPACK_ID:
            license_name = str(manifest.get("license", "")).strip()
            if not license_name:
                raise RuntimeError("demucs_ft_drums modelpack.json missing license")
            if not _demucs_modelpack_has_license_file(zf):
                raise RuntimeError("demucs_ft_drums modelpack zip missing LICENSE file")

    return manifest


def _resolve_demucs_modelpack(
    config: dict[str, Any],
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    modelpack_ids, id_error = _demucs_modelpack_ids_for_config(config)
    if id_error:
        return None, None, id_error

    configured = config.get("demucs_modelpack_zip_path")
    candidates: list[Path] = []
    if isinstance(configured, str) and configured.strip():
        candidates.append(Path(configured).expanduser())
    else:
        candidates.extend(_default_demucs_modelpack_candidates(modelpack_ids))

    last_error: str | None = None
    expected_ids = set(modelpack_ids)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            manifest = _validate_demucs_modelpack_archive(candidate, expected_ids=expected_ids)
        except Exception as exc:
            last_error = f"invalid demucs modelpack {candidate}: {exc}"
            continue
        return candidate, manifest, None

    if last_error:
        return None, None, last_error
    expected_filenames = [
        SUPPORTED_DEMUCS_MODELPACK_FILENAMES[modelpack_id]
        for modelpack_id in modelpack_ids
        if modelpack_id in SUPPORTED_DEMUCS_MODELPACK_FILENAMES
    ]
    return None, None, f"{' or '.join(expected_filenames)} not found in default search locations"


def build_default_stem_separation_provider_registry() -> dict[str, Any]:
    return {
        DEMUCS_PROVIDER: _separate_stems_with_demucs,
        ROFORMER_PROVIDER: _separate_stems_with_roformer,
    }


def _load_stem_separation_provider_path(provider_path: str) -> Any:
    module_name, sep, attr_name = provider_path.partition(":")
    if not sep or not module_name.strip() or not attr_name.strip():
        raise RuntimeError(
            f"invalid stem separation provider path '{provider_path}'; expected module.submodule:function"
        )
    module = importlib.import_module(module_name.strip())
    provider = getattr(module, attr_name.strip(), None)
    if provider is None or not callable(provider):
        raise RuntimeError(f"stem separation provider '{provider_path}' is not callable")
    return provider


ROFORMER_ENV_PYTHON = "AURAL_ROFORMER_PYTHON"
ROFORMER_ENV_REPO = "AURAL_ROFORMER_REPO"
ROFORMER_ENV_COMMAND = "AURAL_ROFORMER_COMMAND"
ROFORMER_ENV_TIMEOUT = "AURAL_ROFORMER_TIMEOUT_SEC"
ROFORMER_STEM_ROLE_ALIASES: dict[str, str] = {
    "vocal": "vocals",
    "vocals": "vocals",
    "voice": "vocals",
    "drum": "drums",
    "drums": "drums",
    "bass": "bass",
    "guitar": "guitar",
    "guitars": "guitar",
    "piano": "keys",
    "keys": "keys",
    "keyboard": "keys",
    "synth": "keys",
    "other": "other",
    "accompaniment": "other",
    "instrumental": "other",
    "no_vocals": "other",
}


def _config_or_env(config: dict[str, Any], key: str, env_name: str) -> str:
    value = config.get(key)
    if value is None:
        value = os.environ.get(env_name, "")
    return str(value).strip()


def _resolve_roformer_runtime(config: dict[str, Any]) -> tuple[Path | None, Path | None, str | None, str | None]:
    python_raw = _config_or_env(config, "roformer_python", ROFORMER_ENV_PYTHON)
    repo_raw = _config_or_env(config, "roformer_repo", ROFORMER_ENV_REPO)
    command = _config_or_env(config, "roformer_command", ROFORMER_ENV_COMMAND)
    if not python_raw or not repo_raw or not command:
        return None, None, None, (
            f"{ROFORMER_ENV_PYTHON}, {ROFORMER_ENV_REPO}, and {ROFORMER_ENV_COMMAND} are required"
        )

    python = Path(python_raw).expanduser()
    repo = Path(repo_raw).expanduser()
    if not python.exists():
        return None, None, None, f"{ROFORMER_ENV_PYTHON} does not exist: {python}"
    if not python.is_file():
        return None, None, None, f"{ROFORMER_ENV_PYTHON} is not a file: {python}"
    if not repo.exists():
        return None, None, None, f"{ROFORMER_ENV_REPO} does not exist: {repo}"
    if not repo.is_dir():
        return None, None, None, f"{ROFORMER_ENV_REPO} is not a directory: {repo}"
    return python, repo, command, None


def roformer_runtime_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return JSON-serializable RoFormer/MSST provider diagnostics."""
    cfg = config or {}
    python_raw = _config_or_env(cfg, "roformer_python", ROFORMER_ENV_PYTHON)
    repo_raw = _config_or_env(cfg, "roformer_repo", ROFORMER_ENV_REPO)
    command = _config_or_env(cfg, "roformer_command", ROFORMER_ENV_COMMAND)
    timeout_raw = _config_or_env(cfg, "roformer_timeout_sec", ROFORMER_ENV_TIMEOUT)

    python_path = Path(python_raw).expanduser() if python_raw else None
    repo_path = Path(repo_raw).expanduser() if repo_raw else None
    python, repo, command_template, runtime_error = _resolve_roformer_runtime(cfg)

    missing: list[str] = []
    if runtime_error:
        if not python_raw:
            missing.append(f"{ROFORMER_ENV_PYTHON} is unset")
        elif python_path is None or not python_path.exists():
            missing.append(f"{ROFORMER_ENV_PYTHON} does not exist: {python_path}")
        elif not python_path.is_file():
            missing.append(f"{ROFORMER_ENV_PYTHON} is not a file: {python_path}")

        if not repo_raw:
            missing.append(f"{ROFORMER_ENV_REPO} is unset")
        elif repo_path is None or not repo_path.exists():
            missing.append(f"{ROFORMER_ENV_REPO} does not exist: {repo_path}")
        elif not repo_path.is_dir():
            missing.append(f"{ROFORMER_ENV_REPO} is not a directory: {repo_path}")

        if not command:
            missing.append(f"{ROFORMER_ENV_COMMAND} is unset")
        if not missing:
            missing.append(runtime_error)

    return {
        "configured": python is not None and repo is not None and command_template is not None,
        "provider": ROFORMER_PROVIDER,
        "missing": missing,
        "env": {
            ROFORMER_ENV_PYTHON: python_raw or None,
            ROFORMER_ENV_REPO: repo_raw or None,
            ROFORMER_ENV_COMMAND: command or None,
            ROFORMER_ENV_TIMEOUT: timeout_raw or None,
        },
        "python": str(python_path) if python_path is not None else None,
        "python_exists": bool(python_path is not None and python_path.exists()),
        "python_is_file": bool(python_path is not None and python_path.is_file()),
        "repo": str(repo_path) if repo_path is not None else None,
        "repo_exists": bool(repo_path is not None and repo_path.exists()),
        "repo_is_dir": bool(repo_path is not None and repo_path.is_dir()),
        "command": command or None,
        "timeout_sec": _roformer_timeout_seconds(cfg),
    }


def _roformer_timeout_seconds(config: dict[str, Any]) -> float:
    raw_value = _config_or_env(config, "roformer_timeout_sec", ROFORMER_ENV_TIMEOUT)
    if not raw_value:
        return 60.0 * 60.0
    try:
        timeout = float(raw_value)
    except ValueError:
        return 60.0 * 60.0
    return max(1.0, timeout)


def _shell_quote(value: Path | str) -> str:
    text = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _format_roformer_command(
    command: str,
    *,
    python: Path,
    repo: Path,
    mix_wav: Path,
    out_dir: Path,
    stems_dir: Path,
    config_json: Path,
    mix_sha256: str,
    shifts: int,
) -> str:
    values = {
        "python": str(python),
        "python_q": _shell_quote(python),
        "repo_path": str(repo),
        "repo_path_q": _shell_quote(repo),
        "mix_wav": str(mix_wav),
        "mix_wav_q": _shell_quote(mix_wav),
        "out_dir": str(out_dir),
        "out_dir_q": _shell_quote(out_dir),
        "stems_dir": str(stems_dir),
        "stems_dir_q": _shell_quote(stems_dir),
        "config_json": str(config_json),
        "config_json_q": _shell_quote(config_json),
        "mix_sha256": mix_sha256,
        "shifts": str(max(1, int(shifts))),
    }
    return command.format(**values)


def _normalize_roformer_stem_role(path: Path) -> str | None:
    key = path.stem.strip().lower().replace(" ", "_").replace("-", "_")
    role = ROFORMER_STEM_ROLE_ALIASES.get(key, INPUT_STEM_ROLE_ALIASES.get(key, key))
    if role in KNOWN_INPUT_STEM_ROLES:
        return role
    return None


def _collect_roformer_stem_files(*roots: Path) -> dict[str, Path]:
    stem_files: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for candidate in sorted(root.rglob("*.wav")):
            role = _normalize_roformer_stem_role(candidate)
            if role is not None and role not in stem_files:
                stem_files[role] = candidate
    return stem_files


def _copy_roformer_stems(
    stem_files: dict[str, Path],
    stems_dir: Path,
    *,
    protected_roles: Iterable[str] | None = None,
) -> dict[str, str]:
    protected_set = {str(r).strip().lower() for r in (protected_roles or [])}
    out: dict[str, str] = {}
    for role, src in sorted(stem_files.items()):
        dst = stems_dir / f"{role}.wav"
        if role in protected_set and dst.is_file():
            out[role] = f"audio/stems/{dst.name}"
            continue
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        out[role] = f"audio/stems/{dst.name}"
    return out


def _separate_stems_with_roformer(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    config: dict[str, Any],
    protected_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    if bool(config.get("disable_stem_separation", False)) or str(
        config.get("stem_separation_provider", "")
    ).strip().lower() == "none":
        return {"ok": False, "status": "skipped", "reason": "stem separation disabled by config"}

    python, repo, command_template, runtime_error = _resolve_roformer_runtime(config)
    if python is None or repo is None or command_template is None:
        return {"ok": False, "status": "skipped", "provider": ROFORMER_PROVIDER, "reason": runtime_error}

    try:
        with tempfile.TemporaryDirectory(prefix="aural_roformer_") as temp_dir:
            temp = Path(temp_dir)
            out_dir = temp / "stems"
            out_dir.mkdir(parents=True, exist_ok=True)
            config_json = temp / "provider_config.json"
            config_json.write_text(
                json.dumps(
                    {
                        "mix_wav": str(mix_wav),
                        "out_dir": str(out_dir),
                        "stems_dir": str(stems_dir),
                        "mix_sha256": mix_sha256,
                        "shifts": max(1, int(shifts)),
                        "config": config,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            command = _format_roformer_command(
                command_template,
                python=python,
                repo=repo,
                mix_wav=mix_wav,
                out_dir=out_dir,
                stems_dir=stems_dir,
                config_json=config_json,
                mix_sha256=mix_sha256,
                shifts=shifts,
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                command,
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                shell=True,
                timeout=_roformer_timeout_seconds(config),
            )
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "status": "skipped",
                    "provider": ROFORMER_PROVIDER,
                    "reason": f"roformer provider command failed with exit code {proc.returncode}",
                    "stderr": proc.stderr.strip()[:1000],
                }

            discovered = _collect_roformer_stem_files(out_dir, stems_dir)
            if not discovered:
                return {
                    "ok": False,
                    "status": "skipped",
                    "provider": ROFORMER_PROVIDER,
                    "reason": "roformer provider command produced no recognized role-named wav stems",
                }

            stem_paths = _copy_roformer_stems(discovered, stems_dir, protected_roles=protected_roles)
            return {
                "ok": True,
                "status": "fresh",
                "provider": ROFORMER_PROVIDER,
                "stem_paths": stem_paths,
                "cache_hit": False,
                "shifts": int(max(1, shifts)),
                "repo_path": str(repo),
            }
    except (OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        return {"ok": False, "status": "skipped", "provider": ROFORMER_PROVIDER, "reason": str(exc)}


def validate_roformer_runtime(
    mix_wav: Path | str,
    *,
    stems_dir: Path | str | None = None,
    shifts: int = 1,
    config: dict[str, Any] | None = None,
    require_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run the external RoFormer provider once and return a validation report."""
    mix = Path(mix_wav)
    cfg = config or {}
    required = [str(role).strip().lower() for role in (require_roles or []) if str(role).strip()]
    if stems_dir is None:
        with tempfile.TemporaryDirectory(prefix="aural_roformer_validate_") as temp_dir:
            return validate_roformer_runtime(
                mix,
                stems_dir=Path(temp_dir) / "stems",
                shifts=shifts,
                config=cfg,
                require_roles=required,
            )

    stems = Path(stems_dir)
    stems.mkdir(parents=True, exist_ok=True)
    result = _separate_stems_with_roformer(
        mix,
        stems,
        mix_sha256=_sha256_file(mix),
        shifts=max(1, int(shifts)),
        config=cfg,
        protected_roles=[],
    )
    stem_paths = result.get("stem_paths", {})
    roles = sorted(stem_paths.keys()) if isinstance(stem_paths, dict) else []
    missing_roles = [role for role in required if role not in roles]
    ok = bool(result.get("ok")) and not missing_roles
    reason = result.get("reason")
    if bool(result.get("ok")) and missing_roles:
        reason = f"missing required RoFormer stem roles: {', '.join(missing_roles)}"

    return {
        "ok": ok,
        "provider": ROFORMER_PROVIDER,
        "mix_wav": str(mix),
        "stems_dir": str(stems),
        "status": result.get("status", "unknown"),
        "reason": reason,
        "require_roles": required,
        "missing_roles": missing_roles,
        "roles": roles,
        "stem_paths": stem_paths if isinstance(stem_paths, dict) else {},
        "runtime": roformer_runtime_status(cfg),
        "raw_result": result,
    }


def _run_stem_separation(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    config: dict[str, Any],
    provider_name: str,
    provider_path: str | None,
    protected_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    if provider_name == "none":
        return {"ok": False, "status": "skipped", "reason": "stem separation disabled by config", "provider": "none"}

    if provider_path:
        provider_fn = _load_stem_separation_provider_path(provider_path)
    else:
        provider_fn = build_default_stem_separation_provider_registry().get(provider_name)
        if provider_fn is None:
            raise RuntimeError(f"unknown stem separation provider '{provider_name}'")

    # Some custom providers loaded via --stem-separation-provider-path may
    # not accept the protected_roles kwarg. Inspect the provider's signature
    # and only forward when it's an accepted parameter so we don't break
    # third-party providers.
    import inspect as _inspect
    provider_sig = _inspect.signature(provider_fn)
    provider_kwargs: dict[str, Any] = {
        "mix_sha256": mix_sha256,
        "shifts": shifts,
        "config": config,
    }
    if "protected_roles" in provider_sig.parameters:
        provider_kwargs["protected_roles"] = protected_roles

    result = provider_fn(
        mix_wav,
        stems_dir,
        **provider_kwargs,
    )
    if isinstance(result, dict):
        result.setdefault("provider", provider_name)
        if provider_path:
            result.setdefault("provider_path", provider_path)
        return result
    raise RuntimeError(f"stem separation provider '{provider_name}' returned unsupported payload type")


def _prepare_demucs_weight_file(
    modelpack_zip: Path,
    modelpack_manifest: dict[str, Any],
) -> tuple[Path, dict[str, Any], Path]:
    weight_info = _primary_demucs_weight_info(modelpack_zip, modelpack_manifest)
    rel_path = str(weight_info.get("path", "")).strip()
    modelpack_id = _demucs_modelpack_id_from_manifest(modelpack_manifest)
    version = str(modelpack_manifest.get("version", "unknown")).strip() or "unknown"
    checksum = str(weight_info.get("sha256", "")).strip().lower()
    cache_root = Path(tempfile.gettempdir()) / "auralprimer_demucs_modelpacks"
    cache_dir = cache_root / f"{_safe_slug(modelpack_id)}_{_safe_slug(version)}_{checksum[:16]}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    weight_name = Path(rel_path).name
    weight_path = cache_dir / weight_name
    if not weight_path.is_file():
        with zipfile.ZipFile(modelpack_zip) as zf:
            data = zf.read(rel_path)
        weight_path.write_bytes(data)

    actual = _sha256_file(weight_path)
    if actual != checksum:
        raise RuntimeError(
            f"demucs weight checksum mismatch for {weight_path.name}: expected {checksum} got {actual}"
        )

    return weight_path, weight_info, cache_dir


def _load_demucs_model(weight_path: Path) -> Any:
    import torch
    from demucs.states import set_state

    try:
        package = torch.load(weight_path, map_location="cpu", weights_only=False)
    except TypeError:
        package = torch.load(weight_path, map_location="cpu")

    klass = package["klass"]
    args = package["args"]
    kwargs = dict(package["kwargs"])
    sig = inspect.signature(klass)
    for key in list(kwargs):
        if key not in sig.parameters:
            del kwargs[key]

    model = klass(*args, **kwargs)
    set_state(model, package["state"])
    model.eval()
    return model


def _demucs_separation_cache_dir(
    *,
    mix_sha256: str,
    modelpack_id: str,
    version: str,
    weight_sha: str,
    shifts: int,
) -> Path:
    if not _is_sha256_hex(weight_sha):
        raise ValueError("demucs separation cache requires a verified weight sha256")
    return (
        Path(tempfile.gettempdir())
        / "auralprimer_demucs_stem_cache"
        / (
            f"{mix_sha256[:24]}_{_safe_slug(modelpack_id)}_"
            f"{_safe_slug(version)}_{weight_sha[:12]}_sh{max(1, shifts)}"
        )
    )


def _read_wav_tensor(path: Path) -> tuple[Any, int]:
    import torch
    import wave
    from array import array

    with wave.open(str(path), "rb") as w:
        channels = int(w.getnchannels())
        sr = int(w.getframerate())
        sampwidth = int(w.getsampwidth())
        nframes = int(w.getnframes())
        if channels <= 0 or sr <= 0 or nframes <= 0:
            raise RuntimeError(f"invalid wav for demucs separation: {path}")
        if sampwidth != 2:
            raise RuntimeError(f"unsupported wav sample width for demucs separation: {sampwidth}")
        raw = w.readframes(nframes)

    pcm = array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()

    tensor = torch.tensor(list(pcm), dtype=torch.float32)
    frame_count = max(1, len(pcm) // channels)
    tensor = tensor[: frame_count * channels].view(frame_count, channels).t() / 32768.0
    if channels == 1:
        tensor = tensor.repeat(2, 1)
    elif channels > 2:
        tensor = tensor[:2, :]
    return tensor.contiguous(), sr


def _write_wav_tensor(path: Path, audio: Any, samplerate: int) -> None:
    import numpy as np
    import wave

    tensor = audio.detach().cpu().float().clamp(-1.0, 1.0)
    if tensor.ndim != 2:
        raise RuntimeError(f"expected 2D audio tensor for {path}, got shape {tuple(tensor.shape)}")
    channels, _length = tensor.shape
    if channels == 1:
        tensor = tensor.repeat(2, 1)
        channels = 2
    elif channels > 2:
        tensor = tensor[:2, :]
        channels = 2

    pcm = (tensor.t().numpy() * 32767.0).round().astype(np.int16, copy=False)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(samplerate))
        w.writeframes(pcm.tobytes())


def _normalize_demucs_stem_name(source_name: str) -> str:
    key = source_name.strip().lower().replace(" ", "_")
    return DEMUCS_STEM_ROLE_ALIASES.get(key, key)


def _normalize_input_stem_role(role_name: str) -> str | None:
    key = role_name.strip().lower().replace(" ", "_")
    normalized = INPUT_STEM_ROLE_ALIASES.get(key, key)
    if normalized not in KNOWN_INPUT_STEM_ROLES:
        return None
    return normalized


def _resolved_input_stem_paths(config: dict[str, Any]) -> dict[str, Path]:
    raw_paths = config.get("input_stem_paths")
    if not isinstance(raw_paths, dict):
        return {}

    out: dict[str, Path] = {}
    for role_name, raw_path in sorted(raw_paths.items(), key=lambda item: str(item[0])):
        normalized_role = _normalize_input_stem_role(str(role_name))
        if normalized_role is None:
            continue
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        src = Path(raw_path).expanduser()
        if not src.is_file():
            continue
        out[normalized_role] = src
    return out


def _copy_input_stems_from_config(stems_dir: Path, config: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for normalized_role, src in _resolved_input_stem_paths(config).items():
        dst = stems_dir / f"{normalized_role}.wav"
        shutil.copyfile(src, dst)
        out[normalized_role] = f"audio/stems/{dst.name}"
    return out


def _synthesize_mix_wav_from_input_stems(dst_wav: Path, config: dict[str, Any]) -> tuple[float, int] | None:
    stem_paths = _resolved_input_stem_paths(config)
    if not stem_paths:
        return None

    import numpy as np
    import wave

    mixed: np.ndarray | None = None
    target_sr: int | None = None

    for stem_path in stem_paths.values():
        with wave.open(str(stem_path), "rb") as w:
            channels = int(w.getnchannels())
            sr = int(w.getframerate())
            sampwidth = int(w.getsampwidth())
            nframes = int(w.getnframes())
            if channels <= 0 or sr <= 0 or nframes <= 0:
                raise RuntimeError(f"invalid wav for synthesized mix: {stem_path}")
            if sampwidth != 2:
                raise RuntimeError(
                    f"unsupported wav sample width for synthesized mix: {stem_path} ({sampwidth})"
                )
            raw = w.readframes(nframes)

        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32, copy=False)
        frame_count = max(1, audio.size // channels)
        audio = audio[: frame_count * channels].reshape(frame_count, channels)
        if channels == 1:
            audio = np.repeat(audio, 2, axis=1)
        elif channels > 2:
            audio = audio[:, :2]

        audio = audio / 32768.0
        if target_sr is None:
            target_sr = sr
        elif sr != target_sr:
            raise RuntimeError(
                f"configured input stems must share the same sample rate; got {sr} for {stem_path} vs {target_sr}"
            )

        if mixed is None:
            mixed = audio.copy()
        else:
            if mixed.shape[0] < audio.shape[0]:
                pad = np.zeros((audio.shape[0] - mixed.shape[0], mixed.shape[1]), dtype=np.float32)
                mixed = np.vstack((mixed, pad))
            elif audio.shape[0] < mixed.shape[0]:
                pad = np.zeros((mixed.shape[0] - audio.shape[0], audio.shape[1]), dtype=np.float32)
                audio = np.vstack((audio, pad))
            mixed += audio

    if mixed is None or target_sr is None:
        return None

    mixed /= max(1, len(stem_paths))
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.999:
        mixed *= 0.98 / peak

    pcm = (np.clip(mixed, -1.0, 1.0) * 32767.0).round().astype(np.int16, copy=False)
    with wave.open(str(dst_wav), "wb") as w:
        w.setnchannels(int(pcm.shape[1]) if pcm.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(int(target_sr))
        w.writeframes(pcm.tobytes())

    return _wav_duration_sec(dst_wav)


def _is_safe_demucs_cache_stem_filename(filename: str) -> bool:
    value = filename.strip()
    if not value or "\\" in value or "/" in value or ":" in value:
        return False
    path = Path(value)
    return path.name == value and path.suffix.lower() == ".wav" and path.stem.strip() != ""


def _demucs_cache_meta_matches(
    cache_meta: dict[str, Any],
    *,
    cache_dir: Path,
    mix_sha256: str,
    modelpack_id: str,
    version: str,
    architecture: str,
    weight_sha: str,
    shifts: int,
) -> bool:
    expected = {
        "provider": DEMUCS_PROVIDER,
        "mix_sha256": mix_sha256,
        "modelpack_id": modelpack_id,
        "modelpack_version": version,
        "architecture": architecture,
        "weight_sha256": weight_sha,
        "shifts": int(max(1, shifts)),
    }
    for key, value in expected.items():
        if cache_meta.get(key) != value:
            return False

    stem_files = cache_meta.get("stem_files", {})
    if not isinstance(stem_files, dict) or not stem_files:
        return False
    for stem_name, filename in stem_files.items():
        stem_role = str(stem_name).strip().lower()
        file_name = str(filename)
        if stem_role not in KNOWN_INPUT_STEM_ROLES:
            return False
        if not _is_safe_demucs_cache_stem_filename(file_name):
            return False
        if not (cache_dir / file_name).is_file():
            return False
    return True


def _copy_cached_stems(
    cache_dir: Path,
    stems_dir: Path,
    stem_files: dict[str, str],
    *,
    protected_roles: Iterable[str] | None = None,
) -> dict[str, str]:
    """Copy Demucs-cached stems into the pack's audio/stems/ directory.

    ``protected_roles`` lists roles that the user already supplied as input
    stems (typically the high-quality Suno-exported (Keyboard).wav landing
    at audio/stems/keys.wav before separation ran). For those roles we
    KEEP the existing on-disk file and report THAT path in the returned
    dict, rather than overwriting with whatever Demucs separated from the
    synthesized mix. Without this guard, Demucs's piano head can mis-
    classify the gospel-piano material the user explicitly provided as
    a clean stem -- routing parts of it into ``other``/``guitar`` and
    leaving the canonical ``keys.wav`` partially empty.
    """
    protected_set = {str(r).strip().lower() for r in (protected_roles or [])}
    out: dict[str, str] = {}
    for stem_name, filename in stem_files.items():
        stem_role = str(stem_name).strip().lower()
        if stem_role not in KNOWN_INPUT_STEM_ROLES or not _is_safe_demucs_cache_stem_filename(str(filename)):
            continue
        src = cache_dir / filename
        dst = stems_dir / filename
        if stem_role in protected_set and dst.is_file():
            # User-supplied input stem already on disk -- keep it. Skip the
            # cache->pack copy for this role and report the existing file
            # path so the caller's manifest stays consistent.
            out[stem_role] = f"audio/stems/{filename}"
            continue
        if not src.is_file():
            continue
        shutil.copyfile(src, dst)
        out[stem_role] = f"audio/stems/{filename}"
    return out


def _separate_stems_with_demucs(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    config: dict[str, Any],
    protected_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    if bool(config.get("disable_stem_separation", False)) or str(
        config.get("stem_separation_provider", "")
    ).strip().lower() == "none":
        return {"ok": False, "status": "skipped", "reason": "stem separation disabled by config"}

    modelpack_zip, modelpack_manifest, err = _resolve_demucs_modelpack(config)
    if modelpack_zip is None or modelpack_manifest is None:
        return {"ok": False, "status": "skipped", "reason": err or "demucs modelpack unavailable"}

    modelpack_id = _demucs_modelpack_id_from_manifest(modelpack_manifest)
    if modelpack_id == DEMUCS_FT_DRUMS_MODELPACK_ID:
        return _separate_stems_with_demucs_ft_drums(
            mix_wav,
            stems_dir,
            mix_sha256=mix_sha256,
            shifts=shifts,
            modelpack_zip=modelpack_zip,
            modelpack_manifest=modelpack_manifest,
            protected_roles=protected_roles,
        )

    return _separate_stems_with_demucs_single_model(
        mix_wav,
        stems_dir,
        mix_sha256=mix_sha256,
        shifts=shifts,
        modelpack_zip=modelpack_zip,
        modelpack_manifest=modelpack_manifest,
        protected_roles=protected_roles,
    )


def _separate_stems_with_demucs_single_model(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    modelpack_zip: Path,
    modelpack_manifest: dict[str, Any],
    protected_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    try:
        from demucs.apply import apply_model
        from demucs.audio import convert_audio
    except Exception as exc:
        return {"ok": False, "status": "skipped", "reason": f"demucs runtime unavailable: {exc}"}

    try:
        weight_path, weight_info, modelpack_cache_dir = _prepare_demucs_weight_file(modelpack_zip, modelpack_manifest)
    except Exception as exc:
        return {"ok": False, "status": "skipped", "reason": f"demucs modelpack prepare failed: {exc}"}

    version = str(modelpack_manifest.get("version", "unknown")).strip() or "unknown"
    modelpack_id = _demucs_modelpack_id_from_manifest(modelpack_manifest)
    architecture = str(modelpack_manifest.get("architecture", "unknown")).strip() or "unknown"
    weight_sha = str(weight_info.get("sha256", "")).strip().lower()
    sep_cache_dir = _demucs_separation_cache_dir(
        mix_sha256=mix_sha256,
        modelpack_id=modelpack_id,
        version=version,
        weight_sha=weight_sha,
        shifts=shifts,
    )
    sep_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_meta_path = sep_cache_dir / "separation_meta.json"

    if cache_meta_path.is_file():
        try:
            cache_meta = json.loads(cache_meta_path.read_text("utf-8"))
            if isinstance(cache_meta, dict) and _demucs_cache_meta_matches(
                cache_meta,
                cache_dir=sep_cache_dir,
                mix_sha256=mix_sha256,
                modelpack_id=modelpack_id,
                version=version,
                architecture=architecture,
                weight_sha=weight_sha,
                shifts=shifts,
            ):
                stem_files = cache_meta.get("stem_files", {})
                stem_paths = _copy_cached_stems(
                    sep_cache_dir, stems_dir, stem_files,
                    protected_roles=protected_roles,
                )
                return {
                    "ok": True,
                    "status": "cached",
                    "provider": DEMUCS_PROVIDER,
                    "modelpack_id": modelpack_id,
                    "modelpack_version": version,
                    "architecture": architecture,
                    "modelpack_path": str(modelpack_zip),
                    "weight_path": str(weight_path),
                    "stem_paths": stem_paths,
                    "cache_hit": True,
                    "shifts": int(max(1, shifts)),
                }
        except Exception:
            pass

    try:
        model = _load_demucs_model(weight_path)
        wav, sr = _read_wav_tensor(mix_wav)
        wav = convert_audio(wav, sr, model.samplerate, model.audio_channels)

        ref = wav.mean(0)
        ref_mean = ref.mean()
        ref_std = ref.std().clamp(min=1e-6)
        wav = (wav - ref_mean) / ref_std

        device = select_device("AURAL_DEMUCS_DEVICE")
        sources = apply_model(
            model,
            wav[None],
            device=device,
            shifts=max(1, int(shifts)),
            split=True,
            overlap=0.25,
            progress=False,
            num_workers=0,
        )[0]
        sources = (sources * ref_std) + ref_mean

        stem_files: dict[str, str] = {}
        for source, source_name in zip(sources, model.sources):
            stem_name = _normalize_demucs_stem_name(str(source_name))
            filename = f"{stem_name}.wav"
            _write_wav_tensor(sep_cache_dir / filename, source, int(model.samplerate))
            stem_files[stem_name] = filename

        cache_meta = {
            "provider": DEMUCS_PROVIDER,
            "mix_sha256": mix_sha256,
            "modelpack_id": modelpack_id,
            "modelpack_version": version,
            "architecture": architecture,
            "weight_sha256": weight_sha,
            "modelpack_path": str(modelpack_zip),
            "weight_path": str(weight_path),
            "samplerate": int(model.samplerate),
            "audio_channels": int(model.audio_channels),
            "sources": [str(source) for source in model.sources],
            "stem_files": stem_files,
            "cache_key": sep_cache_dir.name,
            "shifts": int(max(1, shifts)),
            "device": device,
        }
        cache_meta_path.write_text(json.dumps(cache_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        stem_paths = _copy_cached_stems(
            sep_cache_dir, stems_dir, stem_files,
            protected_roles=protected_roles,
        )
        return {
            "ok": True,
            "status": "fresh",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": modelpack_id,
            "modelpack_version": version,
            "architecture": architecture,
            "modelpack_path": str(modelpack_zip),
            "weight_path": str(weight_path),
            "stem_paths": stem_paths,
            "cache_hit": False,
            "shifts": int(max(1, shifts)),
            "device": device,
        }
    except Exception as exc:
        return {"ok": False, "status": "skipped", "reason": f"demucs separation failed: {exc}"}


def _separate_stems_with_demucs_ft_drums(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    modelpack_zip: Path,
    modelpack_manifest: dict[str, Any],
    protected_roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run default Demucs for full stems, then replace drums with FT output."""

    baseline_zip, baseline_manifest, baseline_err = _resolve_demucs_modelpack(
        {"stem_separation_modelpack_id": DEMUCS_MODELPACK_ID}
    )
    if baseline_zip is None or baseline_manifest is None:
        return {
            "ok": False,
            "status": "skipped",
            "reason": f"demucs_ft_drums baseline modelpack unavailable: {baseline_err or 'demucs_6 unavailable'}",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
        }

    baseline = _separate_stems_with_demucs_single_model(
        mix_wav,
        stems_dir,
        mix_sha256=mix_sha256,
        shifts=shifts,
        modelpack_zip=baseline_zip,
        modelpack_manifest=baseline_manifest,
        protected_roles=protected_roles,
    )
    if not baseline.get("ok"):
        return {
            "ok": False,
            "status": "skipped",
            "reason": f"demucs_ft_drums baseline separation failed: {baseline.get('reason') or baseline.get('status')}",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
            "baseline_result": baseline,
        }

    refinement = _separate_stems_with_demucs_single_model(
        mix_wav,
        stems_dir,
        mix_sha256=mix_sha256,
        shifts=shifts,
        modelpack_zip=modelpack_zip,
        modelpack_manifest=modelpack_manifest,
        protected_roles=protected_roles,
    )
    if not refinement.get("ok"):
        return {
            "ok": False,
            "status": "skipped",
            "reason": f"demucs_ft_drums refinement failed: {refinement.get('reason') or refinement.get('status')}",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
            "baseline_result": baseline,
            "refinement_result": refinement,
        }

    baseline_paths = baseline.get("stem_paths")
    refinement_paths = refinement.get("stem_paths")
    if not isinstance(baseline_paths, dict) or not isinstance(refinement_paths, dict):
        return {
            "ok": False,
            "status": "skipped",
            "reason": "demucs_ft_drums requires baseline and refinement stem_paths",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
            "baseline_result": baseline,
            "refinement_result": refinement,
        }
    refined_drums = refinement_paths.get("drums")
    if not isinstance(refined_drums, str) or not refined_drums.strip():
        return {
            "ok": False,
            "status": "skipped",
            "reason": "demucs_ft_drums refinement did not produce a drums stem",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
            "baseline_result": baseline,
            "refinement_result": refinement,
        }

    version = str(modelpack_manifest.get("version", "unknown")).strip() or "unknown"
    architecture = str(modelpack_manifest.get("architecture", "unknown")).strip() or "unknown"
    baseline_manifest_id = _demucs_modelpack_id_from_manifest(baseline_manifest)
    stem_paths = {str(role): str(path) for role, path in baseline_paths.items()}
    stem_paths["drums"] = refined_drums
    status = "cached" if baseline.get("status") == "cached" and refinement.get("status") == "cached" else "fresh"

    return {
        "ok": True,
        "status": status,
        "provider": DEMUCS_PROVIDER,
        "modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID,
        "modelpack_version": version,
        "architecture": architecture,
        "modelpack_path": str(modelpack_zip),
        "stem_paths": stem_paths,
        "cache_hit": status == "cached",
        "shifts": int(max(1, shifts)),
        "composite": True,
        "refinement_role": "drums",
        "baseline_modelpack_id": baseline_manifest_id,
        "baseline_modelpack_version": baseline.get("modelpack_version"),
        "baseline_result": baseline,
        "refinement_result": refinement,
    }


MIDI_TICKS_PER_QUARTER = 480
MIDI_CHANNEL_BASS = 0
MIDI_CHANNEL_RHYTHM_GUITAR = 1
MIDI_CHANNEL_LEAD_GUITAR = 2
MIDI_CHANNEL_KEYS = 3
MIDI_CHANNEL_MELODIC = 4  # legacy fallback
MIDI_CHANNEL_VOCALS = 5
MIDI_CHANNEL_DRUMS = 9
MIDI_CHANNEL_STRUCTURE = 15
DRUM_AUDIT_DEFAULT_THRESHOLDS_DBFS: tuple[float, ...] = (-50.0, -45.0, -40.0)
DRUM_AUDIT_DBFS_FLOOR: float = -120.0
DRUM_NOTE_LABELS: dict[int, str] = {
    35: "kick",
    36: "kick",
    38: "snare",
    40: "snare",
    41: "tom_floor",
    42: "hh_closed",
    46: "hh_open",
    47: "tom_low",
    49: "crash",
    50: "tom_high",
    51: "ride",
}
DRUM_CLASS_TO_CANONICAL_NOTE: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "hh_closed": 42,
    "hh_open": 46,
    "crash": 49,
    "ride": 51,
    "tom_high": 50,
    "tom_low": 47,
    "tom_floor": 41,
    "hi_hat": 42,
    "toms": 47,
    "cymbals": 49,
}

# Map instrument roles to MIDI channels.
_INSTRUMENT_MIDI_CHANNELS: dict[str, int] = {
    "bass": MIDI_CHANNEL_BASS,
    "rhythm_guitar": MIDI_CHANNEL_RHYTHM_GUITAR,
    "lead_guitar": MIDI_CHANNEL_LEAD_GUITAR,
    "keys": MIDI_CHANNEL_KEYS,
    "melodic": MIDI_CHANNEL_MELODIC,
    "vocals": MIDI_CHANNEL_VOCALS,
}

# Pretty track names for MIDI output.
_INSTRUMENT_TRACK_NAMES: dict[str, str] = {
    "bass": "Bass",
    "rhythm_guitar": "Rhythm Guitar",
    "lead_guitar": "Lead Guitar",
    "keys": "Keys",
    "melodic": "Melodic",
    "vocals": "Vocals",
}


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _sec_to_ticks(sec: float, *, bpm: float, ticks_per_quarter: int = MIDI_TICKS_PER_QUARTER) -> int:
    if sec <= 0:
        return 0
    beats = sec * (bpm / 60.0)
    return int(round(beats * float(ticks_per_quarter)))


def _encode_vlq(value: int) -> bytes:
    v = _clamp_int(int(value), 0, 0x0FFFFFFF)
    out = [v & 0x7F]
    v >>= 7
    while v > 0:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    out.reverse()
    return bytes(out)


def _meta_event(meta_type: int, payload: bytes) -> bytes:
    return bytes([0xFF, meta_type & 0x7F]) + _encode_vlq(len(payload)) + payload


def _meta_text_event(meta_type: int, text: str) -> bytes:
    return _meta_event(meta_type, text.encode("utf-8", errors="replace"))


def _note_on(channel: int, note: int, velocity: int) -> bytes:
    ch = _clamp_int(channel, 0, 15)
    n = _clamp_int(note, 0, 127)
    v = _clamp_int(velocity, 1, 127)
    return bytes([0x90 | ch, n, v])


def _note_off(channel: int, note: int) -> bytes:
    ch = _clamp_int(channel, 0, 15)
    n = _clamp_int(note, 0, 127)
    return bytes([0x80 | ch, n, 0])


def _build_midi_track_chunk(events: list[tuple[int, bytes]]) -> bytes:
    events_sorted = sorted(events, key=lambda item: item[0])
    body = bytearray()
    last_tick = 0

    for abs_tick, payload in events_sorted:
        t = max(last_tick, int(abs_tick))
        body.extend(_encode_vlq(t - last_tick))
        body.extend(payload)
        last_tick = t

    body.extend(b"\x00\xFF\x2F\x00")  # End of track
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def _midi_channel_pool(base_channel: int) -> list[int]:
    base = _clamp_int(base_channel, 0, 15)
    reserved = {MIDI_CHANNEL_DRUMS, MIDI_CHANNEL_STRUCTURE}
    pool = [base]
    pool.extend(ch for ch in range(16) if ch != base and ch not in reserved)
    return pool


def _melodic_note_midi_events(
    notes: list[Any],
    *,
    bpm: float,
    base_channel: int,
    default_note_dur: int,
) -> list[tuple[int, bytes]]:
    track_events: list[tuple[int, bytes]] = []
    channel_pool = _midi_channel_pool(base_channel)
    active_until: dict[tuple[int, int], int] = {}

    for n in sorted(
        notes,
        key=lambda item: (
            float(getattr(item, "t_on", 0.0)),
            int(getattr(item, "pitch", 60)),
            float(getattr(item, "t_off", 0.0)),
        ),
    ):
        t_on = _sec_to_ticks(float(getattr(n, "t_on", 0.0)), bpm=bpm)
        t_off = _sec_to_ticks(float(getattr(n, "t_off", 0.0)), bpm=bpm)
        if t_off <= t_on:
            t_off = t_on + default_note_dur
        pitch = _clamp_int(int(getattr(n, "pitch", 60)), 0, 127)
        vel = _clamp_int(int(getattr(n, "velocity", 90)), 1, 127)

        channel = channel_pool[0]
        for candidate in channel_pool:
            if active_until.get((candidate, pitch), -1) <= t_on:
                channel = candidate
                break
        else:
            channel = min(channel_pool, key=lambda candidate: active_until.get((candidate, pitch), -1))

        active_until[(channel, pitch)] = t_off
        track_events.append((t_on, _note_on(channel, pitch, vel)))
        track_events.append((t_off, _note_off(channel, pitch)))

    return track_events


def _build_notes_mid_bytes(
    *,
    bpm: float,
    beats: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    drum_events: list[Any],
    melodic_notes: list[Any] | None = None,
    instrument_tracks: dict[str, list[Any]] | None = None,
) -> bytes:
    """Build a multi-track MIDI file.

    If ``instrument_tracks`` is provided, each instrument gets its own MIDI track
    with a dedicated channel.  Otherwise, falls back to a single 'Melodic' track
    from ``melodic_notes`` for backward compatibility.
    """
    bpm_safe = 120.0 if bpm <= 0 else float(bpm)
    tempo_us_per_quarter = _clamp_int(int(round(60_000_000.0 / bpm_safe)), 1, 0xFFFFFF)

    conductor_events: list[tuple[int, bytes]] = [
        (0, _meta_text_event(0x03, "Conductor")),
        (0, _meta_event(0x51, tempo_us_per_quarter.to_bytes(3, "big"))),
        (0, _meta_event(0x58, bytes([4, 2, 24, 8]))),  # 4/4
    ]

    for sec in sections:
        t0 = float(sec.get("t0", 0.0) or 0.0)
        label = str(sec.get("label", "section"))
        conductor_events.append((_sec_to_ticks(t0, bpm=bpm_safe), _meta_text_event(0x06, f"SECTION:{label}")))

    for beat in beats:
        t = float(beat.get("t", 0.0) or 0.0)
        bar = int(beat.get("bar", 0) or 0) + 1
        beat_num = int(beat.get("beat", 0) or 0) + 1
        conductor_events.append((_sec_to_ticks(t, bpm=bpm_safe), _meta_text_event(0x06, f"BEAT:{bar}:{beat_num}")))

    structure_events: list[tuple[int, bytes]] = [(0, _meta_text_event(0x03, "Structure"))]
    beat_dur_ticks = max(1, MIDI_TICKS_PER_QUARTER // 16)
    section_dur_ticks = max(1, MIDI_TICKS_PER_QUARTER // 4)

    for beat in beats:
        t = float(beat.get("t", 0.0) or 0.0)
        tick = _sec_to_ticks(t, bpm=bpm_safe)
        downbeat = int(beat.get("beat", 0) or 0) == 0
        note = 36 if downbeat else 37
        vel = 104 if downbeat else 80
        structure_events.append((tick, _note_on(MIDI_CHANNEL_STRUCTURE, note, vel)))
        structure_events.append((tick + beat_dur_ticks, _note_off(MIDI_CHANNEL_STRUCTURE, note)))

    for sec in sections:
        t0 = float(sec.get("t0", 0.0) or 0.0)
        tick = _sec_to_ticks(t0, bpm=bpm_safe)
        structure_events.append((tick, _note_on(MIDI_CHANNEL_STRUCTURE, 84, 112)))
        structure_events.append((tick + section_dur_ticks, _note_off(MIDI_CHANNEL_STRUCTURE, 84)))

    drum_track_events: list[tuple[int, bytes]] = [(0, _meta_text_event(0x03, "Drums"))]
    for ev in drum_events:
        t_on = _sec_to_ticks(float(getattr(ev, "time", 0.0)), bpm=bpm_safe)
        dur = _sec_to_ticks(float(getattr(ev, "duration", 0.05)), bpm=bpm_safe)
        dur = max(1, dur)
        note = _clamp_int(int(getattr(ev, "note", 36)), 0, 127)
        vel = _clamp_int(int(getattr(ev, "velocity", 90)), 1, 127)
        drum_track_events.append((t_on, _note_on(MIDI_CHANNEL_DRUMS, note, vel)))
        drum_track_events.append((t_on + dur, _note_off(MIDI_CHANNEL_DRUMS, note)))

    tracks: list[list[tuple[int, bytes]]] = [
        conductor_events,
        structure_events,
        drum_track_events,
    ]

    default_note_dur = max(1, MIDI_TICKS_PER_QUARTER // 8)

    if instrument_tracks:
        # Write each instrument as a separate MIDI track with dedicated channel.
        for role in ("bass", "rhythm_guitar", "lead_guitar", "keys", "vocals"):
            notes = instrument_tracks.get(role)
            if not notes:
                continue
            channel = _INSTRUMENT_MIDI_CHANNELS.get(role, MIDI_CHANNEL_MELODIC)
            track_name = _INSTRUMENT_TRACK_NAMES.get(role, role.replace("_", " ").title())
            track_events: list[tuple[int, bytes]] = [(0, _meta_text_event(0x03, track_name))]
            track_events.extend(
                _melodic_note_midi_events(
                    notes,
                    bpm=bpm_safe,
                    base_channel=channel,
                    default_note_dur=default_note_dur,
                )
            )
            tracks.append(track_events)
    elif melodic_notes:
        # Legacy single-melodic-track path.
        melodic_track_events: list[tuple[int, bytes]] = [(0, _meta_text_event(0x03, "Melodic"))]
        melodic_track_events.extend(
            _melodic_note_midi_events(
                melodic_notes,
                bpm=bpm_safe,
                base_channel=MIDI_CHANNEL_MELODIC,
                default_note_dur=default_note_dur,
            )
        )
        tracks.append(melodic_track_events)

    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")  # format 1 (multi-track)
        + len(tracks).to_bytes(2, "big")
        + MIDI_TICKS_PER_QUARTER.to_bytes(2, "big")
    )
    chunks = b"".join(_build_midi_track_chunk(track_events) for track_events in tracks)
    return header + chunks


def _find_preferred_mix_audio_in_dir(src_dir: Path) -> Path | None:
    preferred = [
        "mix.wav",
        "mix.mp3",
        "mix.ogg",
        "mix.flac",
    ]
    for name in preferred:
        p = src_dir / name
        if p.is_file():
            return p
    return None


def _find_audio_source_in_dir(src_dir: Path) -> Path | None:
    """Find one audio source file in a folder deterministically.

    Priority:
    1) common mix file names in the directory root
    2) first audio file by sorted relative path (recursive)
    """

    preferred_mix = _find_preferred_mix_audio_in_dir(src_dir)
    if preferred_mix is not None:
        return preferred_mix

    exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
    candidates = [p for p in src_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.relative_to(src_dir).as_posix().lower())
    return candidates[0]


def _resolve_requested_drum_stem_path(
    args: argparse.Namespace,
    config: dict[str, Any],
) -> tuple[Path | None, str]:
    explicit = getattr(args, "drum_stem_path", None)
    if isinstance(explicit, str) and explicit.strip():
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate, "arg"

    configured = config.get("drum_stem_path")
    if isinstance(configured, str) and configured.strip():
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate, "config"

    return None, "mix_fallback"


def _resolve_transcription_options(
    args: argparse.Namespace,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if config is None:
        config = {}
    raw_drum_filter = (
        getattr(args, "drum_filter", None)
        if getattr(args, "drum_filter", None) is not None
        else config.get("drum_filter", config.get("drum_engine", "auto"))
    )
    raw_drum_filter_text = "" if raw_drum_filter is None else str(raw_drum_filter).strip().lower()
    drum_engine_selection = "profile" if raw_drum_filter_text in {"", "auto"} else "explicit"
    normalized_drum_filter, warnings = resolve_drum_engine(raw_drum_filter)

    raw_melodic_method = getattr(args, "melodic_method", DEFAULT_MELODIC_METHOD)
    melodic_method = validate_melodic_method(raw_melodic_method)
    if melodic_method is None:
        return None, (
            f"invalid --melodic-method '{raw_melodic_method}'. "
            f"supported: {', '.join(KNOWN_MELODIC_METHODS)}"
        )

    raw_wholemix = (
        getattr(args, "wholemix_transcriber", None)
        if getattr(args, "wholemix_transcriber", None) is not None
        else config.get("wholemix_transcriber")
    )
    wholemix_transcriber = str(raw_wholemix).strip().lower() if raw_wholemix else ""
    if wholemix_transcriber and wholemix_transcriber not in {"muscriptor"}:
        return None, f"invalid --wholemix-transcriber '{raw_wholemix}'. supported: muscriptor"

    raw_transcription_profile = (
        getattr(args, "transcription_profile", None)
        or config.get("transcription_profile")
        or DEFAULT_TRANSCRIPTION_PROFILE
    )
    transcription_profile = validate_transcription_profile(raw_transcription_profile)
    if transcription_profile is None:
        return None, (
            f"invalid transcription profile '{raw_transcription_profile}'. "
            f"supported: {', '.join(KNOWN_TRANSCRIPTION_PROFILES)}"
        )

    shifts_raw = getattr(args, "shifts", 1)
    try:
        shifts = int(shifts_raw)
    except Exception:
        return None, f"invalid --shifts '{shifts_raw}': must be integer >= 1"
    if shifts < 1:
        return None, f"invalid --shifts '{shifts_raw}': must be integer >= 1"

    multi_filter = bool(getattr(args, "multi_filter", False))
    raw_beat_analysis_mode = (
        getattr(args, "beat_analysis_mode", None)
        or config.get("beat_analysis_mode")
        or DEFAULT_BEAT_ANALYSIS_MODE
    )
    beat_analysis_mode = str(raw_beat_analysis_mode).strip().lower()
    if beat_analysis_mode not in KNOWN_BEAT_ANALYSIS_MODES:
        return None, (
            f"invalid beat analysis mode '{raw_beat_analysis_mode}'. "
            f"supported: {', '.join(KNOWN_BEAT_ANALYSIS_MODES)}"
        )

    raw_provider = (
        getattr(args, "stem_separation_provider", None)
        or config.get("stem_separation_provider")
        or DEFAULT_STEM_SEPARATION_PROVIDER
    )
    provider_path = (
        getattr(args, "stem_separation_provider_path", None)
        or config.get("stem_separation_provider_path")
    )
    provider_name = str(raw_provider).strip().lower()
    if provider_name in {"", "auto"}:
        if provider_path:
            provider_name = "external"
        else:
            modelpack_zip, _, modelpack_err = _resolve_demucs_modelpack(config)
            if modelpack_zip is not None:
                provider_name = DEMUCS_PROVIDER
            else:
                # Demucs gate flip (see docs/research-decision-gates.md
                # 'ADT Architecture Revision (2026-05-07)' and path 3 of
                # docs/research-deep-dive-adt-2026-05-07.md). When `auto`
                # falls through to no separator because the modelpack is
                # absent, surface a structured warning so downstream
                # consumers can flag the import as separator-degraded
                # rather than silently treating it as a normal `none`
                # selection. Sept 2025 Enhanced-ADT literature reports
                # +5–10% F1 from Demucs preprocessing alone, so users
                # importing without it are paying a meaningful drum
                # transcription quality cost.
                provider_name = "none"
                requested_demucs_id = _requested_demucs_modelpack_id(config) or DEMUCS_MODELPACK_ID
                warnings.append(
                    "stem_separation_provider=auto fell through to 'none' because the "
                    f"{requested_demucs_id} modelpack was not found "
                    f"({modelpack_err or 'no candidates checked'}); production drum "
                    "transcription quality is reduced without Demucs preprocessing"
                )
    elif provider_name not in {DEMUCS_PROVIDER, ROFORMER_PROVIDER, "none"} and not provider_path:
        provider_path = str(raw_provider).strip()
        provider_name = "external"

    requested_drum_stem, requested_drum_stem_kind = _resolve_requested_drum_stem_path(args, config)
    modelpack_zip, modelpack_manifest, _modelpack_err = _resolve_demucs_modelpack(config)

    # Drum stem-silence gate (defensive post-filter, see transcription.py).
    # CLI > config > env > built-in default; env handling lives inside
    # validate_drum_events_against_stem_silence so leaving these None
    # is the desired behavior unless the user overrode them at the CLI/config layer.
    drum_silence_gate_dbfs_raw = (
        getattr(args, "drum_silence_gate_dbfs", None)
        if getattr(args, "drum_silence_gate_dbfs", None) is not None
        else config.get("drum_silence_gate_dbfs")
    )
    drum_silence_gate_window_ms_raw = (
        getattr(args, "drum_silence_gate_window_ms", None)
        if getattr(args, "drum_silence_gate_window_ms", None) is not None
        else config.get("drum_silence_gate_window_ms")
    )
    drum_silence_gate_dbfs: float | None
    if drum_silence_gate_dbfs_raw is None:
        drum_silence_gate_dbfs = None
    else:
        try:
            drum_silence_gate_dbfs = float(drum_silence_gate_dbfs_raw)
        except (TypeError, ValueError):
            return None, f"invalid --drum-silence-gate-dbfs '{drum_silence_gate_dbfs_raw}': must be a number"
    drum_silence_gate_window_ms: float | None
    if drum_silence_gate_window_ms_raw is None:
        drum_silence_gate_window_ms = None
    else:
        try:
            drum_silence_gate_window_ms = float(drum_silence_gate_window_ms_raw)
        except (TypeError, ValueError):
            return None, (
                f"invalid --drum-silence-gate-window-ms '{drum_silence_gate_window_ms_raw}': must be a number"
            )
        if drum_silence_gate_window_ms <= 0.0:
            return None, (
                f"invalid --drum-silence-gate-window-ms '{drum_silence_gate_window_ms_raw}': must be > 0"
            )

    drum_silence_gate_disabled = bool(
        getattr(args, "drum_silence_gate_disabled", False)
        or config.get("drum_silence_gate_disabled", False)
    )

    return {
        "drum_engine_requested": raw_drum_filter,
        "drum_engine": "profile" if drum_engine_selection == "profile" else normalized_drum_filter,
        "drum_filter_requested": raw_drum_filter,
        "drum_filter": "profile" if drum_engine_selection == "profile" else normalized_drum_filter,
        "drum_engine_selection": drum_engine_selection,
        "drum_profile_engines": (
            drum_engines_for_profile(transcription_profile)
            if drum_engine_selection == "profile"
            else []
        ),
        "drum_source_kind": requested_drum_stem_kind,
        "drum_source_path": str(requested_drum_stem) if requested_drum_stem is not None else None,
        "drum_source_sha256": _sha256_file(requested_drum_stem) if requested_drum_stem is not None else None,
        "stem_separation_provider": provider_name,
        "stem_separation_provider_path": provider_path,
        "stem_separation_modelpack_id": (
            str(modelpack_manifest.get("id")) if isinstance(modelpack_manifest, dict) else None
        ),
        "stem_separation_modelpack_version": (
            str(modelpack_manifest.get("version")) if isinstance(modelpack_manifest, dict) else None
        ),
        "warnings": warnings,
        "melodic_method": melodic_method,
        "wholemix_transcriber": wholemix_transcriber,
        "transcription_profile": transcription_profile,
        "beat_analysis_mode": beat_analysis_mode,
        "shifts": shifts,
        "multi_filter": multi_filter,
        "drum_silence_gate_dbfs": drum_silence_gate_dbfs,
        "drum_silence_gate_window_ms": drum_silence_gate_window_ms,
        "drum_silence_gate_disabled": drum_silence_gate_disabled,
    }, None


def _add_transcription_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--drum-filter", "--drum-engine", dest="drum_filter", default=None)
    p.add_argument("--drum-stem-path")
    p.add_argument("--melodic-method", default=DEFAULT_MELODIC_METHOD)
    p.add_argument(
        "--wholemix-transcriber",
        dest="wholemix_transcriber",
        default=None,
        help=(
            "Opt-in whole-mix multi-instrument engine (e.g. 'muscriptor'): "
            "transcribes the full mix in one pass, replacing per-stem drum + "
            "melodic transcription for the roles it covers. Falls back to the "
            "per-stem pipeline when unset or the engine is unavailable."
        ),
    )
    p.add_argument("--transcription-profile", default=DEFAULT_TRANSCRIPTION_PROFILE)
    p.add_argument("--beat-analysis-mode", default=DEFAULT_BEAT_ANALYSIS_MODE)
    p.add_argument("--stem-separation-provider", default=DEFAULT_STEM_SEPARATION_PROVIDER)
    p.add_argument("--stem-separation-provider-path")
    p.add_argument("--stem-separation-modelpack-id")
    p.add_argument("--demucs-modelpack-zip-path")
    p.add_argument("--shifts", type=int, default=1)
    p.add_argument("--multi-filter", action="store_true")
    p.add_argument(
        "--drum-silence-gate-dbfs",
        dest="drum_silence_gate_dbfs",
        type=float,
        default=None,
        help=(
            "Drop drum events whose local stem RMS is below this dBFS threshold "
            f"(default: {DEFAULT_DRUM_SILENCE_GATE_DBFS:g} dBFS, env: "
            "AURALPRIMER_DRUM_SILENCE_GATE_DBFS)."
        ),
    )
    p.add_argument(
        "--drum-silence-gate-window-ms",
        dest="drum_silence_gate_window_ms",
        type=float,
        default=None,
        help=(
            "Half-window (ms) for local RMS computation around each drum event "
            f"(default: {DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS:g} ms, env: "
            "AURALPRIMER_DRUM_SILENCE_GATE_WINDOW_MS)."
        ),
    )
    p.add_argument(
        "--no-drum-silence-gate",
        dest="drum_silence_gate_disabled",
        action="store_true",
        default=False,
        help=(
            "Disable the stem-silence validation pass for drum events. Equivalent to "
            "setting AURALPRIMER_DRUM_SILENCE_GATE_DISABLED=1."
        ),
    )


def _try_relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def _resolve_guitar_split_source(
    auralsong_root: Path,
    mix_wav: Path,
    config: dict[str, Any],
) -> tuple[Path, str]:
    configured = config.get("guitar_stem_path")
    if isinstance(configured, str) and configured.strip():
        p = Path(configured).expanduser()
        if p.is_file():
            return p, "config"

    for candidate in (
        auralsong_root / "audio" / "stems" / "guitar.wav",
        auralsong_root / "audio" / "stems" / "Guitar.wav",
    ):
        if candidate.is_file():
            return candidate, "stems_guitar"

    return mix_wav, "mix_fallback"


def _resolve_drum_transcription_source(
    args: argparse.Namespace,
    auralsong_root: Path,
    mix_wav: Path,
    config: dict[str, Any],
) -> tuple[Path, str]:
    configured, configured_kind = _resolve_requested_drum_stem_path(args, config)
    if configured is not None:
        return configured, configured_kind

    for candidate in (
        auralsong_root / "audio" / "stems" / "drums.wav",
        auralsong_root / "audio" / "stems" / "Drums.wav",
    ):
        if candidate.is_file():
            return candidate, "separated_drums"

    return mix_wav, "mix_fallback"


def _events_json_from_drum_result(
    drum_result: Any,
    melodic_result: Any,
    *,
    requested_filter: str,
    melodic_method: str,
    instrument_results: list[Any] | None = None,
) -> dict[str, Any]:
    onsets = [
        {
            "t": round(float(e.time), 6),
            "note": int(e.note),
            "velocity": int(e.velocity),
            "duration": round(float(e.duration), 6),
            "instrument": "drums",
        }
        for e in drum_result.events
    ]

    tracks_list: list[dict[str, Any]] = [
        {
            "track_id": "drums_main",
            "role": "drums",
            "name": "Drums",
            "algorithm_requested": requested_filter,
            "algorithm_used": drum_result.used_algorithm,
            "attempted_algorithms": drum_result.attempted_algorithms,
            "meta": {"warnings": drum_result.warnings},
        },
    ]

    notes: list[dict[str, Any]] = []

    if instrument_results:
        for inst_result in instrument_results:
            role = inst_result.instrument
            track_id = f"{role}_main"
            pretty_name = _INSTRUMENT_TRACK_NAMES.get(role, role.replace("_", " ").title())
            tracks_list.append(
                {
                    "track_id": track_id,
                    "role": role,
                    "name": pretty_name,
                    "algorithm_requested": melodic_method,
                    "algorithm_used": inst_result.used_method,
                    "attempted_methods": inst_result.attempted_methods,
                    "meta": {"warnings": inst_result.warnings},
                }
            )
            for n in inst_result.notes:
                notes.append(
                    {
                        "track_id": track_id,
                        "t_on": round(float(n.t_on), 6),
                        "t_off": round(float(n.t_off), 6),
                        "pitch": int(n.pitch),
                        "velocity": int(n.velocity),
                        "instrument": role,
                    }
                )
    else:
        # Legacy single melodic track path.
        tracks_list.append(
            {
                "track_id": "melodic_main",
                "role": "melodic",
                "name": "Melodic",
                "algorithm_requested": melodic_method,
                "algorithm_used": melodic_result.used_method,
                "attempted_methods": melodic_result.attempted_methods,
                "meta": {"warnings": melodic_result.warnings},
            }
        )
        for n in melodic_result.notes:
            notes.append(
                {
                    "track_id": "melodic_main",
                    "t_on": round(float(n.t_on), 6),
                    "t_off": round(float(n.t_off), 6),
                    "pitch": int(n.pitch),
                    "velocity": int(n.velocity),
                    "instrument": "melodic",
                }
            )

    payload: dict[str, Any] = {
        "events_version": "1.0.0",
        "tracks": tracks_list,
        "onsets": onsets,
        "notes": notes,
        "chords": [],
    }
    return payload


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _drum_lane_label(note: int | None, fallback: str | None = None) -> str:
    if note is not None:
        return DRUM_NOTE_LABELS.get(int(note), f"note_{int(note)}")
    return fallback or "unknown"


def _dbfs(value: float) -> float:
    if value <= 0.0:
        return DRUM_AUDIT_DBFS_FLOOR
    return max(DRUM_AUDIT_DBFS_FLOOR, 20.0 * math.log10(value))


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 2)
    pos = max(0.0, min(1.0, pct)) * float(len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(float(ordered[lo]), 2)
    frac = pos - float(lo)
    interpolated = (ordered[lo] * (1.0 - frac)) + (ordered[hi] * frac)
    return round(float(interpolated), 2)


def _local_rms_peak_dbfs_at(
    samples: list[float],
    sr: int,
    time_sec: float,
    half_window_samples: int,
) -> tuple[float, float]:
    if not samples or sr <= 0 or half_window_samples <= 0:
        return DRUM_AUDIT_DBFS_FLOOR, DRUM_AUDIT_DBFS_FLOOR
    center = int(round(float(time_sec) * sr))
    lo = max(0, center - half_window_samples)
    hi = min(len(samples), center + half_window_samples)
    if hi <= lo:
        return DRUM_AUDIT_DBFS_FLOOR, DRUM_AUDIT_DBFS_FLOOR
    seg = samples[lo:hi]
    rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))
    peak = max((abs(x) for x in seg), default=0.0)
    return round(_dbfs(rms), 2), round(_dbfs(peak), 2)


def _auralsong_transcription_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    pipeline = manifest.get("pipeline")
    if not isinstance(pipeline, dict):
        return {}
    transcription = pipeline.get("transcription")
    return transcription if isinstance(transcription, dict) else {}


def _auralsong_stem_silence_gate_meta(transcription: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("stem_silence_gate", "drum_silence_gate"):
        value = transcription.get(key)
        if isinstance(value, dict):
            return value

    engine_meta = transcription.get("drum_engine_meta")
    if isinstance(engine_meta, dict):
        value = engine_meta.get("stem_silence_gate")
        if isinstance(value, dict):
            return value
    return None


def _resolve_auralsong_drum_stem(root: Path, manifest: dict[str, Any]) -> Path | None:
    transcription = _auralsong_transcription_manifest(manifest)
    candidates: list[Path] = []
    raw_source = transcription.get("drum_source_path")
    if isinstance(raw_source, str) and raw_source.strip():
        raw_path = Path(raw_source)
        candidates.append(raw_path if raw_path.is_absolute() else root / raw_path)

    candidates.extend(
        [
            root / "audio" / "stems" / "drums.wav",
            root / "audio" / "stems" / "Drums.wav",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else None


def _load_auralsong_drum_events(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events_path = root / "features" / "events.json"
    if events_path.is_file():
        payload = json.loads(events_path.read_text("utf-8"))
        raw_onsets = payload.get("onsets", []) if isinstance(payload, dict) else []
        events: list[dict[str, Any]] = []
        for onset in raw_onsets:
            if not isinstance(onset, dict):
                continue
            instrument = str(onset.get("instrument", "drums")).strip().lower()
            if instrument and instrument != "drums":
                continue
            try:
                time_sec = float(onset.get("t"))
                note = int(onset.get("note"))
            except (TypeError, ValueError):
                continue
            events.append(
                {
                    "time": time_sec,
                    "note": note,
                    "lane": _drum_lane_label(note),
                    "velocity": int(onset.get("velocity", 0) or 0),
                }
            )
        return events, {"source": "events_json", "path": str(events_path)}

    midi_path = root / "features" / "notes.mid"
    if midi_path.is_file():
        reference_events, reference_meta = load_drum_reference(midi_path)
        events = [
            {
                "time": float(event.time),
                "note": DRUM_CLASS_TO_CANONICAL_NOTE.get(event.drum_class),
                "lane": event.drum_class,
                "velocity": None,
            }
            for event in reference_events
        ]
        return events, {"source": "notes_mid", "path": str(midi_path), "midi_meta": reference_meta}

    return [], {"source": "none", "path": None}


def _counter_by_key(events: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for event in events:
        value = event.get(key)
        if value is None:
            value = "unknown"
        counter[str(value)] += 1
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def cmd_audit_drums(args: argparse.Namespace) -> int:
    root = Path(args.auralsong_dir)
    manifest_path = root / "manifest.json"
    if not root.exists() or not root.is_dir():
        print(json.dumps({"ok": False, "error": f"auralsong directory does not exist: {root}"}, sort_keys=True))
        return 1
    if not manifest_path.is_file():
        print(json.dumps({"ok": False, "error": "missing manifest.json"}, sort_keys=True))
        return 1

    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"failed to read manifest.json: {exc}"}, sort_keys=True))
        return 1
    if not isinstance(manifest, dict):
        print(json.dumps({"ok": False, "error": "manifest.json must contain an object"}, sort_keys=True))
        return 1

    events, event_source = _load_auralsong_drum_events(root)
    pipeline = manifest.get("pipeline")
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    transcription = _auralsong_transcription_manifest(manifest)
    stem_gate = _auralsong_stem_silence_gate_meta(transcription)
    stem_path = _resolve_auralsong_drum_stem(root, manifest)
    threshold_values = list(getattr(args, "threshold_dbfs", None) or DRUM_AUDIT_DEFAULT_THRESHOLDS_DBFS)
    thresholds = sorted(float(value) for value in threshold_values)
    window_ms = float(getattr(args, "window_ms", DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS))
    half_window_samples = 0

    payload: dict[str, Any] = {
        "ok": True,
        "auralsong": str(root),
        "event_source": event_source,
        "manifest": {
            "title": manifest.get("title"),
            "profile": manifest.get("profile") or pipeline.get("profile"),
            "transcription_profile": transcription.get("transcription_profile"),
            "drum_source_kind": transcription.get("drum_source_kind"),
            "drum_filter": transcription.get("drum_filter"),
            "drum_filter_requested": transcription.get("drum_filter_requested"),
            "drum_filter_used": transcription.get("drum_filter_used"),
            "drum_engine_selection": transcription.get("drum_engine_selection"),
            "stem_silence_gate_present": isinstance(stem_gate, dict),
            "stem_silence_gate": stem_gate if isinstance(stem_gate, dict) else None,
        },
        "events": {
            "count": len(events),
            "by_note": _counter_by_key(events, "note"),
            "by_lane": _counter_by_key(events, "lane"),
        },
        "drum_stem": {
            "path": str(stem_path) if stem_path is not None else None,
            "exists": bool(stem_path is not None and stem_path.is_file()),
        },
        "stem_energy": {
            "window_ms": window_ms,
            "thresholds_dbfs": thresholds,
            "available": False,
            "skip_reason": None,
        },
    }

    if stem_path is None or not stem_path.is_file():
        payload["stem_energy"]["skip_reason"] = "stem_unavailable"
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not events:
        payload["stem_energy"]["skip_reason"] = "no_drum_events"
        print(json.dumps(payload, sort_keys=True))
        return 0

    try:
        from aural_ingest.algorithms._common import read_wav_mono_normalized

        samples, sr = read_wav_mono_normalized(stem_path)
    except Exception as exc:
        payload["stem_energy"]["skip_reason"] = f"stem_load_failed: {exc}"
        print(json.dumps(payload, sort_keys=True))
        return 0

    if not samples or sr <= 0:
        payload["stem_energy"]["skip_reason"] = "stem_load_failed"
        print(json.dumps(payload, sort_keys=True))
        return 0

    half_window_samples = max(1, int(round((window_ms / 1000.0) * sr)))
    measurements: list[dict[str, Any]] = []
    for event in events:
        rms_dbfs, peak_dbfs = _local_rms_peak_dbfs_at(samples, sr, event["time"], half_window_samples)
        measurements.append(
            {
                "time": round(float(event["time"]), 6),
                "note": event.get("note"),
                "lane": event.get("lane"),
                "rms_dbfs": rms_dbfs,
                "peak_dbfs": peak_dbfs,
            }
        )

    rms_values = [float(item["rms_dbfs"]) for item in measurements]
    below_thresholds: dict[str, Any] = {}
    for threshold in thresholds:
        below = [item for item in measurements if float(item["rms_dbfs"]) < threshold]
        below_thresholds[f"{threshold:g}"] = {
            "count": len(below),
            "by_note": _counter_by_key(below, "note"),
            "by_lane": _counter_by_key(below, "lane"),
        }

    payload["stem_energy"].update(
        {
            "available": True,
            "sample_rate": sr,
            "half_window_samples": half_window_samples,
            "rms_dbfs_percentiles": {
                "min": _percentile(rms_values, 0.0),
                "p01": _percentile(rms_values, 0.01),
                "p05": _percentile(rms_values, 0.05),
                "p50": _percentile(rms_values, 0.50),
                "p95": _percentile(rms_values, 0.95),
                "max": _percentile(rms_values, 1.0),
            },
            "below_thresholds": below_thresholds,
            "quietest_events": sorted(measurements, key=lambda item: float(item["rms_dbfs"]))[:12],
        }
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


def cmd_transcribe_query_lazy(args: Any) -> int:
    """Load the recogniser only when a query actually arrives.

    Importing at module scope would drag transformers and torch into every
    invocation of the CLI, including the ones that just print `stages`.
    """
    from .voice_query import cmd_transcribe_query

    return cmd_transcribe_query(args)


def cmd_stages(_args: argparse.Namespace) -> int:
    # Keep output stable + simple.
    for st in STAGES:
        print(json.dumps(_serialize_stage_declaration(st), sort_keys=True))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.auralsong_dir)
    manifest = root / "manifest.json"
    if not manifest.exists():
        print(json.dumps({"ok": False, "error": "missing manifest.json"}, sort_keys=True))
        return 1

    data = json.loads(manifest.read_text("utf-8"))
    print(json.dumps({"ok": True, "manifest": data}, sort_keys=True))
    return 0


def _event_note_tuple(note: dict[str, Any]) -> tuple[float, float, int, int]:
    return (
        round(float(note.get("t_on", 0.0) or 0.0), 6),
        round(float(note.get("t_off", 0.0) or 0.0), 6),
        int(note.get("pitch", 0) or 0),
        int(note.get("velocity", 0) or 0),
    )


def _validate_note_tuple_matches(
    *,
    role: str,
    idx: int,
    label: str,
    expected: tuple[float, float, int, int],
    actual: tuple[float, float, int, int],
    tolerance_sec: float,
) -> None:
    expected_t_on, expected_t_off, expected_pitch, expected_velocity = expected
    actual_t_on, actual_t_off, actual_pitch, actual_velocity = actual
    if (
        expected_pitch != actual_pitch
        or expected_velocity != actual_velocity
        or abs(expected_t_on - actual_t_on) > tolerance_sec
        or abs(expected_t_off - actual_t_off) > tolerance_sec
    ):
        raise ValueError(
            f"{label} disagree for {role} note {idx}: "
            f"events=({expected_t_on}, {expected_t_off}, {expected_pitch}, {expected_velocity}) "
            f"verifier=({actual_t_on}, {actual_t_off}, {actual_pitch}, {actual_velocity})"
        )


def _validate_events_json_matches_notes_mid(root: Path) -> dict[str, Any] | None:
    events_path = root / "features" / "events.json"
    if not events_path.is_file():
        return None

    from aural_ingest.algorithms.piano_midi import decode_midi_notes
    from aural_ingest.piano_benchmark import evaluate_piano, parse_piano_midi_reference

    payload = json.loads(events_path.read_text("utf-8"))
    raw_notes = payload.get("notes", [])
    if not isinstance(raw_notes, list) or not raw_notes:
        return None

    notes_mid = root / "features" / "notes.mid"
    notes_by_role: dict[str, list[dict[str, Any]]] = {}
    for raw_note in raw_notes:
        if not isinstance(raw_note, dict):
            raise ValueError("events.json notes must be objects")
        role = str(raw_note.get("instrument", "")).strip()
        if not role or role == "drums":
            continue
        notes_by_role.setdefault(role, []).append(raw_note)

    tolerance_sec = 0.01
    summary: dict[str, Any] = {
        "events_notes_mid": {
            "roles": {},
            "verifiers": ["events_json", "piano_midi_decoder", "piano_benchmark_parser"],
        }
    }
    for role, expected_notes in sorted(notes_by_role.items()):
        decoded_notes = decode_midi_notes(notes_mid, instrument=role)
        benchmark_events = parse_piano_midi_reference(notes_mid, role=role)
        expected_sorted = sorted(
            expected_notes,
            key=lambda item: (
                float(item.get("t_on", 0.0) or 0.0),
                int(item.get("pitch", 0) or 0),
                float(item.get("t_off", 0.0) or 0.0),
                int(item.get("velocity", 0) or 0),
            ),
        )
        if len(decoded_notes) != len(expected_sorted):
            raise ValueError(
                "events.json and piano_midi_decoder disagree for "
                f"{role}: {len(expected_sorted)} event notes vs {len(decoded_notes)} MIDI notes"
            )
        if len(benchmark_events) != len(expected_sorted):
            raise ValueError(
                "events.json and piano_benchmark_parser disagree for "
                f"{role}: {len(expected_sorted)} event notes vs {len(benchmark_events)} benchmark notes"
            )

        for idx, (event_note, midi_note) in enumerate(zip(expected_sorted, decoded_notes, strict=True)):
            expected_tuple = _event_note_tuple(event_note)
            actual_tuple = (
                round(float(midi_note.t_on), 6),
                round(float(midi_note.t_off), 6),
                int(midi_note.pitch),
                int(midi_note.velocity),
            )
            _validate_note_tuple_matches(
                role=role,
                idx=idx,
                label="events.json and piano_midi_decoder",
                expected=expected_tuple,
                actual=actual_tuple,
                tolerance_sec=tolerance_sec,
            )

        for idx, (event_note, benchmark_note) in enumerate(zip(expected_sorted, benchmark_events, strict=True)):
            expected_tuple = _event_note_tuple(event_note)
            actual_tuple = (
                round(float(benchmark_note.time), 6),
                round(float(benchmark_note.time + benchmark_note.duration), 6),
                int(benchmark_note.pitch),
                int(benchmark_note.velocity),
            )
            _validate_note_tuple_matches(
                role=role,
                idx=idx,
                label="events.json and piano_benchmark_parser",
                expected=expected_tuple,
                actual=actual_tuple,
                tolerance_sec=tolerance_sec,
            )

        secondary = evaluate_piano(
            decoded_notes,
            benchmark_events,
            tolerance_sec=tolerance_sec,
            offset_tolerance_sec=tolerance_sec,
            velocity_tolerance=0,
        )
        if (
            secondary.tp != len(expected_sorted)
            or secondary.fp != 0
            or secondary.fn != 0
            or secondary.offset_velocity_tp != len(expected_sorted)
        ):
            raise ValueError(
                "piano_midi_decoder and piano_benchmark_parser disagree for "
                f"{role}: f1={secondary.f1:.4f}, offset_velocity_f1={secondary.note_with_offset_velocity_f1:.4f}"
            )

        summary["events_notes_mid"]["roles"][role] = {
            "events_json_notes": len(expected_sorted),
            "piano_benchmark_parser_notes": len(benchmark_events),
            "piano_midi_decoder_notes": len(decoded_notes),
            "secondary_f1": round(secondary.f1, 4),
            "secondary_offset_velocity_f1": round(secondary.note_with_offset_velocity_f1, 4),
        }

    if not summary["events_notes_mid"]["roles"]:
        return None
    return summary


def _validate_feedpak(root: Path) -> int:
    """Validate a ``.feedpak`` pack (manifest.yaml layout).

    Checks manifest-referenced artifacts emitted by ``feedpak_writer.write_feedpak``:
    stems, arrangements, MIDI/artifact pointers, model JSON documents, and
    directory outputs must stay inside the pack and exist; schema-known JSON
    artifacts must validate. Feedpaks are stems-only; there is intentionally no
    ``audio/mix.wav`` requirement.
    """
    import yaml

    from . import feedpak_validate

    try:
        manifest = yaml.safe_load((root / "manifest.yaml").read_text("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest.yaml must be a YAML mapping")
        manifest_errors = feedpak_validate.iter_errors(manifest, "manifest.schema.json")
        if manifest_errors:
            raise ValueError(f"schema invalid: {'; '.join(manifest_errors)}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"manifest.yaml: {e}"}, sort_keys=True))
        return 1

    file_refs: list[str] = []
    path_refs: list[str] = []
    json_refs: list[str] = []
    schema_refs: dict[str, str] = {}

    def rel_error(rel: str) -> str | None:
        norm = rel.replace("\\", "/")
        posix_path = PurePosixPath(norm)
        if not rel.strip():
            return "must not be empty"
        if Path(rel).is_absolute() or posix_path.is_absolute():
            return "must be relative"
        if ".." in posix_path.parts:
            return "must not contain '..'"
        return None

    def add_file_ref(rel: str, *, json_doc: bool = False, schema_name: str | None = None) -> str | None:
        err = rel_error(rel)
        if err is not None:
            return err
        file_refs.append(rel)
        if json_doc or schema_name:
            json_refs.append(rel)
        if schema_name:
            schema_refs[rel] = schema_name
        return None

    def add_path_ref(rel: str) -> str | None:
        err = rel_error(rel)
        if err is not None:
            return err
        path_refs.append(rel)
        return None

    def fail_pointer(path: str, detail: str) -> int:
        print(json.dumps({"ok": False, "error": f"manifest.yaml {path}: {detail}"}, sort_keys=True))
        return 1

    stems = manifest.get("stems")
    if not isinstance(stems, list) or not stems:
        print(json.dumps({"ok": False, "error": "manifest.yaml stems must be a non-empty list"}, sort_keys=True))
        return 1
    for idx, entry in enumerate(stems):
        rel = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not rel:
            return fail_pointer(f"stems[{idx}].file", "must be a non-empty relpath")
        err = add_file_ref(rel)
        if err is not None:
            return fail_pointer(f"stems[{idx}].file", err)

    arrangements = manifest.get("arrangements")
    if not isinstance(arrangements, list) or not arrangements:
        print(
            json.dumps({"ok": False, "error": "manifest.yaml arrangements must be a non-empty list"}, sort_keys=True)
        )
        return 1
    for idx, entry in enumerate(arrangements):
        if not isinstance(entry, dict):
            return fail_pointer(f"arrangements[{idx}]", "must be a mapping")
        found_arrangement_pointer = False
        for path_key in ("notation", "file"):
            rel = entry.get(path_key)
            if rel is None:
                continue
            if not isinstance(rel, str) or not rel:
                return fail_pointer(f"arrangements[{idx}].{path_key}", "must be a non-empty relpath")
            schema_name = "notation.schema.json" if path_key == "notation" else "arrangement.schema.json"
            err = add_file_ref(rel, schema_name=schema_name)
            if err is not None:
                return fail_pointer(f"arrangements[{idx}].{path_key}", err)
            found_arrangement_pointer = True
        if not found_arrangement_pointer:
            return fail_pointer(f"arrangements[{idx}]", "must set file or notation")

    sidecar_schemas = {
        "lyrics": "lyrics.schema.json",
        "song_timeline": "song-timeline.schema.json",
        "drum_tab": "drum-tab.schema.json",
        "keys": "keys.schema.json",
        "harmony": "harmony.schema.json",
        "vocal_pitch": "vocal-pitch.schema.json",
        "vocal_pitch_contour": "vocal-pitch-contour.schema.json",
    }
    for key, schema_name in sidecar_schemas.items():
        rel = manifest.get(key)
        if rel is None:
            continue
        if not isinstance(rel, str) or not rel:
            return fail_pointer(key, "must be a non-empty relpath")
        err = add_file_ref(rel, schema_name=schema_name)
        if err is not None:
            return fail_pointer(key, err)

    rel = manifest.get("aural_notes_mid")
    if rel is not None:
        if not isinstance(rel, str) or not rel:
            return fail_pointer("aural_notes_mid", "must be a non-empty relpath")
        err = add_file_ref(rel)
        if err is not None:
            return fail_pointer("aural_notes_mid", err)

    for key in ("aural_spectrogram", "aural_benchmark"):
        rel = manifest.get(key)
        if rel is None:
            continue
        if not isinstance(rel, str) or not rel:
            return fail_pointer(key, "must be a non-empty relpath")
        err = add_path_ref(rel)
        if err is not None:
            return fail_pointer(key, err)

    for key in ("aural_refine_candidates", "aural_fingering"):
        raw = manifest.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            return fail_pointer(key, "must be a mapping of role to relpath")
        for role, rel in raw.items():
            if not isinstance(rel, str) or not rel:
                return fail_pointer(f"{key}.{role}", "must be a non-empty relpath")
            err = add_file_ref(
                rel,
                json_doc=True,
                schema_name="aural-fingering.schema.json" if key == "aural_fingering" else None,
            )
            if err is not None:
                return fail_pointer(f"{key}.{role}", err)

    missing = [rel for rel in file_refs if not (root / rel).is_file()]
    missing.extend(rel for rel in path_refs if not (root / rel).exists())
    if missing:
        print(json.dumps({"ok": False, "missing": missing}, sort_keys=True))
        return 1

    try:
        for rel in json_refs:
            try:
                payload = json.loads((root / rel).read_text("utf-8"))
            except Exception as e:
                raise ValueError(f"{rel}: {e}") from e
            schema_name = schema_refs.get(rel)
            if schema_name:
                errors = feedpak_validate.iter_errors(payload, schema_name)
                if errors:
                    raise ValueError(f"{rel}: schema invalid: {'; '.join(errors)}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, sort_keys=True))
        return 1

    print(json.dumps({"ok": True, "pack_type": "feedpak"}, sort_keys=True))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.auralsong_dir)
    # Pack layout is detected by manifest flavor: feedpaks carry manifest.yaml,
    # .auralsong packs carry manifest.json.
    if (root / "manifest.yaml").is_file():
        return _validate_feedpak(root)
    required = [
        "manifest.json",
        "audio/mix.wav",
        "features/notes.mid",
    ]

    missing = [p for p in required if not (root / p).exists()]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}, sort_keys=True))
        return 1

    # Minimal semantic checks (deterministic and fast)
    try:
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
        duration = float(manifest.get("duration_sec", 0.0) or 0.0)
        if duration <= 0:
            raise ValueError("duration_sec must be > 0")

        midi_bytes = (root / "features/notes.mid").read_bytes()
        if len(midi_bytes) < 14:
            raise ValueError("notes.mid too small")
        if midi_bytes[0:4] != b"MThd":
            raise ValueError("notes.mid missing MThd header")
        if midi_bytes[8:10] not in {b"\x00\x00", b"\x00\x01"}:
            raise ValueError("notes.mid has unsupported MIDI format")
        if b"MTrk" not in midi_bytes:
            raise ValueError("notes.mid missing track chunks")
        if b"\xFF\x51\x03" not in midi_bytes:
            raise ValueError("notes.mid missing SetTempo meta event")
        verifier_summary = _validate_events_json_matches_notes_mid(root)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, sort_keys=True))
        return 1

    payload: dict[str, Any] = {"ok": True}
    if verifier_summary:
        payload["verifiers"] = verifier_summary
    print(json.dumps(payload, sort_keys=True))
    return 0


def cmd_benchmark_drums(args: argparse.Namespace) -> int:
    stem = Path(args.stem_path)
    reference = Path(args.reference_path)
    if not stem.is_file():
        log(f"stem does not exist: {stem}")
        return 2
    if not reference.is_file():
        log(f"reference does not exist: {reference}")
        return 2
    if float(args.tolerance_ms) <= 0.0:
        log(f"invalid --tolerance-ms '{args.tolerance_ms}': must be > 0")
        return 2

    try:
        reference_events, reference_meta = load_drum_reference(reference)
    except Exception as exc:
        log(f"failed to load drum reference: {exc}")
        return 2

    if not reference_events:
        log("drum reference did not yield any benchmarkable drum events")
        return 2

    requested_algorithms = []
    for raw in list(args.algorithm or []):
        normalized = raw.strip().lower()
        if not normalized or normalized == "auto":
            normalized = DEFAULT_DRUM_FILTER
        requested_algorithms.append(normalized)

    algorithm_ids = _dedupe_preserve_order(requested_algorithms or list(KNOWN_DRUM_FILTERS))
    registry = build_default_drum_algorithm_registry()
    results = benchmark_algorithms(
        stem,
        reference_events,
        algorithm_ids,
        registry,
        tolerance_sec=float(args.tolerance_ms) / 1000.0,
    )
    payload = {
        "ok": any("error" not in result for result in results),
        "stem_path": str(stem),
        "reference_path": str(reference),
        "reference_count": len(reference_events),
        "reference_meta": reference_meta,
        "algorithm_metadata": {
            algorithm_id: drum_engine_metadata(algorithm_id) for algorithm_id in algorithm_ids
        },
        "tolerance_ms": round(float(args.tolerance_ms), 3),
        "class_order": list(BENCHMARK_CLASS_ORDER),
        "results": results,
    }

    if bool(getattr(args, "json_output", False)):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(format_benchmark_summary(payload))

    return 0 if payload["ok"] else 1


def cmd_benchmark_quality(args: argparse.Namespace) -> int:
    from aural_ingest.quality_benchmark import (
        build_quality_manifest_from_scan,
        filter_quality_cases,
        load_quality_manifest,
        run_quality_benchmark_suite,
        scan_corpus,
        write_quality_manifest_from_scan,
        write_quality_outputs,
    )

    if getattr(args, "scan_root", None):
        scan_root = Path(args.scan_root)
        write_manifest = getattr(args, "write_manifest", None)
        if write_manifest:
            out_path = write_quality_manifest_from_scan(
                scan_root,
                Path(write_manifest),
                include_unreferenced=not bool(getattr(args, "referenced_only", False)),
            )
            print(str(out_path))
            return 0
        payload = scan_corpus(scan_root)
        if getattr(args, "manifest_json", False):
            payload = build_quality_manifest_from_scan(
                payload,
                include_unreferenced=not bool(getattr(args, "referenced_only", False)),
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    manifest = getattr(args, "manifest", None)
    if not manifest:
        log("benchmark-quality requires --manifest or --scan-root")
        return 2

    try:
        cases = load_quality_manifest(Path(manifest))
        cases = filter_quality_cases(
            cases,
            case_filters=getattr(args, "case_filter", None),
            roles=getattr(args, "role", None),
            max_cases=getattr(args, "max_cases", None),
        )
        if not cases:
            log("quality benchmark has no cases after filtering")
            return 2
        payload = run_quality_benchmark_suite(
            cases,
            profile=getattr(args, "transcription_profile", DEFAULT_TRANSCRIPTION_PROFILE),
            algorithms=getattr(args, "algorithm", None),
            tolerance_ms=float(getattr(args, "tolerance_ms", 60.0)),
        )
        out_dir = write_quality_outputs(
            payload,
            output_root=Path(getattr(args, "out_root", "benchmarks/quality/runs")),
            label=getattr(args, "label", "full-corpus-quality"),
        )
    except Exception as exc:
        log(f"quality benchmark failed: {exc}")
        return 1

    print(str(out_dir))
    return 0


def cmd_refine_piano(args: argparse.Namespace) -> int:
    from aural_ingest.piano_refinement import run_piano_refinement_workbench

    try:
        out_dir = run_piano_refinement_workbench(
            audio_path=Path(args.audio),
            source_midi_path=Path(args.source_midi),
            reference_midi_path=Path(args.reference_midi) if args.reference_midi else None,
            methods=getattr(args, "method", None),
            output_root=Path(getattr(args, "out_root", "benchmarks/piano/refinement_runs")),
            label=getattr(args, "label", "piano-refinement"),
            tolerance_ms=float(getattr(args, "tolerance_ms", 60.0)),
            offset_tolerance_ms=float(getattr(args, "offset_tolerance_ms", 120.0)),
            velocity_tolerance=int(getattr(args, "velocity_tolerance", 20)),
            source_offset_sec=float(getattr(args, "source_offset_sec", 0.0)),
            reference_offset_sec=float(getattr(args, "reference_offset_sec", 0.0)),
            bpm=float(getattr(args, "bpm", 120.0)),
        )
    except FileNotFoundError as exc:
        log(str(exc))
        return 2
    except Exception as exc:
        log(f"piano refinement failed: {exc}")
        return 4

    print(str(out_dir))
    return 0


def _runtime_dependency_snapshot(module_name: str, dependency_policy: dict[str, Any]) -> dict[str, Any]:
    distribution_name = str(dependency_policy.get("distribution") or module_name)
    payload: dict[str, Any] = {
        **dependency_policy,
        "distribution": distribution_name,
    }
    try:
        payload["installed"] = True
        payload["installed_version"] = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        payload["installed"] = False
        payload["installed_error"] = "distribution not installed"
    except Exception as exc:
        payload["installed"] = False
        payload["installed_error"] = str(exc)

    try:
        module = importlib.import_module(module_name)
        payload["ok"] = True
        payload["version"] = getattr(
            module,
            "__version__",
            payload.get("installed_version", "unknown"),
        )
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
        if "installed_version" in payload:
            payload["version"] = payload["installed_version"]

    return payload


def _basic_pitch_runtime_feature(
    basic_pitch_model_asset: dict[str, Any],
    dependencies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    basic_pitch_dep = dependencies.get("basic_pitch", {})
    basic_pitch_inference_dep = dependencies.get("basic_pitch.inference", {})
    onnxruntime_dep = dependencies.get("onnxruntime", {})
    tensorflow_dep = dependencies.get("tensorflow", {})
    model_path = str(basic_pitch_model_asset.get("path") or "")
    model_path_lower = model_path.lower()
    model_installed = bool(basic_pitch_model_asset.get("ok"))
    if not model_installed:
        backend = "unresolved"
        backend_dependency_key: str | None = None
        backend_dep: dict[str, Any] = {}
    elif model_path_lower.endswith(".onnx"):
        backend = "onnx"
        backend_dependency_key = "onnxruntime"
        backend_dep = onnxruntime_dep
    elif model_path_lower.endswith(".tflite"):
        backend = "tflite"
        backend_dependency_key = "tensorflow"
        backend_dep = tensorflow_dep
    else:
        backend = "tensorflow_saved_model"
        backend_dependency_key = "tensorflow"
        backend_dep = tensorflow_dep

    missing: list[str] = []
    if not basic_pitch_model_asset.get("ok"):
        missing.append("basic_pitch_model")
    if not basic_pitch_dep.get("ok"):
        missing.append("basic_pitch")
    if not basic_pitch_inference_dep.get("ok"):
        missing.append("basic_pitch.inference")
    if backend_dependency_key is not None and not backend_dep.get("ok"):
        missing.append(backend_dependency_key)

    dependency_warnings: list[str] = []
    if not tensorflow_dep.get("ok"):
        dependency_warnings.append("tensorflow")

    payload: dict[str, Any] = {
        "enabled": not missing,
        "backend": backend,
        "backend_dependency": backend_dependency_key,
        "model_installed": model_installed,
        "runtime_importable": bool(basic_pitch_dep.get("ok") and basic_pitch_inference_dep.get("ok")),
        "backend_importable": bool(backend_dependency_key is not None and backend_dep.get("ok")),
        "package_dependency_health_ok": not dependency_warnings,
        "methods": [
            "basic_pitch",
            "melodic_basic_pitch",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
        ],
        "fallback_behavior": "auto profiles fall through to non-Basic-Pitch methods; strict Basic Pitch requests fail",
    }
    if missing:
        payload["missing"] = missing
    if dependency_warnings:
        payload["dependency_warnings"] = dependency_warnings
    if basic_pitch_dep.get("error"):
        payload["runtime_error"] = basic_pitch_dep.get("error")
    if basic_pitch_inference_dep.get("error"):
        payload["inference_error"] = basic_pitch_inference_dep.get("error")
    if backend_dep.get("error"):
        payload["backend_error"] = backend_dep.get("error")
    return payload


BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH = Path("benchmarks") / "meter" / "beat_this_dbn_barline_listening_review.json"
BEAT_THIS_REVIEW_REQUIRED_CASES: tuple[str, ...] = (
    "psalm_121_my_help.feedpak",
    "psalm_130_please_hear_me.feedpak",
    "psalm_5_every_morning.feedpak",
)
MUSDB_SDR_EVIDENCE_GLOB = Path("benchmarks") / "quality" / "runs" / "*_musdb_separation_sdr.json"
ADTOF_RUNTIME_EVIDENCE_GLOB = Path("benchmarks") / "runtime" / "runs" / "*_adtof_runtime.json"
DRUM_STEMSEP_RUNTIME_EVIDENCE_GLOB = Path("benchmarks") / "runtime" / "runs" / "*_drum_stemsep_runtime.json"
RMVPE_RUNTIME_EVIDENCE_GLOB = Path("benchmarks") / "runtime" / "runs" / "*_rmvpe_runtime.json"
ROFORMER_RUNTIME_EVIDENCE_GLOB = Path("benchmarks") / "runtime" / "runs" / "*_roformer_runtime.json"
QMUL_HR_GUITAR_RUNTIME_EVIDENCE_GLOB = Path("benchmarks") / "runtime" / "runs" / "*_qmul_hr_guitar_runtime.json"
MIR_ST500_VOCALS_EVIDENCE_GLOB = Path("benchmarks") / "vocals" / "gt_runs" / "*_mir_st500_vocals.json"
MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = (
    Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"
)
MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"


def _model_upgrade_evidence_root() -> Path:
    raw_root = os.environ.get(MODEL_UPGRADE_EVIDENCE_ROOT_ENV, "").strip()
    if raw_root:
        return Path(raw_root).expanduser().resolve()

    def looks_like_evidence_root(path: Path) -> bool:
        return (path / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).is_file()

    cwd = Path.cwd().resolve()
    if looks_like_evidence_root(cwd):
        return cwd

    source_root = Path(__file__).resolve().parents[4]
    if looks_like_evidence_root(source_root):
        return source_root

    if getattr(sys, "frozen", False):
        return cwd
    return source_root


def _evidence_filename_timestamp(path: Path) -> float | None:
    parts = path.stem.split("_")
    if len(parts) < 2:
        return None
    date_part, time_part = parts[0], parts[1]
    if not (len(date_part) == 8 and len(time_part) == 6 and date_part.isdigit() and time_part.isdigit()):
        return None
    micro_part = parts[2] if len(parts) > 2 and len(parts[2]) == 6 and parts[2].isdigit() else "000000"
    try:
        parsed = datetime.strptime(
            date_part + time_part + micro_part,
            "%Y%m%d%H%M%S%f",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.timestamp()


def _evidence_candidates(pattern: Path) -> list[Path]:
    if not pattern.parent.exists():
        return []
    candidates: list[tuple[int, float, float, Path]] = []
    for path in pattern.parent.glob(pattern.name):
        try:
            stat_result = path.stat()
        except OSError:
            continue
        if not stat.S_ISREG(stat_result.st_mode):
            continue
        filename_timestamp = _evidence_filename_timestamp(path)
        if filename_timestamp is None:
            candidates.append((0, stat_result.st_mtime, stat_result.st_mtime, path))
        else:
            candidates.append((1, filename_timestamp, stat_result.st_mtime, path))
    return [path for *_sort_key, path in sorted(candidates, reverse=True)]


def _is_iso8601_utc_z_timestamp(value: str) -> bool:
    if "T" not in value or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(None)


def _beat_this_review_evidence_status() -> dict[str, Any]:
    path = _model_upgrade_evidence_root() / BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH
    status: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "required_cases": list(BEAT_THIS_REVIEW_REQUIRED_CASES),
        "reviewed_cases": [],
        "missing_cases": list(BEAT_THIS_REVIEW_REQUIRED_CASES),
        "metadata_errors": [],
        "invalid_cases": [],
        "ready": False,
        "reason": None,
    }
    if not path.is_file():
        status["reason"] = f"review evidence not found: {path}"
        return status

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        status["reason"] = f"could not read review evidence: {exc}"
        return status
    if not isinstance(payload, dict):
        status["reason"] = "review evidence must be a JSON object"
        return status

    status["version"] = payload.get("version")
    status["reviewed_by"] = payload.get("reviewed_by")
    status["reviewed_at_utc"] = payload.get("reviewed_at_utc")
    status["source_smoke_report"] = payload.get("source_smoke_report")
    metadata_errors: list[str] = []
    if payload.get("gate") != "beat_this_barline_listening_review":
        metadata_errors.append("gate must be 'beat_this_barline_listening_review'")
    if payload.get("version") != 1:
        metadata_errors.append("version must be 1")
    reviewed_by = str(payload.get("reviewed_by") or "").strip()
    if not reviewed_by or reviewed_by == "TODO":
        metadata_errors.append("reviewed_by must identify the reviewer")
    reviewed_at_utc = str(payload.get("reviewed_at_utc") or "").strip()
    if not reviewed_at_utc or reviewed_at_utc == "TODO":
        metadata_errors.append("reviewed_at_utc must record the review timestamp")
    elif not _is_iso8601_utc_z_timestamp(reviewed_at_utc):
        metadata_errors.append("reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z")
    if payload.get("source_smoke_report") != "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md":
        metadata_errors.append("source_smoke_report must be 'benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md'")
    status["metadata_errors"] = metadata_errors

    raw_cases = payload.get("cases")
    if isinstance(raw_cases, dict):
        cases = raw_cases
    elif isinstance(raw_cases, list):
        cases = {}
        for item in raw_cases:
            if isinstance(item, dict):
                case_id = item.get("id") or item.get("pack") or item.get("name")
                if isinstance(case_id, str) and case_id.strip():
                    cases[case_id.strip()] = item
    else:
        status["reason"] = "review evidence must contain cases as an object or list"
        return status

    reviewed: list[str] = []
    invalid: list[str] = []
    for case_id in BEAT_THIS_REVIEW_REQUIRED_CASES:
        item = cases.get(case_id)
        if not isinstance(item, dict):
            continue
        barlines_ok = item.get("barlines_ok") is True
        listening_ok = item.get("listening_ok") is True
        if barlines_ok and listening_ok:
            reviewed.append(case_id)
        else:
            missing_flags = [
                label
                for label, ok in (("barlines_ok", barlines_ok), ("listening_ok", listening_ok))
                if not ok
            ]
            invalid.append(f"{case_id}: {', '.join(missing_flags)} must be true")

    missing = [case_id for case_id in BEAT_THIS_REVIEW_REQUIRED_CASES if case_id not in reviewed]
    status["reviewed_cases"] = reviewed
    status["missing_cases"] = missing
    status["invalid_cases"] = invalid
    status["ready"] = not metadata_errors and not missing and not invalid
    if status["ready"]:
        status["reason"] = None
    elif metadata_errors:
        status["reason"] = "; ".join(metadata_errors)
    elif invalid:
        status["reason"] = "; ".join(invalid)
    else:
        status["reason"] = "missing reviewed cases: " + ", ".join(missing)
    return status


def _canonical_report_modelpack_id(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        payload.get("modelpack_id"),
        payload.get("stem_separation_modelpack_id"),
    ]
    config = payload.get("config")
    if isinstance(config, dict):
        candidates.extend(
            [
                config.get("stem_separation_modelpack_id"),
                config.get("demucs_modelpack_id"),
            ]
        )
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        value = _canonical_demucs_modelpack_id(candidate)
        return value or None
    return None


MUSDB_SDR_EVIDENCE_REQUIRED_ROLES: tuple[str, ...] = ("bass", "drums", "other", "vocals")
MUSDB_SDR_EVIDENCE_MIN_TRACKS = 10
MUSDB_SDR_EVIDENCE_REQUIRED_DATASET = "musdb18_or_musdb18_hq"
MUSDB_SDR_EVIDENCE_REQUIRED_SPLIT = "test"
MIR_ST500_VOCALS_EVIDENCE_REQUIRED_LIMIT: int | None = None


def _musdb_role_summary_rejection(summary: Any) -> str | None:
    if not isinstance(summary, dict):
        return "report summary is missing"
    try:
        tracks_ok = int(summary.get("tracks_ok") or 0)
    except (TypeError, ValueError):
        return "summary.tracks_ok must be numeric"
    if tracks_ok <= 0:
        return "summary.tracks_ok must be greater than zero"
    for key in ("tracks_failed", "tracks_skipped"):
        try:
            count = int(summary.get(key) or 0)
        except (TypeError, ValueError):
            return f"summary.{key} must be numeric"
        if count != 0:
            return f"summary.{key} must be zero"
    if tracks_ok < MUSDB_SDR_EVIDENCE_MIN_TRACKS:
        return f"summary.tracks_ok must be at least {MUSDB_SDR_EVIDENCE_MIN_TRACKS}"
    try:
        median_sdr_mean = float(summary.get("median_sdr_mean"))
    except (TypeError, ValueError):
        return "summary.median_sdr_mean must be numeric"
    if not math.isfinite(median_sdr_mean):
        return "summary.median_sdr_mean must be finite"
    role_summary = summary.get("role_summary")
    if not isinstance(role_summary, dict):
        return "summary.role_summary is missing"
    for role in MUSDB_SDR_EVIDENCE_REQUIRED_ROLES:
        metrics = role_summary.get(role)
        if not isinstance(metrics, dict):
            return f"summary.role_summary.{role} is missing"
        try:
            track_count = int(metrics.get("track_count") or 0)
        except (TypeError, ValueError):
            track_count = 0
        if track_count <= 0:
            return f"summary.role_summary.{role}.track_count must be greater than zero"
        if track_count != tracks_ok:
            return f"summary.role_summary.{role}.track_count must equal summary.tracks_ok"
        try:
            median_sdr_mean = float(metrics.get("median_sdr_mean"))
        except (TypeError, ValueError):
            return f"summary.role_summary.{role}.median_sdr_mean must be numeric"
        if not math.isfinite(median_sdr_mean):
            return f"summary.role_summary.{role}.median_sdr_mean must be finite"
    return None


def _musdb_sdr_report_evidence_status(
    *,
    provider: str,
    required_modelpack_id: str | None = None,
) -> dict[str, Any]:
    root = _model_upgrade_evidence_root()
    pattern = root / MUSDB_SDR_EVIDENCE_GLOB
    candidates = _evidence_candidates(pattern)
    status: dict[str, Any] = {
        "glob": str(pattern),
        "provider": provider,
        "required_modelpack_id": required_modelpack_id,
        "required_dataset": MUSDB_SDR_EVIDENCE_REQUIRED_DATASET,
        "required_split": MUSDB_SDR_EVIDENCE_REQUIRED_SPLIT,
        "latest_matching_identity_only": True,
        "candidate_count": len(candidates),
        "matching_identity_candidate_count": 0,
        "candidates_checked": 0,
        "rejection_count": 0,
        "rejections": [],
        "path": None,
        "summary": None,
        "ready": False,
        "reason": None,
    }
    failures: list[str] = []

    def reject(path: Path, reason: str, payload: dict[str, Any] | None = None) -> None:
        failures.append(f"{path}: {reason}")
        rejection: dict[str, Any] = {"path": str(path), "reason": reason}
        if payload is not None:
            rejection["provider"] = payload.get("provider")
            rejection["modelpack_id"] = _canonical_report_modelpack_id(payload)
            rejection["dataset"] = payload.get("dataset")
            rejection["split"] = payload.get("split")
            rejection["ok"] = payload.get("ok")
            summary = payload.get("summary")
            if isinstance(summary, dict):
                rejection["tracks_ok"] = summary.get("tracks_ok")
                rejection["median_sdr_mean"] = summary.get("median_sdr_mean")
        status["rejection_count"] = int(status["rejection_count"]) + 1
        if len(status["rejections"]) < 10:
            status["rejections"].append(rejection)

    for path in candidates:
        status["candidates_checked"] = int(status["candidates_checked"]) + 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reject(path, f"could not read report: {exc}")
            continue
        if not isinstance(payload, dict):
            reject(path, "report is not a JSON object")
            continue
        if payload.get("provider") != provider:
            reject(path, f"provider is {payload.get('provider')!r}, expected {provider!r}", payload)
            continue
        report_modelpack_id = _canonical_report_modelpack_id(payload)
        if required_modelpack_id is not None and report_modelpack_id != required_modelpack_id:
            reject(path, f"modelpack id is {report_modelpack_id!r}, expected {required_modelpack_id!r}", payload)
            continue
        if (
            required_modelpack_id is None
            and provider == DEMUCS_PROVIDER
            and report_modelpack_id not in (None, DEMUCS_MODELPACK_ID)
        ):
            reject(path, "non-default Demucs report does not satisfy the default Demucs baseline", payload)
            continue
        status["matching_identity_candidate_count"] = int(status["matching_identity_candidate_count"]) + 1
        report_dataset = payload.get("dataset")
        if report_dataset != MUSDB_SDR_EVIDENCE_REQUIRED_DATASET:
            reject(
                path,
                (
                    f"dataset is {report_dataset!r}, "
                    f"expected {MUSDB_SDR_EVIDENCE_REQUIRED_DATASET!r}"
                ),
                payload,
            )
            break
        report_split = payload.get("split")
        if report_split != MUSDB_SDR_EVIDENCE_REQUIRED_SPLIT:
            reject(
                path,
                f"split is {report_split!r}, expected {MUSDB_SDR_EVIDENCE_REQUIRED_SPLIT!r}",
                payload,
            )
            break
        summary = payload.get("summary")
        tracks_ok = 0
        if isinstance(summary, dict):
            try:
                tracks_ok = int(summary.get("tracks_ok") or 0)
            except (TypeError, ValueError):
                tracks_ok = 0
        if payload.get("ok") is not True or tracks_ok <= 0:
            reject(path, "report did not complete with at least one successful track", payload)
            break
        role_summary_rejection = _musdb_role_summary_rejection(summary)
        if role_summary_rejection is not None:
            reject(path, role_summary_rejection, payload)
            break
        status.update(
            {
                "path": str(path),
                "summary": summary,
                "ready": True,
                "reason": None,
                "modelpack_id": report_modelpack_id,
            }
        )
        return status

    status["reason"] = (
        "no successful MUSDB SDR report found"
        if not failures
        else "no successful MUSDB SDR report found; " + "; ".join(failures[:3])
    )
    return status


def _musdb_sdr_comparison_status(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    baseline_label: str,
    candidate_label: str,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "baseline": baseline_label,
        "candidate": candidate_label,
        "metric": "summary.median_sdr_mean",
        "baseline_value": None,
        "candidate_value": None,
        "delta": None,
        "ready": False,
        "reason": None,
    }
    if not baseline.get("ready"):
        status["reason"] = f"{baseline_label} evidence is not ready"
        return status
    if not candidate.get("ready"):
        status["reason"] = f"{candidate_label} evidence is not ready"
        return status
    try:
        baseline_value = float((baseline.get("summary") or {}).get("median_sdr_mean"))
        candidate_value = float((candidate.get("summary") or {}).get("median_sdr_mean"))
    except (AttributeError, TypeError, ValueError):
        status["reason"] = "comparison summaries must provide numeric median_sdr_mean"
        return status
    if not math.isfinite(baseline_value) or not math.isfinite(candidate_value):
        status["reason"] = "comparison summaries must provide finite median_sdr_mean"
        return status
    delta = candidate_value - baseline_value
    status.update(
        {
            "baseline_value": baseline_value,
            "candidate_value": candidate_value,
            "delta": delta,
        }
    )
    if delta < 0.0:
        status["reason"] = (
            f"{candidate_label} median_sdr_mean {candidate_value:.6f} is below "
            f"{baseline_label} {baseline_value:.6f}"
        )
        return status
    status["ready"] = True
    return status


def _runtime_validation_report_evidence_status(
    *,
    evidence_glob: Path,
    engine: str,
    required_bool: str | None = None,
    count_field: str | None = None,
    allowed_statuses: Iterable[str] | None = None,
    identity_field: str = "engine",
    required_roles: Iterable[str] | None = None,
    runtime_required_field: str | None = None,
) -> dict[str, Any]:
    root = _model_upgrade_evidence_root()
    pattern = root / evidence_glob
    candidates = _evidence_candidates(pattern)
    allowed_status_set = {str(item) for item in (allowed_statuses or [])}
    required_role_set = {str(role).strip().lower() for role in (required_roles or []) if str(role).strip()}
    status: dict[str, Any] = {
        "glob": str(pattern),
        "engine": engine,
        "identity_field": identity_field,
        "required_bool": required_bool,
        "count_field": count_field,
        "runtime_required_field": runtime_required_field,
        "allowed_statuses": sorted(allowed_status_set),
        "required_roles": sorted(required_role_set),
        "latest_candidate_only": True,
        "candidate_count": len(candidates),
        "candidates_checked": 0,
        "rejection_count": 0,
        "rejections": [],
        "path": None,
        "report": None,
        "ready": False,
        "reason": None,
    }
    failures: list[str] = []

    def reject(path: Path, reason: str, payload: dict[str, Any] | None = None) -> None:
        failures.append(f"{path}: {reason}")
        rejection: dict[str, Any] = {"path": str(path), "reason": reason}
        if payload is not None:
            rejection[identity_field] = payload.get(identity_field)
            rejection["ok"] = payload.get("ok")
            rejection["status"] = payload.get("status")
            if required_bool is not None:
                rejection[required_bool] = payload.get(required_bool)
            if count_field is not None:
                rejection[count_field] = payload.get(count_field)
                if count_field == "event_count":
                    rejection["events"] = payload.get("events")
                if count_field == "note_count":
                    rejection["notes"] = payload.get("notes")
            if required_role_set:
                rejection["roles"] = payload.get("roles")
                rejection["require_roles"] = payload.get("require_roles")
                rejection["missing_roles"] = payload.get("missing_roles")
                rejection["stem_paths"] = payload.get("stem_paths")
            rejection["runtime"] = payload.get("runtime")
        status["rejection_count"] = int(status["rejection_count"]) + 1
        if len(status["rejections"]) < 10:
            status["rejections"].append(rejection)

    for path in candidates[:1]:
        status["candidates_checked"] = int(status["candidates_checked"]) + 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reject(path, f"could not read report: {exc}")
            continue
        if not isinstance(payload, dict):
            reject(path, "report is not a JSON object")
            continue
        if payload.get(identity_field) != engine:
            reject(path, f"{identity_field} is {payload.get(identity_field)!r}, expected {engine!r}", payload)
            continue
        if payload.get("ok") is not True:
            reject(path, "runtime validation report ok is not true", payload)
            continue
        if allowed_status_set and str(payload.get("status")) not in allowed_status_set:
            reject(path, f"status is {payload.get('status')!r}, expected one of {sorted(allowed_status_set)!r}", payload)
            continue
        if required_bool is not None and payload.get(required_bool) is not True:
            reject(path, f"{required_bool} must be true", payload)
            continue
        if count_field is not None:
            raw_count = payload.get(count_field)
            if type(raw_count) is not int:
                reject(path, f"{count_field} must be an integer greater than zero", payload)
                continue
            count = raw_count
            if count <= 0:
                reject(path, f"{count_field} must be greater than zero", payload)
                continue
            array_field = {"event_count": "events", "note_count": "notes"}.get(count_field)
            if array_field is not None:
                items = payload.get(array_field)
                if not isinstance(items, list):
                    reject(path, f"report is missing {array_field}[]", payload)
                    continue
                if len(items) != count:
                    reject(path, f"{array_field} length must equal {count_field}", payload)
                    continue
                if not items:
                    reject(path, f"{array_field}[] must not be empty", payload)
                    continue
                item_rejection = None
                for idx, item in enumerate(items):
                    if array_field == "events":
                        item_rejection = _runtime_event_payload_rejection(item, idx)
                    elif array_field == "notes":
                        item_rejection = _runtime_note_payload_rejection(item, idx)
                    if item_rejection is not None:
                        break
                if item_rejection is not None:
                    reject(path, item_rejection, payload)
                    continue
        runtime = payload.get("runtime")
        if not isinstance(runtime, dict):
            reject(path, "runtime must be a JSON object", payload)
            continue
        if runtime_required_field is not None:
            if runtime.get(runtime_required_field) is not True:
                reject(path, f"runtime.{runtime_required_field} must be true", payload)
                continue
        elif runtime.get("ready") is not True and runtime.get("configured") is not True:
            reject(path, "runtime.ready or runtime.configured must be true", payload)
            continue
        if required_role_set:
            required_requested = {
                str(role).strip().lower()
                for role in (payload.get("require_roles") or [])
                if str(role).strip()
            }
            if not required_role_set.issubset(required_requested):
                missing_requested = sorted(required_role_set - required_requested)
                reject(path, "require_roles must include: " + ", ".join(missing_requested), payload)
                continue
            missing_reported = {
                str(role).strip().lower()
                for role in (payload.get("missing_roles") or [])
                if str(role).strip()
            }
            if missing_reported:
                reject(path, "missing_roles must be empty", payload)
                continue
            roles = {str(role).strip().lower() for role in (payload.get("roles") or []) if str(role).strip()}
            if not required_role_set.issubset(roles):
                missing_roles = sorted(required_role_set - roles)
                reject(path, "roles missing required entries: " + ", ".join(missing_roles), payload)
                continue
            stem_paths = payload.get("stem_paths")
            if not isinstance(stem_paths, dict):
                reject(path, "stem_paths must be an object when roles are required", payload)
                continue
            stem_path_roles = {
                str(role).strip().lower()
                for role, role_path in stem_paths.items()
                if str(role).strip() and isinstance(role_path, str) and role_path.strip()
            }
            if not required_role_set.issubset(stem_path_roles):
                missing_stem_paths = sorted(required_role_set - stem_path_roles)
                reject(path, "stem_paths missing required entries: " + ", ".join(missing_stem_paths), payload)
                continue

        report = {
            identity_field: payload.get(identity_field),
            "ok": payload.get("ok"),
            "status": payload.get("status"),
            "reason": payload.get("reason"),
        }
        for key in (
            "wav_path",
            "mix_wav",
            "instrument",
            "event_count",
            "note_count",
            "require_events",
            "require_notes",
            "require_roles",
            "roles",
            "missing_roles",
            "stem_paths",
        ):
            if key in payload:
                report[key] = payload.get(key)
        report["runtime_ready"] = runtime.get(runtime_required_field) if runtime_required_field else (
            runtime.get("ready") or runtime.get("configured")
        )
        status.update(
            {
                "path": str(path),
                "report": report,
                "ready": True,
                "reason": None,
            }
        )
        return status

    status["reason"] = (
        "no successful runtime validation report found"
        if not failures
        else "no successful runtime validation report found; " + "; ".join(failures[:3])
    )
    return status


def _finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _json_int(value: Any, *, lo: int, hi: int) -> int | None:
    if type(value) is not int:
        return None
    if lo <= value <= hi:
        return value
    return None


def _runtime_event_payload_rejection(item: Any, index: int) -> str | None:
    label = f"events[{index}]"
    if not isinstance(item, dict):
        return f"{label} must be an object"
    time_sec = _finite_number(item.get("time"))
    if time_sec is None or time_sec < 0.0:
        return f"{label}.time must be a finite nonnegative number"
    if _json_int(item.get("note"), lo=0, hi=127) is None:
        return f"{label}.note must be an integer MIDI note in [0, 127]"
    if _json_int(item.get("velocity"), lo=1, hi=127) is None:
        return f"{label}.velocity must be an integer MIDI velocity in [1, 127]"
    if "duration" in item:
        duration = _finite_number(item.get("duration"))
        if duration is None or duration < 0.0:
            return f"{label}.duration must be a finite nonnegative number when present"
    return None


def _runtime_note_payload_rejection(item: Any, index: int) -> str | None:
    label = f"notes[{index}]"
    if not isinstance(item, dict):
        return f"{label} must be an object"
    t_on = _finite_number(item.get("t_on"))
    if t_on is None or t_on < 0.0:
        return f"{label}.t_on must be a finite nonnegative number"
    t_off = _finite_number(item.get("t_off"))
    if t_off is None or t_off <= t_on:
        return f"{label}.t_off must be a finite number greater than t_on"
    if _json_int(item.get("pitch"), lo=0, hi=127) is None:
        return f"{label}.pitch must be an integer MIDI note in [0, 127]"
    if _json_int(item.get("velocity"), lo=1, hi=127) is None:
        return f"{label}.velocity must be an integer MIDI velocity in [1, 127]"
    instrument = item.get("instrument")
    if not isinstance(instrument, str) or not instrument.strip():
        return f"{label}.instrument must be a non-empty string"
    for field, upper in (("string", 8), ("fret", 36)):
        if field in item and _json_int(item.get(field), lo=0, hi=upper) is None:
            return f"{label}.{field} must be an integer in [0, {upper}] when present"
    return None


def _mir_st500_vocals_report_evidence_status(*, algorithm: str = "melodic_rmvpe") -> dict[str, Any]:
    root = _model_upgrade_evidence_root()
    pattern = root / MIR_ST500_VOCALS_EVIDENCE_GLOB
    candidates = _evidence_candidates(pattern)
    status: dict[str, Any] = {
        "glob": str(pattern),
        "dataset": "mir_st500",
        "family": "melodic",
        "algorithm": algorithm,
        "latest_matching_identity_only": True,
        "candidate_count": len(candidates),
        "matching_identity_candidate_count": 0,
        "candidates_checked": 0,
        "rejection_count": 0,
        "rejections": [],
        "path": None,
        "summary": None,
        "case_count": 0,
        "algorithm_case_count": 0,
        "ready": False,
        "reason": None,
    }
    failures: list[str] = []

    def reject(path: Path, reason: str, payload: dict[str, Any] | None = None) -> None:
        failures.append(f"{path}: {reason}")
        rejection: dict[str, Any] = {"path": str(path), "reason": reason}
        if payload is not None:
            rejection["ok"] = payload.get("ok")
            rejection["dataset"] = payload.get("dataset")
            rejection["family"] = payload.get("family")
            rejection["case_count"] = payload.get("case_count")
            extra = payload.get("extra")
            if isinstance(extra, dict):
                rejection["split"] = extra.get("split")
                rejection["variant"] = extra.get("variant")
                rejection["algorithms"] = extra.get("algorithms")
        status["rejection_count"] = int(status["rejection_count"]) + 1
        if len(status["rejections"]) < 10:
            status["rejections"].append(rejection)

    for path in candidates:
        status["candidates_checked"] = int(status["candidates_checked"]) + 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reject(path, f"could not read report: {exc}")
            continue
        if not isinstance(payload, dict):
            reject(path, "report is not a JSON object")
            continue
        if payload.get("dataset") != "mir_st500":
            reject(path, f"dataset is {payload.get('dataset')!r}, expected 'mir_st500'", payload)
            continue
        if payload.get("family") != "melodic":
            reject(path, f"family is {payload.get('family')!r}, expected 'melodic'", payload)
            continue
        extra = payload.get("extra")
        if not isinstance(extra, dict):
            reject(path, "report is missing extra metadata", payload)
            continue
        if extra.get("split") != "test":
            reject(path, f"extra.split is {extra.get('split')!r}, expected 'test'", payload)
            continue
        if extra.get("variant") != "vocal":
            reject(path, f"extra.variant is {extra.get('variant')!r}, expected 'vocal'", payload)
            continue
        if extra.get("limit") is not MIR_ST500_VOCALS_EVIDENCE_REQUIRED_LIMIT:
            reject(path, "extra.limit must be null for full test/vocal coverage", payload)
            continue
        algorithms = extra.get("algorithms")
        if not isinstance(algorithms, list) or algorithm not in {str(item) for item in algorithms}:
            reject(path, f"extra.algorithms must include {algorithm!r}", payload)
            continue
        status["matching_identity_candidate_count"] = int(status["matching_identity_candidate_count"]) + 1
        if payload.get("ok") is not True:
            reject(path, "report ok is not true", payload)
            break
        try:
            case_count = int(payload.get("case_count") or 0)
        except (TypeError, ValueError):
            case_count = 0
        if case_count <= 0:
            reject(path, "case_count must be greater than zero", payload)
            break
        cases = payload.get("cases")
        if not isinstance(cases, list):
            reject(path, "report is missing cases[]", payload)
            break
        matching_cases = [
            row
            for row in cases
            if isinstance(row, dict) and row.get("algorithm_id") == algorithm
        ]
        if not matching_cases:
            reject(path, f"no cases for algorithm {algorithm!r}", payload)
            break
        if len(matching_cases) != case_count:
            reject(path, f"{algorithm} case count must equal report case_count", payload)
            break
        errored = [row for row in matching_cases if row.get("error")]
        if errored:
            reject(path, f"{len(errored)} {algorithm} cases have errors", payload)
            break

        summary = payload.get("summary")
        algorithm_summary = None
        if isinstance(summary, dict):
            per_algorithm = summary.get("per_algorithm")
            if isinstance(per_algorithm, dict):
                algorithm_summary = per_algorithm.get(algorithm)
        if not isinstance(algorithm_summary, dict):
            reject(path, f"summary.per_algorithm.{algorithm} is missing", payload)
            break
        try:
            cases_total = int(algorithm_summary.get("cases") or 0)
            cases_ok = int(algorithm_summary.get("cases_ok") or 0)
            cases_err = int(algorithm_summary.get("cases_err") or 0)
        except (TypeError, ValueError):
            reject(path, f"summary.per_algorithm.{algorithm} cases/cases_ok/cases_err must be numeric", payload)
            break
        if cases_total != case_count:
            reject(path, f"summary.per_algorithm.{algorithm}.cases must equal report case_count", payload)
            break
        if cases_ok <= 0:
            reject(path, f"summary.per_algorithm.{algorithm}.cases_ok must be greater than zero", payload)
            break
        if cases_ok != case_count:
            reject(path, f"summary.per_algorithm.{algorithm}.cases_ok must equal report case_count", payload)
            break
        if cases_err != 0:
            reject(path, f"summary.per_algorithm.{algorithm}.cases_err must be zero", payload)
            break
        invalid_metric = False
        for key in ("precision", "recall", "f1"):
            try:
                value_float = float(algorithm_summary.get(key))
            except (TypeError, ValueError):
                reject(path, f"summary.per_algorithm.{algorithm}.{key} must be numeric", payload)
                invalid_metric = True
                break
            if not math.isfinite(value_float) or value_float < 0.0 or value_float > 1.0:
                reject(path, f"summary.per_algorithm.{algorithm}.{key} must be finite and between 0 and 1", payload)
                invalid_metric = True
                break
        if invalid_metric:
            break
        invalid_case = False
        for row in matching_cases:
            for key in ("tp", "fp", "fn"):
                try:
                    value = int(row.get(key))
                except (TypeError, ValueError):
                    reject(path, f"{algorithm} case {row.get('case_id')!r} has non-numeric {key}", payload)
                    invalid_case = True
                    break
                if value < 0:
                    reject(path, f"{algorithm} case {row.get('case_id')!r} has negative {key}", payload)
                    invalid_case = True
                    break
            if invalid_case:
                break
            else:
                for key in ("precision", "recall", "f1"):
                    try:
                        value_float = float(row.get(key))
                    except (TypeError, ValueError):
                        reject(path, f"{algorithm} case {row.get('case_id')!r} has non-numeric {key}", payload)
                        invalid_case = True
                        break
                    if not math.isfinite(value_float):
                        reject(path, f"{algorithm} case {row.get('case_id')!r} has non-finite {key}", payload)
                        invalid_case = True
                        break
                if invalid_case:
                    break
                else:
                    continue
        if invalid_case:
            break
        status.update(
            {
                "path": str(path),
                "summary": algorithm_summary,
                "case_count": case_count,
                "algorithm_case_count": len(matching_cases),
                "ready": True,
                "reason": None,
            }
        )
        return status

    status["reason"] = (
        "no successful MIR-ST500 vocal benchmark report found"
        if not failures
        else "no successful MIR-ST500 vocal benchmark report found; " + "; ".join(failures[:3])
    )
    return status


def _model_upgrade_gates_snapshot(
    *,
    demucs_ft_modelpack_path: Path | str | None,
    demucs_ft_modelpack_manifest: dict[str, Any] | None,
    demucs_ft_modelpack_error: str | None,
    roformer_status: dict[str, Any],
    asset_payload: Any,
) -> dict[str, Any]:
    """Summarize the external gates that still block model-upgrade promotion.

    This is deliberately read-only and lightweight: it checks env vars,
    filesystem presence, adapter readiness helpers, and small dataset discovery
    diagnostics. It does not launch model subprocesses or run benchmarks.
    """

    def gate(ready: bool, *, kind: str, reason: str | None, **extra: Any) -> dict[str, Any]:
        return {
            "ready": ready,
            "kind": kind,
            "reason": reason,
            **extra,
        }

    def env_root_status(env_var: str) -> dict[str, Any]:
        raw = os.environ.get(env_var, "").strip()
        path = Path(raw).expanduser() if raw else None
        return {
            "env_var": env_var,
            "configured": bool(raw),
            "path": str(path) if path is not None else None,
            "exists": bool(path is not None and path.exists()),
            "is_dir": bool(path is not None and path.is_dir()),
        }

    def musdb_gate() -> dict[str, Any]:
        from aural_ingest.quality_benchmark import discover_musdb18_tracks, inspect_quality_dataset_sources

        datasets = inspect_quality_dataset_sources()
        musdb_datasets = {key: datasets[key] for key in ("musdb18_hq", "musdb18") if key in datasets}
        ready_roots: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        for dataset_id, dataset in musdb_datasets.items():
            for root in dataset.get("roots", []):
                path_text = str(root.get("path") or "")
                if not root.get("exists") or not path_text:
                    continue
                root_report = {
                    "dataset": dataset_id,
                    "env_var": root.get("env_var"),
                    "path": path_text,
                    "sample_track_count": 0,
                    "error": None,
                }
                try:
                    tracks = discover_musdb18_tracks(Path(path_text), split="test", limit=1)
                    root_report["sample_track_count"] = len(tracks)
                    if tracks:
                        root_report["sample_track"] = tracks[0].track_id
                        ready_roots.append(root_report)
                    else:
                        diagnostics.append(f"{path_text} has no test WAV-format MUSDB tracks")
                except Exception as exc:  # pragma: no cover - defensive against local filesystem surprises
                    root_report["error"] = str(exc)
                    diagnostics.append(str(exc))
                root["discovery"] = root_report
        dataset_ready = bool(ready_roots)
        report_evidence = _musdb_sdr_report_evidence_status(provider=DEMUCS_PROVIDER)
        ready = bool(report_evidence.get("ready"))
        reason = None if ready else str(report_evidence.get("reason") or "missing Demucs MUSDB SDR report")
        return gate(
            ready,
            kind="benchmark_report",
            reason=reason,
            datasets=musdb_datasets,
            ready_roots=ready_roots,
            dataset_ready=dataset_ready,
            dataset_reason=None
            if dataset_ready
            else "; ".join(diagnostics) or "AURAL_MUSDB18_HQ_ROOT or AURAL_MUSDB18_ROOT is unset",
            evidence=report_evidence,
        )

    def mir_st500_gate() -> dict[str, Any]:
        root = env_root_status("AURAL_MIR_ST500_ROOT")
        diagnosis: dict[str, object] | None = None
        ready = False
        reason: str | None = None
        if not root["configured"]:
            reason = "AURAL_MIR_ST500_ROOT is unset"
        elif not root["exists"]:
            reason = f"AURAL_MIR_ST500_ROOT does not exist: {root['path']}"
        elif not root["is_dir"]:
            reason = f"AURAL_MIR_ST500_ROOT is not a directory: {root['path']}"
        else:
            from aural_ingest.dataset_adapters.mir_st500 import diagnose_corpus

            diagnosis = diagnose_corpus(Path(str(root["path"])), split="test", variant="vocal", limit=1)
            ready = int(diagnosis.get("emitted_count", 0)) > 0
            reason = None if ready else str(diagnosis.get("reason") or "no test vocal cases discovered")
        return gate(
            ready,
            kind="dataset",
            reason=reason,
            root=root,
            diagnosis=diagnosis,
        )

    musdb = musdb_gate()
    mir_st500 = mir_st500_gate()
    beat_this_review = _beat_this_review_evidence_status()
    demucs_ft_sdr_evidence = _musdb_sdr_report_evidence_status(
        provider=DEMUCS_PROVIDER,
        required_modelpack_id=DEMUCS_FT_DRUMS_MODELPACK_ID,
    )
    roformer_sdr_evidence = _musdb_sdr_report_evidence_status(provider=ROFORMER_PROVIDER)
    roformer_sdr_comparison = _musdb_sdr_comparison_status(
        baseline=musdb.get("evidence") if isinstance(musdb.get("evidence"), dict) else {},
        candidate=roformer_sdr_evidence,
        baseline_label="default Demucs",
        candidate_label="RoFormer",
    )
    roformer_validation_evidence = _runtime_validation_report_evidence_status(
        evidence_glob=ROFORMER_RUNTIME_EVIDENCE_GLOB,
        engine=ROFORMER_PROVIDER,
        identity_field="provider",
        allowed_statuses=("fresh",),
        required_roles=MUSDB_SDR_EVIDENCE_REQUIRED_ROLES,
        runtime_required_field="configured",
    )
    rmvpe_validation_evidence = _runtime_validation_report_evidence_status(
        evidence_glob=RMVPE_RUNTIME_EVIDENCE_GLOB,
        engine="melodic_rmvpe",
        allowed_statuses=("ready", "ok"),
        runtime_required_field="ready",
    )
    mir_st500_report_evidence = _mir_st500_vocals_report_evidence_status()
    adtof_validation_evidence = _runtime_validation_report_evidence_status(
        evidence_glob=ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
        runtime_required_field="configured",
    )
    drum_stemsep_validation_evidence = _runtime_validation_report_evidence_status(
        evidence_glob=DRUM_STEMSEP_RUNTIME_EVIDENCE_GLOB,
        engine="drum_stemsep",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
        runtime_required_field="configured",
    )
    qmul_validation_evidence = _runtime_validation_report_evidence_status(
        evidence_glob=QMUL_HR_GUITAR_RUNTIME_EVIDENCE_GLOB,
        engine="qmul_hr_guitar",
        required_bool="require_notes",
        count_field="note_count",
        allowed_statuses=("ok",),
        runtime_required_field="configured",
    )

    from aural_ingest.algorithms import adtof_drums, drum_stemsep, melodic_rmvpe, qmul_hr_guitar

    rmvpe_checkpoint = resolve_rmvpe_checkpoint_path(_default_basic_pitch_model_roots())
    rmvpe_runtime = melodic_rmvpe.runtime_status(rmvpe_checkpoint)
    adtof_runtime = adtof_drums.runtime_status()
    drum_stemsep_runtime = drum_stemsep.runtime_status()
    qmul_runtime = qmul_hr_guitar.runtime_status()
    demucs_ft_asset = asset_payload(
        demucs_ft_modelpack_path,
        kind="demucs-modelpack",
        required=False,
    )
    if demucs_ft_modelpack_manifest is not None:
        demucs_ft_asset["manifest"] = {
            "id": demucs_ft_modelpack_manifest.get("id"),
            "version": demucs_ft_modelpack_manifest.get("version"),
            "license": demucs_ft_modelpack_manifest.get("license"),
        }
    if demucs_ft_modelpack_error:
        demucs_ft_asset["error"] = demucs_ft_modelpack_error

    gates: dict[str, dict[str, Any]] = {
        "beat_this_barline_listening_review": gate(
            bool(beat_this_review.get("ready")),
            kind="manual_review",
            reason=(
                None
                if beat_this_review.get("ready")
                else "requires human bar-line/listening review evidence for the DBN refresh-meter smokes"
            ),
            evidence_required=[
                "reviewed Psalm 121 refresh-meter timeline",
                "reviewed Psalm 130 refresh-meter timeline",
                "reviewed Psalm 5 refresh-meter timeline",
            ],
            evidence=beat_this_review,
        ),
        "musdb_sdr_baseline": musdb,
        "demucs_ft_drums_sdr": gate(
            bool(demucs_ft_asset.get("ok") and demucs_ft_sdr_evidence.get("ready")),
            kind="modelpack_and_benchmark_report",
            reason=(
                None
                if demucs_ft_asset.get("ok") and demucs_ft_sdr_evidence.get("ready")
                else "requires a license-verified demucs_ft_drums modelpack and a successful MUSDB SDR report"
            ),
            modelpack=demucs_ft_asset,
            evidence=demucs_ft_sdr_evidence,
        ),
        "roformer_musdb_comparison": gate(
            bool(
                musdb.get("ready")
                and roformer_validation_evidence.get("ready")
                and roformer_sdr_evidence.get("ready")
                and roformer_sdr_comparison.get("ready")
            ),
            kind="runtime_validation_and_benchmark_comparison",
            reason=(
                None
                if (
                    musdb.get("ready")
                    and roformer_validation_evidence.get("ready")
                    and roformer_sdr_evidence.get("ready")
                    and roformer_sdr_comparison.get("ready")
                )
                else str(
                    roformer_sdr_comparison.get("reason")
                    or "requires successful RoFormer/MSST runtime validation plus baseline Demucs and RoFormer MUSDB SDR reports"
                )
            ),
            runtime=roformer_status,
            musdb_ready=musdb.get("ready"),
            runtime_evidence=roformer_validation_evidence,
            evidence=roformer_sdr_evidence,
            comparison=roformer_sdr_comparison,
        ),
        "rmvpe_mir_st500_vocals": gate(
            bool(rmvpe_validation_evidence.get("ready") and mir_st500_report_evidence.get("ready")),
            kind="runtime_and_benchmark_report",
            reason=(
                None
                if rmvpe_validation_evidence.get("ready") and mir_st500_report_evidence.get("ready")
                else "requires a successful RMVPE runtime validation report and MIR-ST500 vocal benchmark report"
            ),
            runtime=rmvpe_runtime,
            runtime_evidence=rmvpe_validation_evidence,
            mir_st500=mir_st500,
            benchmark_evidence=mir_st500_report_evidence,
        ),
        "adtof_external_runtime": gate(
            bool(adtof_validation_evidence.get("ready")),
            kind="runtime_validation_report",
            reason=(
                None
                if adtof_validation_evidence.get("ready")
                else "requires a successful ADTOF runtime validation report with drum events"
            ),
            runtime=adtof_runtime,
            evidence=adtof_validation_evidence,
        ),
        "drum_stemsep_external_runtime": gate(
            bool(drum_stemsep_validation_evidence.get("ready")),
            kind="runtime_validation_report",
            reason=(
                None
                if drum_stemsep_validation_evidence.get("ready")
                else "requires a successful DrumSep runtime validation report with drum events"
            ),
            runtime=drum_stemsep_runtime,
            evidence=drum_stemsep_validation_evidence,
        ),
        "qmul_hr_guitar_external_runtime": gate(
            bool(qmul_validation_evidence.get("ready")),
            kind="runtime_validation_report",
            reason=(
                None
                if qmul_validation_evidence.get("ready")
                else "requires a successful QMUL guitar runtime validation report with notes"
            ),
            runtime=qmul_runtime,
            evidence=qmul_validation_evidence,
        ),
    }
    ready = sorted(name for name, item in gates.items() if item.get("ready"))
    pending = sorted(name for name, item in gates.items() if not item.get("ready"))
    evidence_root = _model_upgrade_evidence_root()
    return {
        "ok": not pending,
        "exit_code_affects_runtime_check": False,
        "evidence_root": str(evidence_root),
        "evidence_root_env_var": MODEL_UPGRADE_EVIDENCE_ROOT_ENV,
        "evidence_checklist": str(evidence_root / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST),
        "evidence_checklist_relative_path": MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST.as_posix(),
        "ready": ready,
        "pending": pending,
        "gates": gates,
    }


def _mt3_runtime_snapshot() -> dict[str, Any]:
    import numpy as np

    def sha256_path(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def sha256_tree(root: Path) -> str:
        hasher = hashlib.sha256()
        for child in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
            rel = child.relative_to(root).as_posix().encode("utf-8")
            hasher.update(rel)
            hasher.update(b"\0")
            hasher.update(sha256_path(child).encode("ascii"))
            hasher.update(b"\0")
        return hasher.hexdigest()

    def asset_payload(path_value: Path | str | None, *, kind: str, required: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": kind,
            "required": required,
        }
        if path_value is None:
            payload["ok"] = False
            payload["error"] = "missing"
            return payload

        path = Path(path_value)
        payload["path"] = str(path)
        payload["exists"] = path.exists()
        if not path.exists():
            payload["ok"] = False
            payload["error"] = "missing"
            return payload

        if path.is_file():
            payload["type"] = "file"
            payload["size_bytes"] = path.stat().st_size
            payload["sha256"] = sha256_path(path)
        elif path.is_dir():
            payload["type"] = "dir"
            payload["sha256_tree"] = sha256_tree(path)
        else:
            payload["ok"] = False
            payload["error"] = "unsupported-path-type"
            return payload

        payload["ok"] = True
        return payload

    def simple_asset_payload(path_value: Path | str | None, *, kind: str, required: bool) -> dict[str, Any]:
        return asset_payload(path_value, kind=kind, required=required)

    basic_pitch_model = resolve_basic_pitch_model_path(_default_basic_pitch_model_roots())
    ffmpeg_path = _resolve_ffmpeg_path()
    demucs_modelpack_path, demucs_modelpack_manifest, demucs_modelpack_error = _resolve_demucs_modelpack({})
    demucs_ft_modelpack_path, demucs_ft_modelpack_manifest, demucs_ft_modelpack_error = _resolve_demucs_modelpack(
        {"stem_separation_modelpack_id": DEMUCS_FT_DRUMS_MODELPACK_ID}
    )
    roformer_status = roformer_runtime_status({})

    payload = {
        "ok": True,
        "policies": {
            "beat_tempo": BEAT_TEMPO_PRODUCTION_POLICY,
            "stem_separation": STEM_SEPARATION_PROVIDER_POLICY,
            "benchmark_thresholds": BENCHMARK_THRESHOLD_POLICY,
        },
        "dependencies": {},
        "drum_engines": available_mt3_modelpacks(),
        "assets": {
            "basic_pitch_model": asset_payload(
                basic_pitch_model,
                kind="basic-pitch-model",
                required=True,
            ),
            "ffmpeg": asset_payload(ffmpeg_path, kind="ffmpeg", required=False),
            "demucs_modelpack": asset_payload(
                demucs_modelpack_path,
                kind="demucs-modelpack",
                required=False,
            ),
            "demucs_ft_drums_modelpack": asset_payload(
                demucs_ft_modelpack_path,
                kind="demucs-modelpack",
                required=False,
            ),
        },
        "runtime_features": {
            "roformer": {
                "enabled": bool(roformer_status.get("configured")),
                "provider": ROFORMER_PROVIDER,
                "runtime": roformer_status,
                "fallback_behavior": "auto separation uses Demucs when available; explicit RoFormer requests skip when runtime is unconfigured",
            }
        },
    }
    payload["stages"] = _runtime_stage_snapshot(
        payload["drum_engines"],
        demucs_modelpack_path,
        demucs_modelpack_manifest,
        demucs_modelpack_error,
    )
    if demucs_modelpack_manifest is not None:
        payload["assets"]["demucs_modelpack"]["manifest"] = {
            "id": demucs_modelpack_manifest.get("id"),
            "version": demucs_modelpack_manifest.get("version"),
        }
    if demucs_modelpack_error:
        payload["assets"]["demucs_modelpack"]["error"] = demucs_modelpack_error
    if demucs_ft_modelpack_manifest is not None:
        payload["assets"]["demucs_ft_drums_modelpack"]["manifest"] = {
            "id": demucs_ft_modelpack_manifest.get("id"),
            "version": demucs_ft_modelpack_manifest.get("version"),
        }
    if demucs_ft_modelpack_error:
        payload["assets"]["demucs_ft_drums_modelpack"]["error"] = demucs_ft_modelpack_error

    for module_name, dependency_policy in RUNTIME_DEPENDENCY_POLICIES.items():
        dependency_payload = _runtime_dependency_snapshot(module_name, dependency_policy)
        payload["dependencies"][module_name] = dependency_payload
        if dependency_policy.get("required") and not dependency_payload.get("ok"):
            payload["ok"] = False

    payload["runtime_features"]["basic_pitch"] = _basic_pitch_runtime_feature(
        payload["assets"]["basic_pitch_model"],
        payload["dependencies"],
    )
    if not payload["runtime_features"]["basic_pitch"]["enabled"]:
        payload["ok"] = False

    mt3_module = sys.modules.get("mt3_infer")
    mt3_load_model = getattr(mt3_module, "load_model", None) if mt3_module is not None else None
    if callable(mt3_load_model):
        for engine_id, engine_info in payload["drum_engines"].items():
            if not engine_info.get("ok"):
                continue
            checkpoint_path = str(engine_info.get("checkpoint_path", "")).strip()
            model_id = str(engine_info.get("model_id", "")).strip()
            if not checkpoint_path or not model_id:
                continue
            try:
                ensure_mt3_transformers_compat()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        model = mt3_load_model(
                            model_id,
                            checkpoint_path=checkpoint_path,
                            device="cpu",
                            auto_download=False,
                        )
                        midi = model.transcribe(np.zeros(16_000, dtype=np.float32), sr=16_000)
                note_count = 0
                for track in getattr(midi, "tracks", []):
                    for message in track:
                        if getattr(message, "type", "") == "note_on" and int(getattr(message, "velocity", 0)) > 0:
                            note_count += 1
                engine_info["loadable"] = True
                engine_info["adapter_class"] = model.__class__.__name__
                engine_info["transcribe_smoke_ok"] = True
                engine_info["smoke_note_count"] = note_count
                del model
            except Exception as exc:
                engine_info["loadable"] = False
                engine_info["transcribe_smoke_ok"] = False
                engine_info["error"] = str(exc)
                payload["ok"] = False

    payload["stages"] = _runtime_stage_snapshot(
        payload["drum_engines"],
        demucs_modelpack_path,
        demucs_modelpack_manifest,
        demucs_modelpack_error,
    )

    mt3_assets: dict[str, dict[str, Any]] = {}
    for engine_id, engine_info in payload["drum_engines"].items():
        checkpoint_path = str(engine_info.get("checkpoint_path", "")).strip()
        asset = asset_payload(
            checkpoint_path or None,
            kind="mt3-checkpoint",
            required=bool(engine_info.get("ok")),
        )
        if engine_info.get("ok"):
            asset["engine"] = engine_id
            asset["modelpack_id"] = engine_info.get("modelpack_id")
            asset["modelpack_version"] = engine_info.get("modelpack_version")
        mt3_assets[engine_id] = asset
    payload["assets"]["mt3_checkpoints"] = mt3_assets
    payload["model_upgrade_gates"] = _model_upgrade_gates_snapshot(
        demucs_ft_modelpack_path=demucs_ft_modelpack_path,
        demucs_ft_modelpack_manifest=demucs_ft_modelpack_manifest,
        demucs_ft_modelpack_error=demucs_ft_modelpack_error,
        roformer_status=roformer_status,
        asset_payload=simple_asset_payload,
    )

    if not payload["assets"]["basic_pitch_model"].get("ok"):
        payload["ok"] = False

    if not any(item.get("ok") for item in payload["drum_engines"].values()):
        payload["warnings"] = [
            "No MT3 modelpacks were discovered. Install mr_mt3 and/or yourmt3 modelpacks for local learned-drum benchmarking."
        ]

    return payload


def cmd_runtime_check(args: argparse.Namespace) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            payload = _mt3_runtime_snapshot()
    if getattr(args, "require_model_upgrade_gates", False):
        model_upgrade_gates = payload.get("model_upgrade_gates")
        if not isinstance(model_upgrade_gates, dict):
            payload["ok"] = False
            payload.setdefault("errors", []).append("model-upgrade gate snapshot missing or malformed")
        else:
            model_upgrade_gates["exit_code_affects_runtime_check"] = True
            gate_ok = model_upgrade_gates.get("ok")
            if gate_ok is not True:
                pending = model_upgrade_gates.get("pending")
                pending_text = ", ".join(str(item) for item in pending) if isinstance(pending, list) else "unknown"
                payload["ok"] = False
                payload.setdefault("errors", []).append(f"model-upgrade gates pending: {pending_text}")
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


def cmd_model_setup(args: argparse.Namespace) -> int:
    """Emit per-external-model setup descriptors (install + license-accept URLs)
    for the AuralStudio "Model setup" surface to render."""
    from aural_ingest.model_setup import model_setup_snapshot

    print(json.dumps(model_setup_snapshot(), sort_keys=True))
    return 0


_MUSCRIPTOR_WEIGHTS_FILE = "model.safetensors"


def _spec_available_for_cli(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _muscriptor_needs_auth(exc: BaseException) -> bool:
    """Whether a hub failure means "sign in / accept the license" rather than a
    transport problem.

    Type first: huggingface_hub raises GatedRepoError / RepositoryNotFoundError
    for exactly this case. The string fallback only runs when those types can't
    be imported, and matches 401/403 on word boundaries -- hub errors carry a
    hex request id, so a naive substring match false-positives on it.
    """
    try:
        from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

        if isinstance(exc, (GatedRepoError, RepositoryNotFoundError)):
            return True
    except Exception:
        pass

    lowered = str(exc).lower()
    if re.search(r"(?<![0-9a-f])(401|403)(?![0-9a-f])", lowered):
        return True
    return any(
        phrase in lowered
        for phrase in ("gated", "awaiting a review", "access to model", "unauthorized",
                       "authenticated", "accept the")
    )


def _muscriptor_check_access(*, repo_id: str, size: str) -> int:
    """Report whether the weights can be fetched -- without fetching them.

    This is what lets the setup dialog avoid demanding a pasted token: if the
    user is already signed in (``huggingface-cli login`` leaves a stored
    credential that ``huggingface_hub`` picks up on its own) and has accepted
    the license, there is nothing left to type.

    Deliberately NOT ``model_info``: a gated repo's metadata is public, so
    ``model_info`` succeeds even with a junk token and would wave through a
    user who cannot actually download. ``auth_check`` tests the entitlement
    itself. Neither transfers weights.
    """
    payload: dict[str, Any] = {"ok": False, "size": size, "repo": repo_id, "check_only": True}
    try:
        from huggingface_hub import HfApi
        from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
    except Exception as exc:
        payload["error"] = f"huggingface_hub unavailable: {exc}"
        print(json.dumps(payload))
        return 1

    api = HfApi()
    # `whoami` validates the credential; a token merely *existing* says nothing
    # about whether it still works, and that distinction decides whether the
    # dialog asks for a token or tells the user to accept the license.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            api.whoami()
        payload["authenticated"] = True
    except Exception:
        payload["authenticated"] = False

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            api.auth_check(repo_id)
    except (GatedRepoError, RepositoryNotFoundError) as exc:
        payload["error"] = str(exc).splitlines()[0]
        payload["needs_license_acceptance"] = True
        print(json.dumps(payload))
        return 1
    except Exception as exc:  # transport / DNS / proxy -- not an entitlement problem
        payload["error"] = str(exc).splitlines()[0]
        payload["needs_license_acceptance"] = False
        print(json.dumps(payload))
        return 1

    payload["ok"] = True
    payload["access_granted"] = True
    print(json.dumps(payload))
    return 0


def _emit_line(payload: dict[str, Any]) -> None:
    """One JSON object per line, flushed immediately.

    The Studio dialog renders these as they arrive, so buffering here would
    reproduce exactly the "no useful updates" problem this reporting exists to
    solve.
    """
    print(json.dumps(payload), flush=True)


def _muscriptor_weights_total_bytes(repo_id: str) -> int | None:
    """Size of the weights file, for a percentage. Best-effort: a missing total
    degrades the UI to "N MB downloaded", never blocks the download."""
    try:
        from huggingface_hub import HfApi

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = HfApi().model_info(repo_id, files_metadata=True)
        for sibling in info.siblings or []:
            if sibling.rfilename == _MUSCRIPTOR_WEIGHTS_FILE:
                return int(sibling.size) if sibling.size else None
    except Exception:
        return None
    return None


def _watch_incomplete_downloads(
    repo_dir: Path, total_bytes: int | None, stop: threading.Event
) -> None:
    """Report bytes-on-disk while huggingface_hub downloads.

    Polls the ``*.incomplete`` blobs rather than hooking huggingface_hub's tqdm:
    the partial file is a stable, public artifact of the cache layout, whereas
    the progress-bar internals are not part of its API.
    """
    last_reported = -1
    while not stop.wait(1.0):
        try:
            done = sum(p.stat().st_size for p in repo_dir.rglob("*.incomplete"))
        except Exception:
            continue
        # Only speak when something changed, so a stalled transfer is visible
        # as silence rather than a stream of identical lines.
        if done and done != last_reported:
            last_reported = done
            payload: dict[str, Any] = {"event": "progress", "downloaded_bytes": done}
            if total_bytes:
                payload["total_bytes"] = total_bytes
                payload["pct"] = round(min(100.0, done * 100.0 / total_bytes), 1)
            _emit_line(payload)


def cmd_muscriptor_download(args: argparse.Namespace) -> int:
    """Download MuScriptor's gated weights into the user's HuggingFace cache.

    The engine itself ships with the sidecar; only the CC-BY-NC-4.0 weights are
    fetched here, and only once the user has accepted the model license and is
    authenticated (``HF_TOKEN`` / ``huggingface-cli login``).

    Streams ``{"event": "progress", ...}`` lines while transferring and ends
    with a single result object, so the Studio dialog can show real bytes
    instead of an indefinite spinner. Fetches the files directly rather than
    calling ``load_model``: building the model would pull 5.5 GB into RAM to
    prove a download that the files on disk already prove.
    """
    from aural_ingest.algorithms.muscriptor import _DEFAULT_SIZE

    requested = getattr(args, "size", "") or os.environ.get("AURAL_MUSCRIPTOR_SIZE", "")
    size = requested.strip() or _DEFAULT_SIZE
    repo_id = f"MuScriptor/muscriptor-{size}"

    if getattr(args, "check_only", False):
        return _muscriptor_check_access(repo_id=repo_id, size=size)

    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.constants import HF_HUB_CACHE
    except Exception as exc:
        _emit_line({"ok": False, "size": size, "error": f"huggingface_hub unavailable: {exc}"})
        return 1

    if not _spec_available_for_cli("muscriptor"):
        _emit_line({"ok": False, "size": size, "error": "engine unavailable in this build"})
        return 1

    total_bytes = _muscriptor_weights_total_bytes(repo_id)
    _emit_line(
        {
            "event": "start",
            "repo": repo_id,
            "size": size,
            "total_bytes": total_bytes,
            "file": _MUSCRIPTOR_WEIGHTS_FILE,
        }
    )

    org, _, name = repo_id.partition("/")
    repo_dir = Path(HF_HUB_CACHE) / f"models--{org}--{name}"
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_incomplete_downloads,
        args=(repo_dir, total_bytes, stop),
        daemon=True,
    )
    watcher.start()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # config.json carries the architecture load_model reads; without it
            # the weights alone are unusable.
            for filename in ("config.json", _MUSCRIPTOR_WEIGHTS_FILE):
                path = hf_hub_download(repo_id=repo_id, filename=filename)
    except Exception as exc:
        _emit_line(
            {
                "ok": False,
                "size": size,
                "repo": repo_id,
                "error": str(exc),
                "needs_license_acceptance": _muscriptor_needs_auth(exc),
            }
        )
        return 1
    finally:
        stop.set()
        watcher.join(timeout=2.0)

    _emit_line({"ok": True, "size": size, "repo": repo_id, "path": str(path)})
    return 0


def _convert_auralsong_to_feedpak(working_dir: Path) -> Path:
    """Convert a finished ``.auralsong`` working dir in place to a ``.feedpak``.

    Writes ``<name>.feedpak`` next to ``working_dir`` via the feedpak writer
    and removes the ``.auralsong`` working dir. The final output path uses
    the ``.feedpak`` extension regardless of what extension the caller passed
    as ``--out``.

    If the caller passed a non-``.feedpak`` extension (typically ``.auralsong``
    from external scripts written before the 2026-06-22 ingest overhaul), we
    rewrite the final filename to use ``.feedpak`` and log a NOTICE on stderr
    so the caller can discover the new path. Both Studio's Cleanup readiness
    check and the game's discovery loop route by file extension to pick the
    right reader (manifest.yaml for ``.feedpak`` vs manifest.json for
    ``.auralsong``); silently writing feedpak content under ``.auralsong``
    makes the pack read as "Invalid (missing title)" downstream.
    """
    parent = working_dir.parent
    summary = write_feedpak(working_dir, parent)
    feedpak_dir = Path(summary["feedpak_dir"])

    # Pick the final on-disk name: keep the user's base name but pin the
    # extension to .feedpak (the format we actually emitted). If the user
    # already passed .feedpak this is a no-op.
    base_stem = working_dir.name
    if base_stem.endswith(".auralsong"):
        base_stem = base_stem[: -len(".auralsong")]
    elif base_stem.endswith(".feedpak"):
        base_stem = base_stem[: -len(".feedpak")]
    desired = parent / f"{base_stem}.feedpak"

    if not working_dir.name.endswith(".feedpak"):
        log(
            f"notice: --out {working_dir.name!r} writes feedpak content; "
            f"final artifact will be {desired.name!r}"
        )

    # Stage the feedpak under a temp name so we can free the desired path
    # safely (the writer may have already emitted to desired, or it may
    # have used a different name).
    staged = parent / (desired.name + ".feedpak.tmp")
    if staged.exists():
        shutil.rmtree(staged)
    feedpak_dir.rename(staged)

    # Remove the .auralsong working dir AND any prior file at the desired
    # path so the final rename lands cleanly.
    if working_dir.exists():
        shutil.rmtree(working_dir)
    if desired.exists() and desired != working_dir:
        shutil.rmtree(desired)

    staged.rename(desired)
    return desired


def cmd_import_musicxml(args: argparse.Namespace) -> int:
    """Build a ``.feedpak`` directly from a MusicXML score (no transcription).

    The score already carries notes, tempo, time signature and a metronomic bar
    grid, so this bypasses audio transcription entirely. A co-located render is
    attached as the pack's audio when present (or via ``--audio``).
    """
    from aural_ingest.musicxml_feedpak import build_feedpak_from_musicxml

    src = Path(args.input_musicxml_path)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"no such file: {src}"}))
        return 2
    try:
        result = build_feedpak_from_musicxml(
            src,
            Path(args.out),
            audio_path=args.audio or None,
            title=args.title or None,
            artist=args.artist or None,
            genre=args.genre or None,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    src = Path(args.input_audio_path)
    out = Path(args.out)
    profile = args.profile
    config = _parse_config_arg(args.config)
    config = _config_with_cli_demucs_modelpack_options(args, config)
    tr_opts, tr_err = _resolve_transcription_options(args, config)
    if tr_opts is None:
        log(tr_err or "invalid transcription options")
        return 2
    for w in tr_opts.get("warnings", []):
        log(w)

    if not src.exists():
        log(f"input does not exist: {src}")
        return 2

    input_stem_paths = _resolved_input_stem_paths(config)
    if input_stem_paths:
        try:
            src_resolved = src.resolve()
            if any(stem_path.resolve() == src_resolved for stem_path in input_stem_paths.values()):
                config = {**config, "_synthesized_mix_from_input_stems": True}
        except Exception:
            pass

    # Stage 0: init_auralsong
    emit(ProgressEvent(type="stage_start", id="init_auralsong", progress=0.0))
    _mkdir(out)
    _mkdir(out / "audio")
    _mkdir(out / "features")
    _mkdir(out / "meta")

    source_sha = _sha256_file(src)
    song_id = _stable_song_id(source_sha, profile, tr_opts)

    # MVP timing metadata is minimal.
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "song_id": song_id,
        "title": args.title or src.stem,
        "artist": args.artist or "",
        # Empty rather than absent so the field always exists; the pack
        # writer drops it when blank rather than recording a genre of "".
        "genre": getattr(args, "genre", "") or "",
        "duration_sec": 0.0,
        "source": {
            "original_filename": src.name,
            "original_sha256": source_sha,
            "ingest_timestamp": config.get(
                "ingest_timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ),
        },
        "timing": {
            "audio_sample_rate_hz": None,
            "audio_start_offset_sec": 0.0,
            "timebase": "audio",
        },
        "pipeline": {
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": PIPELINE_VERSION,
            "profile": profile,
            "stage_fingerprints": {st.id: st.version for st in STAGES},
            "transcription": tr_opts,
        },
        "recognition": _recognition_manifest_block(tr_opts),
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
            "midi": {"notes_path": "features/notes.mid"},
        },
    }

    _write_json(out / "manifest.json", manifest)
    emit(ProgressEvent(type="stage_done", id="init_auralsong", progress=0.1, artifact="manifest.json"))

    # Stage 1: decode_audio
    emit(ProgressEvent(type="stage_start", id="decode_audio", progress=0.1))
    dst_wav = out / "audio" / "mix.wav"
    try:
        emit(ProgressEvent(type="stage_progress", id="decode_audio", progress=0.15, message="decoding to PCM wav"))
        duration_sec, sr = _decode_to_wav(src, dst_wav)
    except Exception as e:
        log(str(e))
        emit(ProgressEvent(type="stage_done", id="decode_audio", progress=0.3, message="failed", artifact=None))
        return 3

    manifest["duration_sec"] = float(round(duration_sec, 6))
    manifest["timing"]["audio_sample_rate_hz"] = int(sr)
    manifest["assets"]["audio"]["mix_path"] = "audio/mix.wav"
    _write_json(out / "manifest.json", manifest)
    emit(ProgressEvent(type="stage_done", id="decode_audio", progress=0.3, artifact="audio/mix.wav"))

    # Stage 2: beats_tempo
    emit(ProgressEvent(type="stage_start", id="beats_tempo", progress=0.3))
    emit(
        ProgressEvent(
            type="stage_progress",
            id="beats_tempo",
            progress=0.35,
            message=f"analyzing tempo ({tr_opts['beat_analysis_mode']})",
        )
    )
    bpm, beats, tempo_map, beat_tempo_meta = _analyze_beats_tempo(
        dst_wav,
        duration_sec=duration_sec,
        config=config,
        beat_analysis_mode=str(tr_opts["beat_analysis_mode"]),
    )
    _write_json(out / "features" / "beats.json", beats)
    _write_json(out / "features" / "tempo_map.json", tempo_map)
    manifest.setdefault("assets", {}).setdefault("features", {})["beats_path"] = "features/beats.json"
    manifest["assets"]["features"]["tempo_map_path"] = "features/tempo_map.json"
    manifest.setdefault("pipeline", {})["beats_tempo"] = beat_tempo_meta
    _write_json(out / "manifest.json", manifest)
    emit(
        ProgressEvent(
            type="stage_done",
            id="beats_tempo",
            progress=0.55,
            message="tempo/beat structure captured for MIDI export",
            artifact="features/beats.json",
        )
    )

    # Stage 3: sections
    emit(ProgressEvent(type="stage_start", id="sections", progress=0.55))
    emit(ProgressEvent(type="stage_progress", id="sections", progress=0.6, message="segmenting"))
    sections = {"sections_version": "1.0.0", "sections": _generate_sections(duration_sec, bpm)}
    _write_json(out / "features" / "sections.json", sections)
    manifest.setdefault("assets", {}).setdefault("features", {})["sections_path"] = "features/sections.json"
    _write_json(out / "manifest.json", manifest)
    emit(
        ProgressEvent(
            type="stage_done",
            id="sections",
            progress=0.7,
            message="sections captured for MIDI export",
            artifact="features/sections.json",
        )
    )

    mix_sha256 = _sha256_file(dst_wav)

    # Stage 4: separate_stems (Demucs modelpack-gated)
    stems_dir = out / "audio" / "stems"
    _mkdir(stems_dir)
    copied_input_stems = _copy_input_stems_from_config(stems_dir, config)
    if copied_input_stems:
        audio_assets = manifest.setdefault("assets", {}).setdefault("audio", {})
        stems_assets = audio_assets.setdefault("stems", {})
        for stem_name, stem_relpath in copied_input_stems.items():
            stems_assets[f"{stem_name}_path"] = stem_relpath
        manifest.setdefault("pipeline", {})["input_stems"] = {
            "source": "config",
            "roles": sorted(copied_input_stems.keys()),
            "stem_paths": copied_input_stems,
        }
        _write_json(out / "manifest.json", manifest)
    emit(ProgressEvent(type="stage_start", id="separate_stems", progress=0.7))
    emit(
        ProgressEvent(
            type="stage_progress",
            id="separate_stems",
            progress=0.73,
            message="separating stems with Demucs",
        )
    )
    try:
        separation_summary = _run_stem_separation(
            dst_wav,
            stems_dir,
            mix_sha256=mix_sha256,
            shifts=int(tr_opts.get("shifts", 1) or 1),
            config=config,
            provider_name=str(tr_opts.get("stem_separation_provider") or "none"),
            provider_path=(
                str(tr_opts.get("stem_separation_provider_path"))
                if tr_opts.get("stem_separation_provider_path")
                else None
            ),
            # Don't let Demucs overwrite stems the user already supplied --
            # the user's Suno-exported (Keyboard).wav can be cleaner than a
            # stem separated from the synthesized mix, so any role in
            # copied_input_stems is left as-is on disk.
            protected_roles=copied_input_stems.keys(),
        )
    except Exception as exc:
        separation_summary = {
            "ok": False,
            "status": "skipped",
            "reason": f"stem separation provider failed: {exc}",
            "provider": tr_opts.get("stem_separation_provider"),
            "provider_path": tr_opts.get("stem_separation_provider_path"),
        }
    if separation_summary.get("ok"):
        audio_assets = manifest.setdefault("assets", {}).setdefault("audio", {})
        stems_assets = audio_assets.setdefault("stems", {})
        stem_paths = separation_summary.get("stem_paths", {})
        if isinstance(stem_paths, dict):
            for stem_name, stem_relpath in stem_paths.items():
                stems_assets[f"{stem_name}_path"] = stem_relpath

        manifest.setdefault("pipeline", {})["stem_separation"] = {
            "provider": separation_summary.get("provider"),
            "provider_path": separation_summary.get("provider_path"),
            "modelpack_id": separation_summary.get("modelpack_id"),
            "modelpack_version": separation_summary.get("modelpack_version"),
            "architecture": separation_summary.get("architecture"),
            "modelpack_path": separation_summary.get("modelpack_path"),
            "weight_path": separation_summary.get("weight_path"),
            "source_path": "audio/mix.wav",
            "mix_sha256": mix_sha256,
            "cache_hit": bool(separation_summary.get("cache_hit", False)),
            "shifts": int(separation_summary.get("shifts", 1) or 1),
            "device": separation_summary.get("device"),
            "stems": sorted(stem_paths.keys()) if isinstance(stem_paths, dict) else [],
            # Roles whose on-disk audio came from user-supplied input stems
            # rather than from this separation pass -- demucs was prevented
            # from overwriting them to preserve the user's clean source.
            "protected_stems": sorted(copied_input_stems.keys()),
        }
        _write_json(out / "manifest.json", manifest)
        emit(
            ProgressEvent(
                type="stage_done",
                id="separate_stems",
                progress=0.78,
                artifact="audio/stems/drums.wav",
                message="cached" if separation_summary.get("cache_hit") else None,
            )
        )
    else:
        msg = str(separation_summary.get("reason") or "stem separation unavailable")
        log(msg)
        tr_opts["warnings"] = [*tr_opts.get("warnings", []), msg]
        manifest.setdefault("pipeline", {})["stem_separation"] = {
            "provider": separation_summary.get("provider"),
            "provider_path": separation_summary.get("provider_path"),
            "status": "skipped",
            "reason": msg,
            "source_path": "audio/mix.wav",
            "mix_sha256": mix_sha256,
        }
        _write_json(out / "manifest.json", manifest)
        emit(
            ProgressEvent(
                type="stage_done",
                id="separate_stems",
                progress=0.78,
                message="skipped",
                artifact=None,
            )
        )

    # Stage 5: split_guitar_stems (use Demucs guitar stem when available)
    emit(ProgressEvent(type="stage_start", id="split_guitar_stems", progress=0.78))
    lead_stem = stems_dir / "lead_guitar.wav"
    rhythm_stem = stems_dir / "rhythm_guitar.wav"
    split_summary: dict[str, Any] | None = None
    split_source, split_source_kind = _resolve_guitar_split_source(out, dst_wav, config)
    synthesized_input_mix = bool(config.get("_synthesized_mix_from_input_stems"))

    if lead_stem.is_file() and rhythm_stem.is_file():
        audio_assets = manifest.setdefault("assets", {}).setdefault("audio", {})
        stems_assets = audio_assets.setdefault("stems", {})
        stems_assets["lead_guitar_path"] = "audio/stems/lead_guitar.wav"
        stems_assets["rhythm_guitar_path"] = "audio/stems/rhythm_guitar.wav"
        stems_assets["guitar_split_source_path"] = stems_assets.get("guitar_path") or _try_relpath(split_source, out)
        stems_assets["guitar_split_source_kind"] = "provided_split"

        manifest.setdefault("pipeline", {})["guitar_split"] = {
            "status": "reused",
            "source_path": stems_assets["guitar_split_source_path"],
            "source_kind": "provided_split",
        }
        _write_json(out / "manifest.json", manifest)
        emit(
            ProgressEvent(
                type="stage_done",
                id="split_guitar_stems",
                progress=0.86,
                artifact="audio/stems/lead_guitar.wav",
                message="reused",
            )
        )
    elif synthesized_input_mix and split_source_kind == "mix_fallback":
        msg = "skipped: synthesized input-stem mix has no guitar source"
        manifest.setdefault("pipeline", {})["guitar_split"] = {
            "status": "skipped",
            "reason": msg,
            "source_path": "audio/mix.wav",
            "source_kind": "input_stems_mix",
        }
        _write_json(out / "manifest.json", manifest)
        emit(
            ProgressEvent(
                type="stage_done",
                id="split_guitar_stems",
                progress=0.86,
                message="skipped",
                artifact=None,
            )
        )
    else:
        try:
            emit(
                ProgressEvent(
                    type="stage_progress",
                    id="split_guitar_stems",
                    progress=0.82,
                    message="splitting guitar lead/rhythm stems",
                )
            )
            split_summary = split_lead_rhythm_guitar_stem(split_source, lead_stem, rhythm_stem)

            audio_assets = manifest.setdefault("assets", {}).setdefault("audio", {})
            stems_assets = audio_assets.setdefault("stems", {})
            stems_assets["lead_guitar_path"] = "audio/stems/lead_guitar.wav"
            stems_assets["rhythm_guitar_path"] = "audio/stems/rhythm_guitar.wav"
            stems_assets["guitar_split_source_path"] = _try_relpath(split_source, out)
            stems_assets["guitar_split_source_kind"] = split_source_kind

            manifest.setdefault("pipeline", {})["guitar_split"] = {
                **split_summary,
                "source_path": _try_relpath(split_source, out),
                "source_kind": split_source_kind,
            }
            _write_json(out / "manifest.json", manifest)
            emit(
                ProgressEvent(
                    type="stage_done",
                    id="split_guitar_stems",
                    progress=0.86,
                    artifact="audio/stems/lead_guitar.wav",
                )
            )
        except Exception as e:
            msg = f"guitar split failed: {e}"
            log(msg)
            tr_opts["warnings"] = [*tr_opts.get("warnings", []), msg]
            emit(
                ProgressEvent(
                    type="stage_done",
                    id="split_guitar_stems",
                    progress=0.86,
                    message="skipped",
                    artifact=None,
                )
            )

    # Stage 6: transcribe_drums (recovery scaffold)
    emit(ProgressEvent(type="stage_start", id="transcribe_drums", progress=0.86))
    emit(
        ProgressEvent(type="stage_progress", id="transcribe_drums", progress=0.9, message="analyzing drum onsets")
    )
    drum_source, drum_source_kind = _resolve_drum_transcription_source(args, out, dst_wav, config)
    drum_source_path = _try_relpath(drum_source, out)
    manifest.setdefault("assets", {}).setdefault("audio", {}).setdefault("stems", {})[
        "drum_transcription_source_path"
    ] = drum_source_path
    manifest["assets"]["audio"]["stems"]["drum_transcription_source_kind"] = drum_source_kind
    _write_json(out / "manifest.json", manifest)

    # Whole-mix transcription (opt-in): run the full mix through a single
    # multi-instrument engine and use its per-role output in place of per-stem
    # drum + melodic transcription. Fail-safe: a None result (engine absent,
    # gated weights unavailable, or inference error) falls through to the
    # normal per-stem path.
    wholemix_result = None
    wholemix_active = False
    if tr_opts.get("wholemix_transcriber") == "muscriptor":
        from aural_ingest.algorithms import muscriptor as _muscriptor

        if _muscriptor.available():
            emit(
                ProgressEvent(
                    type="stage_progress",
                    id="transcribe_drums",
                    progress=0.90,
                    message="whole-mix transcription (MuScriptor)",
                )
            )
            # Condition on what the separator found rather than letting the
            # model guess. Free: the stems already exist by this point, and a
            # stem with sound in it is an instrument that is present.
            conditioning = _muscriptor.instruments_from_stems(stems_dir)
            if conditioning:
                log(f"conditioning MuScriptor on: {', '.join(conditioning)}")
            wholemix_result = _muscriptor.transcribe_mix(
                dst_wav, instruments=conditioning
            )
        if wholemix_result is None:
            log(
                "whole-mix transcriber 'muscriptor' unavailable or produced nothing; "
                "falling back to per-stem transcription"
            )
        else:
            wholemix_active = True

    if (
        not wholemix_active
        and is_mt3_drum_engine(tr_opts["drum_engine"])
        and drum_source_kind == "mix_fallback"
    ):
        log(
            f"requested MT3 drum engine '{tr_opts['drum_engine']}' requires an explicit or separated drum stem; mix fallback is not allowed"
        )
        return 4

    skip_synthetic_mix_drums = (
        synthesized_input_mix
        and drum_source_kind == "mix_fallback"
        and "drums" not in copied_input_stems
    )
    if wholemix_active:
        drum_result = DrumTranscriptionResult(
            events=wholemix_result.drums,
            used_algorithm="muscriptor_wholemix",
            attempted_algorithms=["muscriptor"],
            warnings=[],
            meta={"backend": "muscriptor_wholemix", **wholemix_result.meta},
        )
    elif skip_synthetic_mix_drums:
        msg = "skipped drum transcription: synthesized input-stem mix has no drum source"
        log(msg)
        drum_result = DrumTranscriptionResult(
            events=[],
            used_algorithm=None,
            attempted_algorithms=[],
            warnings=[msg],
            meta={"backend": "skipped", "skip_reason": "input_stems_mix_without_drums"},
        )
    else:
        drum_registry = build_default_drum_algorithm_registry()
        if tr_opts.get("drum_engine_selection") == "profile":
            drum_result = transcribe_drums_with_profile(
                drum_source,
                profile=tr_opts.get("transcription_profile"),
                algorithm_registry=drum_registry,
                logger=log,
            )
        else:
            drum_result = transcribe_drums(
                drum_source,
                requested_engine=tr_opts["drum_filter_requested"],
                algorithm_registry=drum_registry,
                logger=log,
            )
    if (
        not wholemix_active
        and is_mt3_drum_engine(tr_opts["drum_filter_requested"])
        and drum_result.used_algorithm is None
    ):
        log("requested MT3 drum engine did not produce a chart; aborting import")
        return 4

    # Stage 6b: validate drum events against stem silence (post-filter).
    # Drops events whose neighborhood in the separated drum stem is below
    # the configured dBFS gate. Applies uniformly to every drum engine
    # (heuristic + MT3) so engine code stays untouched. See
    # `validate_drum_events_against_stem_silence` for behavior, defaults,
    # and overrides.
    if drum_result.events and drum_source_kind != "mix_fallback":
        gated_events, gate_meta = validate_drum_events_against_stem_silence(
            drum_result.events,
            drum_source,
            gate_dbfs=tr_opts.get("drum_silence_gate_dbfs"),
            window_ms=tr_opts.get("drum_silence_gate_window_ms"),
            disabled=tr_opts.get("drum_silence_gate_disabled") or None,
            logger=log,
        )
        drum_result = DrumTranscriptionResult(
            events=gated_events,
            used_algorithm=drum_result.used_algorithm,
            attempted_algorithms=drum_result.attempted_algorithms,
            warnings=drum_result.warnings,
            meta={**drum_result.meta, "stem_silence_gate": gate_meta},
        )
        tr_opts["drum_silence_gate"] = gate_meta
    else:
        # Either no events (nothing to gate) or we transcribed from the
        # full mix (no separated drum stem -> gating against the mix would
        # never trigger). Record skip metadata so the manifest remains
        # consistent across imports.
        skip_meta = {
            "gate_dbfs": tr_opts.get("drum_silence_gate_dbfs") or DEFAULT_DRUM_SILENCE_GATE_DBFS,
            "window_ms": tr_opts.get("drum_silence_gate_window_ms") or DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS,
            "disabled": bool(tr_opts.get("drum_silence_gate_disabled")),
            "events_in": len(drum_result.events),
            "events_out": len(drum_result.events),
            "dropped": 0,
            "stem_path_used": str(drum_source) if drum_source_kind != "mix_fallback" else None,
            "stem_load_ok": False,
            "quietest_dropped_dbfs": None,
            "skip_reason": "no_separated_drum_stem" if drum_source_kind == "mix_fallback" else "no_events",
        }
        tr_opts["drum_silence_gate"] = skip_meta
        drum_result = DrumTranscriptionResult(
            events=drum_result.events,
            used_algorithm=drum_result.used_algorithm,
            attempted_algorithms=drum_result.attempted_algorithms,
            warnings=drum_result.warnings,
            meta={**drum_result.meta, "stem_silence_gate": skip_meta},
        )

    # Collect available instrument stems for per-instrument melodic transcription.
    instrument_stems: dict[str, Path] = {}
    bass_stem = stems_dir / "bass.wav"
    if bass_stem.is_file():
        instrument_stems["bass"] = bass_stem
    rhythm_guitar_stem = stems_dir / "rhythm_guitar.wav"
    if rhythm_guitar_stem.is_file():
        instrument_stems["rhythm_guitar"] = rhythm_guitar_stem
    if lead_stem.is_file():
        instrument_stems["lead_guitar"] = lead_stem
    keys_stem = stems_dir / "keys.wav"
    if keys_stem.is_file():
        instrument_stems["keys"] = keys_stem
    vocals_stem = stems_dir / "vocals.wav"
    if vocals_stem.is_file():
        instrument_stems["vocals"] = vocals_stem

    instrument_results = None
    if wholemix_active:
        instrument_results = [
            InstrumentTranscriptionResult(
                instrument=role,
                notes=notes,
                used_method="muscriptor_wholemix",
                attempted_methods=["muscriptor"],
                warnings=[],
            )
            for role, notes in wholemix_result.melodic.items()
        ]
    elif instrument_stems:
        emit(
            ProgressEvent(
                type="stage_progress",
                id="transcribe_drums",
                progress=0.92,
                message=f"transcribing {len(instrument_stems)} melodic instrument(s)",
            )
        )
        instrument_results = transcribe_all_melodic_stems(
            instrument_stems,
            requested_method=tr_opts["melodic_method"],
            logger=log,
        )

    # Legacy fallback: transcribe a single melodic track ONLY when no
    # per-instrument stems were transcribed. When instrument_results exist
    # the full-mix pass would be discarded for notes.mid yet still drive
    # events.json/metadata -- a wasted full transcription -- so we skip it
    # and build everything from instrument_results instead.
    melodic_result = None
    if not instrument_results and not wholemix_active:
        melodic_source = lead_stem if lead_stem.is_file() else dst_wav
        emit(
            ProgressEvent(type="stage_progress", id="transcribe_drums", progress=0.94, message="analyzing melodic notes")
        )
        melodic_registry = build_default_melodic_algorithm_registry()
        melodic_result = transcribe_melodic(
            melodic_source,
            requested_method=tr_opts["melodic_method"],
            algorithm_registry=melodic_registry,
            logger=log,
        )

    # Build MIDI output.
    if instrument_results or wholemix_active:
        inst_tracks = {r.instrument: r.notes for r in (instrument_results or [])}
        notes_mid = _build_notes_mid_bytes(
            bpm=bpm,
            beats=beats["beats"],
            sections=sections["sections"],
            drum_events=drum_result.events,
            instrument_tracks=inst_tracks,
        )
    else:
        notes_mid = _build_notes_mid_bytes(
            bpm=bpm,
            beats=beats["beats"],
            sections=sections["sections"],
            drum_events=drum_result.events,
            melodic_notes=melodic_result.notes,
        )
    (out / "features" / "notes.mid").write_bytes(notes_mid)
    emit(ProgressEvent(type="stage_done", id="transcribe_drums", progress=0.97, artifact="features/notes.mid"))

    fingering_tracks = (
        inst_tracks if (instrument_results or wholemix_active) else {"melodic": melodic_result.notes}
    )
    fingering_paths = _write_fingering_sidecars(out, fingering_tracks)
    if fingering_paths:
        manifest["assets"].setdefault("features", {})["fingering_paths"] = fingering_paths
    if instrument_results:
        vocal_pitch_path = _write_vocal_pitch_sidecar(out, instrument_results)
        if vocal_pitch_path is not None:
            manifest["assets"].setdefault("features", {})["vocal_pitch_path"] = vocal_pitch_path
        vocal_pitch_contour_path = _write_vocal_pitch_contour_sidecar(out, instrument_results)
        if vocal_pitch_contour_path is not None:
            manifest["assets"].setdefault("features", {})[
                "vocal_pitch_contour_path"
            ] = vocal_pitch_contour_path

    # Pitch-aligned CQT spectrogram artifacts (one per melodic stem) for the
    # interactive guided-edit overlay. Best-effort: never fail import over it.
    emit(ProgressEvent(type="stage_start", id="spectrogram", progress=0.97))
    spectro_roles: list[str] = []
    try:
        from aural_ingest.spectrogram import write_spectrogram_artifact

        spec_sources: dict[str, Path] = dict(instrument_stems)
        if not spec_sources:
            fallback = lead_stem if lead_stem.is_file() else dst_wav
            if Path(fallback).is_file():
                spec_sources = {"melodic": fallback}
        # Drums get an overlay too (8-octave, see spectrogram.n_octaves_for_role)
        # so the Studio drum-cleanup editor works out of the box on new imports.
        drums_spec_stem = stems_dir / "drums.wav"
        if drums_spec_stem.is_file():
            spec_sources.setdefault("drums", drums_spec_stem)
        for role, stem_path in spec_sources.items():
            if not Path(stem_path).is_file():
                continue
            try:
                write_spectrogram_artifact(
                    stem_path, out / "features" / "spectrogram" / role, role=role
                )
                spectro_roles.append(role)
            except Exception as e:  # noqa: BLE001
                log(f"spectrogram failed for {role}: {e}")
    except Exception as e:  # noqa: BLE001
        log(f"spectrogram stage skipped: {e}")
    emit(
        ProgressEvent(
            type="stage_done",
            id="spectrogram",
            progress=0.97,
            message=f"spectrograms: {', '.join(spectro_roles) or 'none'}",
            artifact="features/spectrogram/" if spectro_roles else None,
        )
    )

    # Write events.json with per-instrument tracks.
    events_json = _events_json_from_drum_result(
        drum_result,
        melodic_result,
        requested_filter=tr_opts["drum_filter_requested"],
        melodic_method=tr_opts["melodic_method"],
        instrument_results=instrument_results,
    )
    _write_json(out / "features" / "events.json", events_json)

    # Persist effective transcription metadata.
    tr_opts["drum_source_kind"] = drum_source_kind
    tr_opts["drum_source_path"] = drum_source_path
    if tr_opts.get("drum_source_sha256") is None and drum_source_kind != "mix_fallback":
        tr_opts["drum_source_sha256"] = _sha256_file(drum_source)
    tr_opts["drum_engine_backend"] = drum_result.meta.get("backend", "heuristic")
    tr_opts["drum_filter_used"] = drum_result.used_algorithm or tr_opts["drum_filter"]
    tr_opts["drum_attempted_algorithms"] = drum_result.attempted_algorithms
    if drum_result.meta:
        tr_opts["drum_engine_meta"] = drum_result.meta
    tr_opts["drum_score"] = drum_result.meta.get("used_score")
    tr_opts["drum_attempt_scores"] = drum_result.meta.get("attempt_scores", {})

    all_warnings = [*tr_opts.get("warnings", []), *drum_result.warnings]
    if instrument_results:
        for ir in instrument_results:
            all_warnings.extend(ir.warnings)
        tr_opts["instrument_stems_transcribed"] = [ir.instrument for ir in instrument_results]
        tr_opts["instrument_melodic_methods_used"] = {
            ir.instrument: ir.used_method for ir in instrument_results
        }
        tr_opts["instrument_melodic_attempted_methods"] = {
            ir.instrument: ir.attempted_methods for ir in instrument_results
        }
        tr_opts["instrument_melodic_scores"] = {
            ir.instrument: ir.used_score for ir in instrument_results
        }
        tr_opts["instrument_melodic_attempt_scores"] = {
            ir.instrument: ir.attempt_scores for ir in instrument_results
        }
        # Derive a representative top-level melodic summary from the
        # per-instrument results (the legacy single-track pass is skipped
        # when instrument stems exist).
        primary = instrument_results[0]
        tr_opts["melodic_method_used"] = primary.used_method or tr_opts["melodic_method"]
        tr_opts["melodic_attempted_methods"] = primary.attempted_methods
        tr_opts["melodic_score"] = primary.used_score
        tr_opts["melodic_attempt_scores"] = primary.attempt_scores
    else:
        all_warnings.extend(melodic_result.warnings)
        tr_opts["melodic_method_used"] = melodic_result.used_method or tr_opts["melodic_method"]
        tr_opts["melodic_attempted_methods"] = melodic_result.attempted_methods
        tr_opts["melodic_score"] = melodic_result.used_score
        tr_opts["melodic_attempt_scores"] = melodic_result.attempt_scores
    tr_opts["warnings"] = list(dict.fromkeys(all_warnings))

    manifest["pipeline"]["transcription"] = tr_opts
    manifest["recognition"] = {
        "summary": {
            "drums": {
                "requested_engine": tr_opts.get("drum_filter_requested"),
                "used_engine": tr_opts.get("drum_filter_used"),
            },
            "melodic": {
                "requested_engine": tr_opts.get("melodic_method"),
                "used_engine": tr_opts.get("melodic_method_used"),
            },
            "profile": tr_opts.get("transcription_profile"),
        },
        "profile": tr_opts.get("transcription_profile"),
        "drums": {
            "requested_engine": tr_opts.get("drum_filter_requested"),
            "normalized_engine": tr_opts.get("drum_filter"),
            "used_engine": tr_opts.get("drum_filter_used"),
            "source_kind": tr_opts.get("drum_source_kind"),
            "source_path": tr_opts.get("drum_source_path"),
            "attempted_engines": tr_opts.get("drum_attempted_algorithms", []),
            "score": tr_opts.get("drum_score"),
            "attempt_scores": tr_opts.get("drum_attempt_scores", {}),
            "warnings": [*drum_result.warnings],
        },
        "melodic": {
            "requested_engine": tr_opts.get("melodic_method"),
            "used_engine": tr_opts.get("melodic_method_used"),
            "attempted_engines": tr_opts.get("melodic_attempted_methods", []),
            "score": tr_opts.get("melodic_score"),
            "attempt_scores": tr_opts.get("melodic_attempt_scores", {}),
            "warnings": [*(melodic_result.warnings if melodic_result is not None else [])],
            "instrument_stems": tr_opts.get("instrument_stems_transcribed", []),
            "instrument_engines": tr_opts.get("instrument_melodic_methods_used", {}),
            "instrument_attempted_engines": tr_opts.get("instrument_melodic_attempted_methods", {}),
            "instrument_scores": tr_opts.get("instrument_melodic_scores", {}),
        },
    }
    _write_json(out / "manifest.json", manifest)

    # Stage 7: midi_finalize
    emit(ProgressEvent(type="stage_start", id="midi_finalize", progress=0.97))
    emit(ProgressEvent(type="stage_done", id="midi_finalize", progress=1.0, artifact="features/notes.mid"))

    # Optional override (generally not needed once decode stage computes duration)
    if args.duration_sec is not None:
        manifest["duration_sec"] = float(args.duration_sec)
        _write_json(out / "manifest.json", manifest)

    # Stage 6a: convert the finished .auralsong working layout in place to a
    # .feedpak so the durable artifact at --out is the native pack format.
    if IMPORT_EMIT_FEEDPAK:
        try:
            feedpak_path = _convert_auralsong_to_feedpak(out)
        except Exception as exc:  # noqa: BLE001 -- surface but don't crash import
            log(f"feedpak conversion failed: {exc}")
            emit(
                ProgressEvent(
                    type="stage_done",
                    id="write_feedpak",
                    progress=1.0,
                    message="failed",
                    artifact=None,
                )
            )
            return 5
        emit(
            ProgressEvent(
                type="stage_done",
                id="write_feedpak",
                progress=1.0,
                message=str(feedpak_path),
                artifact="manifest.yaml",
            )
        )

    return 0


def cmd_import_dir(args: argparse.Namespace) -> int:
    src_dir = Path(args.input_dir_path)
    out = Path(args.out)
    if not src_dir.exists() or not src_dir.is_dir():
        log(f"input directory does not exist: {src_dir}")
        return 2

    config = _parse_config_arg(args.config)
    temp_mix_ctx: tempfile.TemporaryDirectory[str] | None = None
    src_audio = _find_preferred_mix_audio_in_dir(src_dir)
    if src_audio is None:
        try:
            input_stem_paths = _resolved_input_stem_paths(config)
            if input_stem_paths:
                temp_mix_ctx = tempfile.TemporaryDirectory(prefix="auralprimer_input_stems_mix_")
                temp_mix_path = Path(temp_mix_ctx.name) / "mix.wav"
                synth = _synthesize_mix_wav_from_input_stems(temp_mix_path, config)
                if synth is not None:
                    src_audio = temp_mix_path
                    config = {**config, "_synthesized_mix_from_input_stems": True}
        except Exception as exc:
            if temp_mix_ctx is not None:
                temp_mix_ctx.cleanup()
                temp_mix_ctx = None
            log(f"failed to synthesize mix from configured input stems: {exc}")
            return 2

    if src_audio is None:
        src_audio = _find_audio_source_in_dir(src_dir)
    if src_audio is None:
        log(f"no supported audio files found in directory: {src_dir}")
        return 2

    try:
        # Reuse the main import pipeline by forwarding selected source.
        import_args = argparse.Namespace(
            input_audio_path=str(src_audio),
            out=str(out),
            profile=args.profile,
            config=json.dumps(config) if config else args.config,
            title=args.title,
            artist=args.artist,
            duration_sec=args.duration_sec,
            drum_filter=getattr(args, "drum_filter", "auto"),
            drum_stem_path=getattr(args, "drum_stem_path", None),
            drum_silence_gate_dbfs=getattr(args, "drum_silence_gate_dbfs", None),
            drum_silence_gate_window_ms=getattr(args, "drum_silence_gate_window_ms", None),
            drum_silence_gate_disabled=bool(getattr(args, "drum_silence_gate_disabled", False)),
            melodic_method=getattr(args, "melodic_method", DEFAULT_MELODIC_METHOD),
            transcription_profile=getattr(args, "transcription_profile", DEFAULT_TRANSCRIPTION_PROFILE),
            beat_analysis_mode=getattr(args, "beat_analysis_mode", DEFAULT_BEAT_ANALYSIS_MODE),
            stem_separation_provider=getattr(args, "stem_separation_provider", DEFAULT_STEM_SEPARATION_PROVIDER),
            stem_separation_provider_path=getattr(args, "stem_separation_provider_path", None),
            stem_separation_modelpack_id=getattr(args, "stem_separation_modelpack_id", None),
            demucs_modelpack_zip_path=getattr(args, "demucs_modelpack_zip_path", None),
            shifts=getattr(args, "shifts", 1),
            multi_filter=bool(getattr(args, "multi_filter", False)),
        )
        return cmd_import(import_args)
    finally:
        if temp_mix_ctx is not None:
            temp_mix_ctx.cleanup()


def cmd_benchmark_transcribers(args: argparse.Namespace) -> int:
    """Run a transcriber roster on one stem -> features/benchmark/<role>/.

    Per-engine note sets + a manifest for the Studio's side-by-side spectrogram
    comparison view. Scores each engine against features/ground_truth.<role>.mid
    when present. Prints a JSON status line.
    """
    from aural_ingest.benchmark_overlay import run_benchmark_overlay

    auralsong = Path(args.auralsong_dir)
    if not auralsong.is_dir():
        print(json.dumps({"ok": False, "error": f"auralsong not a directory: {auralsong}"}, sort_keys=True))
        return 1
    engines = None
    if args.engines:
        engines = [e.strip() for e in str(args.engines).split(",") if e.strip()]
    result = run_benchmark_overlay(
        auralsong,
        args.instrument,
        engine_ids=engines,
        onset_tolerance_sec=args.onset_tolerance,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


def cmd_refine_candidates(args: argparse.Namespace) -> int:
    """Pre-compute per-region candidate transcriptions for the Refine workspace.

    Reads an existing AuralSong, runs the 4 candidate transcription variants
    per requested instrument, and writes
    ``features/refine_candidates.<instrument>.json`` for each. The Studio
    Refine workspace consumes those files at edit time; the runtime game
    never reads them.

    Prints a JSON status line per CLI convention so callers (Studio,
    benchmark harnesses) can parse the result without re-grepping stdout.
    """
    from aural_ingest.refine_precompute import (
        pack_feature_dirname,
        precompute_refine_candidates,
    )

    auralsong = Path(args.auralsong_dir)
    if not auralsong.is_dir():
        print(
            json.dumps(
                {"ok": False, "error": f"auralsong not a directory: {auralsong}"},
                sort_keys=True,
            )
        )
        return 1

    feat_dir = pack_feature_dirname(auralsong)
    instruments: list[str] = list(args.instrument or ["keys"])
    results: dict[str, dict[str, object]] = {}
    for inst in instruments:
        try:
            payload = precompute_refine_candidates(
                auralsong_root=auralsong, instrument=inst
            )
            results[inst] = {
                "ok": True,
                "regions": len(payload["regions"]),
                "candidates": list(payload["candidates"].keys()),
                "song_duration_sec": payload["song_duration_sec"],
                "pipeline_signature": payload["pipeline_signature"],
                "out_path": str(
                    auralsong / feat_dir / f"refine_candidates.{inst}.json"
                ),
            }
        except Exception as exc:
            results[inst] = {"ok": False, "error": str(exc)}

    overall_ok = all(bool(r.get("ok")) for r in results.values())
    print(json.dumps({"ok": overall_ok, "instruments": results}, sort_keys=True))
    return 0 if overall_ok else 1


def cmd_build_spectrogram(args: argparse.Namespace) -> int:
    """Build the CQT spectrogram overlay artifact(s) for an existing AuralSong.

    Mirrors the spectrogram stage of import, but on-demand: for each requested
    melodic stem at ``audio/stems/<role>.wav`` (drums excluded by default),
    compute
    the pitch-aligned CQT and write ``features/spectrogram/<role>/``. The Studio
    Refine (cleanup) workspace consumes those tiles + spectrogram.json.

    Prints a JSON status line per CLI convention so callers can parse the result.
    """
    from aural_ingest.spectrogram import write_spectrogram_artifact
    from aural_ingest.pack_paths import pack_feature_dirname, resolve_stem_paths

    auralsong = Path(args.auralsong_dir)
    if not auralsong.is_dir():
        print(
            json.dumps(
                {"ok": False, "error": f"auralsong not a directory: {auralsong}"},
                sort_keys=True,
            )
        )
        return 1

    feat_dir = pack_feature_dirname(auralsong)
    # Manifest-driven stem resolution (feedpak/sloppak stems[] first, then a
    # glob fallback for legacy .auralsong packs). Handles sloppak stems/*.ogg.
    # Drums are excluded from the melodic default but buildable on explicit
    # request (`--instrument drums`) for the Studio drum-cleanup overlay.
    default_excluded = {"drums"}
    present: dict[str, Path] = {}
    for role, stem_path in resolve_stem_paths(auralsong).items():
        present[role] = stem_path

    requested = list(args.instrument or [])
    available = sorted(present.keys())
    mix_stem = present.get("mix")

    # role -> (audio source, built_from_mix). A pack imported from a MusicXML
    # score carries only the full mix (the notes come from the score, not from
    # separating stems), so a requested role has no dedicated stem. Build its
    # overlay from the mix instead of refusing — the spectrogram is a pitch view
    # of the audio to edit notes against, and the mix is the audio the user
    # supplied. Only when there is NO audio at all is there nothing to do.
    targets: dict[str, tuple[Path, bool]] = {}
    if requested and "melodic" not in requested:
        for role in requested:
            if role in present:
                targets[role] = (present[role], False)
            elif mix_stem is not None:
                targets[role] = (mix_stem, True)
    else:
        # Default (or explicit "melodic"): all separated melodic stems present.
        for role in present:
            if role not in default_excluded and role != "mix":
                targets[role] = (present[role], False)

    if not targets:
        reason = (
            "This pack has no audio to build a spectrogram from."
            if not available
            else (
                f"No audio available for the requested role(s) {requested or ['melodic']}. "
                f"Stems: {available}."
            )
        )
        payload_err: dict[str, object] = {
            "ok": False,
            "roles": {},
            "error": reason,
            "requested_roles": requested or ["melodic"],
            "available_stems": available,
        }
        if args.instrument:
            payload_err["instrument"] = list(args.instrument)
        print(json.dumps(payload_err, sort_keys=True))
        return 1

    results: dict[str, dict[str, object]] = {}
    for role, (stem_path, from_mix) in targets.items():
        try:
            geom = write_spectrogram_artifact(
                stem_path,
                auralsong / feat_dir / "spectrogram" / role,
                role=role,
            )
            results[role] = {
                "ok": True,
                "n_frames": int(geom.get("n_frames", 0)),
                "tiles": len(geom.get("tiles", []) or []),
                "from_mix": from_mix,
            }
        except Exception as exc:  # noqa: BLE001
            results[role] = {"ok": False, "error": str(exc)}

    overall_ok = bool(results) and any(bool(r.get("ok")) for r in results.values())
    payload: dict[str, object] = {"ok": overall_ok, "roles": results, "available_stems": available}
    if not overall_ok:
        # Every requested role errored — surface the first reason so the UI has
        # something to show beyond "ok:false".
        first_err = next(
            (str(r.get("error")) for r in results.values() if not r.get("ok") and r.get("error")),
            "spectrogram build failed for all requested roles",
        )
        payload["error"] = first_err
    if args.instrument:
        payload["instrument"] = list(args.instrument)
    print(json.dumps(payload, sort_keys=True))
    return 0 if overall_ok else 1


def cmd_prep_arrangements(args: argparse.Namespace) -> int:
    """Build aural/notes.mid + song_timeline.json from a pack's arrangement JSONs.

    Reads the manifest's ``arrangements[].file`` Rocksmith-style wire JSONs
    (sloppak/feedpak) and derives the game's melodic ``aural/notes.mid`` (one
    named Instrument per role, CONTRACT C3) plus ``song_timeline.json`` from the
    first arrangement's beats/sections (CONTRACT C4). Stamps the corresponding
    manifest keys (order-preserving). Drums are NOT charted here — the game
    charts drums from the pack-root ``drum_tab.json``.

    Existing output files are skipped unless ``--force`` (protects cleanup
    anchors). A drums-only pack gets no bogus aural_notes_mid key. Prints the
    standard trailing JSON status line.
    """
    from aural_ingest.arrangement_prep import prep_arrangements

    pack = Path(args.auralsong_dir)
    if not pack.is_dir():
        print(json.dumps({"ok": False, "error": f"pack not a directory: {pack}"}, sort_keys=True))
        return 1
    try:
        status = prep_arrangements(pack, force=bool(getattr(args, "force", False)))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"prep-arrangements failed: {exc}"}, sort_keys=True))
        return 1
    print(json.dumps(status, sort_keys=True))
    return 0 if status.get("ok") else 1


def _load_case_ids(path) -> set[str]:
    """Load a set of case ids from a JSON or plain-text allow-list.

    Accepts three shapes so the same flag consumes either a stratified
    sample manifest or a hand-written list:

    * ``{"cases": [{"case_id": "..."}, ...]}`` -- the stratified sample JSON.
    * ``["id1", "id2", ...]`` -- a bare JSON array of case ids.
    * newline-delimited text -- one case id per line (``#`` comments and
      blank lines ignored).
    """
    from pathlib import Path as _Path

    text = _Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        obj = json.loads(text)
        if isinstance(obj, dict):
            rows = obj.get("cases", [])
            return {
                str(r["case_id"]) if isinstance(r, dict) else str(r)
                for r in rows
            }
        if isinstance(obj, list):
            return {
                str(r["case_id"]) if isinstance(r, dict) else str(r)
                for r in obj
            }
        raise ValueError("unsupported case-id-file JSON shape")
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line)
    return ids


def _case_ids_from_args(args: argparse.Namespace) -> set[str] | None:
    case_id_file = getattr(args, "case_id_file", None)
    if not case_id_file:
        return None
    cif = Path(case_id_file)
    if not cif.is_file():
        raise FileNotFoundError(f"case-id-file not found: {cif}")
    case_ids = _load_case_ids(cif)
    if not case_ids:
        raise ValueError(f"no case ids in {cif}")
    return case_ids


def _filter_cases_by_id(cases: list[Any], case_ids: set[str] | None) -> list[Any]:
    if not case_ids:
        return cases
    return [case for case in cases if str(getattr(case, "case_id", "")) in case_ids]


def _case_adapter_limit(limit: int | None, case_ids: set[str] | None) -> int | None:
    return None if case_ids else limit


def _limit_cases(cases: list[Any], limit: int | None) -> list[Any]:
    if limit is None:
        return cases
    return cases[: max(0, int(limit))]


def _filter_cases_by_duration(
    cases: list[Any],
    *,
    min_duration_sec: float | None = None,
    max_duration_sec: float | None = None,
) -> list[Any]:
    if min_duration_sec is None and max_duration_sec is None:
        return cases
    if min_duration_sec is not None and min_duration_sec < 0:
        raise ValueError("min-duration-sec must be >= 0")
    if max_duration_sec is not None and max_duration_sec < 0:
        raise ValueError("max-duration-sec must be >= 0")
    if (
        min_duration_sec is not None
        and max_duration_sec is not None
        and min_duration_sec > max_duration_sec
    ):
        raise ValueError("min-duration-sec must be <= max-duration-sec")

    out: list[Any] = []
    for case in cases:
        try:
            duration = float(getattr(case, "duration_sec"))
        except (TypeError, ValueError):
            continue
        if min_duration_sec is not None and duration < min_duration_sec:
            continue
        if max_duration_sec is not None and duration > max_duration_sec:
            continue
        out.append(case)
    return out


def cmd_align_drum_onsets(args: argparse.Namespace) -> int:
    """Snap the pack's drum-tab hit times onto the drums-stem audio transients.

    Reads the manifest-declared ``drum_tab`` artifact (falling back to pack-root
    ``drum_tab.json``) plus the drums stem, refines each hit's time to the
    nearest real transient in its voice's frequency band (see
    ``drum_onset_align``), writes the same artifact back in place, and prints a
    JSON status line so the Studio button / import step can report the result.
    """
    from aural_ingest.drum_onset_align import align_drum_tab_to_onsets
    from aural_ingest.pack_paths import resolve_drum_tab_path, resolve_stem_paths

    pack = Path(args.auralsong_dir)
    tab_path = resolve_drum_tab_path(pack)
    # Manifest-driven so a sloppak's stems/drums.ogg resolves; fall back to the
    # historical audio/stems/drums.wav for a legacy pack.
    drums = resolve_stem_paths(pack).get("drums")
    if drums is None:
        legacy = pack / "audio" / "stems" / "drums.wav"
        drums = legacy if legacy.is_file() else None
    if not tab_path.is_file():
        print(json.dumps({"ok": False, "error": f"no drum tab at {tab_path}"}, sort_keys=True))
        return 1
    if drums is None or not drums.is_file():
        print(json.dumps({"ok": False, "error": f"no drums stem in {pack}"}, sort_keys=True))
        return 1
    try:
        tab = json.loads(tab_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"failed to read {tab_path}: {exc}"}, sort_keys=True))
        return 1
    aligned, stats = align_drum_tab_to_onsets(tab, drums)
    tab_path.write_text(json.dumps(aligned, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, **stats}, sort_keys=True))
    return 0


def _pack_audio_for_analysis(pack: Path) -> tuple[Path, bool]:
    """Return an audio file to analyze for a pack, plus whether it is a temp file
    to clean up. Prefers an explicit ``audio/mix.*`` file, then a
    manifest-resolved full mix (feedpak/sloppak default-flagged/full stem);
    otherwise sums the non-derived stems into a temp wav. The explicit mix
    check comes first because older feedpaks can carry stale/non-full
    ``default: true`` flags on an instrument stem, and meter analysis needs the
    full song mix when it is present.

    Stems are loaded at a common sample rate + mono via librosa, so mixed-rate
    stems can never produce a time-warped sum. The temp file is cleaned up on a
    write failure here (the caller also unlinks it in its finally block).
    """
    from aural_ingest.pack_paths import resolve_mix_path, resolve_stem_paths

    for ext in ("wav", "ogg", "mp3", "flac"):
        explicit_mix = pack / "audio" / f"mix.{ext}"
        if explicit_mix.is_file():
            return explicit_mix.resolve(), False

    mix = resolve_mix_path(pack)
    if mix is not None and mix.is_file():
        return mix, False
    import numpy as np
    import soundfile as sf
    import librosa

    # Sum the SOURCE stems (exclude any full/mix stem so we don't double-count).
    stems = resolve_stem_paths(pack)
    target_sr = 44100
    summed = None
    for role in sorted(stems):
        if role in {"full", "mix"}:
            continue
        p = stems[role]
        if not p.is_file():
            continue
        # sr=target_sr forces a single rate; mono=True collapses channels.
        y, _ = librosa.load(str(p), sr=target_sr, mono=True)
        y = np.asarray(y, dtype="float64")
        if summed is None:
            summed = y
        else:
            n = min(len(summed), len(y))
            summed = summed[:n] + y[:n]
    if summed is None:
        raise FileNotFoundError(f"no full mix or source stems resolvable in {pack}")
    peak = float(np.max(np.abs(summed))) + 1e-9
    summed = (summed / peak).astype("float32")
    fd, tmp_name = tempfile.mkstemp(prefix="auralmeter_", suffix=".wav")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        sf.write(str(tmp), summed, target_sr)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, True


def cmd_refresh_meter(args: argparse.Namespace) -> int:
    """Re-run beat/downbeat/meter tracking on an EXISTING pack and rewrite only
    ``song_timeline.json`` (beats + time_signatures + tempos), in place.

    Uses the neural meter engine (Beat This!, modelpack-gated); if unavailable,
    reports ``ok:false`` and leaves the pack untouched rather than silently
    falling back to the old hardcoded 4/4. Never rewrites notes.mid (the game
    derives note seconds from its tempo events), drum_tab.json (absolute time),
    or arrangements. ``sections`` in the timeline are preserved.
    """
    from aural_ingest import meter_tracker
    from aural_ingest.feedpak_writer import _build_song_timeline

    pack = Path(args.auralsong_dir)
    tl_path = pack / "song_timeline.json"
    if not tl_path.is_file():
        print(json.dumps({"ok": False, "error": f"no song_timeline.json in {pack}"}, sort_keys=True))
        return 1
    try:
        existing = json.loads(tl_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"failed to read song_timeline.json: {exc}"}, sort_keys=True))
        return 1

    tmp_audio: Path | None = None
    backup_name: str | None = None
    try:
        audio, is_tmp = _pack_audio_for_analysis(pack)
        tmp_audio = audio if is_tmp else None
        # Duration from the audio (soundfile info is cheap).
        import soundfile as sf

        info = sf.info(str(audio))
        duration_sec = float(info.frames) / float(info.samplerate) if info.samplerate else 0.0

        result = meter_tracker.track_meter(audio, duration_sec=duration_sec, config={})
        if result is None:
            print(json.dumps(
                {"ok": False, "error": "meter model unavailable (no Beat This! checkpoint) — pack left unchanged"},
                sort_keys=True,
            ))
            return 1
        _bpm, beats, tempo_map, meta = result

        new_tl = _build_song_timeline(tempo_map, beats, None)
        # Preserve anything the model doesn't produce (sections, duration, etc.).
        for k, v in existing.items():
            if k not in new_tl:
                new_tl[k] = v
        # Back up the previous timeline so a bad meter call is recoverable — the
        # new grid moves beats/downbeats, which desyncs anything anchored to the
        # OLD grid (quantized cleanup edits, drum_tab snapping, notation measures).
        backup = tl_path.parent / (tl_path.name + ".bak")
        backup.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        backup_name = backup.name
        # Atomic overwrite: write the new timeline to a sibling temp file, then
        # os.replace() it over song_timeline.json. A truncating write_text() could
        # leave the primary half-written on a disk-full / I/O error / kill — and
        # this file is the artifact the editor grid + game timeline depend on.
        # os.replace is atomic within one volume, so the primary is always either
        # the old file or the fully-written new one, never a partial. The backup
        # (written just above) is the belt-and-suspenders second copy.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(tl_path.parent), prefix=tl_path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(new_tl, indent=2))
            os.replace(tmp_name, tl_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        # Ensure the manifest points at the timeline we just wrote. A sloppak
        # (or any manifest pack) whose manifest lacks a song_timeline key gets
        # it stamped now, order-preserving + unknown-key-safe. Best-effort:
        # the timeline itself is already written, so a manifest hiccup here must
        # not fail the command.
        try:
            from aural_ingest.pack_paths import load_pack_manifest, update_manifest_keys

            _mf = load_pack_manifest(pack)
            if _mf is not None and not _mf.get("song_timeline"):
                update_manifest_keys(pack, {"song_timeline": "song_timeline.json"})
        except Exception:  # noqa: BLE001 — manifest stamp is non-fatal
            pass
        print(json.dumps(
            {
                "ok": True,
                "beat_source": meta.get("beat_source"),
                "postprocessor": meta.get("postprocessor"),
                "bpm": meta.get("estimated_bpm"),
                "time_signature": meta.get("time_signature"),
                "beats_per_bar": meta.get("beats_per_bar"),
                "beats": meta.get("detected_beat_count"),
                "downbeats": meta.get("detected_downbeat_count"),
                "meter_denominator_provisional": meta.get("meter_denominator_provisional", False),
                "backup": backup.name,
                "warning": (
                    "grid changed; edits anchored to the old grid (quantized "
                    "placements, drum-onset snaps, notation measures) may need "
                    "re-alignment. Previous timeline saved to " + backup.name
                ),
            },
            sort_keys=True,
        ))
        return 0
    except Exception as exc:  # noqa: BLE001
        err = {"ok": False, "error": f"refresh-meter failed: {exc}"}
        if backup_name is not None:
            # The backup was written and the rewrite is atomic, so the original
            # timeline is intact (either still at song_timeline.json, or in the
            # backup). Tell the user so recovery isn't a guess.
            err["backup"] = backup_name
            err["recovery"] = (
                f"song_timeline.json is unchanged or restorable from {backup_name}"
            )
        print(json.dumps(err, sort_keys=True))
        return 1
    finally:
        if tmp_audio is not None:
            try:
                tmp_audio.unlink()
            except Exception:
                pass


def cmd_gt_benchmark(args: argparse.Namespace) -> int:
    """Run a ground-truth benchmark sweep and write the JSON report.

    Resolves the dataset adapter + algorithm family, iterates cases,
    scores each prediction, and aggregates per-bucket summaries.

    Per-case progress goes to stderr (optional ``--progress`` flag) so
    stdout stays a single JSON object suitable for piping.
    """
    import sys as _sys
    from pathlib import Path as _Path

    from aural_ingest.ground_truth_benchmark import run_sweep, write_report

    dataset = args.dataset
    corpus_root = _Path(args.corpus_root)
    if not corpus_root.is_dir():
        print(
            json.dumps(
                {"ok": False, "error": f"corpus root not a directory: {corpus_root}"},
                sort_keys=True,
            )
        )
        return 1

    try:
        requested_case_ids = _case_ids_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    adapter_limit = _case_adapter_limit(args.limit, requested_case_ids)

    if dataset == "egmd":
        from aural_ingest.dataset_adapters.egmd import yield_cases

        style_filter = getattr(args, "style_filter", None) or None
        cases = list(
            yield_cases(
                corpus_root,
                split=args.split,
                style_filter=style_filter,
                case_ids=requested_case_ids,
                limit=adapter_limit,
            )
        )
        family = "drums"
    elif dataset == "guitarset":
        from aural_ingest.dataset_adapters.guitarset import yield_cases as _yc

        cases = list(
            _yc(corpus_root, variant=args.variant or "mic", limit=adapter_limit)
        )
        cases = _filter_cases_by_id(cases, requested_case_ids)
        family = "melodic"
    elif dataset == "guitarset_bass":
        from aural_ingest.dataset_adapters.guitarset import yield_low_string_cases

        cases = list(
            yield_low_string_cases(
                corpus_root,
                variant=args.variant or "hex_debleeded",
                limit=adapter_limit,
            )
        )
        cases = _filter_cases_by_id(cases, requested_case_ids)
        family = "melodic"
    elif dataset == "guitar_techs":
        from aural_ingest.dataset_adapters.guitar_techs import yield_cases as _yc

        cases = list(
            _yc(corpus_root, signal=args.variant or "directinput", limit=adapter_limit)
        )
        cases = _filter_cases_by_id(cases, requested_case_ids)
        family = "melodic"
    elif dataset == "piano_synthetic":
        from aural_ingest.dataset_adapters.piano_synthetic import yield_cases as _yc

        cases = list(_yc(corpus_root, limit=adapter_limit))
        cases = _filter_cases_by_id(cases, requested_case_ids)
        family = "melodic"
    elif dataset == "maestro":
        from aural_ingest.dataset_adapters.maestro import yield_cases as _yc

        cases = list(_yc(corpus_root, split=args.split, limit=adapter_limit))
        cases = _filter_cases_by_id(cases, requested_case_ids)
        family = "melodic"
    elif dataset == "mir_st500":
        from aural_ingest.dataset_adapters.mir_st500 import yield_cases as _yc

        cases = list(
            _yc(
                corpus_root,
                split=args.split,
                variant=args.variant or "vocal",
                case_ids=requested_case_ids,
                limit=adapter_limit,
            )
        )
        family = "melodic"
    else:
        print(json.dumps({"ok": False, "error": f"unknown dataset: {dataset}"}))
        return 1

    try:
        cases = _filter_cases_by_duration(
            cases,
            min_duration_sec=getattr(args, "min_duration_sec", None),
            max_duration_sec=getattr(args, "max_duration_sec", None),
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if requested_case_ids is not None:
        cases = _limit_cases(cases, args.limit)

    if not cases:
        print(json.dumps({"ok": False, "error": "no cases yielded"}))
        return 1

    progress = bool(args.progress)

    def on_case(idx: int, score) -> None:
        if progress:
            print(
                f"[{idx:>4}/{len(cases)}] {score.algorithm_id}  "
                f"{score.case_id}  f1={score.f1:.3f} tp={score.tp} fp={score.fp} fn={score.fn}",
                file=_sys.stderr,
                flush=True,
            )

    scores = run_sweep(
        cases,
        algorithms=list(args.algorithm),
        family=family,
        tolerance_sec=args.tolerance_ms / 1000.0,
        pitch_tolerance_semitones=int(args.pitch_tolerance_semitones),
        on_case=on_case,
    )

    out_path = _Path(args.output)
    report = write_report(
        scores,
        out_path=out_path,
        dataset=dataset,
        family=family,
        extra={
            "split": args.split,
            "variant": args.variant,
            "limit": args.limit,
            "min_duration_sec": getattr(args, "min_duration_sec", None),
            "max_duration_sec": getattr(args, "max_duration_sec", None),
            "tolerance_ms": args.tolerance_ms,
            "pitch_tolerance_semitones": args.pitch_tolerance_semitones,
        },
    )

    summary = report["summary"]["overall"]
    per_alg = {
        alg: {
            "cases": stats["cases"],
            "f1": stats["f1"],
            "precision": stats["precision"],
            "recall": stats["recall"],
            "onset_mae_sec": stats["onset_mae_sec"],
        }
        for alg, stats in report["summary"]["per_algorithm"].items()
    }
    print(
        json.dumps(
            {
                "ok": True,
                "dataset": dataset,
                "family": family,
                "case_count": report["case_count"],
                "overall": summary,
                "per_algorithm": per_alg,
                "report_path": str(out_path),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aural_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_stages = sub.add_parser("stages")
    s_stages.set_defaults(func=cmd_stages)

    s_validate = sub.add_parser("validate")
    s_validate.add_argument("auralsong_dir")
    s_validate.set_defaults(func=cmd_validate)

    s_info = sub.add_parser("info")
    s_info.add_argument("auralsong_dir")
    s_info.set_defaults(func=cmd_info)

    s_audit_drums = sub.add_parser("audit-drums")
    s_audit_drums.add_argument("auralsong_dir")
    s_audit_drums.add_argument("--window-ms", type=float, default=DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS)
    s_audit_drums.add_argument(
        "--threshold-dbfs",
        type=float,
        action="append",
        dest="threshold_dbfs",
        help="Low-energy threshold to report; repeat to provide multiple thresholds.",
    )
    s_audit_drums.set_defaults(func=cmd_audit_drums)

    s_runtime = sub.add_parser("runtime-check")
    s_runtime.add_argument(
        "--require-model-upgrade-gates",
        action="store_true",
        help="Fail if any model-upgrade promotion gate in model_upgrade_gates is still pending.",
    )
    s_runtime.set_defaults(func=cmd_runtime_check)

    s_model_setup = sub.add_parser("model-setup")
    s_model_setup.set_defaults(func=cmd_model_setup)

    s_ms_download = sub.add_parser("muscriptor-download")
    # Empty default so AURAL_MUSCRIPTOR_SIZE / _DEFAULT_SIZE can win; an
    # argparse default would always shadow both.
    s_ms_download.add_argument("--size", default="")
    s_ms_download.add_argument("--check-only", action="store_true", dest="check_only")
    s_ms_download.set_defaults(func=cmd_muscriptor_download)

    s_benchmark = sub.add_parser("benchmark-drums")
    s_benchmark.add_argument("stem_path")
    s_benchmark.add_argument("reference_path")
    s_benchmark.add_argument("--algorithm", action="append")
    s_benchmark.add_argument("--tolerance-ms", type=float, default=60.0, dest="tolerance_ms")
    s_benchmark.add_argument("--json", action="store_true", dest="json_output")
    s_benchmark.set_defaults(func=cmd_benchmark_drums)

    s_quality = sub.add_parser("benchmark-quality")
    s_quality.add_argument("--manifest")
    s_quality.add_argument("--scan-root")
    s_quality.add_argument("--write-manifest")
    s_quality.add_argument("--manifest-json", action="store_true", dest="manifest_json")
    s_quality.add_argument("--referenced-only", action="store_true", dest="referenced_only")
    s_quality.add_argument("--case-filter", action="append", dest="case_filter")
    s_quality.add_argument("--role", action="append")
    s_quality.add_argument("--max-cases", type=int, dest="max_cases")
    s_quality.add_argument("--algorithm", action="append")
    s_quality.add_argument("--transcription-profile", default=DEFAULT_TRANSCRIPTION_PROFILE)
    s_quality.add_argument("--tolerance-ms", type=float, default=60.0, dest="tolerance_ms")
    s_quality.add_argument("--label", default="full-corpus-quality")
    s_quality.add_argument("--out-root", default="benchmarks/quality/runs")
    s_quality.set_defaults(func=cmd_benchmark_quality)

    s_refine_piano = sub.add_parser("refine-piano")
    s_refine_piano.add_argument("--audio", required=True)
    s_refine_piano.add_argument("--source-midi", required=True)
    s_refine_piano.add_argument("--reference-midi")
    s_refine_piano.add_argument("--method", action="append")
    s_refine_piano.add_argument("--tolerance-ms", type=float, default=60.0, dest="tolerance_ms")
    s_refine_piano.add_argument("--offset-tolerance-ms", type=float, default=120.0, dest="offset_tolerance_ms")
    s_refine_piano.add_argument("--velocity-tolerance", type=int, default=20, dest="velocity_tolerance")
    s_refine_piano.add_argument("--source-offset-sec", type=float, default=0.0, dest="source_offset_sec")
    s_refine_piano.add_argument("--reference-offset-sec", type=float, default=0.0, dest="reference_offset_sec")
    s_refine_piano.add_argument("--bpm", type=float, default=120.0)
    s_refine_piano.add_argument("--label", default="piano-refinement")
    s_refine_piano.add_argument("--out-root", default="benchmarks/piano/refinement_runs")
    s_refine_piano.set_defaults(func=cmd_refine_piano)

    # Voice search for the MR client (protocol §6). Imported lazily inside the
    # handler, not here: the module pulls in transformers and torch, and every
    # other command would pay that import cost for a feature it never uses.
    s_voice = sub.add_parser(
        "transcribe-query",
        help="Transcribe a short spoken search query (mono 16 kHz WAV) to JSON.",
    )
    s_voice.add_argument("wav", help="Path to the recorded query.")
    s_voice.add_argument(
        "--model",
        default="",
        help="Whisper model id; defaults to the smallest English one.",
    )
    s_voice.set_defaults(func=cmd_transcribe_query_lazy)

    s_import_xml = sub.add_parser("import-musicxml")
    s_import_xml.add_argument("input_musicxml_path")
    s_import_xml.add_argument("--out", required=True, help="output directory for <stem>.feedpak")
    s_import_xml.add_argument("--audio", default="", help="audio to attach (defaults to a render beside the score)")
    s_import_xml.add_argument("--title", default="")
    s_import_xml.add_argument("--artist", default="")
    s_import_xml.add_argument("--genre", default="", help="Free-text genre, for library filtering.")
    s_import_xml.set_defaults(func=cmd_import_musicxml)

    s_import = sub.add_parser("import")
    s_import.add_argument("input_audio_path")
    s_import.add_argument("--out", required=True)
    s_import.add_argument("--profile", default="full")
    s_import.add_argument("--config")
    s_import.add_argument("--title")
    s_import.add_argument("--artist")
    s_import.add_argument("--genre", help="Free-text genre, for library filtering.")
    s_import.add_argument("--duration-sec", type=float, dest="duration_sec")
    _add_transcription_options(s_import)
    s_import.set_defaults(func=cmd_import)

    s_import_dir = sub.add_parser("import-dir")
    s_import_dir.add_argument("input_dir_path")
    s_import_dir.add_argument("--out", required=True)
    s_import_dir.add_argument("--profile", default="full")
    s_import_dir.add_argument("--config")
    s_import_dir.add_argument("--title")
    s_import_dir.add_argument("--artist")
    s_import_dir.add_argument("--genre", help="Free-text genre, for library filtering.")
    s_import_dir.add_argument("--duration-sec", type=float, dest="duration_sec")
    _add_transcription_options(s_import_dir)
    s_import_dir.set_defaults(func=cmd_import_dir)

    s_refine_candidates = sub.add_parser(
        "refine-candidates",
        help="Pre-compute per-region candidate transcriptions for the Studio Refine workspace.",
    )
    s_refine_candidates.add_argument(
        "auralsong_dir",
        help="Path to an existing AuralSong root (the directory containing manifest.json + audio/).",
    )
    s_refine_candidates.add_argument(
        "--instrument",
        action="append",
        choices=sorted(["keys", "bass", "guitar", "lead_guitar", "rhythm_guitar", "drums", "vocals", "melodic"]),
        help="Instrument to precompute. May be repeated. Defaults to 'keys' if omitted.",
    )
    s_refine_candidates.set_defaults(func=cmd_refine_candidates)

    s_spectrogram = sub.add_parser(
        "spectrogram",
        help="Build the CQT spectrogram overlay artifact(s) for an existing AuralSong's melodic stems.",
    )
    s_spectrogram.add_argument(
        "auralsong_dir",
        help="Path to an existing AuralSong root (the directory containing manifest.json + audio/).",
    )
    s_spectrogram.add_argument(
        "--instrument",
        action="append",
        choices=sorted(
            [
                "keys",
                "bass",
                "guitar",
                "lead_guitar",
                "rhythm_guitar",
                "drums",
                "melodic",
                "vocals",
            ]
        ),
        help=(
            "Stem to build. May be repeated. Defaults to all melodic stems present; "
            "'drums' (8-octave overlay for the drum-cleanup editor) is built only on explicit request."
        ),
    )
    s_spectrogram.set_defaults(func=cmd_build_spectrogram)

    s_prep_arr = sub.add_parser(
        "prep-arrangements",
        help=(
            "Build aural/notes.mid + song_timeline.json from a pack's "
            "(sloppak/feedpak) arrangement wire JSONs, in place."
        ),
    )
    s_prep_arr.add_argument(
        "auralsong_dir",
        help="Path to an existing pack root (the directory containing manifest.yaml + arrangements/).",
    )
    s_prep_arr.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing aural/notes.mid + song_timeline.json (default: skip existing to protect cleanup anchors).",
    )
    s_prep_arr.set_defaults(func=cmd_prep_arrangements)

    s_align_drums = sub.add_parser(
        "align-drum-onsets",
        help="Snap a pack's drum_tab hit times onto the drums-stem audio transients (in place).",
    )
    s_align_drums.add_argument(
        "auralsong_dir",
        help="Path to an existing pack root (drum_tab.json + a manifest-listed or audio/stems/ drums stem, wav or ogg).",
    )
    s_align_drums.set_defaults(func=cmd_align_drum_onsets)

    s_refresh_meter = sub.add_parser(
        "refresh-meter",
        help="Re-run neural beat/downbeat/meter tracking on an existing pack and rewrite song_timeline.json (in place).",
    )
    s_refresh_meter.add_argument(
        "auralsong_dir",
        help="Path to an existing pack root (song_timeline.json + a manifest-listed full mix or source stems, wav or ogg).",
    )
    s_refresh_meter.set_defaults(func=cmd_refresh_meter)

    s_bench_overlay = sub.add_parser(
        "benchmark-transcribers",
        help="Run a transcriber roster on one stem -> features/benchmark/<role>/ for the Studio comparison view.",
    )
    s_bench_overlay.add_argument(
        "auralsong_dir",
        help="Path to an existing AuralSong root (the directory containing manifest.json + audio/).",
    )
    s_bench_overlay.add_argument(
        "--instrument",
        default="keys",
        choices=sorted(["keys", "bass", "guitar", "lead_guitar", "rhythm_guitar", "vocals", "melodic"]),
        help="Instrument stem to benchmark. Defaults to 'keys'.",
    )
    s_bench_overlay.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engine ids to run (override the per-instrument default roster).",
    )
    s_bench_overlay.add_argument(
        "--onset-tolerance",
        type=float,
        default=0.05,
        help="Onset match tolerance (seconds) for ground-truth scoring. Default 0.05.",
    )
    s_bench_overlay.set_defaults(func=cmd_benchmark_transcribers)

    s_gt_benchmark = sub.add_parser(
        "gt-benchmark",
        help=(
            "Sweep transcription algorithms across an annotated ground-truth "
            "dataset (E-GMD / GuitarSet / Guitar-TECHS / MIR-ST500) and emit a JSON report."
        ),
    )
    s_gt_benchmark.add_argument(
        "--dataset",
        required=True,
        choices=sorted([
            "egmd",
            "guitarset",
            "guitarset_bass",
            "guitar_techs",
            "piano_synthetic",
            "maestro",
            "mir_st500",
        ]),
        help=(
            "Annotated corpus to sweep. ``guitarset_bass`` is the low-string"
            " filter of GuitarSet for bass-pitch benchmarks."
        ),
    )
    s_gt_benchmark.add_argument(
        "--corpus-root",
        required=True,
        help="Filesystem root containing the dataset's extracted subtrees.",
    )
    s_gt_benchmark.add_argument(
        "--algorithm",
        action="append",
        required=True,
        help=(
            "Algorithm id to evaluate (e.g. ``combined_filter``, "
            "``melodic_basic_pitch``). Repeat to sweep multiple."
        ),
    )
    s_gt_benchmark.add_argument(
        "--split",
        default="test",
        help=(
            "Dataset split when applicable (E-GMD: train/test/validation). "
            "Ignored by datasets without a published split."
        ),
    )
    s_gt_benchmark.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of cases (useful for smoke runs).",
    )
    s_gt_benchmark.add_argument(
        "--min-duration-sec",
        type=float,
        default=None,
        help="Keep only cases whose annotated duration is at least this many seconds.",
    )
    s_gt_benchmark.add_argument(
        "--max-duration-sec",
        type=float,
        default=None,
        help="Keep only cases whose annotated duration is at most this many seconds.",
    )
    s_gt_benchmark.add_argument(
        "--case-id-file",
        default=None,
        help=(
            "Path to a stratified-sample JSON (``{cases:[{case_id}]}``) "
            "or newline-delimited case-id list. Only listed cases are swept, so "
            "``--limit`` no longer collapses onto the first-N (single-groove) rows."
        ),
    )
    s_gt_benchmark.add_argument(
        "--style-filter",
        action="append",
        default=None,
        help="E-GMD only: restrict to these ``style`` values. Repeat to allow several.",
    )
    s_gt_benchmark.add_argument(
        "--tolerance-ms",
        type=float,
        default=50.0,
        help="Onset tolerance for greedy pairing (default 50ms).",
    )
    s_gt_benchmark.add_argument(
        "--pitch-tolerance-semitones",
        type=int,
        default=0,
        help="Melodic pitch tolerance (default 0 = pitch-exact).",
    )
    s_gt_benchmark.add_argument(
        "--variant",
        default=None,
        help=(
            "Dataset-specific variant: GuitarSet (mic/pickup_mix/hex_*); "
            "Guitar-TECHS (directinput/micamp); MIR-ST500 (vocal/mixture)."
        ),
    )
    s_gt_benchmark.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON report.",
    )
    s_gt_benchmark.add_argument(
        "--progress",
        action="store_true",
        help="Print a per-case progress line on stderr.",
    )
    s_gt_benchmark.set_defaults(func=cmd_gt_benchmark)

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
