"""Benchmark adapter for the YourMT3 drum engine.

The ground-truth benchmark imports algorithms as
``aural_ingest.algorithms.<id>`` modules. Production already knows how to run
YourMT3 through ``transcription._transcribe_drums_mt3_events``; this shim only
exposes that path under the benchmark's module contract.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aural_ingest.transcription import DrumEvent


ENGINE_ID = "yourmt3_drums"


def transcribe(stem_path: Path | str, **_kwargs: Any) -> list[DrumEvent]:
    """Transcribe one drum stem with YourMT3.

    Heavy imports and checkpoint resolution happen inside the shared MT3 helper,
    so importing this module stays safe on machines without the modelpack.
    Missing checkpoints are surfaced as RuntimeError, matching drum_crnn's
    benchmark-visible failure contract.
    """
    from aural_ingest.transcription import _transcribe_drums_mt3_events

    try:
        events, _meta = _transcribe_drums_mt3_events(Path(stem_path), ENGINE_ID)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{ENGINE_ID} modelpack/checkpoint unavailable: {exc}") from exc
    return events
