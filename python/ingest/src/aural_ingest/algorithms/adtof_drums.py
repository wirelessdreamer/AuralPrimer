"""Research-only adapter for the official MZehren/ADTOF drum model.

ADTOF's code, weights, and dataset are CC BY-NC-SA 4.0, and the official
runtime depends on TensorFlow, tapcorrect, and madmom. That stack is too heavy
and too license-sensitive for the frozen sidecar, so this engine is an
opt-in benchmark path only:

* no TensorFlow / ADTOF imports at module import time;
* no project dependency on the ADTOF package;
* returns [] when the dedicated ADTOF runtime is not configured;
* shells out to a separate Python via AURAL_ADTOF_PYTHON.

Setup instructions live in ``python/ingest/scripts/SETUP-ADTOF.md``.
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

ENGINE_ID = "adtof_drums"

_ENV_PYTHON = "AURAL_ADTOF_PYTHON"
_ENV_REPO = "AURAL_ADTOF_REPO"
_TIMEOUT_SEC = 60 * 20
_TAIL_CHARS = 2000

_MODEL_MODULE_RELPATH = Path("adtof") / "model" / "model.py"
_WEIGHT_INDEX_RELPATH = Path("adtof") / "models" / "Frame_RNN_adtofAll_0.index"
_WEIGHT_DATA_GLOB = "Frame_RNN_adtofAll_0.data-*"

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
    "TT": 47,
    "TOM": 47,
    "TOMS": 47,
    "CY": 49,
    "CYMBAL": 49,
    "CYMBALS": 49,
    "CY+RD": 49,
    "CY_RD": 49,
    "RD": 49,
    "RIDE": 49,
}

_CLASS_INDEX_TO_CANONICAL_NOTE: dict[int, int] = {
    0: 36,
    1: 38,
    2: 42,
    3: 47,
    4: 49,
}


@dataclass(frozen=True)
class _RuntimeConfig:
    python: Path
    repo: Path
    model_module: Path
    weight_index: Path
    weight_data_files: tuple[Path, ...]


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _weight_data_files(repo: Path) -> tuple[Path, ...]:
    return tuple(sorted((repo / "adtof" / "models").glob(_WEIGHT_DATA_GLOB)))


def _runner_script() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[3] / "scripts" / "run_adtof_adapter.py",
        Path.cwd() / "python" / "ingest" / "scripts" / "run_adtof_adapter.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def runtime_status() -> dict[str, Any]:
    """Return JSON-serializable ADTOF runtime configuration diagnostics."""
    python_path = os.environ.get(_ENV_PYTHON, "").strip()
    repo_path = os.environ.get(_ENV_REPO, "").strip()
    python = Path(python_path).expanduser() if python_path else None
    repo = Path(repo_path).expanduser() if repo_path else None
    model_module = repo / _MODEL_MODULE_RELPATH if repo is not None else None
    weight_index = repo / _WEIGHT_INDEX_RELPATH if repo is not None else None
    weight_data = _weight_data_files(repo) if repo is not None else ()
    runner = _runner_script()

    missing: list[str] = []
    if python is None:
        missing.append(f"{_ENV_PYTHON} is unset")
    elif not python.exists():
        missing.append(f"{_ENV_PYTHON} does not exist: {python}")
    elif not python.is_file():
        missing.append(f"{_ENV_PYTHON} is not a file: {python}")

    if repo is None:
        missing.append(f"{_ENV_REPO} is unset")
    elif not repo.exists():
        missing.append(f"{_ENV_REPO} does not exist: {repo}")
    elif not repo.is_dir():
        missing.append(f"{_ENV_REPO} is not a directory: {repo}")
    else:
        if model_module is None or not model_module.is_file():
            missing.append(f"official ADTOF model module missing: {repo / _MODEL_MODULE_RELPATH}")
        if weight_index is None or not weight_index.is_file():
            missing.append(f"official ADTOF checkpoint index missing: {repo / _WEIGHT_INDEX_RELPATH}")
        if not weight_data:
            missing.append(f"official ADTOF checkpoint data file missing: {repo / 'adtof' / 'models' / _WEIGHT_DATA_GLOB}")

    if runner is None:
        missing.append("AuralPrimer ADTOF runner script not found")

    return {
        "configured": not missing,
        "engine": ENGINE_ID,
        "missing": missing,
        "env": {
            _ENV_PYTHON: python_path or None,
            _ENV_REPO: repo_path or None,
        },
        "python": str(python) if python is not None else None,
        "python_exists": bool(python is not None and python.exists()),
        "python_is_file": bool(python is not None and python.is_file()),
        "repo": str(repo) if repo is not None else None,
        "repo_exists": bool(repo is not None and repo.exists()),
        "repo_is_dir": bool(repo is not None and repo.is_dir()),
        "model_module": str(model_module) if model_module is not None else None,
        "model_module_exists": bool(model_module is not None and model_module.is_file()),
        "weight_index": str(weight_index) if weight_index is not None else None,
        "weight_index_exists": bool(weight_index is not None and weight_index.is_file()),
        "weight_data_files": [str(path) for path in weight_data],
        "runner": str(runner) if runner is not None else None,
    }


def _configured_runtime() -> _RuntimeConfig | None:
    status = runtime_status()
    if not status["configured"]:
        return None
    python_raw = status.get("python")
    repo_raw = status.get("repo")
    if not isinstance(python_raw, str) or not isinstance(repo_raw, str):
        return None
    repo = Path(repo_raw)
    return _RuntimeConfig(
        python=Path(python_raw),
        repo=repo,
        model_module=repo / _MODEL_MODULE_RELPATH,
        weight_index=repo / _WEIGHT_INDEX_RELPATH,
        weight_data_files=_weight_data_files(repo),
    )


def _canonical_note_from_value(value: Any) -> int | None:
    if isinstance(value, str):
        label = value.strip().upper().replace(" ", "_").replace("-", "_")
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
    if 0.0 < velocity <= 1.0:
        velocity *= 127.0
    return max(1, min(127, int(round(velocity))))


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _raw_event_to_drum_event(raw_event: Any) -> DrumEvent | None:
    if isinstance(raw_event, dict):
        time_value = _first_present(raw_event, ("time", "onset", "onset_sec", "start", "start_time"))
        note_value = _first_present(raw_event, ("note", "pitch", "midi_note", "midi", "class", "label", "instrument"))
        velocity_value = _first_present(raw_event, ("velocity", "v", "confidence", "probability"))
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


def _parse_runner_events(raw_events: Any) -> list[DrumEvent]:
    if isinstance(raw_events, dict):
        raw_events = raw_events.get("events", [])
    if not isinstance(raw_events, list):
        return []

    events = [event for raw_event in raw_events if (event := _raw_event_to_drum_event(raw_event)) is not None]
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


def _run_runtime(stem: Path) -> tuple[list[DrumEvent], dict[str, Any]]:
    status = runtime_status()
    runtime = _configured_runtime()
    runner = _runner_script()
    if runtime is None or runner is None:
        return [], {
            "status": "not_configured",
            "reason": "; ".join(status["missing"]) or "ADTOF runtime is not configured",
            "runtime": status,
            "event_count": 0,
        }

    try:
        with tempfile.TemporaryDirectory(prefix="aural_adtof_") as temp_dir:
            temp = Path(temp_dir)
            out_json = temp / "events.json"
            contract = temp / "request.json"
            contract.write_text(
                json.dumps(
                    {
                        "wav_path": str(stem),
                        "out_json": str(out_json),
                        "repo_path": str(runtime.repo),
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(runtime.repo) + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [str(runtime.python), str(runner), str(contract)],
                cwd=str(runtime.repo),
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
    """Run the external ADTOF contract once and return a validation report."""
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
        "runtime": diagnostics.get("runtime", runtime_status()),
    }
    for key in ("returncode", "stdout_tail", "stderr_tail"):
        if key in diagnostics:
            report[key] = diagnostics[key]
    if include_events:
        report["events"] = [_drum_event_payload(event) for event in events]
    return report


def transcribe(stem_path: Path | str, **kwargs: Any) -> list[DrumEvent]:
    """Transcribe a drum stem through the external ADTOF runtime.

    Missing env vars, missing runner, runtime failures, or malformed output all
    return [] so this research engine can be registered without disturbing the
    sidecar or fallback chains.
    """
    del kwargs
    events, _diagnostics = _run_runtime(Path(stem_path))
    return events
