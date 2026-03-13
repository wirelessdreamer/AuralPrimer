"""YIN with large frame (80ms) optimized for bass — experiment.

Bass fundamentals down to 30 Hz need at least 2 full periods in a frame.
At 48kHz, 80ms = 3840 samples, covering ~2 periods of 25 Hz.
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
        frame_sec=0.08,
        hop_sec=0.02,
        yin_threshold=0.15,
        **kwargs,
    )
