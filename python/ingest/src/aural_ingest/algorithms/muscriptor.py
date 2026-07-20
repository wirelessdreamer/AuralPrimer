"""Optional MuScriptor whole-mix multi-instrument transcription adapter.

MuScriptor (Kyutai x Mirelo -- MIT code / **CC-BY-NC-4.0, GATED** weights)
transcribes a full mix into per-instrument notes in a single pass, rather than
per separated stem. Because the HuggingFace weights are gated (the user must
agree to the model's terms to download them) we ship **no weights**: this
adapter is import-safe and completely inert unless the user has
``pip install muscriptor`` and authenticated with HuggingFace. When MuScriptor
is unavailable -- or its weights can't be fetched, or inference errors -- every
entry point returns ``None`` and the caller falls back to the normal per-stem
pipeline. Nothing here imports ``torch`` / ``muscriptor`` at module import
time; the heavy import is deferred to :func:`transcribe_mix`.

Output mapping: MuScriptor labels every note with one of its 35 fixed MT3
instrument-group names (``electric_bass``, ``voice``, ``drums`` ...). We bucket
those into AuralStudio roles (bass / rhythm_guitar / keys / vocals / drums) via
:data:`_DEFAULT_ROLE_MAP` (overridable with ``AURAL_MUSCRIPTOR_ROLE_MAP_JSON``);
melodic groups become :class:`MelodicNote`, the ``drums`` group becomes
:class:`DrumEvent`. MuScriptor emits no velocity, so a constant is fabricated
(consistent with the other non-velocity engines).
"""
from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from aural_ingest.device import select_device
from aural_ingest.transcription import DrumEvent, MelodicNote

ENGINE_ID = "muscriptor"

_SIZE_ENV = "AURAL_MUSCRIPTOR_SIZE"
_DEVICE_ENV = "AURAL_MUSCRIPTOR_DEVICE"
_DISABLED_ENV = "AURAL_MUSCRIPTOR_DISABLED"
_WEIGHTS_ENV = "AURAL_MUSCRIPTOR_WEIGHTS"
_ROLE_MAP_ENV = "AURAL_MUSCRIPTOR_ROLE_MAP_JSON"
_INSTRUMENTS_ENV = "AURAL_MUSCRIPTOR_INSTRUMENTS"

# `large` (5.5 GB of weights) is the default: this is an opt-in engine the user
# deliberately sets up, so accuracy beats download size. Override with
# AURAL_MUSCRIPTOR_SIZE=medium (1.2 GB) or small (0.4 GB).
_DEFAULT_SIZE = "large"
_DEFAULT_VELOCITY = 100  # MuScriptor predicts no velocity; fabricate a constant.
_DRUM_INSTRUMENT = "drums"
_CATCHALL_ROLE = "keys"

# MuScriptor MT3 instrument-group name -> AuralStudio role. The ``drums`` group
# is handled separately (routed to DrumEvent). Any melodic group not listed
# here (strings / brass / sax / woodwind / timpani / orchestra_hit ...) falls
# through to the ``keys`` pitched catch-all so its notes still surface.
_DEFAULT_ROLE_MAP: dict[str, str] = {
    "acoustic_bass": "bass",
    "electric_bass": "bass",
    "contrabass": "bass",
    "acoustic_guitar": "rhythm_guitar",
    "clean_electric_guitar": "rhythm_guitar",
    "distorted_electric_guitar": "rhythm_guitar",
    "acoustic_piano": "keys",
    "electric_piano": "keys",
    "organ": "keys",
    "chromatic_percussion": "keys",
    "orchestral_harp": "keys",
    "synth_lead": "keys",
    "synth_pad": "keys",
    "voice": "vocals",
}


@dataclass
class MuScriptorResult:
    """Whole-mix transcription bucketed into AuralStudio roles."""

    melodic: dict[str, list[MelodicNote]]
    drums: list[DrumEvent]
    meta: dict[str, Any] = field(default_factory=dict)


def _disabled() -> bool:
    return os.environ.get(_DISABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def available() -> bool:
    """Whether MuScriptor can be used: the package is importable and not
    disabled by env. Does NOT import the package (no torch cost)."""
    if _disabled():
        return False
    try:
        return importlib.util.find_spec("muscriptor") is not None
    except Exception:
        return False


def _load_role_map() -> dict[str, str]:
    raw = os.environ.get(_ROLE_MAP_ENV, "").strip()
    merged = dict(_DEFAULT_ROLE_MAP)
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                merged.update({str(k): str(v) for k, v in override.items()})
        except Exception:
            pass  # malformed override -> defaults
    return merged


def role_for_instrument(group: str, role_map: dict[str, str] | None = None) -> str:
    """Map a MuScriptor instrument-group name to an AuralStudio role.

    ``"drums"`` -> ``"drums"``; an unmapped melodic group -> the ``keys``
    catch-all so no transcribed note is silently dropped.
    """
    if group == _DRUM_INSTRUMENT:
        return "drums"
    table = role_map if role_map is not None else _DEFAULT_ROLE_MAP
    return table.get(group, _CATCHALL_ROLE)


def _parse_instruments(raw: str) -> list[str] | None:
    names = [tok.strip() for tok in raw.split(",") if tok.strip()]
    return names or None


def _is_note_start(ev: Any) -> bool:
    # NoteStartEvent: pitch, start_time, index, instrument (no end_time).
    return hasattr(ev, "start_time") and hasattr(ev, "instrument") and not hasattr(ev, "end_time")


def _is_note_end(ev: Any) -> bool:
    # NoteEndEvent: end_time + a reference to its start_event.
    return hasattr(ev, "end_time")


def events_to_role_buckets(
    events: Iterable[Any], role_map: dict[str, str] | None = None
) -> MuScriptorResult:
    """Pair MuScriptor's NoteStart/NoteEnd event stream and bucket into roles.

    Pure logic (no torch / muscriptor import) so it is unit-testable with
    duck-typed fake events. Mirrors MuScriptor's own pairing: a
    ``NoteEndEvent`` carries ``start_event`` (its originating ``NoteStartEvent``)
    and ``end_time``. ``ProgressEvent`` / ``ChunkBoundary`` items are ignored.
    """
    table = role_map if role_map is not None else _DEFAULT_ROLE_MAP
    melodic: dict[str, list[MelodicNote]] = {}
    drums: list[DrumEvent] = []
    open_starts: dict[int, Any] = {}
    group_counts: dict[str, int] = {}

    for ev in events:
        if _is_note_start(ev):
            open_starts[getattr(ev, "index", id(ev))] = ev
            continue
        if not _is_note_end(ev):
            continue  # ProgressEvent / ChunkBoundary / anything else
        start = getattr(ev, "start_event", None)
        if start is None:
            start = open_starts.pop(getattr(ev, "start_event_index", None), None)
        if start is None:
            continue  # unpaired end -> skip defensively
        open_starts.pop(getattr(start, "index", None), None)

        group = str(getattr(start, "instrument", ""))
        group_counts[group] = group_counts.get(group, 0) + 1
        role = role_for_instrument(group, table)
        pitch = int(getattr(start, "pitch"))
        t_on = float(getattr(start, "start_time"))
        t_off = float(getattr(ev, "end_time"))

        if role == "drums":
            drums.append(DrumEvent(time=t_on, note=pitch, velocity=_DEFAULT_VELOCITY))
        else:
            if t_off < t_on:
                t_off = t_on
            melodic.setdefault(role, []).append(
                MelodicNote(
                    t_on=t_on,
                    t_off=t_off,
                    pitch=pitch,
                    velocity=_DEFAULT_VELOCITY,
                    instrument=role,
                )
            )

    for notes in melodic.values():
        notes.sort(key=lambda n: (n.t_on, n.pitch))
    drums.sort(key=lambda d: (d.time, d.note))
    return MuScriptorResult(
        melodic=melodic, drums=drums, meta={"instrument_group_counts": group_counts}
    )


def transcribe_mix(mix_wav_path: Path) -> MuScriptorResult | None:
    """Transcribe a whole mix with MuScriptor, bucketed into AuralStudio roles.

    Returns ``None`` (never raises) when MuScriptor is unavailable, its gated
    weights can't be downloaded, or inference fails -- so the caller cleanly
    falls back to the per-stem pipeline. The ``muscriptor`` / ``torch`` import
    is deferred to here.
    """
    if not available():
        return None
    try:
        from muscriptor import TranscriptionModel  # heavy: pulls torch
    except Exception:
        return None
    try:
        size = os.environ.get(_SIZE_ENV, "").strip() or _DEFAULT_SIZE
        weights = os.environ.get(_WEIGHTS_ENV, "").strip() or size
        device = select_device(_DEVICE_ENV)
        model = TranscriptionModel.load_model(weights, device=device)
        instruments = _parse_instruments(os.environ.get(_INSTRUMENTS_ENV, ""))
        events = model.transcribe(str(mix_wav_path), instruments=instruments)
        result = events_to_role_buckets(events, _load_role_map())
        result.meta.update({"engine": ENGINE_ID, "size": size, "device": str(device)})
        return result
    except Exception:
        # Gated-weight download failure / OOM / inference error -> fall back.
        return None
