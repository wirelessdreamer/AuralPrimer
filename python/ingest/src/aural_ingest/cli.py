from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aural_ingest.drum_benchmark import (
    BENCHMARK_CLASS_ORDER,
    benchmark_algorithms,
    format_benchmark_summary,
    load_drum_reference,
)
from aural_ingest.guitar_split import split_lead_rhythm_guitar_stem
from aural_ingest.progress import ProgressEvent, emit, log
from aural_ingest.transcription import (
    DEFAULT_DRUM_FILTER,
    DEFAULT_MELODIC_METHOD,
    KNOWN_DRUM_FILTERS,
    build_default_drum_algorithm_registry,
    build_default_melodic_algorithm_registry,
    transcribe_melodic,
    transcribe_drums_dsp,
    resolve_drum_filter,
    validate_melodic_method,
)


@dataclass(frozen=True)
class Stage:
    id: str
    version: str
    outputs: list[str]


PIPELINE_ID = "aural_ingest"
PIPELINE_VERSION = "0.1.0"
SCHEMA_VERSION = "1.0.0"
DEMUCS_MODELPACK_ID = "demucs_6"
DEMUCS_MODELPACK_FILENAME = "demucs_6.zip"
DEMUCS_PROVIDER = "demucs"
DEMUCS_STEM_ROLE_ALIASES: dict[str, str] = {"piano": "keys"}
DEMUCS_PRIMARY_STEM_ROLES: tuple[str, ...] = ("drums", "bass", "guitar", "keys", "vocals")


STAGES: list[Stage] = [
    Stage(id="init_songpack", version="0.1.0", outputs=["manifest.json"]),
    # We always produce mix.wav. Compressed assets are optional (only produced when ffmpeg is available).
    Stage(id="decode_audio", version="0.2.0", outputs=["audio/mix.wav", "audio/mix.mp3", "audio/mix.ogg"]),
    Stage(id="beats_tempo", version="0.2.0", outputs=["features/notes.mid"]),
    Stage(id="sections", version="0.2.0", outputs=["features/notes.mid"]),
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
    ),
    Stage(
        id="split_guitar_stems",
        version="0.1.0",
        outputs=["audio/stems/lead_guitar.wav", "audio/stems/rhythm_guitar.wav"],
    ),
    Stage(id="transcribe_drums", version="0.2.0", outputs=["features/notes.mid"]),
    Stage(id="midi_finalize", version="0.1.0", outputs=["features/notes.mid"]),
]


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
        return json.loads(p.read_text("utf-8"))

    return json.loads(raw)


def _have_ffmpeg() -> bool:
    try:
        cp = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
        return cp.returncode == 0
    except FileNotFoundError:
        return False


def _decode_to_wav(src: Path, dst_wav: Path, *, target_sr: int = 48_000) -> tuple[float, int]:
    """Create a deterministic PCM16 mono WAV (48kHz default).

    Returns (duration_sec, sample_rate).

    Supported inputs:
    - .wav (PCM16): copied through (no resample)
    - others: requires ffmpeg on PATH
    """

    if src.suffix.lower() == ".wav":
        # Keep the original wav as-is. This keeps behavior simple and avoids ffmpeg dependency
        # for our CI + unit tests.
        shutil.copyfile(src, dst_wav)
        duration, sr = _wav_duration_sec(dst_wav)
        return duration, sr

    if not _have_ffmpeg():
        raise RuntimeError(
            "ffmpeg not found on PATH; non-wav inputs require ffmpeg for decode. "
            "Provide a .wav input or install ffmpeg."
        )

    # Force deterministic output:
    # - PCM16 mono
    # - normalized sample rate
    # Note: ffmpeg can still include encoder metadata in non-wav outputs; we only require wav here.
    cmd = [
        "ffmpeg",
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
    }


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _default_demucs_modelpack_candidates() -> list[Path]:
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
        for candidate in (
            root / "modelpacks" / DEMUCS_MODELPACK_FILENAME,
            root / "AuralPrimerPortable" / "modelpacks" / DEMUCS_MODELPACK_FILENAME,
            root / "dist" / "modelpacks" / DEMUCS_MODELPACK_FILENAME,
            root / DEMUCS_MODELPACK_FILENAME,
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


def _resolve_demucs_modelpack(
    config: dict[str, Any],
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    configured = config.get("demucs_modelpack_zip_path")
    candidates: list[Path] = []
    if isinstance(configured, str) and configured.strip():
        candidates.append(Path(configured).expanduser())
    candidates.extend(_default_demucs_modelpack_candidates())

    last_error: str | None = None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            manifest = _read_zip_json(candidate, "modelpack.json")
        except Exception as exc:
            last_error = f"invalid demucs modelpack {candidate}: {exc}"
            continue
        if str(manifest.get("id", "")).strip() != DEMUCS_MODELPACK_ID:
            last_error = f"unexpected modelpack id in {candidate}: {manifest.get('id')!r}"
            continue
        return candidate, manifest, None

    if last_error:
        return None, None, last_error
    return None, None, f"{DEMUCS_MODELPACK_FILENAME} not found in default search locations"


def _prepare_demucs_weight_file(
    modelpack_zip: Path,
    modelpack_manifest: dict[str, Any],
) -> tuple[Path, dict[str, Any], Path]:
    weights = modelpack_manifest.get("weights")
    if not isinstance(weights, list) or not weights:
        raise RuntimeError(f"{modelpack_zip} modelpack.json missing weights[]")

    weight_info = weights[0]
    if not isinstance(weight_info, dict):
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0] must be an object")

    rel_path = str(weight_info.get("path", "")).strip()
    if not rel_path:
        raise RuntimeError(f"{modelpack_zip} modelpack.json weights[0].path missing")

    version = str(modelpack_manifest.get("version", "unknown")).strip() or "unknown"
    checksum = str(weight_info.get("sha256", "")).strip().lower()
    cache_root = Path(tempfile.gettempdir()) / "auralprimer_demucs_modelpacks"
    cache_dir = cache_root / f"{DEMUCS_MODELPACK_ID}_{_safe_slug(version)}_{checksum[:16] or 'nocheck'}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    weight_name = Path(rel_path).name
    weight_path = cache_dir / weight_name
    if not weight_path.is_file():
        with zipfile.ZipFile(modelpack_zip) as zf:
            data = zf.read(rel_path)
        weight_path.write_bytes(data)

    if checksum:
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


def _copy_cached_stems(cache_dir: Path, stems_dir: Path, stem_files: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for stem_name, filename in stem_files.items():
        src = cache_dir / filename
        dst = stems_dir / filename
        if not src.is_file():
            continue
        shutil.copyfile(src, dst)
        out[stem_name] = f"audio/stems/{filename}"
    return out


def _separate_stems_with_demucs(
    mix_wav: Path,
    stems_dir: Path,
    *,
    mix_sha256: str,
    shifts: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    if bool(config.get("disable_stem_separation", False)) or str(
        config.get("stem_separation_provider", "")
    ).strip().lower() == "none":
        return {"ok": False, "status": "skipped", "reason": "stem separation disabled by config"}

    modelpack_zip, modelpack_manifest, err = _resolve_demucs_modelpack(config)
    if modelpack_zip is None or modelpack_manifest is None:
        return {"ok": False, "status": "skipped", "reason": err or "demucs modelpack unavailable"}

    try:
        import torch
        from demucs.apply import apply_model
        from demucs.audio import convert_audio
    except Exception as exc:
        return {"ok": False, "status": "skipped", "reason": f"demucs runtime unavailable: {exc}"}

    try:
        weight_path, weight_info, modelpack_cache_dir = _prepare_demucs_weight_file(modelpack_zip, modelpack_manifest)
    except Exception as exc:
        return {"ok": False, "status": "skipped", "reason": f"demucs modelpack prepare failed: {exc}"}

    version = str(modelpack_manifest.get("version", "unknown")).strip() or "unknown"
    architecture = str(modelpack_manifest.get("architecture", "unknown")).strip() or "unknown"
    weight_sha = str(weight_info.get("sha256", "")).strip().lower()
    sep_cache_dir = (
        Path(tempfile.gettempdir())
        / "auralprimer_demucs_stem_cache"
        / f"{mix_sha256[:24]}_{_safe_slug(version)}_{weight_sha[:12] or 'nocheck'}_sh{max(1, shifts)}"
    )
    sep_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_meta_path = sep_cache_dir / "separation_meta.json"

    if cache_meta_path.is_file():
        try:
            cache_meta = json.loads(cache_meta_path.read_text("utf-8"))
            if isinstance(cache_meta, dict):
                stem_files = cache_meta.get("stem_files", {})
                if isinstance(stem_files, dict) and all((sep_cache_dir / name).is_file() for name in stem_files.values()):
                    stem_paths = _copy_cached_stems(sep_cache_dir, stems_dir, stem_files)
                    return {
                        "ok": True,
                        "status": "cached",
                        "provider": DEMUCS_PROVIDER,
                        "modelpack_id": DEMUCS_MODELPACK_ID,
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

        device = "cuda" if torch.cuda.is_available() else "cpu"
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
            "modelpack_id": DEMUCS_MODELPACK_ID,
            "modelpack_version": version,
            "architecture": architecture,
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

        stem_paths = _copy_cached_stems(sep_cache_dir, stems_dir, stem_files)
        return {
            "ok": True,
            "status": "fresh",
            "provider": DEMUCS_PROVIDER,
            "modelpack_id": DEMUCS_MODELPACK_ID,
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


MIDI_TICKS_PER_QUARTER = 480
MIDI_CHANNEL_MELODIC = 0
MIDI_CHANNEL_DRUMS = 9
MIDI_CHANNEL_STRUCTURE = 15


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


def _build_notes_mid_bytes(
    *,
    bpm: float,
    beats: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    drum_events: list[Any],
    melodic_notes: list[Any],
) -> bytes:
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

    melodic_track_events: list[tuple[int, bytes]] = [(0, _meta_text_event(0x03, "Melodic"))]
    default_note_dur = max(1, MIDI_TICKS_PER_QUARTER // 8)
    for n in melodic_notes:
        t_on = _sec_to_ticks(float(getattr(n, "t_on", 0.0)), bpm=bpm_safe)
        t_off = _sec_to_ticks(float(getattr(n, "t_off", 0.0)), bpm=bpm_safe)
        if t_off <= t_on:
            t_off = t_on + default_note_dur
        pitch = _clamp_int(int(getattr(n, "pitch", 60)), 0, 127)
        vel = _clamp_int(int(getattr(n, "velocity", 90)), 1, 127)
        melodic_track_events.append((t_on, _note_on(MIDI_CHANNEL_MELODIC, pitch, vel)))
        melodic_track_events.append((t_off, _note_off(MIDI_CHANNEL_MELODIC, pitch)))

    tracks = [
        conductor_events,
        structure_events,
        drum_track_events,
        melodic_track_events,
    ]

    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")  # format 1 (multi-track)
        + len(tracks).to_bytes(2, "big")
        + MIDI_TICKS_PER_QUARTER.to_bytes(2, "big")
    )
    chunks = b"".join(_build_midi_track_chunk(track_events) for track_events in tracks)
    return header + chunks


def _find_audio_source_in_dir(src_dir: Path) -> Path | None:
    """Find one audio source file in a folder deterministically.

    Priority:
    1) common mix file names in the directory root
    2) first audio file by sorted relative path (recursive)
    """

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

    exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
    candidates = [p for p in src_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.relative_to(src_dir).as_posix().lower())
    return candidates[0]


def _extract_dtx_referenced_paths(dtx_path: Path) -> list[Path]:
    """Extract best-effort referenced file paths from a DTX chart file.

    Supported directives (MVP):
    - #WAVxx <path>
    - #BGM <path>
    - #PREIMAGE <path>
    - #PREVIEW <path>
    - #VIDEO <path>
    - #AVIZZ <path>
    """

    try:
        raw = dtx_path.read_text("utf-8", errors="replace")
    except Exception:
        return []

    out: list[Path] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or not s.startswith("#"):
            continue

        # Drop trailing comments after ';' in the same line.
        s = s.split(";", 1)[0].strip()
        if not s:
            continue

        parts = s.split(maxsplit=1)
        if len(parts) < 2:
            continue

        token = parts[0].upper()
        value = parts[1].strip().strip('"').strip("'")
        if not value:
            continue

        is_supported = (
            token.startswith("#WAV")
            or token in {"#BGM", "#PREIMAGE", "#PREVIEW", "#VIDEO", "#AVIZZ"}
        )
        if not is_supported:
            continue

        # DTX references are usually relative to chart folder.
        rel = value.replace("\\", "/")
        out.append((dtx_path.parent / rel).resolve())

    return out


def _find_audio_source_for_dtx(dtx_path: Path) -> Path | None:
    """Resolve one deterministic audio source for a DTX chart.

    Priority:
    1) existing referenced audio files from DTX directives
    2) fallback to folder scan in chart directory
    """

    audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

    for p in _extract_dtx_referenced_paths(dtx_path):
        if p.is_file() and p.suffix.lower() in audio_exts:
            return p

    return _find_audio_source_in_dir(dtx_path.parent)


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
    raw_drum_filter = getattr(args, "drum_filter", DEFAULT_DRUM_FILTER)
    normalized_drum_filter, warnings = resolve_drum_filter(raw_drum_filter)

    raw_melodic_method = getattr(args, "melodic_method", DEFAULT_MELODIC_METHOD)
    melodic_method = validate_melodic_method(raw_melodic_method)
    if melodic_method is None:
        return None, (
            f"invalid --melodic-method '{raw_melodic_method}'. "
            "supported: auto, pyin, basic_pitch"
        )

    shifts_raw = getattr(args, "shifts", 1)
    try:
        shifts = int(shifts_raw)
    except Exception:
        return None, f"invalid --shifts '{shifts_raw}': must be integer >= 1"
    if shifts < 1:
        return None, f"invalid --shifts '{shifts_raw}': must be integer >= 1"

    multi_filter = bool(getattr(args, "multi_filter", False))
    requested_drum_stem, requested_drum_stem_kind = _resolve_requested_drum_stem_path(args, config)
    modelpack_zip, modelpack_manifest, _modelpack_err = _resolve_demucs_modelpack(config)

    return {
        "drum_filter_requested": raw_drum_filter,
        "drum_filter": normalized_drum_filter,
        "drum_source_kind": requested_drum_stem_kind,
        "drum_source_path": str(requested_drum_stem) if requested_drum_stem is not None else None,
        "drum_source_sha256": _sha256_file(requested_drum_stem) if requested_drum_stem is not None else None,
        "stem_separation_provider": DEMUCS_PROVIDER if modelpack_zip is not None else None,
        "stem_separation_modelpack_id": (
            str(modelpack_manifest.get("id")) if isinstance(modelpack_manifest, dict) else None
        ),
        "stem_separation_modelpack_version": (
            str(modelpack_manifest.get("version")) if isinstance(modelpack_manifest, dict) else None
        ),
        "warnings": warnings,
        "melodic_method": melodic_method,
        "shifts": shifts,
        "multi_filter": multi_filter,
    }, None


def _add_transcription_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--drum-filter", default=DEFAULT_DRUM_FILTER)
    p.add_argument("--drum-stem-path")
    p.add_argument("--melodic-method", default=DEFAULT_MELODIC_METHOD)
    p.add_argument("--shifts", type=int, default=1)
    p.add_argument("--multi-filter", action="store_true")


def _try_relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def _resolve_guitar_split_source(
    songpack_root: Path,
    mix_wav: Path,
    config: dict[str, Any],
) -> tuple[Path, str]:
    configured = config.get("guitar_stem_path")
    if isinstance(configured, str) and configured.strip():
        p = Path(configured).expanduser()
        if p.is_file():
            return p, "config"

    for candidate in (
        songpack_root / "audio" / "stems" / "guitar.wav",
        songpack_root / "audio" / "stems" / "Guitar.wav",
    ):
        if candidate.is_file():
            return candidate, "stems_guitar"

    return mix_wav, "mix_fallback"


def _resolve_drum_transcription_source(
    args: argparse.Namespace,
    songpack_root: Path,
    mix_wav: Path,
    config: dict[str, Any],
) -> tuple[Path, str]:
    configured, configured_kind = _resolve_requested_drum_stem_path(args, config)
    if configured is not None:
        return configured, configured_kind

    for candidate in (
        songpack_root / "audio" / "stems" / "drums.wav",
        songpack_root / "audio" / "stems" / "Drums.wav",
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

    notes = [
        {
            "t_on": round(float(n.t_on), 6),
            "t_off": round(float(n.t_off), 6),
            "pitch": int(n.pitch),
            "velocity": int(n.velocity),
            "instrument": "melodic",
        }
        for n in melodic_result.notes
    ]

    payload: dict[str, Any] = {
        "events_version": "1.0.0",
        "tracks": [
            {
                "track_id": "drums_main",
                "role": "drums",
                "name": "Drums",
                "algorithm_requested": requested_filter,
                "algorithm_used": drum_result.used_algorithm,
                "attempted_algorithms": drum_result.attempted_algorithms,
                "meta": {"warnings": drum_result.warnings},
            },
            {
                "track_id": "melodic_main",
                "role": "melodic",
                "name": "Melodic",
                "algorithm_requested": melodic_method,
                "algorithm_used": melodic_result.used_method,
                "attempted_methods": melodic_result.attempted_methods,
                "meta": {"warnings": melodic_result.warnings},
            }
        ],
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


def cmd_stages(_args: argparse.Namespace) -> int:
    # Keep output stable + simple.
    for st in STAGES:
        print(json.dumps({"id": st.id, "version": st.version, "outputs": st.outputs}, sort_keys=True))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.songpack_dir)
    manifest = root / "manifest.json"
    if not manifest.exists():
        print(json.dumps({"ok": False, "error": "missing manifest.json"}, sort_keys=True))
        return 1

    data = json.loads(manifest.read_text("utf-8"))
    print(json.dumps({"ok": True, "manifest": data}, sort_keys=True))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.songpack_dir)
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
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, sort_keys=True))
        return 1

    print(json.dumps({"ok": True}, sort_keys=True))
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
        "tolerance_ms": round(float(args.tolerance_ms), 3),
        "class_order": list(BENCHMARK_CLASS_ORDER),
        "results": results,
    }

    if bool(getattr(args, "json_output", False)):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(format_benchmark_summary(payload))

    return 0 if payload["ok"] else 1


def cmd_import(args: argparse.Namespace) -> int:
    src = Path(args.input_audio_path)
    out = Path(args.out)
    profile = args.profile
    config = _parse_config_arg(args.config)
    tr_opts, tr_err = _resolve_transcription_options(args, config)
    if tr_opts is None:
        log(tr_err or "invalid transcription options")
        return 2
    for w in tr_opts.get("warnings", []):
        log(w)

    if not src.exists():
        log(f"input does not exist: {src}")
        return 2

    # Stage 0: init_songpack
    emit(ProgressEvent(type="stage_start", id="init_songpack", progress=0.0))
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
    emit(ProgressEvent(type="stage_done", id="init_songpack", progress=0.1, artifact="manifest.json"))

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

    # Stage 2: beats_tempo (simple analysis)
    emit(ProgressEvent(type="stage_start", id="beats_tempo", progress=0.3))
    bpm_hint = float(config.get("bpm_hint")) if "bpm_hint" in config else None
    if bpm_hint is not None and bpm_hint > 0:
        bpm = float(round(bpm_hint, 3))
    else:
        emit(ProgressEvent(type="stage_progress", id="beats_tempo", progress=0.35, message="estimating bpm"))
        bpm = _estimate_bpm_from_wav(dst_wav)

    beats = {"beats_version": "1.0.0", "beats": _generate_beats(duration_sec, bpm)}
    tempo = {"tempo_version": "1.0.0", "segments": [{"t0": 0.0, "bpm": bpm, "time_signature": "4/4"}]}
    emit(
        ProgressEvent(
            type="stage_done",
            id="beats_tempo",
            progress=0.55,
            message="tempo/beat structure captured for MIDI export",
        )
    )

    # Stage 3: sections
    emit(ProgressEvent(type="stage_start", id="sections", progress=0.55))
    emit(ProgressEvent(type="stage_progress", id="sections", progress=0.6, message="segmenting"))
    sections = {"sections_version": "1.0.0", "sections": _generate_sections(duration_sec, bpm)}
    emit(
        ProgressEvent(
            type="stage_done",
            id="sections",
            progress=0.7,
            message="sections captured for MIDI export",
        )
    )

    mix_sha256 = _sha256_file(dst_wav)

    # Stage 4: separate_stems (Demucs htdemucs_6s)
    stems_dir = out / "audio" / "stems"
    _mkdir(stems_dir)
    emit(ProgressEvent(type="stage_start", id="separate_stems", progress=0.7))
    emit(
        ProgressEvent(
            type="stage_progress",
            id="separate_stems",
            progress=0.73,
            message="separating stems with htdemucs_6s",
        )
    )
    separation_summary = _separate_stems_with_demucs(
        dst_wav,
        stems_dir,
        mix_sha256=mix_sha256,
        shifts=int(tr_opts.get("shifts", 1) or 1),
        config=config,
    )
    if separation_summary.get("ok"):
        audio_assets = manifest.setdefault("assets", {}).setdefault("audio", {})
        stems_assets = audio_assets.setdefault("stems", {})
        stem_paths = separation_summary.get("stem_paths", {})
        if isinstance(stem_paths, dict):
            for stem_name, stem_relpath in stem_paths.items():
                stems_assets[f"{stem_name}_path"] = stem_relpath

        manifest.setdefault("pipeline", {})["stem_separation"] = {
            "provider": separation_summary.get("provider"),
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
            "provider": DEMUCS_PROVIDER,
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

    drum_registry = build_default_drum_algorithm_registry()
    drum_result = transcribe_drums_dsp(
        drum_source,
        requested_filter=tr_opts["drum_filter_requested"],
        algorithm_registry=drum_registry,
        logger=log,
    )
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

    notes_mid = _build_notes_mid_bytes(
        bpm=bpm,
        beats=beats["beats"],
        sections=sections["sections"],
        drum_events=drum_result.events,
        melodic_notes=melodic_result.notes,
    )
    (out / "features" / "notes.mid").write_bytes(notes_mid)
    emit(ProgressEvent(type="stage_done", id="transcribe_drums", progress=0.97, artifact="features/notes.mid"))

    # Persist effective transcription metadata.
    tr_opts["drum_source_kind"] = drum_source_kind
    tr_opts["drum_source_path"] = drum_source_path
    if tr_opts.get("drum_source_sha256") is None and drum_source_kind != "mix_fallback":
        tr_opts["drum_source_sha256"] = _sha256_file(drum_source)
    tr_opts["drum_filter_used"] = drum_result.used_algorithm or tr_opts["drum_filter"]
    tr_opts["drum_attempted_algorithms"] = drum_result.attempted_algorithms
    tr_opts["melodic_method_used"] = melodic_result.used_method or tr_opts["melodic_method"]
    tr_opts["melodic_attempted_methods"] = melodic_result.attempted_methods
    tr_opts["warnings"] = list(
        dict.fromkeys(
            [
                *tr_opts.get("warnings", []),
                *drum_result.warnings,
                *melodic_result.warnings,
            ]
        )
    )
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
        },
        "drums": {
            "requested_engine": tr_opts.get("drum_filter_requested"),
            "normalized_engine": tr_opts.get("drum_filter"),
            "used_engine": tr_opts.get("drum_filter_used"),
            "source_kind": tr_opts.get("drum_source_kind"),
            "source_path": tr_opts.get("drum_source_path"),
            "attempted_engines": tr_opts.get("drum_attempted_algorithms", []),
            "warnings": [*drum_result.warnings],
        },
        "melodic": {
            "requested_engine": tr_opts.get("melodic_method"),
            "used_engine": tr_opts.get("melodic_method_used"),
            "attempted_engines": tr_opts.get("melodic_attempted_methods", []),
            "warnings": [*melodic_result.warnings],
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

    return 0


def cmd_import_dir(args: argparse.Namespace) -> int:
    src_dir = Path(args.input_dir_path)
    out = Path(args.out)
    if not src_dir.exists() or not src_dir.is_dir():
        log(f"input directory does not exist: {src_dir}")
        return 2

    src_audio = _find_audio_source_in_dir(src_dir)
    if src_audio is None:
        log(f"no supported audio files found in directory: {src_dir}")
        return 2

    # Reuse the main import pipeline by forwarding selected source.
    import_args = argparse.Namespace(
        input_audio_path=str(src_audio),
        out=str(out),
        profile=args.profile,
        config=args.config,
        title=args.title,
        artist=args.artist,
        duration_sec=args.duration_sec,
        drum_filter=getattr(args, "drum_filter", DEFAULT_DRUM_FILTER),
        drum_stem_path=getattr(args, "drum_stem_path", None),
        melodic_method=getattr(args, "melodic_method", DEFAULT_MELODIC_METHOD),
        shifts=getattr(args, "shifts", 1),
        multi_filter=bool(getattr(args, "multi_filter", False)),
    )
    return cmd_import(import_args)


def cmd_import_dtx(args: argparse.Namespace) -> int:
    dtx = Path(args.dtx_path)
    out = Path(args.out)
    if not dtx.exists() or not dtx.is_file():
        log(f"dtx file does not exist: {dtx}")
        return 2

    src_audio = _find_audio_source_for_dtx(dtx)
    if src_audio is None:
        log(f"no supported audio files found for dtx: {dtx}")
        return 2

    import_args = argparse.Namespace(
        input_audio_path=str(src_audio),
        out=str(out),
        profile=args.profile,
        config=args.config,
        title=args.title or dtx.stem,
        artist=args.artist,
        duration_sec=args.duration_sec,
        drum_filter=getattr(args, "drum_filter", DEFAULT_DRUM_FILTER),
        drum_stem_path=getattr(args, "drum_stem_path", None),
        melodic_method=getattr(args, "melodic_method", DEFAULT_MELODIC_METHOD),
        shifts=getattr(args, "shifts", 1),
        multi_filter=bool(getattr(args, "multi_filter", False)),
    )
    return cmd_import(import_args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aural_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_stages = sub.add_parser("stages")
    s_stages.set_defaults(func=cmd_stages)

    s_validate = sub.add_parser("validate")
    s_validate.add_argument("songpack_dir")
    s_validate.set_defaults(func=cmd_validate)

    s_info = sub.add_parser("info")
    s_info.add_argument("songpack_dir")
    s_info.set_defaults(func=cmd_info)

    s_benchmark = sub.add_parser("benchmark-drums")
    s_benchmark.add_argument("stem_path")
    s_benchmark.add_argument("reference_path")
    s_benchmark.add_argument("--algorithm", action="append")
    s_benchmark.add_argument("--tolerance-ms", type=float, default=60.0, dest="tolerance_ms")
    s_benchmark.add_argument("--json", action="store_true", dest="json_output")
    s_benchmark.set_defaults(func=cmd_benchmark_drums)

    s_import = sub.add_parser("import")
    s_import.add_argument("input_audio_path")
    s_import.add_argument("--out", required=True)
    s_import.add_argument("--profile", default="full")
    s_import.add_argument("--config")
    s_import.add_argument("--title")
    s_import.add_argument("--artist")
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
    s_import_dir.add_argument("--duration-sec", type=float, dest="duration_sec")
    _add_transcription_options(s_import_dir)
    s_import_dir.set_defaults(func=cmd_import_dir)

    s_import_dtx = sub.add_parser("import-dtx")
    s_import_dtx.add_argument("dtx_path")
    s_import_dtx.add_argument("--out", required=True)
    s_import_dtx.add_argument("--profile", default="full")
    s_import_dtx.add_argument("--config")
    s_import_dtx.add_argument("--title")
    s_import_dtx.add_argument("--artist")
    s_import_dtx.add_argument("--duration-sec", type=float, dest="duration_sec")
    _add_transcription_options(s_import_dtx)
    s_import_dtx.set_defaults(func=cmd_import_dtx)

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
