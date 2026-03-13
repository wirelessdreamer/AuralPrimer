"""Adaptive-frame combined approach — instrument-aware frame sizes.

Uses 80ms frames for bass (need 2+ periods of 25 Hz), 50ms for guitar
(faster transients), and 60ms for keys (compromise). Falls back to
melodic_combined's default 60ms for unknown instruments.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.melodic_combined import transcribe as _transcribe_combined
from aural_ingest.transcription import MelodicNote

# Instrument-specific frame and hop sizes (seconds)
_INSTRUMENT_PARAMS: dict[str, dict[str, float]] = {
    "bass": {"frame_sec": 0.08, "hop_sec": 0.02, "min_note_sec": 0.08},
    "rhythm_guitar": {"frame_sec": 0.05, "hop_sec": 0.015, "min_note_sec": 0.05},
    "lead_guitar": {"frame_sec": 0.05, "hop_sec": 0.015, "min_note_sec": 0.05},
    "keys": {"frame_sec": 0.06, "hop_sec": 0.02, "min_note_sec": 0.06},
    "melodic": {"frame_sec": 0.06, "hop_sec": 0.02, "min_note_sec": 0.06},
}


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    """Combined approach with instrument-adaptive frame sizes."""
    params = _INSTRUMENT_PARAMS.get(instrument, _INSTRUMENT_PARAMS["melodic"])
    # Merge instrument params into kwargs (user overrides take precedence)
    merged = {**params, **kwargs}
    return _transcribe_combined(stem_path, instrument=instrument, **merged)
