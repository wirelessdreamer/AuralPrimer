"""
Adapters that turn external annotated audio datasets into the same
canonical event types the AuralPrimer ingest pipeline already consumes.

Each adapter exposes a single iterator:

    yield_cases(corpus_root, split=...) -> Iterable[GroundTruthCase]

so the ground-truth benchmark runner can sweep an arbitrary set of
transcription algorithms across a dataset without any per-dataset
plumbing inside the runner itself.

Supported datasets (corpora prepared under
``E:\\AudioSourceOfTruthData`` per docs/data-prep-status.md):

  - ``egmd``         Expanded Groove MIDI Dataset v1.0.0 -- drums
  - ``guitarset``   GuitarSet v1.1.0 -- acoustic guitar
  - ``guitar_techs`` Guitar-TECHS v1 -- electric guitar
  - ``maestro``      MAESTRO v3.0.0 -- real acoustic piano (keys)
  - ``mir_st500``    MIR-ST500 -- separated lead vocals
"""

from .common import (
    GroundTruthCase,
    GroundTruthChordEvent,
    GroundTruthInstrument,
    GroundTruthKeyEvent,
    GroundTruthScalar,
)

__all__ = [
    "GroundTruthCase",
    "GroundTruthChordEvent",
    "GroundTruthInstrument",
    "GroundTruthKeyEvent",
    "GroundTruthScalar",
]
