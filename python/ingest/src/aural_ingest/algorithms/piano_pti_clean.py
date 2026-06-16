"""Module wrapper: piano_pti + piano_cleanup post-processing.

Same shape as ``piano_basic_pitch_playable`` -- routes through the
production registry to pick up the cleanup pipeline applied by the
``_piano_pti_clean`` closure.
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
    fn = registry.get("piano_pti_clean")
    if fn is None:
        return []
    try:
        return list(fn(stem_path))
    except Exception:
        return []
