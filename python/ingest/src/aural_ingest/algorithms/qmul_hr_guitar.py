"""Research-only adapter for QMUL high-resolution guitar transcription.

The ICASSP 2024 QMUL guitar model is an external research runtime, not a
sidecar dependency. This adapter intentionally works as an opt-in command
wrapper:

* no external imports at module import time;
* returns [] when the runtime command is not configured;
* accepts either a MIDI file or a simple JSON note list from the external
  command;
* is suitable for gt-benchmark once the repo/checkpoint/license are installed.

Setup instructions live in ``python/ingest/scripts/SETUP-QMUL-HR-GUITAR.md``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from aural_ingest.transcription import MelodicNote

ENGINE_ID = "qmul_hr_guitar"

_ENV_PYTHON = "AURAL_QMUL_GUITAR_PYTHON"
_ENV_REPO = "AURAL_QMUL_GUITAR_REPO"
_ENV_COMMAND = "AURAL_QMUL_GUITAR_COMMAND"
_ENV_TIMEOUT = "AURAL_QMUL_GUITAR_TIMEOUT_SEC"
_TAIL_CHARS = 2000


def _configured_runtime() -> tuple[Path, Path, str] | None:
    python_path = os.environ.get(_ENV_PYTHON, "").strip()
    repo_path = os.environ.get(_ENV_REPO, "").strip()
    command = os.environ.get(_ENV_COMMAND, "").strip()
    if not python_path or not repo_path or not command:
        return None

    python = Path(python_path).expanduser()
    repo = Path(repo_path).expanduser()
    if not python.exists() or not python.is_file() or not repo.exists() or not repo.is_dir():
        return None
    return python, repo, command


def runtime_status() -> dict[str, Any]:
    """Return JSON-serializable QMUL runtime configuration diagnostics."""
    python_raw = os.environ.get(_ENV_PYTHON, "").strip()
    repo_raw = os.environ.get(_ENV_REPO, "").strip()
    command = os.environ.get(_ENV_COMMAND, "").strip()
    timeout_raw = os.environ.get(_ENV_TIMEOUT, "").strip()

    python_path = Path(python_raw).expanduser() if python_raw else None
    repo_path = Path(repo_raw).expanduser() if repo_raw else None

    missing: list[str] = []
    if not python_raw:
        missing.append(f"{_ENV_PYTHON} is unset")
    elif python_path is None or not python_path.exists():
        missing.append(f"{_ENV_PYTHON} does not exist: {python_path}")
    elif not python_path.is_file():
        missing.append(f"{_ENV_PYTHON} is not a file: {python_path}")

    if not repo_raw:
        missing.append(f"{_ENV_REPO} is unset")
    elif repo_path is None or not repo_path.exists():
        missing.append(f"{_ENV_REPO} does not exist: {repo_path}")
    elif not repo_path.is_dir():
        missing.append(f"{_ENV_REPO} is not a directory: {repo_path}")

    if not command:
        missing.append(f"{_ENV_COMMAND} is unset")

    return {
        "configured": _configured_runtime() is not None,
        "engine": ENGINE_ID,
        "missing": missing,
        "env": {
            _ENV_PYTHON: python_raw or None,
            _ENV_REPO: repo_raw or None,
            _ENV_COMMAND: command or None,
            _ENV_TIMEOUT: timeout_raw or None,
        },
        "python": str(python_path) if python_path is not None else None,
        "python_exists": bool(python_path is not None and python_path.exists()),
        "python_is_file": bool(python_path is not None and python_path.is_file()),
        "repo": str(repo_path) if repo_path is not None else None,
        "repo_exists": bool(repo_path is not None and repo_path.exists()),
        "repo_is_dir": bool(repo_path is not None and repo_path.is_dir()),
        "command": command or None,
        "timeout_sec": _timeout_seconds(),
    }


def _timeout_seconds() -> float:
    raw_value = os.environ.get(_ENV_TIMEOUT, "").strip()
    if not raw_value:
        return 60.0 * 30.0
    try:
        value = float(raw_value)
    except ValueError:
        return 60.0 * 30.0
    return max(1.0, value)


def _quote(value: Path | str) -> str:
    text = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _format_command(
    command: str,
    *,
    python: Path,
    repo: Path,
    wav_path: Path,
    out_midi: Path,
    out_json: Path,
) -> str:
    values = {
        "python": str(python),
        "python_q": _quote(python),
        "repo_path": str(repo),
        "repo_path_q": _quote(repo),
        "wav_path": str(wav_path),
        "wav_path_q": _quote(wav_path),
        "out_midi": str(out_midi),
        "out_midi_q": _quote(out_midi),
        "out_json": str(out_json),
        "out_json_q": _quote(out_json),
    }
    return command.format(**values)


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


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _raw_note_to_melodic_note(raw_note: Any, *, instrument: str) -> MelodicNote | None:
    if isinstance(raw_note, dict):
        onset_value = _first_present(raw_note, ("t_on", "onset", "onset_sec", "start", "start_time", "time"))
        offset_value = _first_present(raw_note, ("t_off", "offset", "offset_sec", "end", "end_time", "stop"))
        duration_value = _first_present(raw_note, ("duration", "dur", "length"))
        pitch_value = _first_present(raw_note, ("pitch", "note", "midi", "midi_note"))
        velocity_value = _first_present(raw_note, ("velocity", "v", "confidence", "probability"))
        string_value = _first_present(raw_note, ("string", "string_index", "string_idx", "s"))
        fret_value = _first_present(raw_note, ("fret", "fret_number", "f"))
    elif isinstance(raw_note, (list, tuple)) and len(raw_note) >= 3:
        onset_value = raw_note[0]
        offset_value = raw_note[1]
        duration_value = None
        pitch_value = raw_note[2]
        velocity_value = raw_note[3] if len(raw_note) >= 4 else None
        string_value = raw_note[4] if len(raw_note) >= 5 else None
        fret_value = raw_note[5] if len(raw_note) >= 6 else None
    else:
        return None

    try:
        onset = max(0.0, float(onset_value))
        pitch = int(round(float(pitch_value)))
    except (TypeError, ValueError):
        return None
    if pitch < 0 or pitch > 127:
        return None

    try:
        if offset_value is not None:
            offset = float(offset_value)
        elif duration_value is not None:
            offset = onset + max(0.0, float(duration_value))
        else:
            offset = onset + 0.05
    except (TypeError, ValueError):
        offset = onset + 0.05
    if offset <= onset:
        return None
    string_index = _optional_int(string_value)
    fret = _optional_int(fret_value)
    if string_index is not None and not 0 <= string_index <= 8:
        string_index = None
    if fret is not None and not 0 <= fret <= 36:
        fret = None
    if string_index is None or fret is None:
        string_index = None
        fret = None

    return MelodicNote(
        t_on=round(onset, 6),
        t_off=round(offset, 6),
        pitch=pitch,
        velocity=_velocity_from_value(velocity_value),
        instrument=instrument,
        string=string_index,
        fret=fret,
    )


def _parse_json_notes(raw_notes: Any, *, instrument: str) -> list[MelodicNote]:
    if isinstance(raw_notes, dict):
        raw_notes = raw_notes.get("notes", raw_notes.get("events", []))
    if not isinstance(raw_notes, list):
        return []
    notes = [
        note
        for raw_note in raw_notes
        if (note := _raw_note_to_melodic_note(raw_note, instrument=instrument)) is not None
    ]
    notes.sort(key=lambda note: (note.t_on, note.pitch, note.t_off))
    return notes


def _load_output_notes(out_midi: Path, out_json: Path, *, instrument: str) -> list[MelodicNote]:
    if out_json.exists():
        return _parse_json_notes(json.loads(out_json.read_text(encoding="utf-8")), instrument=instrument)
    if out_midi.exists():
        from aural_ingest.algorithms.piano_midi import decode_midi_notes

        return decode_midi_notes(out_midi, instrument=instrument)
    return []


def _tail(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= _TAIL_CHARS:
        return text
    return text[-_TAIL_CHARS:]


def _melodic_note_payload(note: MelodicNote) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "t_on": note.t_on,
        "t_off": note.t_off,
        "pitch": note.pitch,
        "velocity": note.velocity,
        "instrument": note.instrument,
    }
    if note.string is not None:
        payload["string"] = note.string
    if note.fret is not None:
        payload["fret"] = note.fret
    return payload


def _run_runtime(stem: Path, *, instrument: str) -> tuple[list[MelodicNote], dict[str, Any]]:
    status = runtime_status()
    runtime = _configured_runtime()
    if runtime is None:
        return [], {
            "status": "not_configured",
            "reason": "; ".join(status["missing"]) or "QMUL guitar runtime is not configured",
            "runtime": status,
            "note_count": 0,
        }

    python, repo, command_template = runtime
    try:
        with tempfile.TemporaryDirectory(prefix="aural_qmul_guitar_") as temp_dir:
            temp = Path(temp_dir)
            out_midi = temp / "qmul_hr_guitar.mid"
            out_json = temp / "qmul_hr_guitar.json"
            command = _format_command(
                command_template,
                python=python,
                repo=repo,
                wav_path=stem,
                out_midi=out_midi,
                out_json=out_json,
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
                timeout=_timeout_seconds(),
            )
            diagnostics: dict[str, Any] = {
                "status": "ok",
                "runtime": status,
                "returncode": int(proc.returncode),
                "stdout_tail": _tail(proc.stdout),
                "stderr_tail": _tail(proc.stderr),
                "note_count": 0,
            }
            if proc.returncode != 0:
                diagnostics["status"] = "runner_failed"
                diagnostics["reason"] = f"runner exited with code {proc.returncode}"
                return [], diagnostics
            if not out_json.exists() and not out_midi.exists():
                diagnostics["status"] = "missing_output"
                diagnostics["reason"] = f"runner wrote neither {out_json} nor {out_midi}"
                return [], diagnostics
            notes = _load_output_notes(out_midi, out_json, instrument=instrument)
            diagnostics["note_count"] = len(notes)
            return notes, diagnostics
    except subprocess.TimeoutExpired as exc:
        return [], {
            "status": "timeout",
            "reason": f"runner timed out after {_timeout_seconds()} seconds",
            "runtime": status,
            "stdout_tail": _tail(getattr(exc, "stdout", "")),
            "stderr_tail": _tail(getattr(exc, "stderr", "")),
            "note_count": 0,
        }
    except KeyError as exc:
        return [], {
            "status": "command_format_error",
            "reason": f"unknown command token: {exc}",
            "runtime": status,
            "note_count": 0,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        return [], {
            "status": "failed",
            "reason": str(exc),
            "runtime": status,
            "note_count": 0,
        }


def validate_runtime(
    stem_path: Path | str,
    *,
    instrument: str = "lead_guitar",
    require_notes: bool = False,
    include_notes: bool = True,
) -> dict[str, Any]:
    """Run the external QMUL command contract once and return a validation report."""
    stem = Path(stem_path)
    notes, diagnostics = _run_runtime(stem, instrument=instrument)
    status = str(diagnostics.get("status", "unknown"))
    ok = status == "ok" and (bool(notes) or not require_notes)
    reason = diagnostics.get("reason")
    if status == "ok" and require_notes and not notes:
        reason = "runner returned valid output but no guitar notes"

    report: dict[str, Any] = {
        "ok": ok,
        "engine": ENGINE_ID,
        "wav_path": str(stem),
        "instrument": instrument,
        "status": status,
        "reason": reason,
        "require_notes": bool(require_notes),
        "note_count": len(notes),
        "runtime": diagnostics.get("runtime", runtime_status()),
    }
    for key in ("returncode", "stdout_tail", "stderr_tail"):
        if key in diagnostics:
            report[key] = diagnostics[key]
    if include_notes:
        report["notes"] = [_melodic_note_payload(note) for note in notes]
    return report


def transcribe(
    stem_path: Path | str,
    instrument: str = "lead_guitar",
    **_kwargs: Any,
) -> list[MelodicNote]:
    """Transcribe one guitar stem through an external QMUL-compatible command."""
    notes, _diagnostics = _run_runtime(Path(stem_path), instrument=instrument)
    return notes
