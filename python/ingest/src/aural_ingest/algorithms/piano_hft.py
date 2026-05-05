from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.transcription import MelodicNote


def _configured_command(stem_path: Path, out_midi: Path, checkpoint: Path) -> list[str]:
    raw = os.environ.get("AURAL_PIANO_HFT_COMMAND", "").strip()
    if not raw:
        raise RuntimeError(
            "piano_hft requires AURAL_PIANO_HFT_COMMAND with {audio}, {midi}, and {checkpoint} placeholders"
        )
    rendered = raw.format(audio=str(stem_path), midi=str(out_midi), checkpoint=str(checkpoint))
    return shlex.split(rendered, posix=False)


def _checkpoint_path() -> Path:
    raw = os.environ.get("AURAL_PIANO_HFT_CHECKPOINT", "").strip()
    if not raw:
        raise RuntimeError("piano_hft requires AURAL_PIANO_HFT_CHECKPOINT")
    checkpoint = Path(raw)
    if not checkpoint.is_file():
        raise RuntimeError(f"piano_hft checkpoint not found: {checkpoint}")
    return checkpoint


def _summarize_process_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return f": {detail[:800]}" if detail else ""


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    checkpoint = _checkpoint_path()
    with tempfile.TemporaryDirectory(prefix="aural_hft_") as tmp:
        out_midi = Path(tmp) / "hft.mid"
        cmd = _configured_command(stem_path, out_midi, checkpoint)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"piano_hft failed{_summarize_process_error(result)}")
        if not out_midi.is_file():
            raise RuntimeError("piano_hft completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)
