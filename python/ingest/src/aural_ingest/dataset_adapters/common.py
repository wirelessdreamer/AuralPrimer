"""
Shared types for the ground-truth dataset adapters.

A ``GroundTruthCase`` is the per-file unit a benchmark sweep operates on:
audio path + reference events + metadata to bucket results by (kit, style,
tempo, split, etc.). Adapters convert their source format into this shape
once at the boundary so the benchmark runner stays dataset-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aural_ingest.transcription import DrumEvent, MelodicNote


GroundTruthInstrument = Literal["drums", "guitar", "bass", "keys", "vocals", "melodic"]
GroundTruthScalar = str | int | float | bool | None


@dataclass(frozen=True)
class GroundTruthChordEvent:
    t_on: float
    t_off: float
    label: str
    source: str = ""
    confidence: float | None = None


@dataclass(frozen=True)
class GroundTruthKeyEvent:
    t_on: float
    t_off: float
    key: str
    mode: str
    label: str
    confidence: float | None = None


@dataclass(frozen=True)
class GroundTruthCase:
    """One audio file + its reference events from an annotated corpus.

    Either ``drum_events`` xor ``melodic_notes`` is populated; an
    instrument that doesn't fit one of those families can be added later
    by extending the type. The benchmark runner picks the right scoring
    routine based on which is non-empty.
    """

    case_id: str
    """Stable id within the corpus -- e.g. ``egmd:drummer1/eval_session/1``."""

    instrument: GroundTruthInstrument
    audio_path: Path
    duration_sec: float

    drum_events: tuple[DrumEvent, ...] = ()
    melodic_notes: tuple[MelodicNote, ...] = ()
    melodic_note_metadata: tuple[dict[str, GroundTruthScalar], ...] = ()
    """Optional per-note GT metadata aligned 1:1 with ``melodic_notes``.

    GuitarSet uses this to expose string/fret labels without changing the
    pitch/onset scorer's canonical ``MelodicNote`` contract.
    """

    chord_events: tuple[GroundTruthChordEvent, ...] = ()
    key_events: tuple[GroundTruthKeyEvent, ...] = ()

    metadata: dict[str, str] = field(default_factory=dict)
    """Free-form bucketing keys -- ``split``, ``style``, ``kit``, ``tempo``,
    ``player``, ``signal`` (mic/pickup/DI), etc. Aggregation in the
    benchmark report uses these to break down per-group F1."""

    def has_reference(self) -> bool:
        return bool(self.drum_events) or bool(self.melodic_notes)
