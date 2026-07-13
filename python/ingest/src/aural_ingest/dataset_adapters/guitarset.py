"""
GuitarSet v1.1.0 adapter.

Yields one ``GroundTruthCase`` per WAV file from one of the four audio
archives (mic / pickup-mix / hex-original / hex-debleeded), pairing it
with the per-string ``note_midi`` annotations from the JAMS file with
the same basename.

JAMS layout (one annotation per string, strings ordered low E (0)..high E (5)):

    annotations[]
      - namespace: 'pitch_contour'   x6   (continuous f0 trace)
      - namespace: 'note_midi'       x6   (discretised MIDI note events)
      - namespace: 'beat_position'
      - namespace: 'tempo'
      - namespace: 'chord'           x2  (mireval / pretty_midi labels)
      - namespace: 'key_mode'

We pull only the six ``note_midi`` tracks and flatten them into a single
``MelodicNote`` list. Per-string filtering for the bass adapter is
done by ``yield_low_string_cases`` below.

Reference: https://zenodo.org/records/3371780
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Sequence

from aural_ingest.transcription import MelodicNote

from .common import (
    GroundTruthCase,
    GroundTruthChordEvent,
    GroundTruthInstrument,
    GroundTruthKeyEvent,
    GroundTruthScalar,
)


# String indices in GuitarSet JAMS annotations: 0 = low E (E2), 5 = high E (E4).
# Standard tuning open-string MIDI notes.
GUITARSET_OPEN_STRING_MIDI: tuple[int, ...] = (40, 45, 50, 55, 59, 64)

# Audio variant directory + filename suffix per archive.
_VARIANT_DIRS: dict[str, tuple[str, str]] = {
    "mic":           ("guitarset_mono_mic",         "_mic.wav"),
    "pickup_mix":    ("guitarset_mono_pickup_mix",  "_mix.wav"),
    "hex_original":  ("guitarset_hex_original",     "_hex.wav"),
    "hex_debleeded": ("guitarset_hex_debleeded",    "_hex_cln.wav"),
}


def _parse_jams_note_midi(
    jams_path: Path,
    *,
    string_filter: Sequence[int] | None = None,
) -> tuple[list[MelodicNote], list[int]]:
    """Parse one JAMS file's six ``note_midi`` annotations.

    Returns ``(notes, string_indices)`` where ``string_indices[i]`` is
    the (0-indexed, low-E-first) string number for ``notes[i]``. When
    ``string_filter`` is given, only those strings' notes are kept.
    """
    j = json.loads(jams_path.read_text(encoding="utf-8"))
    notes: list[MelodicNote] = []
    string_indices: list[int] = []

    # We rely on the JAMS convention that the six ``note_midi``
    # annotations appear in low-to-high string order. JAMS allows
    # sub-namespace metadata under ``sandbox`` but GuitarSet does not
    # tag each annotation; the order in the file is the convention.
    note_midi_idx = 0
    for ann in j.get("annotations", []):
        if ann.get("namespace") != "note_midi":
            continue
        string_idx = note_midi_idx
        note_midi_idx += 1
        if string_filter is not None and string_idx not in string_filter:
            continue
        for obs in ann.get("data", []):
            t_on = float(obs["time"])
            t_off = t_on + float(obs["duration"])
            # JAMS stores MIDI as float for microtonal accuracy. We
            # round to the nearest integer; production transcription
            # never emits microtones either.
            pitch = int(round(float(obs["value"])))
            # JAMS confidence is in [0,1]; map to velocity 1..127.
            conf = obs.get("confidence")
            vel = int(max(1, min(127, round(((conf if conf is not None else 1.0) * 127)))))
            notes.append(
                MelodicNote(
                    t_on=t_on,
                    t_off=t_off,
                    pitch=pitch,
                    velocity=vel,
                    instrument="guitar",
                )
            )
            string_indices.append(string_idx)

    # Sort by onset, stable across strings.
    order = sorted(range(len(notes)), key=lambda i: (notes[i].t_on, notes[i].pitch))
    return [notes[i] for i in order], [string_indices[i] for i in order]


def _note_metadata_for_strings(
    notes: Sequence[MelodicNote],
    string_indices: Sequence[int],
) -> tuple[dict[str, GroundTruthScalar], ...]:
    out: list[dict[str, GroundTruthScalar]] = []
    for note, string_idx in zip(notes, string_indices, strict=False):
        open_midi = GUITARSET_OPEN_STRING_MIDI[string_idx]
        out.append({
            "string": string_idx,
            "fret": int(note.pitch) - int(open_midi),
            "open_midi": open_midi,
        })
    return tuple(out)


def _confidence(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_bounds(obs: dict[str, object]) -> tuple[float, float]:
    t_on = float(obs.get("time", 0.0) or 0.0)
    duration = float(obs.get("duration", 0.0) or 0.0)
    return t_on, t_on + duration


def _parse_jams_chords(jams_obj: dict[str, object]) -> tuple[GroundTruthChordEvent, ...]:
    events: list[GroundTruthChordEvent] = []
    chord_idx = 0
    annotations = jams_obj.get("annotations", [])
    if not isinstance(annotations, list):
        return ()
    for ann in annotations:
        if not isinstance(ann, dict) or ann.get("namespace") != "chord":
            continue
        source = "mireval" if chord_idx == 0 else "pretty_midi"
        chord_idx += 1
        data = ann.get("data", [])
        if not isinstance(data, list):
            continue
        for obs in data:
            if not isinstance(obs, dict):
                continue
            label = str(obs.get("value", "")).strip()
            if not label:
                continue
            t_on, t_off = _event_bounds(obs)
            if t_off <= t_on:
                continue
            events.append(
                GroundTruthChordEvent(
                    t_on=t_on,
                    t_off=t_off,
                    label=label,
                    source=source,
                    confidence=_confidence(obs.get("confidence")),
                )
            )
    return tuple(events)


def _parse_jams_keys(jams_obj: dict[str, object]) -> tuple[GroundTruthKeyEvent, ...]:
    events: list[GroundTruthKeyEvent] = []
    annotations = jams_obj.get("annotations", [])
    if not isinstance(annotations, list):
        return ()
    for ann in annotations:
        if not isinstance(ann, dict) or ann.get("namespace") != "key_mode":
            continue
        data = ann.get("data", [])
        if not isinstance(data, list):
            continue
        for obs in data:
            if not isinstance(obs, dict):
                continue
            label = str(obs.get("value", "")).strip()
            if not label:
                continue
            key, _, mode = label.partition(":")
            t_on, t_off = _event_bounds(obs)
            if t_off <= t_on:
                continue
            events.append(
                GroundTruthKeyEvent(
                    t_on=t_on,
                    t_off=t_off,
                    key=key,
                    mode=mode,
                    label=label,
                    confidence=_confidence(obs.get("confidence")),
                )
            )
    return tuple(events)


def _scan_pairs(
    corpus_root: Path,
    *,
    variant: str,
) -> list[tuple[Path, Path]]:
    """Scan ``(jams_path, audio_path)`` pairs available for one variant."""
    if variant not in _VARIANT_DIRS:
        raise ValueError(f"unknown GuitarSet variant: {variant!r}")
    ann_dir = corpus_root / "guitarset_annotations"
    audio_dirname, audio_suffix = _VARIANT_DIRS[variant]
    audio_dir = corpus_root / audio_dirname
    if not ann_dir.is_dir() or not audio_dir.is_dir():
        return []

    out: list[tuple[Path, Path]] = []
    for jams_path in sorted(ann_dir.glob("*.jams")):
        basename = jams_path.stem
        audio_path = audio_dir / f"{basename}{audio_suffix}"
        if audio_path.is_file():
            out.append((jams_path, audio_path))
    return out


def yield_cases(
    corpus_root: Path,
    *,
    variant: str = "mic",
    instrument: GroundTruthInstrument = "guitar",
    string_filter: Sequence[int] | None = None,
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield ``GroundTruthCase``s from GuitarSet.

    Parameters
    ----------
    corpus_root
        ``E:\\AudioSourceOfTruthData\\extracted\\guitarset``.
    variant
        Which audio archive to pair with: ``mic`` (default; acoustic
        mic), ``pickup_mix`` (hex pickup mixed mono), ``hex_original``
        (per-string hex pickup), or ``hex_debleeded`` (per-string after
        bleed reduction).
    instrument
        Label kept on the case for the benchmark report. Defaults to
        ``"guitar"``. ``yield_low_string_cases`` below passes
        ``"bass"`` to drive the bass benchmark off the low-E + A
        strings.
    string_filter
        Restrict to a subset of string indices (0..5; 0 = low E).
        ``None`` keeps all six.
    limit
        Cap on cases emitted.
    """
    pairs = _scan_pairs(corpus_root, variant=variant)
    count = 0
    for jams_path, audio_path in pairs:
        try:
            notes, string_indices = _parse_jams_note_midi(
                jams_path, string_filter=string_filter,
            )
        except Exception:
            continue
        if not notes:
            continue

        # Read duration from JAMS metadata (cheap; we already opened it).
        j = json.loads(jams_path.read_text(encoding="utf-8"))
        duration_sec = float(j.get("file_metadata", {}).get("duration", 0.0) or 0.0)
        note_metadata = _note_metadata_for_strings(notes, string_indices)
        chord_events = _parse_jams_chords(j)
        key_events = _parse_jams_keys(j)

        # Style hint from the file basename, which follows a known
        # naming convention: e.g. "00_BN1-129-Eb_comp.jams" =
        # player 00, BN (Bebop) progression 1, 129 BPM, key Eb, comp/solo.
        meta = _filename_metadata(jams_path.stem)
        meta["variant"] = variant
        meta["string_filter"] = ",".join(str(s) for s in string_filter) if string_filter else "all"

        yield GroundTruthCase(
            case_id=f"guitarset:{jams_path.stem}:{variant}",
            instrument=instrument,
            audio_path=audio_path,
            duration_sec=duration_sec,
            melodic_notes=tuple(notes),
            melodic_note_metadata=note_metadata,
            chord_events=chord_events,
            key_events=key_events,
            metadata=meta,
        )
        count += 1
        if limit is not None and count >= limit:
            return


def yield_low_string_cases(
    corpus_root: Path,
    *,
    variant: str = "hex_debleeded",
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Bass proxy: only the low-E (string 0) and A (string 1) annotations.

    Default variant is ``hex_debleeded`` because the bleed-reduced
    per-string pickup is the closest GuitarSet gets to "isolated bass
    register" audio for benchmarking a bass-tuned transcriber.
    """
    yield from yield_cases(
        corpus_root,
        variant=variant,
        instrument="bass",
        string_filter=(0, 1),
        limit=limit,
    )


def _filename_metadata(basename: str) -> dict[str, str]:
    """Best-effort parse of GuitarSet's basename convention.

    Filenames look like ``00_BN1-129-Eb_comp`` or ``05_Jazz3-89-F_solo``.
    We split into ``player``, ``progression``, ``bpm``, ``key``,
    ``style`` (the ``comp`` / ``solo`` tail).
    """
    parts = basename.split("_")
    meta: dict[str, str] = {}
    if len(parts) >= 1:
        meta["player"] = parts[0]
    if len(parts) >= 3:
        # Middle is e.g. ``BN1-129-Eb``.
        middle = parts[1]
        if "-" in middle:
            prog, bpm, *rest = middle.split("-", 2)
            meta["progression"] = prog
            meta["bpm"] = bpm
            if rest:
                meta["key"] = rest[0]
    if len(parts) >= 3:
        meta["style"] = parts[2]
    return meta
