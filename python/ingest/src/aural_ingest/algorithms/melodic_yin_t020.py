"""YIN with tuned threshold (0.20) — experiment: higher threshold for cleaner voicing.

The default YIN threshold of 0.15 may be too aggressive, admitting noisy
frames. This variant tests 0.20 for stricter voicing.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.melodic_yin import transcribe as _transcribe_yin
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    return _transcribe_yin(
        stem_path, instrument=instrument,
        yin_threshold=0.20,
        **kwargs,
    )
