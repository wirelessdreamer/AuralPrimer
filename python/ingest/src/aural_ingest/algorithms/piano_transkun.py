from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.transcription import MelodicNote


def _ensure_transkun_available() -> None:
    try:
        importlib.import_module("transkun")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "piano_transkun requires the optional 'transkun' package in the ingest runtime"
        ) from exc


def _device() -> str:
    return os.environ.get("AURAL_PIANO_DEVICE", "cpu").strip() or "cpu"


def _summarize_process_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return f": {detail[:800]}" if detail else ""


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    _ensure_transkun_available()
    with tempfile.TemporaryDirectory(prefix="aural_transkun_") as tmp:
        out_midi = Path(tmp) / "transkun.mid"
        cmd = [
            sys.executable,
            "-m",
            "transkun.transcribe",
            str(stem_path),
            str(out_midi),
            "--device",
            _device(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"piano_transkun failed{_summarize_process_error(result)}")
        if not out_midi.is_file():
            raise RuntimeError("piano_transkun completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)
