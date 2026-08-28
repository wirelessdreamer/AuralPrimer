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

    # Starts that never received an end.
    #
    # MuScriptor emits NoteStart/NoteEnd as a stream and processes the mix in
    # chunks, so a note running across a chunk boundary can lose its end. Those
    # starts were dropped in silence -- the model found a note and we discarded
    # it -- which matches the reported symptom: bars where the transcription
    # goes empty while the piano is plainly still playing. Measured on Center,
    # 19 gaps over 2s totalling 67s, with the keys stem at -30 dB through them
    # against a -37.5 dB song average.
    #
    # Counted, not silently recovered. Whether these are real notes or noise
    # decides whether recovering them would help, and that is a question for the
    # next run's numbers rather than for a guess here.
    if open_starts:
        group_counts["_unpaired_starts"] = len(open_starts)

    for notes in melodic.values():
        notes.sort(key=lambda n: (n.t_on, n.pitch))
    drums.sort(key=lambda d: (d.time, d.note))
    return MuScriptorResult(
        melodic=melodic, drums=drums, meta={"instrument_group_counts": group_counts}
    )


#: Stem name -> the MuScriptor instrument groups that stem can produce. Used to
#: turn "which stems have sound in them" into a conditioning list.
#: Deliberately narrow. Conditioning works by ruling things OUT, so naming
#: every group a stem could conceivably contain -- organ, contrabass, three
#: flavours of electric guitar -- says almost nothing and the model goes back
#: to guessing. One or two plausible groups per stem is what was measured to
#: move the attribution.
_STEM_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "keys": ("acoustic_piano", "electric_piano", "synth_pad"),
    "guitar": ("acoustic_guitar", "clean_electric_guitar"),
    "rhythm_guitar": ("acoustic_guitar",),
    "lead_guitar": ("clean_electric_guitar",),
    "bass": ("electric_bass",),
    "drums": ("drums",),
    "vocals": ("voice",),
}

#: A stem this quiet at its loudest is separation residue, not a part. Measured
#: on the packs: a stem carrying a real part sits at -28 to -38 dBFS, one the
#: separator emptied sits at -72 to -78, and nothing lives in between.
_STEM_PRESENT_DBFS = -50.0


def instruments_from_stems(stems_dir: Path) -> list[str] | None:
    """Which instrument groups to ask MuScriptor for, from the separated stems.

    Left to itself the model decides what it is hearing, and on a dense mix it
    decides badly: on one worship track it labelled 5271 of ~6500 notes
    acoustic_piano and found no guitar at all in a minute of music that plainly
    has a guitar in it. Told the true instrument set over the same minute it
    found 466 guitar notes and dropped keys from 398 to 253.

    We already separate stems, so we already know what is in the song -- a stem
    with sound in it is an instrument that is present. That makes the answer
    free rather than something to ask the user for, and it is measured from
    this recording rather than assumed from a genre.

    Returns ``None`` when nothing can be measured, which is the same as not
    conditioning at all: the model keeps its current behaviour rather than
    being handed an empty list and told the song is silent.
    """
    try:
        import numpy as np
        import soundfile as sf
    except Exception:
        return None

    wanted: list[str] = []
    for stem, groups in _STEM_INSTRUMENTS.items():
        path = stems_dir / f"{stem}.wav"
        if not path.is_file():
            continue
        try:
            data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        except Exception:
            continue
        if data.size == 0 or rate <= 0:
            continue
        mono = data.mean(axis=1)
        # Loudest second, not the average: an instrument that plays for one
        # section of the song is still in the song, and averaging over a
        # ten-minute track buries it under the silence around it.
        frame = int(rate)
        usable = (len(mono) // frame) * frame
        if usable < frame:
            continue
        blocks = mono[:usable].reshape(-1, frame)
        rms = np.sqrt(np.mean(np.square(blocks), axis=1))
        peak_db = 20.0 * np.log10(max(float(np.percentile(rms, 95)), 1e-9))
        if peak_db > _STEM_PRESENT_DBFS:
            wanted.extend(groups)

    if not wanted:
        return None
    # Deduplicated, order preserved: guitar stems overlap, and the split
    # rhythm/lead stems name groups the combined one already named.
    seen: set[str] = set()
    return [g for g in wanted if not (g in seen or seen.add(g))]


def transcribe_mix(
    mix_wav_path: Path, instruments: list[str] | None = None
) -> MuScriptorResult | None:
    """Transcribe a whole mix with MuScriptor, bucketed into AuralStudio roles.

    ``instruments`` conditions the model on the groups actually present. The
    upstream paper offers conditioning as its one mitigation for unstable
    instrument assignment, and it is what stops a strummed guitar arriving in
    the keys part. An explicit argument wins over the environment variable, so
    a caller that has measured the song beats a global default.

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
        conditioning = instruments or _parse_instruments(
            os.environ.get(_INSTRUMENTS_ENV, "")
        )
        events = model.transcribe(str(mix_wav_path), instruments=conditioning)
        result = events_to_role_buckets(events, _load_role_map())
        result.meta.update({
            "engine": ENGINE_ID,
            "size": size,
            "device": str(device),
            # Recorded because it changes the result: a pack transcribed with
            # conditioning and one without are not comparable, and from the
            # outside they look identical.
            "instruments_conditioned": list(conditioning) if conditioning else None,
        })
        return result
    except Exception:
        # Gated-weight download failure / OOM / inference error -> fall back.
        return None
