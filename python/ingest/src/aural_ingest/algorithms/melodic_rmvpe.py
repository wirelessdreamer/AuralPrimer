"""Optional RMVPE vocal pitch adapter.

The official Dream-High/RMVPE code is not a packaged dependency of the ingest
sidecar, and RMVPE weight distribution needs a separate license audit before
we ship a downloader with a baked URL. This adapter is therefore import-safe
and inert unless a local checkpoint is present. When absent, vocals fall back
to torchcrepe/pyin through the normal melodic chain.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.device import select_device
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

ENGINE_ID = "melodic_rmvpe"

_RMVPE_REPO_ENV = "AURAL_RMVPE_REPO"
_RMVPE_CHECKPOINT_SHA_ENV = "AURAL_RMVPE_CHECKPOINT_SHA256"
_RMVPE_DEVICE_ENV = "AURAL_RMVPE_DEVICE"
_RMVPE_BATCH_ENV = "AURAL_RMVPE_BATCH_SIZE"
_RMVPE_PITCH_THRESHOLD_ENV = "AURAL_RMVPE_PITCH_THRESHOLD"
_RMVPE_SAMPLE_RATE = 16_000
_RMVPE_HOP_SEC = 0.02
_RMVPE_SEG_SEC = 2.55
_SRC_MODULE = "src"
_REPO_INFERENCE_RELPATH = Path("src") / "inference.py"
_CHECKPOINT_MANIFEST = "rmvpe.checkpoint.json"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def _velocity_from_confidence(confidence: float) -> int:
    if not math.isfinite(confidence):
        confidence = 0.5
    return max(24, min(127, int(round(36.0 + max(0.0, min(1.0, confidence)) * 88.0))))


def _coerce_frames(
    frames: Iterable[tuple[float, float] | tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for frame in frames:
        if len(frame) < 2:
            continue
        time_sec = float(frame[0])
        freq = float(frame[1])
        confidence = float(frame[2]) if len(frame) >= 3 else 0.8
        if math.isfinite(time_sec) and time_sec >= 0.0 and math.isfinite(freq):
            out.append((time_sec, freq, confidence))
    out.sort(key=lambda item: item[0])
    return out


def _frames_to_notes(
    frames: Iterable[tuple[float, float] | tuple[float, float, float]],
    *,
    instrument: str,
    min_note_sec: float = 0.08,
) -> list[MelodicNote]:
    fmin, fmax = INSTRUMENT_FREQ_RANGES.get(instrument, INSTRUMENT_FREQ_RANGES["melodic"])
    coerced = _coerce_frames(frames)
    if not coerced:
        return []

    notes: list[MelodicNote] = []
    active_pitch: int | None = None
    active_frames: list[tuple[float, float, float]] = []
    last_time = coerced[0][0]

    def flush(end_time: float) -> None:
        nonlocal active_pitch, active_frames
        if active_pitch is None or not active_frames:
            active_pitch = None
            active_frames = []
            return
        start_time = active_frames[0][0]
        if end_time - start_time >= min_note_sec:
            mean_conf = sum(frame[2] for frame in active_frames) / len(active_frames)
            notes.append(
                MelodicNote(
                    t_on=round(start_time, 6),
                    t_off=round(max(end_time, start_time + min_note_sec), 6),
                    pitch=active_pitch,
                    velocity=_velocity_from_confidence(mean_conf),
                    instrument=instrument,
                )
            )
        active_pitch = None
        active_frames = []

    max_gap = _RMVPE_HOP_SEC * 2.5
    for time_sec, freq, confidence in coerced:
        voiced = fmin <= freq <= fmax
        pitch = _midi_from_freq(freq) if voiced and freq > 0.0 else None
        if active_pitch is not None and (pitch != active_pitch or time_sec - last_time > max_gap):
            flush(last_time + _RMVPE_HOP_SEC)
        if pitch is not None:
            active_pitch = pitch
            active_frames.append((time_sec, freq, confidence))
        last_time = time_sec
    flush(last_time + _RMVPE_HOP_SEC)
    return notes


def _frames_to_contour_doc(
    frames: Iterable[tuple[float, float] | tuple[float, float, float]],
) -> dict[str, object]:
    samples: list[dict[str, float]] = []
    for time_sec, freq, confidence in _coerce_frames(frames):
        if not math.isfinite(freq) or freq <= 0.0:
            continue
        sample = {
            "t": round(float(time_sec), 6),
            "hz": round(float(freq), 6),
        }
        if math.isfinite(confidence):
            sample["confidence"] = round(max(0.0, min(1.0, float(confidence))), 6)
        samples.append(sample)
    return {"version": 1, "samples": samples}


def _find_spec(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_review_status(checkpoint: Path | None) -> dict[str, object]:
    if checkpoint is None or not checkpoint.is_file():
        return {
            "ready": False,
            "source": None,
            "manifest_path": None,
            "expected_sha256": None,
            "actual_sha256": None,
            "reason": "RMVPE checkpoint not found",
        }

    manifest_path = checkpoint.parent / _CHECKPOINT_MANIFEST
    expected = os.environ.get(_RMVPE_CHECKPOINT_SHA_ENV, "").strip().lower()
    source = "env" if expected else None
    manifest_payload: dict[str, object] | None = None
    if not expected and manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_payload = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            manifest_payload = None
        if manifest_payload is None:
            return {
                "ready": False,
                "source": "manifest",
                "manifest_path": str(manifest_path),
                "expected_sha256": None,
                "actual_sha256": None,
                "reason": f"RMVPE checkpoint manifest is malformed: {manifest_path}",
            }
        if manifest_payload.get("filename") != checkpoint.name:
            return {
                "ready": False,
                "source": "manifest",
                "manifest_path": str(manifest_path),
                "expected_sha256": manifest_payload.get("sha256"),
                "actual_sha256": None,
                "reason": f"RMVPE checkpoint manifest filename does not match {checkpoint.name}",
            }
        if manifest_payload.get("license_confirmed") is not True:
            return {
                "ready": False,
                "source": "manifest",
                "manifest_path": str(manifest_path),
                "expected_sha256": manifest_payload.get("sha256"),
                "actual_sha256": None,
                "reason": "RMVPE checkpoint manifest does not confirm license review",
            }
        expected = str(manifest_payload.get("sha256", "")).strip().lower()
        source = "manifest"

    if not _SHA256_RE.fullmatch(expected):
        return {
            "ready": False,
            "source": source,
            "manifest_path": str(manifest_path),
            "expected_sha256": expected or None,
            "actual_sha256": None,
            "reason": (
                f"{_RMVPE_CHECKPOINT_SHA_ENV} must be set to a reviewed 64-character SHA-256 "
                f"or {manifest_path} must contain one"
            ),
        }

    actual = _sha256(checkpoint)
    if actual.lower() != expected:
        return {
            "ready": False,
            "source": source,
            "manifest_path": str(manifest_path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "reason": f"RMVPE checkpoint sha256 mismatch: expected {expected}, got {actual}",
        }
    return {
        "ready": True,
        "source": source,
        "manifest_path": str(manifest_path),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "reason": None,
    }


def _rmvpe_repo_status() -> dict[str, object]:
    repo = os.environ.get(_RMVPE_REPO_ENV, "").strip()
    if not repo:
        return {
            "env": None,
            "path": None,
            "exists": None,
            "is_dir": None,
            "inference_py": None,
            "inference_py_exists": None,
            "module_importable": False,
            "ready": False,
            "reason": f"{_RMVPE_REPO_ENV} is required and must point at a reviewed RMVPE repo checkout",
        }

    repo_path = Path(repo).expanduser()
    inference_py = repo_path / _REPO_INFERENCE_RELPATH
    exists = repo_path.exists()
    is_dir = repo_path.is_dir()
    inference_exists = inference_py.is_file()
    if exists and is_dir and inference_exists and str(repo_path) not in sys.path:
        sys.path.insert(0, str(repo_path))
    module_importable = _find_spec(_SRC_MODULE)
    ready = exists and is_dir and inference_exists and module_importable
    reason = None
    if not exists:
        reason = f"{_RMVPE_REPO_ENV} does not exist: {repo_path}"
    elif not is_dir:
        reason = f"{_RMVPE_REPO_ENV} is not a directory: {repo_path}"
    elif not inference_exists:
        reason = f"RMVPE inference module missing: {inference_py}"
    elif not module_importable:
        reason = "RMVPE src module is not importable"
    return {
        "env": repo,
        "path": str(repo_path),
        "exists": exists,
        "is_dir": is_dir,
        "inference_py": str(inference_py),
        "inference_py_exists": inference_exists,
        "module_importable": module_importable,
        "ready": ready,
        "reason": reason,
    }


def runtime_status(checkpoint_path: Path | str | None = None) -> dict[str, object]:
    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    checkpoint_found = bool(checkpoint is not None and checkpoint.is_file())
    checkpoint_review = _checkpoint_review_status(checkpoint)
    repo_status = _rmvpe_repo_status()
    torch_importable = _find_spec("torch")
    ready = checkpoint_found and bool(checkpoint_review["ready"]) and bool(repo_status["ready"]) and torch_importable
    missing: list[str] = []
    if not checkpoint_found:
        missing.append("RMVPE checkpoint not found")
    elif not checkpoint_review["ready"]:
        missing.append(str(checkpoint_review["reason"] or "RMVPE checkpoint has not been hash-reviewed"))
    if not repo_status["ready"]:
        missing.append(str(repo_status["reason"] or "RMVPE runtime source is not ready"))
    if not torch_importable:
        missing.append("torch is not importable")
    reason = missing[0] if missing else None
    return {
        "engine": ENGINE_ID,
        "checkpoint_found": checkpoint_found,
        "checkpoint_path": str(checkpoint) if checkpoint is not None else None,
        "checkpoint_review": checkpoint_review,
        "repo": repo_status,
        "torch_importable": torch_importable,
        "ready": ready,
        "reason": reason,
        "missing": missing,
    }


def resolved_runtime_status() -> dict[str, object]:
    """Resolve the configured checkpoint and return RMVPE runtime diagnostics."""
    try:
        from aural_ingest.transcription import (
            _default_basic_pitch_model_roots,
            resolve_rmvpe_checkpoint_path,
        )

        checkpoint = resolve_rmvpe_checkpoint_path(_default_basic_pitch_model_roots())
    except Exception as exc:
        status = runtime_status(None)
        reason = f"RMVPE checkpoint resolution failed: {type(exc).__name__}: {exc}"
        status["ready"] = False
        status["reason"] = reason
        missing = list(status.get("missing", []))
        missing.insert(0, reason)
        status["missing"] = missing
        status["resolution_failure"] = {"type": type(exc).__name__, "message": str(exc)}
        return status
    return runtime_status(checkpoint)


def _note_payload(note: MelodicNote) -> dict[str, object]:
    return {
        "t_on": note.t_on,
        "t_off": note.t_off,
        "pitch": note.pitch,
        "velocity": note.velocity,
        "instrument": note.instrument,
    }


def _last_run_dict() -> dict[str, Any]:
    last_run = getattr(transcribe, "last_run", {})
    return last_run if isinstance(last_run, dict) else {}


def validate_runtime(
    stem_path: Path | str,
    *,
    instrument: str = "vocals",
    require_notes: bool = False,
    include_notes: bool = True,
) -> dict[str, object]:
    """Run one RMVPE validation pass and return a JSON-serializable report."""
    stem = Path(stem_path)
    status = resolved_runtime_status()
    if not status["ready"]:
        report: dict[str, object] = {
            "ok": False,
            "engine": ENGINE_ID,
            "wav_path": str(stem),
            "instrument": instrument,
            "status": "not_ready",
            "reason": status["reason"],
            "require_notes": bool(require_notes),
            "note_count": 0,
            "runtime": status,
        }
        if include_notes:
            report["notes"] = []
        return report

    notes = transcribe(stem, instrument=instrument)
    last_run = _last_run_dict()
    warnings = last_run.get("warnings", [])
    warnings_list = [str(item) for item in warnings] if isinstance(warnings, list) else []
    meta = last_run.get("meta", {})
    meta_dict = meta if isinstance(meta, dict) else {}
    failure = meta_dict.get("failure")
    runtime = meta_dict.get("runtime", status)
    status_label = "failed" if failure or warnings_list else "ok"
    reason = None
    if isinstance(failure, dict):
        reason = f"RMVPE inference failed: {failure.get('type')}: {failure.get('message')}"
    elif warnings_list:
        reason = "; ".join(warnings_list)
    elif require_notes and not notes:
        reason = "RMVPE inference returned valid output but no vocal notes"

    ok = status_label == "ok" and (bool(notes) or not require_notes)
    report = {
        "ok": ok,
        "engine": ENGINE_ID,
        "wav_path": str(stem),
        "instrument": instrument,
        "status": status_label,
        "reason": reason,
        "require_notes": bool(require_notes),
        "note_count": len(notes),
        "runtime": runtime,
        "warnings": warnings_list,
    }
    contour = meta_dict.get("vocal_pitch_contour")
    if isinstance(contour, dict):
        samples = contour.get("samples")
        report["contour_sample_count"] = len(samples) if isinstance(samples, list) else None
    if include_notes:
        report["notes"] = [_note_payload(note) for note in notes]
    return report


def _run_rmvpe_f0(stem_path: Path, checkpoint_path: Path, device: str) -> list[tuple[float, float, float]]:
    status = runtime_status(checkpoint_path)
    if not status["ready"]:
        return []

    import numpy as np
    import torch

    from src import Inference, SAMPLE_RATE, to_local_average_cents

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    try:
        import librosa

        audio_np = np.asarray(samples, dtype=np.float32)
        if sr != SAMPLE_RATE:
            audio_np = librosa.resample(audio_np, orig_sr=sr, target_sr=SAMPLE_RATE)
    except Exception:
        if sr != _RMVPE_SAMPLE_RATE:
            return []
        audio_np = np.asarray(samples, dtype=np.float32)

    torch_device = torch.device(device)
    model = torch.load(str(checkpoint_path), map_location=torch_device, weights_only=False)
    model = getattr(model, "module", model)
    model = model.to(torch_device).eval()
    audio = torch.as_tensor(audio_np, dtype=torch.float32, device=torch_device)

    hop_samples = int(round(_RMVPE_HOP_SEC * SAMPLE_RATE))
    seg_len = int(round(_RMVPE_SEG_SEC * SAMPLE_RATE))
    seg_frames = int(round(_RMVPE_SEG_SEC / _RMVPE_HOP_SEC))
    batch_size = max(1, _env_int(_RMVPE_BATCH_ENV, 4))
    pitch_threshold = max(0.0, _env_float(_RMVPE_PITCH_THRESHOLD_ENV, 0.03))

    inference = Inference(model, seg_len, seg_frames, hop_samples, batch_size, torch_device)
    _hidden, salience = inference.inference(audio)
    if hasattr(salience, "detach"):
        salience = salience.detach()
    salience_np = salience.cpu().numpy()
    cents = to_local_average_cents(salience_np, None, pitch_threshold)
    confidence = salience_np.max(axis=1) if salience_np.ndim == 2 else np.zeros_like(cents)
    frames: list[tuple[float, float, float]] = []
    for idx, cent in enumerate(cents):
        freq = 10.0 * (2.0 ** (float(cent) / 1200.0)) if float(cent) > 0.0 else 0.0
        conf = float(confidence[idx]) if idx < len(confidence) else 0.0
        frames.append((idx * _RMVPE_HOP_SEC, freq, conf))
    return frames


def transcribe(stem_path: Path, *, instrument: str = "vocals", **_kwargs) -> list[MelodicNote]:
    transcribe.last_run = {"warnings": [], "scores": {}, "meta": {"engine": ENGINE_ID}}
    try:
        from aural_ingest.transcription import (
            _default_basic_pitch_model_roots,
            resolve_rmvpe_checkpoint_path,
        )

        checkpoint = resolve_rmvpe_checkpoint_path(_default_basic_pitch_model_roots())
        if checkpoint is None:
            status = runtime_status(None)
            transcribe.last_run = {
                "warnings": [str(status["reason"])],
                "scores": {},
                "meta": {"engine": ENGINE_ID, "checkpoint_found": False, "runtime": status},
            }
            return []
        status = runtime_status(checkpoint)
        if not status["ready"]:
            transcribe.last_run = {
                "warnings": [str(status["reason"])],
                "scores": {},
                "meta": {
                    "engine": ENGINE_ID,
                    "checkpoint_found": True,
                    "checkpoint_path": str(checkpoint),
                    "runtime": status,
                },
            }
            return []
        device = select_device(_RMVPE_DEVICE_ENV)
        frames = _run_rmvpe_f0(Path(stem_path), checkpoint, device)
        transcribe.last_run = {
            "warnings": [],
            "scores": {},
            "meta": {
                "engine": ENGINE_ID,
                "checkpoint_found": True,
                "checkpoint_path": str(checkpoint),
                "device": device,
                "runtime": status,
                "vocal_pitch_contour": _frames_to_contour_doc(frames),
            },
        }
        return _frames_to_notes(frames, instrument=instrument)
    except Exception as exc:
        transcribe.last_run = {
            "warnings": ["RMVPE inference failed"],
            "scores": {},
            "meta": {
                "engine": ENGINE_ID,
                "checkpoint_found": False,
                "failure": {"type": type(exc).__name__, "message": str(exc)},
            },
        }
        return []


transcribe.last_run = {"warnings": [], "scores": {}, "meta": {"engine": ENGINE_ID}}
