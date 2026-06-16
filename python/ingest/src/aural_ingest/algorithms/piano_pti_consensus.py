"""Thin module wrapper exposing ``piano_pti.transcribe_consensus`` as a
benchmark-runner-discoverable algorithm.

The ground-truth benchmark runner resolves algorithm IDs to modules via
``importlib.import_module("aural_ingest.algorithms.{id}")``, so for the
stem-vs-mix consensus pipeline to be selectable on the CLI we need a
module with a top-level ``transcribe`` named after it.
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms.piano_pti import transcribe_consensus
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    onset_tolerance_sec: float = 0.10,
    pitch_tolerance_semitones: int = 2,
    **kwargs,
) -> list[MelodicNote]:
    return list(
        transcribe_consensus(
            stem_path,
            instrument=instrument,
            onset_tolerance_sec=onset_tolerance_sec,
            pitch_tolerance_semitones=pitch_tolerance_semitones,
            **kwargs,
        )
    )
