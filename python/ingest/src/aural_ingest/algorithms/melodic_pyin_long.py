"""librosa pYIN with increased frame_length for bass — experiment.

The default frame_length=2048 at 48kHz only covers 42.7ms, which is
less than 2 periods of fmin=30Hz bass. frame_length=4096 (85.3ms)
covers ~2.5 periods and eliminates the fmin warning.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.melodic_librosa_pyin import transcribe as _transcribe_pyin
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    return _transcribe_pyin(
        stem_path, instrument=instrument,
        frame_length=4096,
        hop_length=512,
        **kwargs,
    )
