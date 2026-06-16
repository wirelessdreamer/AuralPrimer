"""Module wrapper around the production "piano_basic_pitch_playable" method.

Resolves the closure from
``build_default_melodic_algorithm_registry(instrument=...)`` so the
benchmark runner can pick it up by importlib name. This is the actual
production keys default per the ``auto`` fallback chain (first method
after the ``piano_auto`` meta-router).
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.transcription import (
    MelodicNote,
    build_default_melodic_algorithm_registry,
)


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    **kwargs,
) -> list[MelodicNote]:
    registry = build_default_melodic_algorithm_registry(instrument=instrument)
    fn = registry.get("piano_basic_pitch_playable")
    if fn is None:
        return []
    try:
        return list(fn(stem_path))
    except Exception:
        return []
