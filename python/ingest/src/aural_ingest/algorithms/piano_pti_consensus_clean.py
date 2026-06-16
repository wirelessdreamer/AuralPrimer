"""Module wrapper around the production ``piano_pti_consensus_clean`` method."""

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
    fn = registry.get("piano_pti_consensus_clean")
    if fn is None:
        return []
    try:
        return list(fn(stem_path))
    except Exception:
        return []
