"""YIN-octave + HPS-pitch hybrid with octave correction post-processing.

Chains melodic_yin_octave_hps (YIN for octave, HPS for fine pitch)
with the octave_correct + median_filter from melodic_octave_fix.

The hybrid pitch estimator reduces false positives (better precision),
while the post-processor catches remaining octave errors that both
YIN and HPS agree on (when harmonics genuinely dominate the fundamental).
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.melodic_yin_octave_hps import transcribe as _transcribe_hybrid
from aural_ingest.algorithms.melodic_octave_fix import _octave_correct, _median_filter_pitches
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    """Hybrid pitch estimation with octave correction."""
    notes = _transcribe_hybrid(stem_path, instrument=instrument, **kwargs)

    if not notes:
        return notes

    notes = _octave_correct(notes, instrument)
    notes = _median_filter_pitches(notes, window=5)

    return notes
