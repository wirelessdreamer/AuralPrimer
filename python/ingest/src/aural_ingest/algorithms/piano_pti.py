from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.transcription import MelodicNote


def _checkpoint_path() -> str | None:
    raw = os.environ.get("AURAL_PIANO_PTI_CHECKPOINT", "").strip()
    if raw:
        checkpoint = Path(raw)
        if not checkpoint.is_file():
            raise RuntimeError(f"piano_pti checkpoint not found: {checkpoint}")
        return str(checkpoint)
    if os.environ.get("AURAL_PIANO_PTI_ALLOW_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}:
        return None
    raise RuntimeError(
        "piano_pti requires AURAL_PIANO_PTI_CHECKPOINT or "
        "AURAL_PIANO_PTI_ALLOW_DOWNLOAD=1 for the upstream downloader"
    )


def _device() -> str:
    return os.environ.get("AURAL_PIANO_DEVICE", "cpu").strip() or "cpu"


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    try:
        pti = importlib.import_module("piano_transcription_inference")
        librosa = importlib.import_module("librosa")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "piano_pti requires the optional 'piano_transcription_inference' package in the ingest runtime"
        ) from exc

    checkpoint_path = _checkpoint_path()
    sample_rate = int(getattr(pti, "sample_rate"))
    transcriptor_cls = getattr(pti, "PianoTranscription")
    audio, _sr = librosa.load(path=str(stem_path), sr=sample_rate, mono=True)

    with tempfile.TemporaryDirectory(prefix="aural_pti_") as tmp:
        out_midi = Path(tmp) / "pti.mid"
        transcriptor = transcriptor_cls(device=_device(), checkpoint_path=checkpoint_path)
        transcriptor.transcribe(audio, str(out_midi))
        if not out_midi.is_file():
            raise RuntimeError("piano_pti completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)
