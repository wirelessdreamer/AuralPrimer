from __future__ import annotations

import contextlib
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Callable, Iterable

from aural_ingest.mt3_compat import ensure_mt3_transformers_compat, suppress_mt3_runtime_warnings

KNOWN_HEURISTIC_DRUM_FILTERS: tuple[str, ...] = (
    "combined_filter",
    "dsp_bandpass_improved",
    "dsp_spectral_flux",
    "aural_onset",
    "adaptive_beat_grid",
    "beat_conditioned_multiband_decoder",
    "spectral_flux_multiband",
    "dsp_bandpass",
    "librosa_superflux",
    "spectral_template_multipass",
    "spectral_template_with_grid",
    "multi_resolution",
    "template_xcorr",
    "probabilistic_pattern",
    "onset_aligned",
    "multi_resolution_template",
    "hybrid_kick_grid",
    "adaptive_beat_grid_multilabel",
)

KNOWN_MT3_DRUM_ENGINES: tuple[str, ...] = (
    "mr_mt3_drums",
    "yourmt3_drums",
)

KNOWN_DRUM_ENGINES: tuple[str, ...] = KNOWN_HEURISTIC_DRUM_FILTERS + KNOWN_MT3_DRUM_ENGINES
KNOWN_DRUM_FILTERS: tuple[str, ...] = KNOWN_DRUM_ENGINES

KNOWN_MELODIC_METHODS: tuple[str, ...] = (
    "auto", 
    "pyin", 
    "basic_pitch",
    "melodic_combined",
    "melodic_octave_fix",
    "melodic_yin_octave_hps_fix",
)

INSTRUMENT_ROLES: tuple[str, ...] = (
    "bass",
    "rhythm_guitar",
    "lead_guitar",
    "keys",
)

# Instrument-specific frequency ranges for melodic transcription.
# (min_freq_hz, max_freq_hz) — tuned to practical pitch range of each instrument.
INSTRUMENT_FREQ_RANGES: dict[str, tuple[float, float]] = {
    "bass": (30.0, 400.0),           # ~B0 (31 Hz) to ~G4 (392 Hz)
    "rhythm_guitar": (75.0, 1400.0), # ~D2 (73 Hz) to ~F6 (1397 Hz)
    "lead_guitar": (75.0, 1400.0),   # same range, different stem
    "keys": (27.0, 4200.0),          # ~A0 (27.5 Hz) to ~C8 (4186 Hz)
    "melodic": (45.0, 1700.0),       # legacy default
}

DEFAULT_DRUM_ENGINE = "adaptive_beat_grid"
DEFAULT_DRUM_FILTER = DEFAULT_DRUM_ENGINE
DEFAULT_MELODIC_METHOD = "auto"

MT3_MODELPACK_DIRNAME = "assets/models"

MT3_DRUM_ENGINE_MODEL_INFO: dict[str, dict[str, Any]] = {
    "mr_mt3_drums": {
        "engine": "mr_mt3_drums",
        "backend": "mt3",
        "model_id": "mr_mt3",
        "modelpack_id": "mr_mt3",
        "checkpoint_path": Path("files") / "checkpoints" / "mr_mt3" / "mt3.pth",
        "format": "pytorch",
        "size_mb": 176.0,
        "speed_x_realtime": 57.0,
        "description": "MR-MT3 drum transcription baseline",
    },
    "yourmt3_drums": {
        "engine": "yourmt3_drums",
        "backend": "mt3",
        "model_id": "yourmt3",
        "modelpack_id": "yourmt3",
        "checkpoint_path": Path("files")
        / "checkpoints"
        / "yourmt3"
        / "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
        / "last.ckpt",
        "format": "pytorch_lightning",
        "size_mb": 536.0,
        "speed_x_realtime": 15.0,
        "description": "YourMT3 drum transcription research candidate",
    },
}

_BENCHMARK_NOTE_TO_CLASS: dict[int, str] = {
    35: "kick",
    36: "kick",
    37: "snare",
    38: "snare",
    39: "snare",
    40: "snare",
    41: "tom3",
    42: "hi_hat",
    43: "tom3",
    44: "hi_hat",
    45: "tom2",
    46: "hi_hat",
    47: "tom2",
    48: "tom1",
    49: "crash",
    50: "tom1",
    51: "ride",
    52: "crash",
    53: "ride",
    55: "crash",
    57: "crash",
    59: "ride",
}

_CLASS_TO_CANONICAL_NOTE: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "hi_hat": 42,
    "crash": 49,
    "ride": 51,
    "tom1": 48,
    "tom2": 47,
    "tom3": 41,
}


@dataclass(frozen=True)
class DrumEvent:
    time: float
    note: int
    velocity: int
    duration: float = 0.05


@dataclass(frozen=True)
class DrumTranscriptionResult:
    events: list[DrumEvent]
    used_algorithm: str | None
    attempted_algorithms: list[str]
    warnings: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


DrumTranscriber = Callable[[Path], list[DrumEvent]]


@dataclass(frozen=True)
class MelodicNote:
    t_on: float
    t_off: float
    pitch: int
    velocity: int
    instrument: str = "melodic"


@dataclass(frozen=True)
class MelodicTranscriptionResult:
    notes: list[MelodicNote]
    used_method: str | None
    attempted_methods: list[str]
    warnings: list[str]


MelodicTranscriber = Callable[[Path], list[MelodicNote]]


@dataclass(frozen=True)
class InstrumentTranscriptionResult:
    """Transcription result for a single instrument stem."""
    instrument: str
    notes: list[MelodicNote]
    used_method: str | None
    attempted_methods: list[str]
    warnings: list[str]
    stem_path: str | None = None


def is_mt3_drum_engine(engine_id: str | None) -> bool:
    if engine_id is None:
        return False
    return str(engine_id).strip().lower() in KNOWN_MT3_DRUM_ENGINES


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def drum_engine_metadata(engine_id: str) -> dict[str, Any]:
    normalized = str(engine_id).strip().lower()
    if normalized in MT3_DRUM_ENGINE_MODEL_INFO:
        return _json_safe_value(MT3_DRUM_ENGINE_MODEL_INFO[normalized])
    return {
        "engine": normalized,
        "backend": "heuristic",
        "description": "Heuristic/DSP drum transcription engine",
    }


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _candidate_model_roots(base: Path) -> list[Path]:
    return [
        base,
        base / MT3_MODELPACK_DIRNAME,
        base / "data" / MT3_MODELPACK_DIRNAME,
        base / "AuralPrimerPortable" / "data" / MT3_MODELPACK_DIRNAME,
    ]


def _default_mt3_model_search_roots(stem_path: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(str(meipass))
        roots.extend([meipass_path, meipass_path.parent])

    try:
        exe_dir = Path(sys.executable).resolve().parent
        roots.extend([exe_dir, exe_dir.parent, exe_dir.parent.parent])
    except Exception:
        pass

    cwd = Path.cwd()
    roots.extend([cwd, cwd.parent])

    try:
        this_file = Path(__file__).resolve()
        roots.extend([this_file.parent, this_file.parents[2], this_file.parents[4]])
    except Exception:
        pass

    if stem_path is not None:
        try:
            roots.extend(list(stem_path.resolve().parents[:6]))
        except Exception:
            pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for candidate in _candidate_model_roots(root):
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _iter_installed_modelpack_dirs(modelpack_id: str, search_roots: Iterable[Path]) -> list[Path]:
    matches: list[Path] = []
    for root in search_roots:
        candidate_root = Path(root)
        if candidate_root.name == modelpack_id and (candidate_root / "modelpack.json").is_file():
            matches.append(candidate_root)
            continue

        id_root = candidate_root / modelpack_id
        if not id_root.is_dir():
            continue
        for version_dir in sorted(
            [child for child in id_root.iterdir() if child.is_dir()],
            key=lambda item: item.name,
            reverse=True,
        ):
            if (version_dir / "modelpack.json").is_file():
                matches.append(version_dir)
    return matches


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _resolve_mt3_checkpoint_from_manifest(
    model_root: Path,
    manifest: dict[str, Any],
    model_id: str,
    default_relpath: Path,
) -> Path | None:
    checkpoints = manifest.get("checkpoints")
    if isinstance(checkpoints, list):
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            candidate_model = str(item.get("model", "")).strip().lower()
            candidate_path = str(item.get("path", "")).strip()
            if candidate_model and candidate_model != model_id:
                continue
            if not candidate_path:
                continue
            candidate = model_root / Path(candidate_path)
            if candidate.is_file():
                return candidate

    candidate = model_root / default_relpath
    if candidate.is_file():
        return candidate

    trimmed = default_relpath
    if list(trimmed.parts[:2]) == ["files", "checkpoints"]:
        candidate = model_root / Path("checkpoints") / Path(*trimmed.parts[2:])
        if candidate.is_file():
            return candidate
    return None


def resolve_mt3_modelpack(
    engine_id: str,
    *,
    stem_path: Path | None = None,
    search_roots: Iterable[Path | str] | None = None,
) -> dict[str, Any]:
    engine = str(engine_id).strip().lower()
    info = MT3_DRUM_ENGINE_MODEL_INFO.get(engine)
    if info is None:
        raise FileNotFoundError(f"unknown MT3 drum engine '{engine_id}'")

    explicit_checkpoint = os.getenv(f"AURALPRIMER_{str(info['model_id']).upper()}_CHECKPOINT_PATH")
    if explicit_checkpoint:
        checkpoint_path = Path(explicit_checkpoint).expanduser()
        if checkpoint_path.is_file():
            return _json_safe_value({
                **info,
                "checkpoint_path_resolved": checkpoint_path,
                "modelpack_root": checkpoint_path.parent,
                "modelpack_manifest": {},
                "modelpack_version": "explicit",
            })
        raise FileNotFoundError(f"configured checkpoint does not exist: {checkpoint_path}")

    env_checkpoint_root = os.getenv("MT3_CHECKPOINT_DIR")
    if env_checkpoint_root:
        env_candidate = Path(env_checkpoint_root).expanduser() / Path(info["checkpoint_path"])
        if env_candidate.is_file():
            return _json_safe_value({
                **info,
                "checkpoint_path_resolved": env_candidate,
                "modelpack_root": Path(env_checkpoint_root).expanduser(),
                "modelpack_manifest": {},
                "modelpack_version": "env",
            })

    if search_roots is not None:
        roots = []
        seen: set[str] = set()
        for raw_root in search_roots:
            for candidate in _candidate_model_roots(Path(raw_root)):
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                roots.append(candidate)
    else:
        roots = _default_mt3_model_search_roots(stem_path)
    for model_root in _iter_installed_modelpack_dirs(str(info["modelpack_id"]), roots):
        manifest_path = model_root / "modelpack.json"
        manifest = _read_json_file(manifest_path)
        checkpoint_path = _resolve_mt3_checkpoint_from_manifest(
            model_root,
            manifest,
            str(info["model_id"]),
            Path(info["checkpoint_path"]),
        )
        if checkpoint_path is None:
            continue
        return _json_safe_value({
            **info,
            "checkpoint_path_resolved": checkpoint_path,
            "modelpack_root": model_root,
            "modelpack_manifest": manifest,
            "modelpack_version": str(manifest.get("version", "unknown")).strip() or "unknown",
        })

    searched = ", ".join(str(root) for root in roots)
    raise FileNotFoundError(
        f"missing modelpack for {engine}: expected installed '{info['modelpack_id']}' checkpoint under files/checkpoints; searched {searched}"
    )


def available_mt3_modelpacks(
    search_roots: Iterable[Path | str] | None = None,
    *,
    stem_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        info = MT3_DRUM_ENGINE_MODEL_INFO[engine_id]
        try:
            resolved = resolve_mt3_modelpack(engine_id, stem_path=stem_path, search_roots=search_roots)
            out[engine_id] = {
                "ok": True,
                "backend": "mt3",
                "engine": engine_id,
                "model_id": resolved["model_id"],
                "modelpack_id": resolved["modelpack_id"],
                "modelpack_version": resolved["modelpack_version"],
                "checkpoint_path": str(resolved["checkpoint_path_resolved"]),
                "modelpack_root": str(resolved["modelpack_root"]),
                "size_mb": resolved.get("size_mb"),
                "speed_x_realtime": resolved.get("speed_x_realtime"),
                "description": resolved.get("description"),
            }
        except Exception as exc:
            out[engine_id] = {
                "ok": False,
                "backend": "mt3",
                "engine": engine_id,
                "model_id": info["model_id"],
                "modelpack_id": info["modelpack_id"],
                "size_mb": info.get("size_mb"),
                "speed_x_realtime": info.get("speed_x_realtime"),
                "description": info.get("description"),
                "error": str(exc),
            }
    return out


def _midi_note_to_benchmark_class(note: int) -> str | None:
    return _BENCHMARK_NOTE_TO_CLASS.get(int(note))


def _normalize_midi_note_to_canonical(note: int) -> int | None:
    drum_class = _midi_note_to_benchmark_class(note)
    if drum_class is None:
        return None
    return _CLASS_TO_CANONICAL_NOTE[drum_class]


def _midi_to_drum_events(midi_file: Any) -> list[DrumEvent]:
    import mido

    merged = mido.merge_tracks(midi_file.tracks)
    ticks_per_beat = int(getattr(midi_file, "ticks_per_beat", 480) or 480)
    tempo = 500000
    current_time_sec = 0.0
    events: list[DrumEvent] = []
    saw_drum_channel = False

    for msg in merged:
        current_time_sec += mido.tick2second(msg.time, ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = int(msg.tempo)
            continue
        if msg.type != "note_on" or int(getattr(msg, "velocity", 0)) <= 0:
            continue
        channel = getattr(msg, "channel", None)
        note = int(getattr(msg, "note", 0))
        if channel == 9:
            saw_drum_channel = True
        canonical_note = _normalize_midi_note_to_canonical(note)
        if canonical_note is None:
            continue
        if saw_drum_channel and channel not in (None, 9):
            continue
        events.append(
            DrumEvent(
                time=max(0.0, float(current_time_sec)),
                note=canonical_note,
                velocity=max(1, min(127, int(getattr(msg, "velocity", 100)))),
            )
        )
    return events


def _transcribe_drums_mt3_events(
    stem_path: Path,
    engine_id: str,
    *,
    search_roots: Iterable[Path | str] | None = None,
) -> tuple[list[DrumEvent], dict[str, Any]]:
    import librosa

    resolved = resolve_mt3_modelpack(engine_id, stem_path=stem_path, search_roots=search_roots)
    checkpoint_path = Path(resolved["checkpoint_path_resolved"])
    modelpack_root = Path(resolved["modelpack_root"])
    os.environ["MT3_CHECKPOINT_DIR"] = str(modelpack_root)
    audio, sr = librosa.load(str(stem_path), sr=16000, mono=True)
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with suppress_mt3_runtime_warnings():
            with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                ensure_mt3_transformers_compat()
                from mt3_infer import load_model

                model = load_model(
                    str(resolved["model_id"]),
                    checkpoint_path=str(checkpoint_path),
                    device="cpu",
                    auto_download=False,
                )
                midi = model.transcribe(audio.astype("float32"), sr=sr)
    except Exception as exc:
        detail = "\n".join(
            part for part in (captured_stdout.getvalue().strip(), captured_stderr.getvalue().strip()) if part
        )
        if detail:
            raise RuntimeError(f"MT3 inference failed: {exc}\n{detail}") from exc
        raise
    return _midi_to_drum_events(midi), {
        "backend": "mt3",
        "model_id": resolved["model_id"],
        "modelpack_id": resolved["modelpack_id"],
        "modelpack_version": resolved["modelpack_version"],
        "checkpoint_path": str(checkpoint_path),
        "modelpack_root": str(modelpack_root),
        "size_mb": resolved.get("size_mb"),
        "speed_x_realtime": resolved.get("speed_x_realtime"),
    }


def build_default_drum_algorithm_registry() -> dict[str, DrumTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        adaptive_beat_grid_multilabel,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        hpss_percussive,
        hybrid_kick_grid,
        librosa_superflux,
        mfcc_cepstral,
        multi_resolution,
        multi_resolution_template,
        nmf_decomposition,
        onset_aligned,
        probabilistic_pattern,
        spectral_flux_multiband,
        spectral_template_multipass,
        spectral_template_with_grid,
        template_xcorr,
    )

    registry: dict[str, DrumTranscriber] = {
        "combined_filter": combined_filter.transcribe,
        "dsp_bandpass_improved": dsp_bandpass_improved.transcribe,
        "dsp_spectral_flux": dsp_spectral_flux.transcribe,
        "aural_onset": aural_onset.transcribe,
        "adaptive_beat_grid": adaptive_beat_grid.transcribe,
        "adaptive_beat_grid_multilabel": adaptive_beat_grid_multilabel.transcribe,
        "beat_conditioned_multiband_decoder": beat_conditioned_multiband_decoder.transcribe,
        "spectral_flux_multiband": spectral_flux_multiband.transcribe,
        "dsp_bandpass": dsp_bandpass.transcribe,
        "librosa_superflux": librosa_superflux.transcribe,
        "spectral_template_multipass": spectral_template_multipass.transcribe,
        "spectral_template_with_grid": spectral_template_with_grid.transcribe,
        "multi_resolution": multi_resolution.transcribe,
        "template_xcorr": template_xcorr.transcribe,
        "probabilistic_pattern": probabilistic_pattern.transcribe,
        "onset_aligned": onset_aligned.transcribe,
        "multi_resolution_template": multi_resolution_template.transcribe,
        "hybrid_kick_grid": hybrid_kick_grid.transcribe,
        "nmf_decomposition": nmf_decomposition.transcribe,
        "mfcc_cepstral": mfcc_cepstral.transcribe,
        "hpss_percussive": hpss_percussive.transcribe,
    }

    def _wrap_mt3(engine_id: str) -> DrumTranscriber:
        def _runner(stem_path: Path) -> list[DrumEvent]:
            events, _meta = _transcribe_drums_mt3_events(stem_path, engine_id)
            return events

        return _runner

    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        registry[engine_id] = _wrap_mt3(engine_id)

    return registry


def _default_basic_pitch_model_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))

    roots.append(Path.cwd())

    try:
        # Prefer repository-local roots when running from source.
        this_file = Path(__file__).resolve()
        roots.extend([this_file.parent, this_file.parents[2], this_file.parents[4]])
    except Exception:
        pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in roots:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def resolve_basic_pitch_model_path(search_roots: Iterable[Path | str]) -> Path | None:
    suffixes = [
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp.onnx",
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp.tflite",
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp",
    ]

    for root in search_roots:
        if root is None:
            continue
        root_path = Path(root)
        for suffix in suffixes:
            candidate = root_path / suffix
            if candidate.is_file() or candidate.is_dir():
                return candidate
    return None


def build_default_melodic_algorithm_registry(
    model_search_roots: Iterable[Path | str] | None = None,
    instrument: str = "melodic",
) -> dict[str, MelodicTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        melodic_basic_pitch,
        melodic_pyin,
        melodic_combined,
        melodic_octave_fix,
        melodic_yin_octave_hps_fix,
    )

    roots = list(model_search_roots) if model_search_roots is not None else _default_basic_pitch_model_roots()
    basic_pitch_model_path = resolve_basic_pitch_model_path(roots)

    _inst = instrument  # capture for closures

    def _basic_pitch(stem_path: Path) -> list[MelodicNote]:
        return melodic_basic_pitch.transcribe(stem_path, model_path=basic_pitch_model_path, instrument=_inst)

    def _pyin(stem_path: Path) -> list[MelodicNote]:
        return melodic_pyin.transcribe(stem_path, instrument=_inst)

    def _combined(stem_path: Path) -> list[MelodicNote]:
        return melodic_combined.transcribe(stem_path, instrument=_inst)

    def _octave_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_octave_fix.transcribe(stem_path, instrument=_inst)

    def _yin_octave_hps_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_yin_octave_hps_fix.transcribe(stem_path, instrument=_inst)

    return {
        "basic_pitch": _basic_pitch,
        "pyin": _pyin,
        "melodic_combined": _combined,
        "melodic_octave_fix": _octave_fix,
        "melodic_yin_octave_hps_fix": _yin_octave_hps_fix,
    }


def resolve_drum_engine(requested_engine: str | None) -> tuple[str, list[str]]:
    if requested_engine is None:
        return DEFAULT_DRUM_ENGINE, []

    rf = requested_engine.strip().lower()
    if not rf or rf == "auto":
        return DEFAULT_DRUM_ENGINE, []
    if rf in KNOWN_DRUM_ENGINES:
        return rf, []

    return DEFAULT_DRUM_ENGINE, [
        f"unknown drum engine '{requested_engine}', falling back to {DEFAULT_DRUM_ENGINE}"
    ]


def resolve_drum_filter(requested_filter: str | None) -> tuple[str, list[str]]:
    return resolve_drum_engine(requested_filter)


def validate_melodic_method(method: str | None) -> str | None:
    if method is None:
        return DEFAULT_MELODIC_METHOD
    m = method.strip().lower()
    if not m:
        return DEFAULT_MELODIC_METHOD
    if m in KNOWN_MELODIC_METHODS:
        return m
    return None


def drum_fallback_chain(requested_filter: str | None) -> list[str]:
    normalized, _warnings = resolve_drum_engine(requested_filter)

    if normalized in KNOWN_MT3_DRUM_ENGINES:
        return [normalized]

    if normalized == "spectral_template_with_grid":
        chain = [
            "spectral_template_with_grid",
            "spectral_template_multipass",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "combined_filter":
        chain = [
            "combined_filter",
            "dsp_bandpass_improved",
            "adaptive_beat_grid",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "adaptive_beat_grid":
        chain = [
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "beat_conditioned_multiband_decoder":
        chain = [
            "beat_conditioned_multiband_decoder",
            "spectral_flux_multiband",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "spectral_flux_multiband":
        chain = [
            "spectral_flux_multiband",
            "beat_conditioned_multiband_decoder",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "aural_onset":
        chain = [
            "aural_onset",
            "combined_filter",
            "adaptive_beat_grid",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
        ]
    elif normalized == "librosa_superflux":
        chain = [
            "librosa_superflux",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    else:
        chain = [
            normalized,
            "combined_filter",
            "dsp_bandpass_improved",
            "adaptive_beat_grid",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]

    out: list[str] = []
    for x in chain:
        if x not in out:
            out.append(x)
    return out


def melodic_fallback_chain(requested_method: str | None, instrument: str = "melodic") -> list[str]:
    normalized = validate_melodic_method(requested_method)
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD

    if normalized == "auto":
        if instrument == "bass":
            chain = ["melodic_yin_octave_hps_fix", "melodic_octave_fix", "melodic_combined", "basic_pitch", "pyin"]
        else:
            chain = ["melodic_octave_fix", "melodic_combined", "basic_pitch", "pyin"]
    elif normalized == "basic_pitch":
        chain = ["basic_pitch", "pyin"]
    else:
        chain = [normalized, "melodic_octave_fix", "melodic_yin_octave_hps_fix", "melodic_combined", "basic_pitch", "pyin"]

    out: list[str] = []
    for x in chain:
        if x not in out:
            out.append(x)
    return out


def transcribe_drums_dsp(
    stem_path: Path,
    requested_filter: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
) -> DrumTranscriptionResult:
    normalized, warnings = resolve_drum_engine(requested_filter)
    attempted: list[str] = []

    for algorithm_id in drum_fallback_chain(normalized):
        attempted.append(algorithm_id)
        fn = algorithm_registry.get(algorithm_id)
        if fn is None:
            msg = f"drum algorithm '{algorithm_id}' unavailable; trying next fallback"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        try:
            events = fn(stem_path)
        except Exception as e:
            msg = f"drum algorithm '{algorithm_id}' failed: {e}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        if events:
            return DrumTranscriptionResult(
                events=events,
                used_algorithm=algorithm_id,
                attempted_algorithms=attempted,
                warnings=warnings,
                meta={"backend": "heuristic"},
            )

    return DrumTranscriptionResult(
        events=[],
        used_algorithm=None,
        attempted_algorithms=attempted,
        warnings=warnings,
        meta={"backend": "heuristic"},
    )


def transcribe_drums(
    stem_path: Path,
    requested_engine: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
) -> DrumTranscriptionResult:
    normalized, warnings = resolve_drum_engine(requested_engine)

    if normalized in KNOWN_MT3_DRUM_ENGINES:
        attempted = [normalized]
        try:
            events, meta = _transcribe_drums_mt3_events(stem_path, normalized)
        except Exception as exc:
            msg = f"drum engine '{normalized}' failed: {exc}"
            warnings.append(msg)
            if logger:
                logger(msg)
            return DrumTranscriptionResult(
                events=[],
                used_algorithm=None,
                attempted_algorithms=attempted,
                warnings=warnings,
                meta={"backend": "mt3", "engine": normalized},
            )

        return DrumTranscriptionResult(
            events=events,
            used_algorithm=normalized,
            attempted_algorithms=attempted,
            warnings=warnings,
            meta=meta,
        )

    return transcribe_drums_dsp(
        stem_path,
        requested_filter=normalized,
        algorithm_registry=algorithm_registry,
        logger=logger,
    )


def transcribe_melodic(
    stem_path: Path,
    requested_method: str | None,
    algorithm_registry: dict[str, MelodicTranscriber],
    logger: Callable[[str], None] | None = None,
    instrument: str = "melodic",
) -> MelodicTranscriptionResult:
    normalized = validate_melodic_method(requested_method)
    warnings: list[str] = []
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD
        warnings.append(
            f"unknown melodic method '{requested_method}', falling back to {DEFAULT_MELODIC_METHOD}"
        )

    attempted: list[str] = []
    for method in melodic_fallback_chain(normalized, instrument=instrument):
        attempted.append(method)
        fn = algorithm_registry.get(method)
        if fn is None:
            msg = f"melodic method '{method}' unavailable; trying next fallback"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        try:
            notes = fn(stem_path)
        except Exception as e:
            msg = f"melodic method '{method}' failed: {e}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        if notes:
            return MelodicTranscriptionResult(
                notes=notes,
                used_method=method,
                attempted_methods=attempted,
                warnings=warnings,
            )

    return MelodicTranscriptionResult(
        notes=[],
        used_method=None,
        attempted_methods=attempted,
        warnings=warnings,
    )


def transcribe_all_melodic_stems(
    stems: dict[str, Path],
    requested_method: str | None,
    logger: Callable[[str], None] | None = None,
) -> list[InstrumentTranscriptionResult]:
    """Transcribe each available instrument stem.

    Builds a per-instrument algorithm registry so that each stem is transcribed
    with appropriate frequency ranges for that instrument.

    Args:
        stems: map of instrument role (e.g. "bass", "lead_guitar") to stem wav path.
        requested_method: user-requested transcription method.
        logger: optional log callback.

    Returns:
        A list of InstrumentTranscriptionResult, one per stem that was transcribed.
    """
    results: list[InstrumentTranscriptionResult] = []

    for instrument, stem_path in sorted(stems.items()):
        if not stem_path.is_file():
            if logger:
                logger(f"melodic stem for '{instrument}' not found: {stem_path}")
            continue

        if logger:
            logger(f"transcribing {instrument} from {stem_path.name}")

        # Build a per-instrument registry so frequency ranges are correct.
        inst_registry = build_default_melodic_algorithm_registry(instrument=instrument)

        result = transcribe_melodic(
            stem_path,
            requested_method=requested_method,
            algorithm_registry=inst_registry,
            logger=logger,
            instrument=instrument,
        )

        # Tag each note with the instrument role.
        tagged_notes = [
            MelodicNote(
                t_on=n.t_on,
                t_off=n.t_off,
                pitch=n.pitch,
                velocity=n.velocity,
                instrument=instrument,
            )
            for n in result.notes
        ]

        results.append(
            InstrumentTranscriptionResult(
                instrument=instrument,
                notes=tagged_notes,
                used_method=result.used_method,
                attempted_methods=result.attempted_methods,
                warnings=result.warnings,
                stem_path=str(stem_path),
            )
        )

    return results
