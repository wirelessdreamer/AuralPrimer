"""Opt-in Route A scaffold for DrumSep-backed drum transcription.

``drum_stemsep`` is intentionally inert by default. It does not import MSST,
torch, librosa, or separator code in this process; instead it shells out to a
dedicated runtime only when the caller configures the runtime Python, runner,
and DrumSep checkpoint. Missing or broken configuration returns ``[]`` so the
engine can be registered without changing existing fallback behavior.

Setup instructions live in ``python/ingest/scripts/SETUP-DRUM-STEMSEP.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from aural_ingest.transcription import DrumEvent

ENGINE_ID = "drum_stemsep"
MODELPACK_ID = "drum_stemsep"

_ENV_PYTHON = "AURAL_DRUM_STEMSEP_PYTHON"
_ENV_REPO = "AURAL_DRUM_STEMSEP_REPO"
_ENV_RUNNER = "AURAL_DRUM_STEMSEP_RUNNER"
_ENV_CHECKPOINT = "AURAL_DRUM_STEMSEP_CHECKPOINT"

_CHECKPOINT_NAME = "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt"
_CHECKPOINT_RELPATH = Path("files") / "checkpoints" / MODELPACK_ID / _CHECKPOINT_NAME
_RUNNER_RELPATH = Path("files") / "bin" / "run_drum_stemsep.py"
_TIMEOUT_SEC = 60 * 30
_TAIL_CHARS = 2000

_ROUTE_A_STEMS = ("kick", "snare", "toms", "hi_hat", "crash", "ride")

_LABEL_TO_CANONICAL_NOTE: dict[str, int] = {
    "BD": 36,
    "KD": 36,
    "KICK": 36,
    "BASS_DRUM": 36,
    "SD": 38,
    "SNARE": 38,
    "HH": 42,
    "HI_HAT": 42,
    "HIHAT": 42,
    "HAT": 42,
    "HATS": 42,
    "CLOSED_HAT": 42,
    "CLOSED_HH": 42,
    "OPEN_HAT": 42,
    "OPEN_HH": 42,
    "TOM": 47,
    "TOMS": 47,
    "TOM_MID": 47,
    "MID_TOM": 47,
    "TOM2": 47,
    "TOM_LOW": 41,
    "LOW_TOM": 41,
    "FLOOR_TOM": 41,
    "TOM3": 41,
    "TOM_HIGH": 48,
    "HIGH_TOM": 48,
    "RACK_TOM": 48,
    "TOM1": 48,
    "CRASH": 49,
    "CY": 49,
    "CYMBAL": 49,
    "CYMBALS": 49,
    "RD": 51,
    "RIDE": 51,
    "RIDE_CYMBAL": 51,
}

_CLASS_INDEX_TO_CANONICAL_NOTE: dict[int, int] = {
    0: 36,
    1: 38,
    2: 47,
    3: 42,
    4: 49,
    5: 51,
}


@dataclass(frozen=True)
class _RuntimeConfig:
    python: Path
    runner: Path
    checkpoint: Path
    cwd: Path


def _existing_env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.exists():
        return path
    return None


def _repo_config_error() -> str | None:
    raw = os.environ.get(_ENV_REPO, "").strip()
    if not raw:
        return None
    repo = Path(raw).expanduser()
    if not repo.exists():
        return f"{_ENV_REPO} does not exist: {repo}"
    if not repo.is_dir():
        return f"{_ENV_REPO} is not a directory: {repo}"
    return None


def _resolve_repo() -> Path | None:
    if _repo_config_error() is not None:
        return None
    return _existing_env_path(_ENV_REPO)


def _iter_modelpack_dirs(stem_path: Path | None) -> list[Path]:
    try:
        from aural_ingest.transcription import (
            _default_mt3_model_search_roots,
            _iter_installed_modelpack_dirs,
        )

        roots = list(_default_mt3_model_search_roots(stem_path))
        return list(_iter_installed_modelpack_dirs(MODELPACK_ID, roots))
    except Exception:
        return []


def _resolve_checkpoint(stem_path: Path | None) -> Path | None:
    explicit = _existing_env_path(_ENV_CHECKPOINT)
    if explicit is not None and explicit.is_file():
        return explicit

    for model_root in _iter_modelpack_dirs(stem_path):
        candidates = [
            model_root / _CHECKPOINT_RELPATH,
            model_root / "files" / "checkpoints" / MODELPACK_ID / "model.ckpt",
            model_root / "files" / "checkpoints" / MODELPACK_ID / "model.pth",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def _resolve_runner(stem_path: Path | None) -> Path | None:
    explicit = _existing_env_path(_ENV_RUNNER)
    if explicit is not None and explicit.is_file():
        return explicit

    for model_root in _iter_modelpack_dirs(stem_path):
        candidate = model_root / _RUNNER_RELPATH
        if candidate.is_file():
            return candidate
    return None


def _configured_runtime(stem_path: Path | None) -> _RuntimeConfig | None:
    python = _existing_env_path(_ENV_PYTHON)
    runner = _resolve_runner(stem_path)
    checkpoint = _resolve_checkpoint(stem_path)
    if python is not None and not python.is_file():
        python = None
    if python is None or runner is None or checkpoint is None or _repo_config_error() is not None:
        return None

    repo = _resolve_repo()
    cwd = repo if repo is not None else runner.parent
    return _RuntimeConfig(python=python, runner=runner, checkpoint=checkpoint, cwd=cwd)


def runtime_status(stem_path: Path | str | None = None) -> dict[str, Any]:
    """Return JSON-serializable DrumSep runtime configuration diagnostics."""
    stem = Path(stem_path) if stem_path is not None else None
    python_raw = os.environ.get(_ENV_PYTHON, "").strip()
    repo_raw = os.environ.get(_ENV_REPO, "").strip()
    runner_raw = os.environ.get(_ENV_RUNNER, "").strip()
    checkpoint_raw = os.environ.get(_ENV_CHECKPOINT, "").strip()

    python_path = Path(python_raw).expanduser() if python_raw else None
    repo_path = Path(repo_raw).expanduser() if repo_raw else None
    repo = _resolve_repo()
    runner = _resolve_runner(stem)
    checkpoint = _resolve_checkpoint(stem)
    runtime = _configured_runtime(stem)
    repo_error = _repo_config_error()

    missing: list[str] = []
    if not python_raw:
        missing.append(f"{_ENV_PYTHON} is unset")
    elif python_path is None or not python_path.exists():
        missing.append(f"{_ENV_PYTHON} does not exist: {python_path}")
    elif not python_path.is_file():
        missing.append(f"{_ENV_PYTHON} is not a file: {python_path}")

    if runner is None:
        if runner_raw:
            missing.append(f"{_ENV_RUNNER} does not point to a file: {Path(runner_raw).expanduser()}")
        else:
            missing.append(f"{_ENV_RUNNER} is unset and no modelpack runner was found")

    if checkpoint is None:
        if checkpoint_raw:
            missing.append(f"{_ENV_CHECKPOINT} does not point to a file: {Path(checkpoint_raw).expanduser()}")
        else:
            missing.append(f"{_ENV_CHECKPOINT} is unset and no modelpack checkpoint was found")

    if repo_error is not None:
        missing.append(repo_error)

    cwd = runtime.cwd if runtime is not None else repo if repo is not None else runner.parent if runner else None
    return {
        "configured": runtime is not None,
        "engine": ENGINE_ID,
        "modelpack_id": MODELPACK_ID,
        "missing": missing,
        "env": {
            _ENV_PYTHON: python_raw or None,
            _ENV_REPO: repo_raw or None,
            _ENV_RUNNER: runner_raw or None,
            _ENV_CHECKPOINT: checkpoint_raw or None,
        },
        "python": str(python_path) if python_path is not None else None,
        "python_exists": bool(python_path is not None and python_path.exists()),
        "python_is_file": bool(python_path is not None and python_path.is_file()),
        "repo": str(repo) if repo is not None else str(repo_path) if repo_path is not None else None,
        "repo_exists": bool(repo_path is not None and repo_path.exists()),
        "repo_is_dir": bool(repo_path is not None and repo_path.is_dir()),
        "runner": str(runner) if runner is not None else None,
        "checkpoint": str(checkpoint) if checkpoint is not None else None,
        "cwd": str(cwd) if cwd is not None else None,
    }


def _normalized_label(value: str) -> str:
    return value.strip().upper().replace(" ", "_").replace("-", "_").replace("/", "_")


def _canonical_note_from_value(value: Any) -> int | None:
    if isinstance(value, str):
        label = _normalized_label(value)
        if label in _LABEL_TO_CANONICAL_NOTE:
            return _LABEL_TO_CANONICAL_NOTE[label]
        try:
            value = int(label)
        except ValueError:
            return None

    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        return None

    if value in _CLASS_INDEX_TO_CANONICAL_NOTE:
        return _CLASS_INDEX_TO_CANONICAL_NOTE[value]

    from aural_ingest.transcription import _normalize_midi_note_to_canonical

    return _normalize_midi_note_to_canonical(value)


def _velocity_from_value(value: Any) -> int:
    if value is None:
        return 100
    try:
        velocity = float(value)
    except (TypeError, ValueError):
        return 100
    if 0.0 <= velocity <= 1.0:
        velocity *= 127.0
    return max(1, min(127, int(round(velocity))))


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _iter_raw_events(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("events"), list):
        return list(raw["events"])

    events: list[Any] = []
    for stem, stem_events in raw.items():
        if _normalized_label(stem) not in _LABEL_TO_CANONICAL_NOTE or not isinstance(stem_events, list):
            continue
        for event in stem_events:
            if isinstance(event, dict):
                with_stem = dict(event)
                with_stem.setdefault("stem", stem)
                events.append(with_stem)
            elif isinstance(event, (list, tuple)):
                events.append([event[0], stem, *event[1:]] if event else event)
    return events


def _raw_event_to_drum_event(raw_event: Any) -> DrumEvent | None:
    if isinstance(raw_event, dict):
        time_value = _first_present(raw_event, ("time", "onset", "onset_sec", "start", "start_time"))
        note_value = _first_present(
            raw_event,
            ("note", "pitch", "midi_note", "midi", "class", "label", "instrument", "stem"),
        )
        velocity_value = _first_present(raw_event, ("velocity", "v", "rms", "peak", "confidence"))
        duration_value = _first_present(raw_event, ("duration", "dur", "length"))
    elif isinstance(raw_event, (list, tuple)) and len(raw_event) >= 2:
        time_value = raw_event[0]
        note_value = raw_event[1]
        velocity_value = raw_event[2] if len(raw_event) >= 3 else None
        duration_value = raw_event[3] if len(raw_event) >= 4 else None
    else:
        return None

    canonical_note = _canonical_note_from_value(note_value)
    if canonical_note is None:
        return None
    try:
        time_sec = max(0.0, float(time_value))
    except (TypeError, ValueError):
        return None
    try:
        duration = max(0.0, float(duration_value)) if duration_value is not None else 0.05
    except (TypeError, ValueError):
        duration = 0.05

    return DrumEvent(
        time=time_sec,
        note=int(canonical_note),
        velocity=_velocity_from_value(velocity_value),
        duration=duration,
    )


def _parse_runner_events(raw: Any) -> list[DrumEvent]:
    events = [event for item in _iter_raw_events(raw) if (event := _raw_event_to_drum_event(item))]
    events.sort(key=lambda event: (event.time, event.note))
    return events


def _drum_event_payload(event: DrumEvent) -> dict[str, float | int]:
    return {
        "time": float(event.time),
        "note": int(event.note),
        "velocity": int(event.velocity),
        "duration": float(event.duration),
    }


def _tail(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= _TAIL_CHARS:
        return text
    return text[-_TAIL_CHARS:]


def _contract_payload(stem: Path, out_json: Path, runtime: _RuntimeConfig) -> dict[str, Any]:
    return {
        "engine": ENGINE_ID,
        "wav_path": str(stem),
        "out_json": str(out_json),
        "checkpoint_path": str(runtime.checkpoint),
        "stems": list(_ROUTE_A_STEMS),
    }


def _run_runtime(stem: Path) -> tuple[list[DrumEvent], dict[str, Any]]:
    status = runtime_status(stem)
    runtime = _configured_runtime(stem)
    if runtime is None:
        return [], {
            "status": "not_configured",
            "reason": "; ".join(status["missing"]) or "DrumSep runtime is not configured",
            "runtime": status,
            "event_count": 0,
        }

    try:
        with tempfile.TemporaryDirectory(prefix="aural_drum_stemsep_") as temp_dir:
            temp = Path(temp_dir)
            out_json = temp / "events.json"
            contract = temp / "request.json"
            contract.write_text(json.dumps(_contract_payload(stem, out_json, runtime)), encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = str(runtime.cwd) + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [str(runtime.python), str(runtime.runner), str(contract)],
                cwd=str(runtime.cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SEC,
            )
            diagnostics: dict[str, Any] = {
                "status": "ok",
                "runtime": status,
                "returncode": int(proc.returncode),
                "stdout_tail": _tail(proc.stdout),
                "stderr_tail": _tail(proc.stderr),
                "event_count": 0,
            }
            if proc.returncode != 0:
                diagnostics["status"] = "runner_failed"
                diagnostics["reason"] = f"runner exited with code {proc.returncode}"
                return [], diagnostics
            if not out_json.exists():
                diagnostics["status"] = "missing_output"
                diagnostics["reason"] = f"runner did not write {out_json}"
                return [], diagnostics
            try:
                raw_events = json.loads(out_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                diagnostics["status"] = "malformed_output"
                diagnostics["reason"] = str(exc)
                return [], diagnostics
            events = _parse_runner_events(raw_events)
            diagnostics["event_count"] = len(events)
            return events, diagnostics
    except subprocess.TimeoutExpired as exc:
        return [], {
            "status": "timeout",
            "reason": f"runner timed out after {_TIMEOUT_SEC} seconds",
            "runtime": status,
            "stdout_tail": _tail(getattr(exc, "stdout", "")),
            "stderr_tail": _tail(getattr(exc, "stderr", "")),
            "event_count": 0,
        }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return [], {
            "status": "failed",
            "reason": str(exc),
            "runtime": status,
            "event_count": 0,
        }


def validate_runtime(
    stem_path: Path | str,
    *,
    require_events: bool = False,
    include_events: bool = True,
) -> dict[str, Any]:
    """Run the external DrumSep contract once and return a validation report."""
    stem = Path(stem_path)
    events, diagnostics = _run_runtime(stem)
    status = str(diagnostics.get("status", "unknown"))
    ok = status == "ok" and (bool(events) or not require_events)
    reason = diagnostics.get("reason")
    if status == "ok" and require_events and not events:
        reason = "runner returned valid output but no drum events"

    report: dict[str, Any] = {
        "ok": ok,
        "engine": ENGINE_ID,
        "wav_path": str(stem),
        "status": status,
        "reason": reason,
        "require_events": bool(require_events),
        "event_count": len(events),
        "runtime": diagnostics.get("runtime", runtime_status(stem)),
    }
    for key in ("returncode", "stdout_tail", "stderr_tail"):
        if key in diagnostics:
            report[key] = diagnostics[key]
    if include_events:
        report["events"] = [_drum_event_payload(event) for event in events]
    return report


def transcribe(stem_path: Path | str, **kwargs: Any) -> list[DrumEvent]:
    """Transcribe a drum stem through an external DrumSep Route A runtime."""
    del kwargs
    events, _diagnostics = _run_runtime(Path(stem_path))
    return events
