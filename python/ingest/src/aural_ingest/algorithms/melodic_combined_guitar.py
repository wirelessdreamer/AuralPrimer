"""
``melodic_combined_guitar`` — recall-tuned ``melodic_combined`` for
isolated guitar audio.

The production-tuned ``melodic_combined`` runs onset detection
(``onset_ratio=3.0``) plus a 60 ms minimum note length. Both were
picked to be safe on noisy multi-instrument stems where every spurious
onset is a gameplay false positive.

Against GuitarSet's clean isolated mic + per-string pickup recordings,
these defaults reject ~78% of the actual notes (production
``melodic_combined`` baseline: F1=0.261, P=0.309, R=0.226). The
bottleneck is recall, not precision.

This variant loosens just two knobs:

  onset_ratio: 3.0  -> 2.0   (admit onsets at 2x local mean, not 3x)
  min_note_sec: 0.06 -> 0.04 (catch staccato single-note runs)

Everything else stays the same. Switching between the two is a
configuration choice; the underlying algorithm is identical.
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.melodic_combined import transcribe as _transcribe_combined
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    overrides = {
        "frame_sec": 0.06,
        "hop_sec": 0.02,
        "min_note_sec": 0.04,
        "onset_ratio": 2.0,
    }
    overrides.update(kwargs)
    return _transcribe_combined(stem_path, instrument=instrument, **overrides)
