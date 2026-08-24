"""feedpak WRITER — convert an existing ``.auralsong`` pack into a ``.feedpak``.

Stage 1 of the native-pack migration (see ``packages/feedpak/README.md``). This
module is write-only: it reads an existing ``.auralsong`` directory and emits a
conformant ``.feedpak`` directory validated against the vendored feedpak
v1.11.0 JSON Schemas. It does NOT touch the live import pipeline.

Public API
----------
``write_feedpak(auralsong_dir, out_dir) -> dict``
    Read the ``.auralsong`` pack at ``auralsong_dir`` and write a ``.feedpak``
    directory under ``out_dir`` (named ``<song>.feedpak``). Returns a summary
    dict (paths written, arrangements, round-trip note count, ...).

feedpak layout produced
-----------------------
::

    <song>.feedpak/
      manifest.yaml                  # feedpak manifest (validated as JSON)
      song_timeline.json             # beats / sections / tempos / time-sigs
      drum_tab.json                  # only if a drums stem/notes exist
      arrangements/
        notation_<role>.json         # one per melodic stem (e.g. keys)
        tab_<role>.json              # when string/fret fingering exists
      audio/
        stems/<role>.wav             # copied from the .auralsong stems
      aural/                         # our authoring artifacts (aural_* exts)
        notes.mid
        spectrogram/<role>/...
        fingering.<role>.json
        refine_candidates.<role>.json
        benchmark/<role>/...

Note → notation mapping
-----------------------
* Notes come from ``features/notes.mid`` (pretty_midi). Each ``.auralsong``
  stem role is matched to a MIDI instrument by name (the same matching used by
  ``algorithms/piano_midi.py``); marker tracks like ``Structure`` are skipped.
* Measures are derived from ``features/tempo_map.json`` (bpm + time signature)
  with downbeats taken from ``features/beats.json`` when present (``beat == 0``
  rows are bar starts); otherwise a uniform grid from the tempo map is used.
* Pitch + onset are preserved losslessly: every MIDI note becomes a beat
  ``{t, dur, notes:[{midi}]}`` placed in the measure containing its onset, at
  the note's exact onset time ``t``. ``midi`` is the exact MIDI pitch.
* ``dur`` is the note VALUE code from the schema enum ``[1,2,4,8,16,32]`` —
  1 = whole, 2 = half, 4 = quarter, 8 = eighth, 16 = sixteenth, 32 =
  thirty-second. We snap ``(end - start)`` against the local quarter-note
  duration (``60 / bpm``) to the nearest of these values (same buckets as the
  TS ``durationGlyph`` in ``visualizers/viz-tab/src/sheetMusic.ts``). Duration
  is therefore approximate; pitch and onset are exact.
* Piano-family roles split into a grand staff: treble ``G2`` for ``midi >= 60``
  (middle C and up), bass ``F4`` for ``midi < 60``. Other roles use a single
  treble staff. One voice (``v: 1``) per staff.

Drums → drum_tab mapping
------------------------
If a MIDI instrument is a drum track (``is_drum`` or a drums stem role), its GM
drum pitches are mapped to feedpak drum-tab lanes (``p``) and emitted as
``hits: [{t, p, v}]`` (see ``_GM_DRUM_LANES``).
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import pretty_midi
import yaml

FEEDPAK_VERSION = "1.11.0"

# Roles that engrave on a grand (treble+bass) staff. Everything else gets a
# single treble staff.
_PIANO_FAMILY_ROLES = {"keys", "piano", "synth", "melodic"}
_FRETTED_ROLE_TUNINGS: dict[str, list[int]] = {
    "bass": [28, 33, 38, 43],
    "guitar": [40, 45, 50, 55, 59, 64],
    "lead_guitar": [40, 45, 50, 55, 59, 64],
    "rhythm_guitar": [40, 45, 50, 55, 59, 64],
}

# General MIDI percussion key -> feedpak drum-tab lane id. Covers the common GM
# drum map; unmapped pitches fall back to a generic ``"perc"`` lane so no hit is
# lost.
_GM_DRUM_LANES: dict[int, str] = {
    35: "kick",
    36: "kick",
    37: "snare",  # side stick
    38: "snare",
    39: "clap",
    40: "snare",
    41: "tom_low",
    42: "hihat_closed",
    43: "tom_low",
    44: "hihat_pedal",
    45: "tom_mid",
    46: "hihat_open",
    47: "tom_mid",
    48: "tom_high",
    49: "crash",
    50: "tom_high",
    51: "ride",
    52: "crash",
    53: "ride",
    55: "crash",
    57: "crash",
    59: "ride",
}

# feedpak notation ``dur`` enum: note-value codes. 1=whole .. 32=thirty-second.
_DUR_VALUES = (1, 2, 4, 8, 16, 32)

# Krumhansl-Schmuckler key profiles, ported from visualizers/viz-tab/src/index.ts.
_KEY_DETECTION_METHOD = "krumhansl_schmuckler_notes_v1"
_CHORD_DETECTION_METHOD = "measure_note_profile_chords_v1"
_SHARP_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT_PC_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
_CHORD_TEMPLATES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("maj7", (0, 4, 7, 11)),
    ("7", (0, 4, 7, 10)),
    ("min7", (0, 3, 7, 10)),
    ("minmaj7", (0, 3, 7, 11)),
    ("dim7", (0, 3, 6, 9)),
    ("hdim7", (0, 3, 6, 10)),
    ("maj", (0, 4, 7)),
    ("min", (0, 3, 7)),
    ("dim", (0, 3, 6)),
    ("aug", (0, 4, 8)),
    ("sus2", (0, 2, 7)),
    ("sus4", (0, 5, 7)),
)
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

_MAJOR_SIGNATURES: dict[str, dict[str, Any]] = {
    "C": {"pitch_class": 0, "accidental_kind": "natural", "accidentals": []},
    "G": {"pitch_class": 7, "accidental_kind": "sharp", "accidentals": ["F#"]},
    "D": {"pitch_class": 2, "accidental_kind": "sharp", "accidentals": ["F#", "C#"]},
    "A": {"pitch_class": 9, "accidental_kind": "sharp", "accidentals": ["F#", "C#", "G#"]},
    "E": {
        "pitch_class": 4,
        "accidental_kind": "sharp",
        "accidentals": ["F#", "C#", "G#", "D#"],
    },
    "B": {
        "pitch_class": 11,
        "accidental_kind": "sharp",
        "accidentals": ["F#", "C#", "G#", "D#", "A#"],
    },
    "F#": {
        "pitch_class": 6,
        "accidental_kind": "sharp",
        "accidentals": ["F#", "C#", "G#", "D#", "A#", "E#"],
    },
    "F": {"pitch_class": 5, "accidental_kind": "flat", "accidentals": ["Bb"]},
    "Bb": {"pitch_class": 10, "accidental_kind": "flat", "accidentals": ["Bb", "Eb"]},
    "Eb": {"pitch_class": 3, "accidental_kind": "flat", "accidentals": ["Bb", "Eb", "Ab"]},
    "Ab": {
        "pitch_class": 8,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db"],
    },
    "Db": {
        "pitch_class": 1,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db", "Gb"],
    },
    "Gb": {
        "pitch_class": 6,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"],
    },
}

_MINOR_SIGNATURES: dict[str, dict[str, Any]] = {
    "A": {"pitch_class": 9, "accidental_kind": "natural", "accidentals": []},
    "E": {"pitch_class": 4, "accidental_kind": "sharp", "accidentals": ["F#"]},
    "B": {"pitch_class": 11, "accidental_kind": "sharp", "accidentals": ["F#", "C#"]},
    "F#": {"pitch_class": 6, "accidental_kind": "sharp", "accidentals": ["F#", "C#", "G#"]},
    "C#": {
        "pitch_class": 1,
        "accidental_kind": "sharp",
        "accidentals": ["F#", "C#", "G#", "D#"],
    },
    "G#": {
        "pitch_class": 8,
        "accidental_kind": "sharp",
        "accidentals": ["F#", "C#", "G#", "D#", "A#"],
    },
    "D": {"pitch_class": 2, "accidental_kind": "flat", "accidentals": ["Bb"]},
    "G": {"pitch_class": 7, "accidental_kind": "flat", "accidentals": ["Bb", "Eb"]},
    "C": {"pitch_class": 0, "accidental_kind": "flat", "accidentals": ["Bb", "Eb", "Ab"]},
    "F": {
        "pitch_class": 5,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db"],
    },
    "Bb": {
        "pitch_class": 10,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db", "Gb"],
    },
    "Eb": {
        "pitch_class": 3,
        "accidental_kind": "flat",
        "accidentals": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"],
    },
}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _role_matches_instrument(role: str, inst_name: str) -> bool:
    """Match a stem role to a MIDI instrument name.

    Mirrors ``algorithms/piano_midi.py``'s ``track_matches``: piano-family
    roles match key/piano/synth tracks; other roles match by their own name.
    """
    norm_role = (role or "").strip().lower()
    name = (inst_name or "").strip().lower().replace("_", " ")
    if not norm_role:
        return True
    if not name:
        return False
    if norm_role in {"keys", "piano", "synth"}:
        return any(token in name for token in ("key", "piano", "synth"))
    if norm_role in {"drums", "drum"}:
        return any(token in name for token in ("drum", "perc"))
    return norm_role.replace("_", " ") in name


def _quantize_dur(duration_sec: float, quarter_sec: float) -> int:
    """Snap a note duration (seconds) to a feedpak ``dur`` note-value code.

    ``quarter_sec`` is the local quarter-note length (``60 / bpm``). Buckets
    mirror the renderer's ``durationGlyph``: >=3 beats whole, >=1.5 half, >=0.75
    quarter, >=0.375 eighth, >=0.1875 sixteenth, else thirty-second.
    """
    if quarter_sec <= 0:
        return 4
    beats = duration_sec / quarter_sec
    if beats >= 3.0:
        return 1
    if beats >= 1.5:
        return 2
    if beats >= 0.75:
        return 4
    if beats >= 0.375:
        return 8
    if beats >= 0.1875:
        return 16
    return 32


def _parse_time_signature(raw: Any) -> tuple[int, int]:
    """Parse a ``"4/4"`` time-signature string into ``(num, den)``."""
    try:
        num_str, den_str = str(raw).split("/", 1)
        num, den = int(num_str), int(den_str)
        if num >= 1 and den >= 1:
            return num, den
    except (ValueError, AttributeError):
        pass
    return 4, 4


def _tempo_segments(tempo_map: dict[str, Any]) -> list[dict[str, Any]]:
    segments = tempo_map.get("segments") if isinstance(tempo_map, dict) else None
    if not isinstance(segments, list) or not segments:
        return [{"bpm": 120.0, "t0": 0.0, "t1": None, "time_signature": "4/4"}]
    return segments


def _derive_measures(
    tempo_map: dict[str, Any],
    beats: dict[str, Any] | None,
    duration_sec: float,
) -> list[dict[str, Any]]:
    """Build measure starts as ``[{idx, t, ts, tempo}]`` (1-based idx).

    Preference order: explicit downbeats from ``beats.json`` (``beat == 0``
    rows); otherwise a uniform bar grid from the tempo map's bpm + time-sig.
    """
    segments = _tempo_segments(tempo_map)
    first = segments[0]
    num, den = _parse_time_signature(first.get("time_signature", "4/4"))
    bpm = float(first.get("bpm") or 120.0)

    downbeats: list[float] = []
    beat_rows = beats.get("beats") if isinstance(beats, dict) else None
    if isinstance(beat_rows, list):
        for row in beat_rows:
            if isinstance(row, dict) and row.get("beat") == 0 and "t" in row:
                try:
                    downbeats.append(float(row["t"]))
                except (TypeError, ValueError):
                    continue

    if len(downbeats) >= 2:
        downbeats = sorted(set(downbeats))
        if downbeats[0] > 1e-6:
            downbeats.insert(0, 0.0)
    else:
        # Uniform grid from tempo: a bar is ``num`` quarter-equivalent beats.
        beat_sec = 60.0 / bpm if bpm > 0 else 0.5
        bar_sec = beat_sec * num * (4.0 / den)
        downbeats = []
        if bar_sec > 0:
            t = 0.0
            limit = max(duration_sec, bar_sec)
            while t <= limit + bar_sec:
                downbeats.append(round(t, 6))
                t += bar_sec
        else:
            downbeats = [0.0]

    measures: list[dict[str, Any]] = []
    for idx, start in enumerate(downbeats, start=1):
        measure: dict[str, Any] = {"idx": idx, "t": float(start)}
        if idx == 1:
            measure["ts"] = [num, den]
            measure["tempo"] = bpm
        measures.append(measure)
    return measures


def _measure_index_for(onset: float, measures: list[dict[str, Any]]) -> int:
    """Return the 0-based position of the measure containing ``onset``."""
    pos = 0
    for i, measure in enumerate(measures):
        if onset + 1e-9 >= measure["t"]:
            pos = i
        else:
            break
    return pos


def _build_notation(
    inst: pretty_midi.Instrument,
    role: str,
    measures_template: list[dict[str, Any]],
    bpm: float,
) -> dict[str, Any]:
    """Convert a MIDI instrument into a feedpak notation document."""
    grand = role.lower() in _PIANO_FAMILY_ROLES
    if grand:
        staves = [
            {"id": "treble", "clef": "G2"},
            {"id": "bass", "clef": "F4"},
        ]
    else:
        staves = [{"id": "treble", "clef": "G2"}]

    quarter_sec = 60.0 / bpm if bpm > 0 else 0.5

    # Per-measure, per-staff list of beat dicts.
    measure_beats: list[dict[str, list[dict[str, Any]]]] = [
        {s["id"]: [] for s in staves} for _ in measures_template
    ]

    for note in sorted(inst.notes, key=lambda n: (n.start, n.pitch)):
        m_pos = _measure_index_for(float(note.start), measures_template)
        staff_id = "bass" if (grand and note.pitch < 60) else "treble"
        measure_beats[m_pos][staff_id].append(
            {
                "t": float(note.start),
                "dur": _quantize_dur(float(note.end - note.start), quarter_sec),
                "notes": [{"midi": int(note.pitch)}],
            }
        )

    out_measures: list[dict[str, Any]] = []
    for m_idx, template in enumerate(measures_template):
        measure: dict[str, Any] = {"idx": template["idx"], "t": template["t"]}
        if "ts" in template:
            measure["ts"] = template["ts"]
        if "tempo" in template:
            measure["tempo"] = template["tempo"]
        staves_obj: dict[str, Any] = {}
        for staff in staves:
            beats = sorted(measure_beats[m_idx][staff["id"]], key=lambda b: b["t"])
            if beats:
                staves_obj[staff["id"]] = {"voices": [{"v": 1, "beats": beats}]}
        if staves_obj:
            measure["staves"] = staves_obj
        out_measures.append(measure)

    return {
        "version": 1,
        "instrument": role,
        "staves": staves,
        "measures": out_measures,
    }


def _build_placeholder_notation(
    measures_template: list[dict[str, Any]],
    bpm: float,
) -> dict[str, Any]:
    """Build a minimal, schema-valid notation with no notes.

    Used as the arrangement fallback when an import has no derivable melodic
    notes (e.g. the sine demo). Emits a single treble staff and a single empty
    measure (no staves/voices) carrying the song's time signature + tempo so the
    feedpak stays valid and renderable.
    """
    first = measures_template[0] if measures_template else None
    measure: dict[str, Any] = {"idx": 1, "t": 0.0}
    if isinstance(first, dict):
        measure["t"] = float(first.get("t", 0.0))
        if "ts" in first:
            measure["ts"] = first["ts"]
        if "tempo" in first:
            measure["tempo"] = first["tempo"]
    if "ts" not in measure:
        measure["ts"] = [4, 4]
    if "tempo" not in measure:
        measure["tempo"] = float(bpm) if bpm > 0 else 120.0
    return {
        "version": 1,
        "instrument": "keys",
        "staves": [{"id": "treble", "clef": "G2"}],
        "measures": [measure],
    }


def _role_from_fingering_filename(path: Path) -> str:
    # role is the middle component: fingering.<role>.json
    return path.name.split(".")[1] if path.name.count(".") >= 2 else path.stem


def _load_fingering_sidecars(auralsong_dir: Path) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for src in sorted(auralsong_dir.glob("features/fingering.*.json")):
        try:
            raw = _load_json(src)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            docs[_role_from_fingering_filename(src)] = raw
    return docs


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int_in_range(value: Any, lo: int, hi: int) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return out if lo <= out <= hi else None


def _tuning_from_fingering_doc(role: str, doc: dict[str, Any]) -> list[int] | None:
    raw_tuning = doc.get("tuning")
    if isinstance(raw_tuning, list) and raw_tuning:
        tuning: list[int] = []
        for value in raw_tuning:
            parsed = _int_in_range(value, 0, 127)
            if parsed is None:
                return None
            tuning.append(parsed)
        return tuning
    return _FRETTED_ROLE_TUNINGS.get(role.lower())


def _arrangement_type_for_role(role: str) -> str:
    role_lower = role.lower()
    if role_lower == "bass":
        return "bass"
    if role_lower in _FRETTED_ROLE_TUNINGS:
        return "guitar"
    if role_lower in _PIANO_FAMILY_ROLES:
        return "piano"
    return role


def _build_tab_arrangement_from_fingering(role: str, doc: dict[str, Any]) -> dict[str, Any] | None:
    tuning = _tuning_from_fingering_doc(role, doc)
    if not tuning:
        return None
    raw_notes = doc.get("notes")
    if not isinstance(raw_notes, list):
        return None

    notes: list[dict[str, Any]] = []
    for raw in raw_notes:
        if not isinstance(raw, dict):
            continue
        t_on = _finite_float(raw.get("t_on", raw.get("t")))
        if t_on is None or t_on < 0:
            continue
        string_idx = _int_in_range(raw.get("string", raw.get("s")), 0, len(tuning) - 1)
        fret = _int_in_range(raw.get("fret", raw.get("f")), 0, 36)
        if string_idx is None or fret is None:
            continue

        out: dict[str, Any] = {
            "t": round(t_on, 6),
            "s": string_idx,
            "f": fret,
        }
        t_off = _finite_float(raw.get("t_off"))
        if t_off is not None and t_off > t_on:
            out["sus"] = round(t_off - t_on, 6)
        pitch = _int_in_range(raw.get("pitch", raw.get("midi")), 0, 127)
        if pitch is not None:
            out["midi"] = pitch
        velocity = _int_in_range(raw.get("velocity", raw.get("v")), 0, 127)
        if velocity is not None:
            out["v"] = velocity
        notes.append(out)

    if not notes:
        return None
    notes.sort(key=lambda note: (note["t"], note["s"], note["f"], note.get("midi", -1)))
    return {
        "name": role.replace("_", " ").title(),
        "tuning": tuning,
        "notes": notes,
    }


def _build_song_timeline(
    tempo_map: dict[str, Any],
    beats: dict[str, Any] | None,
    sections: dict[str, Any] | None,
) -> dict[str, Any]:
    timeline: dict[str, Any] = {"version": 1}

    tempos: list[dict[str, Any]] = []
    time_sigs: list[dict[str, Any]] = []
    for seg in _tempo_segments(tempo_map):
        try:
            t0 = float(seg.get("t0", 0.0))
        except (TypeError, ValueError):
            t0 = 0.0
        bpm = seg.get("bpm")
        if bpm is not None:
            try:
                tempos.append({"time": t0, "bpm": float(bpm)})
            except (TypeError, ValueError):
                pass
        num, den = _parse_time_signature(seg.get("time_signature", "4/4"))
        time_sigs.append({"time": t0, "ts": [num, den]})
    if tempos:
        timeline["tempos"] = tempos
    if time_sigs:
        timeline["time_signatures"] = time_sigs

    beat_rows = beats.get("beats") if isinstance(beats, dict) else None
    if isinstance(beat_rows, list):
        out_beats: list[dict[str, Any]] = []
        for row in beat_rows:
            if not isinstance(row, dict) or "t" not in row:
                continue
            try:
                out_beats.append(
                    {"time": float(row["t"]), "measure": int(row.get("bar", 0)) + 1}
                )
            except (TypeError, ValueError):
                continue
        if out_beats:
            timeline["beats"] = out_beats

    section_rows = sections.get("sections") if isinstance(sections, dict) else None
    if isinstance(section_rows, list):
        out_sections: list[dict[str, Any]] = []
        for i, row in enumerate(section_rows, start=1):
            if not isinstance(row, dict):
                continue
            try:
                out_sections.append(
                    {
                        "name": str(row.get("label", f"section_{i}")),
                        "number": i,
                        "time": float(row.get("t0", 0.0)),
                    }
                )
            except (TypeError, ValueError):
                continue
        if out_sections:
            timeline["sections"] = out_sections

    return timeline


def _build_drum_tab(inst: pretty_midi.Instrument, name: str) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    used_lanes: set[str] = set()
    for note in sorted(inst.notes, key=lambda n: n.start):
        lane = _GM_DRUM_LANES.get(int(note.pitch), "perc")
        used_lanes.add(lane)
        hit: dict[str, Any] = {"t": float(note.start), "p": lane}
        vel = int(note.velocity)
        if 1 <= vel <= 127:
            hit["v"] = vel
        hits.append(hit)
    return {
        "version": 1,
        "name": name,
        "kit": [{"id": lane} for lane in sorted(used_lanes)],
        "hits": hits,
    }


def _build_vocal_pitch_doc(inst: pretty_midi.Instrument) -> dict[str, Any] | None:
    notes: list[dict[str, Any]] = []
    for note in sorted(inst.notes, key=lambda n: (n.start, n.pitch)):
        start = float(note.start)
        duration = max(0.0, float(note.end) - start)
        if duration <= 0:
            continue
        notes.append(
            {
                "t": round(start, 6),
                "d": round(duration, 6),
                "midi": int(note.pitch),
            }
        )
    if not notes:
        return None
    return {"version": 1, "notes": notes}


def _first_existing_feature(auralsong_dir: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = auralsong_dir / "features" / name
        if candidate.is_file():
            return candidate
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _velocity_to_unit(value: int | float | None) -> float:
    if value is None:
        return 0.7
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.7
    if not math.isfinite(v):
        return 0.7
    if v <= 1.0:
        return _clamp(v, 0.0, 1.0)
    return _clamp(v / 127.0, 0.0, 1.0)


def _cosine_similarity(
    weights: list[float],
    template: tuple[float, ...],
    tonic_pitch_class: int,
) -> float:
    dot = 0.0
    weight_norm = 0.0
    template_norm = 0.0

    for i in range(12):
        w = weights[(i + tonic_pitch_class) % 12]
        t = template[i]
        dot += w * t
        weight_norm += w * w
        template_norm += t * t

    if weight_norm <= 1e-9 or template_norm <= 1e-9:
        return 0.0
    return dot / math.sqrt(weight_norm * template_norm)


def _build_pitch_class_weights(pm: pretty_midi.PrettyMIDI) -> tuple[list[float], int]:
    weights = [0.0] * 12
    note_count = 0
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for note in inst.notes:
            duration = max(0.06, float(note.end) - float(note.start))
            velocity = 0.5 + _velocity_to_unit(int(note.velocity)) * 0.5
            weights[int(note.pitch) % 12] += duration * velocity
            note_count += 1
    return weights, note_count


def _infer_key_signature(pm: pretty_midi.PrettyMIDI) -> dict[str, Any] | None:
    """Infer a song-level key from non-drum MIDI notes.

    This is intentionally deterministic and dependency-free: it mirrors the
    Krumhansl-Schmuckler implementation already used by viz-tab for in-game
    note labeling.
    """
    weights, note_count = _build_pitch_class_weights(pm)
    if note_count == 0 or sum(weights) <= 1e-9:
        return None

    best: dict[str, Any] | None = None
    runner_up_score = float("-inf")

    def consume_candidate(tonic: str, mode: str, signature: dict[str, Any]) -> None:
        nonlocal best, runner_up_score
        pitch_class = int(signature["pitch_class"])
        template = _MAJOR_PROFILE if mode == "major" else _MINOR_PROFILE
        score = _cosine_similarity(weights, template, pitch_class)
        candidate = {
            "key": tonic,
            "tonic": tonic,
            "mode": mode,
            "pitch_class": pitch_class,
            "accidental_kind": signature["accidental_kind"],
            "accidentals": list(signature["accidentals"]),
            "score": score,
            "confidence": 0.0,
            "method": _KEY_DETECTION_METHOD,
            "note_count": note_count,
        }

        if best is None or score > float(best["score"]):
            if best is not None:
                runner_up_score = max(runner_up_score, float(best["score"]))
            best = candidate
        else:
            runner_up_score = max(runner_up_score, score)

    for tonic, signature in _MAJOR_SIGNATURES.items():
        consume_candidate(tonic, "major", signature)
    for tonic, signature in _MINOR_SIGNATURES.items():
        consume_candidate(tonic, "minor", signature)

    if best is None:
        return None

    gap = max(0.0, float(best["score"]) - max(0.0, runner_up_score))
    confidence = _clamp(0.52 + gap * 1.9, 0.52, 0.99)
    best["score"] = round(float(best["score"]), 6)
    best["confidence"] = round(float(confidence), 6)
    return best


def _pc_name(pc: int, accidental_kind: str | None = None) -> str:
    names = _FLAT_PC_NAMES if accidental_kind == "flat" else _SHARP_PC_NAMES
    return names[int(pc) % 12]


def _roman_numeral_for(root_pc: int, quality: str, key_analysis: dict[str, Any] | None) -> str:
    if not key_analysis:
        return ""
    tonic_pc = int(key_analysis.get("pitch_class", 0)) % 12
    mode = str(key_analysis.get("mode", "major")).lower()
    degree = (int(root_pc) - tonic_pc) % 12
    major_map = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV", 6: "bV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
    minor_map = {0: "i", 1: "bII", 2: "ii", 3: "III", 4: "#III", 5: "iv", 6: "bV", 7: "v", 8: "VI", 9: "#VI", 10: "VII", 11: "#VII"}
    numeral = (minor_map if mode == "minor" else major_map).get(degree, "")
    if not numeral:
        return ""
    if quality.startswith("min") and numeral.isupper():
        numeral = numeral.lower()
    elif quality in {"maj", "maj7", "7", "aug", "sus2", "sus4"} and numeral.islower():
        numeral = numeral.upper()
    suffix = {
        "maj7": "maj7",
        "7": "7",
        "min7": "7",
        "minmaj7": "maj7",
        "dim": "dim",
        "dim7": "dim7",
        "hdim7": "m7b5",
        "aug": "aug",
        "sus2": "sus2",
        "sus4": "sus4",
    }.get(quality, "")
    return f"{numeral}{suffix}"


def _note_overlap(start: float, end: float, left: float, right: float) -> float:
    return max(0.0, min(end, right) - max(start, left))


def _pitch_class_weights_for_span(
    pm: pretty_midi.PrettyMIDI,
    start: float,
    end: float,
) -> tuple[list[float], int | None]:
    weights = [0.0] * 12
    lowest_pitch: int | None = None
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for note in inst.notes:
            overlap = _note_overlap(float(note.start), float(note.end), start, end)
            if overlap <= 1e-6:
                continue
            pitch = int(note.pitch)
            weights[pitch % 12] += overlap * (0.5 + _velocity_to_unit(int(note.velocity)) * 0.5)
            if lowest_pitch is None or pitch < lowest_pitch:
                lowest_pitch = pitch
    return weights, lowest_pitch


def _score_chord_template(weights: list[float], root_pc: int, intervals: tuple[int, ...]) -> float:
    total = sum(weights)
    if total <= 1e-9:
        return float("-inf")
    template_pcs = {(root_pc + interval) % 12 for interval in intervals}
    support = sum(weights[pc] for pc in template_pcs)
    extra = total - support
    max_weight = max(weights) if weights else 0.0
    present_floor = max(0.02 * total, 0.08 * max_weight, 1e-9)
    missing = sum(1 for pc in template_pcs if weights[pc] < present_floor)
    return (support / total) - (0.18 * missing) - (0.12 * (extra / total))


def _infer_chord_for_span(
    weights: list[float],
    lowest_pitch: int | None,
    *,
    key_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    total = sum(weights)
    if total <= 1e-9:
        return None
    distinct = sum(1 for value in weights if value >= max(0.04 * total, 1e-9))
    if distinct < 2:
        return None

    best: dict[str, Any] | None = None
    for root_pc in range(12):
        for quality, intervals in _CHORD_TEMPLATES:
            score = _score_chord_template(weights, root_pc, intervals)
            if best is None or score > float(best["score"]):
                best = {
                    "root_pc": root_pc,
                    "quality": quality,
                    "score": score,
                }
    if best is None or float(best["score"]) < 0.28:
        return None

    accidental_kind = str(key_analysis.get("accidental_kind", "")) if key_analysis else ""
    root = _pc_name(int(best["root_pc"]), accidental_kind)
    bass = _pc_name(int(lowest_pitch) % 12, accidental_kind) if lowest_pitch is not None else root
    quality = str(best["quality"])
    return {
        "root": root,
        "quality": quality,
        "bass": bass,
        "rn": _roman_numeral_for(int(best["root_pc"]), quality, key_analysis),
        "score": round(float(best["score"]), 6),
        "confidence": round(_clamp(0.5 + max(0.0, float(best["score"])) * 0.45, 0.5, 0.95), 6),
    }


def _infer_chord_events(
    pm: pretty_midi.PrettyMIDI,
    measures: list[dict[str, Any]],
    duration_sec: float,
    key_analysis: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not measures:
        return []
    starts = [float(measure.get("t", 0.0) or 0.0) for measure in measures]
    if len(starts) >= 2:
        fallback_span = max(0.25, starts[-1] - starts[-2])
    else:
        fallback_span = max(0.25, duration_sec or 4.0)
    events: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else max(duration_sec, start + fallback_span)
        if end <= start + 1e-6:
            continue
        weights, lowest_pitch = _pitch_class_weights_for_span(pm, start, end)
        chord = _infer_chord_for_span(weights, lowest_pitch, key_analysis=key_analysis)
        if chord is None:
            continue
        event = {
            "t": round(start, 6),
            "duration": round(end - start, 6),
            "root": chord["root"],
            "quality": chord["quality"],
            "rn": chord["rn"],
            "bass": chord["bass"],
            "confidence": chord["confidence"],
            "score": chord["score"],
            "method": _CHORD_DETECTION_METHOD,
        }
        if events:
            prev = events[-1]
            if (
                prev.get("root") == event["root"]
                and prev.get("quality") == event["quality"]
                and prev.get("bass") == event["bass"]
            ):
                prev["duration"] = round(float(prev.get("duration", 0.0)) + float(event["duration"]), 6)
                continue
        events.append(event)
    return events


def _build_keys_doc(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "method": analysis["method"],
        "events": [
            {
                "t": 0.0,
                "key": analysis["key"],
                "scale": analysis["mode"],
                "confidence": analysis["confidence"],
                "score": analysis["score"],
            }
        ],
    }


def _build_harmony_doc(analysis: dict[str, Any], chord_events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "key": analysis["key"],
        "mode": analysis["mode"],
        "confidence": analysis["confidence"],
        "method": analysis["method"],
        "chord_method": _CHORD_DETECTION_METHOD,
        "events": chord_events,
    }


def _copy_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def write_feedpak(auralsong_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Write a ``.feedpak`` directory from an existing ``.auralsong`` pack.

    Args:
        auralsong_dir: path to a ``.auralsong`` directory (contains
            ``manifest.json`` and the ``audio/``/``features/`` trees).
        out_dir: directory under which the ``<song>.feedpak`` directory is
            created (created if missing).

    Returns:
        A summary dict with the feedpak path, manifest, arrangements written,
        and the round-trip note count (for tests).
    """
    auralsong_dir = Path(auralsong_dir)
    out_dir = Path(out_dir)
    manifest = _load_json(auralsong_dir / "manifest.json")

    song_stem = auralsong_dir.name
    if song_stem.endswith(".auralsong"):
        song_stem = song_stem[: -len(".auralsong")]
    feedpak_dir = out_dir / f"{song_stem}.feedpak"
    if feedpak_dir.exists():
        if feedpak_dir.is_dir():
            shutil.rmtree(feedpak_dir)
        else:
            feedpak_dir.unlink()
    feedpak_dir.mkdir(parents=True, exist_ok=True)
    (feedpak_dir / "arrangements").mkdir(exist_ok=True)
    (feedpak_dir / "audio" / "stems").mkdir(parents=True, exist_ok=True)
    aural_dir = feedpak_dir / "aural"
    aural_dir.mkdir(exist_ok=True)

    assets = manifest.get("assets", {}) if isinstance(manifest, dict) else {}
    features = assets.get("features", {}) if isinstance(assets, dict) else {}
    audio = assets.get("audio", {}) if isinstance(assets, dict) else {}
    stems_block = audio.get("stems", {}) if isinstance(audio, dict) else {}

    # --- feature files ---------------------------------------------------
    def _load_feature(rel: str | None) -> dict[str, Any] | None:
        if not rel:
            return None
        path = auralsong_dir / rel
        return _load_json(path) if path.exists() else None

    tempo_map = _load_feature(features.get("tempo_map_path")) or {}
    beats = _load_feature(features.get("beats_path"))
    sections = _load_feature(features.get("sections_path"))

    duration = float(manifest.get("duration_sec") or 0.0)
    first_seg = _tempo_segments(tempo_map)[0]
    bpm = float(first_seg.get("bpm") or 120.0)
    measures_template = _derive_measures(tempo_map, beats, duration)

    # --- MIDI ------------------------------------------------------------
    midi_block = assets.get("midi", {}) if isinstance(assets, dict) else {}
    notes_rel = midi_block.get("notes_path")
    pm: pretty_midi.PrettyMIDI | None = None
    if notes_rel and (auralsong_dir / notes_rel).exists():
        pm = pretty_midi.PrettyMIDI(str(auralsong_dir / notes_rel))
    key_analysis = _infer_key_signature(pm) if pm is not None else None
    chord_events = (
        _infer_chord_events(pm, measures_template, duration, key_analysis) if pm is not None else []
    )

    # Determine stem roles present (everything under audio/stems/*; the stems
    # block carries ``<role>_path`` keys plus some non-path metadata keys).
    stem_roles: dict[str, str] = {}
    for key, value in stems_block.items():
        if key.endswith("_path") and isinstance(value, str) and value:
            role = key[: -len("_path")]
            if role.startswith("drum_transcription_source"):
                continue
            stem_roles[role] = value

    # --- stems -----------------------------------------------------------
    stem_entries: list[dict[str, Any]] = []
    for i, (role, rel) in enumerate(sorted(stem_roles.items())):
        src = auralsong_dir / rel
        if not src.exists():
            continue
        ext = src.suffix or ".wav"
        dst_rel = f"audio/stems/{role}{ext}"
        _copy_tree(src, feedpak_dir / dst_rel)
        stem_entries.append({"id": role, "file": dst_rel, "default": i == 0})

    # Stems-only: feedpaks do NOT bundle a combined audio/mix.wav. Players mix
    # the individual stems themselves (the game sums them in its native engine;
    # the Studio mixes them in Web Audio). The decoded full mix stays a
    # working-dir intermediate (demucs input), never shipped in the pack.
    #
    # Sole exception — feedpak requires >=1 stem: if the .auralsong carried no
    # usable separated stem (separation skipped), carry the full mix as a single
    # "mix" stem so every import still yields a schema-valid, stem-based feedpak.
    mix_rel = audio.get("mix_path") if isinstance(audio, dict) else None
    have_mix = isinstance(mix_rel, str) and bool(mix_rel) and (auralsong_dir / mix_rel).exists()
    if not stem_entries and have_mix:
        mix_src = auralsong_dir / mix_rel  # type: ignore[arg-type]
        ext = mix_src.suffix or ".wav"
        dst_rel = f"audio/stems/mix{ext}"
        _copy_tree(mix_src, feedpak_dir / dst_rel)
        stem_entries.append({"id": "mix", "file": dst_rel, "default": True})

    # --- arrangements + notation ----------------------------------------
    arrangement_entries: list[dict[str, Any]] = []
    notation_files: dict[str, dict[str, Any]] = {}
    arrangement_files: dict[str, dict[str, Any]] = {}
    fingering_docs = _load_fingering_sidecars(auralsong_dir)
    drum_tab_doc: dict[str, Any] | None = None
    vocal_pitch_doc: dict[str, Any] | None = None
    roundtrip_notes = 0

    if pm is not None:
        melodic_roles = [r for r in stem_roles if r.lower() not in {"drums", "drum"}]
        if not melodic_roles:
            melodic_roles = ["keys"]
        for role in sorted(melodic_roles):
            inst = next(
                (
                    ins
                    for ins in pm.instruments
                    if not ins.is_drum and _role_matches_instrument(role, ins.name)
                ),
                None,
            )
            if inst is None:
                continue
            notation = _build_notation(inst, role, measures_template, bpm)
            rel = f"arrangements/notation_{role}.json"
            notation_files[rel] = notation
            roundtrip_notes += len(inst.notes)
            arr_type = _arrangement_type_for_role(role)
            entry: dict[str, Any] = {
                "id": role,
                "name": role.replace("_", " ").title(),
                "type": arr_type,
                "notation": rel,
            }
            tab_doc = _build_tab_arrangement_from_fingering(role, fingering_docs.get(role, {}))
            if tab_doc is not None:
                tab_rel = f"arrangements/tab_{role}.json"
                arrangement_files[tab_rel] = tab_doc
                entry["file"] = tab_rel
                entry["tuning"] = tab_doc["tuning"]
            arrangement_entries.append(entry)
            if role.lower() in {"vocals", "vocal"}:
                vocal_pitch_doc = _build_vocal_pitch_doc(inst)

        # Drums: a drum-flagged instrument or a "drums" stem role.
        drum_inst = next((ins for ins in pm.instruments if ins.is_drum), None)
        if drum_inst is None and any(r.lower() in {"drums", "drum"} for r in stem_roles):
            drum_inst = next(
                (
                    ins
                    for ins in pm.instruments
                    if _role_matches_instrument("drums", ins.name)
                ),
                None,
            )
        if drum_inst is not None and drum_inst.notes:
            drum_tab_doc = _build_drum_tab(drum_inst, "drums")

    if not arrangement_entries:
        # feedpak requires >=1 arrangement. A pack with no derivable melodic
        # notes (e.g. the sine demo, or a no-matching-instrument MIDI) still has
        # to yield a valid feedpak, so emit a minimal placeholder notation:
        # one empty treble staff with a single empty measure.
        placeholder = _build_placeholder_notation(measures_template, bpm)
        rel = "arrangements/notation_keys.json"
        notation_files[rel] = placeholder
        arrangement_entries.append(
            {
                "id": "keys",
                "name": "Keys",
                "type": "piano",
                "notation": rel,
            }
        )

    # --- write notation + side files ------------------------------------
    for rel, doc in notation_files.items():
        (feedpak_dir / rel).write_text(
            json.dumps(doc, indent=2), encoding="utf-8"
        )
    for rel, doc in arrangement_files.items():
        (feedpak_dir / rel).write_text(
            json.dumps(doc, indent=2), encoding="utf-8"
        )

    song_timeline = _build_song_timeline(tempo_map, beats, sections)
    (feedpak_dir / "song_timeline.json").write_text(
        json.dumps(song_timeline, indent=2), encoding="utf-8"
    )

    if key_analysis is not None:
        (feedpak_dir / "keys.json").write_text(
            json.dumps(_build_keys_doc(key_analysis), indent=2), encoding="utf-8"
        )
        (feedpak_dir / "harmony.json").write_text(
            json.dumps(_build_harmony_doc(key_analysis, chord_events), indent=2), encoding="utf-8"
        )

    if drum_tab_doc is not None:
        # Refine the hit times onto the real drums-stem transients — mr_mt3's
        # onsets wobble by tens of ms, which reads as markers off the energy in
        # the cleanup spectrogram. Best-effort + never fatal (degrades to the
        # transcriber's times if audio/librosa are unavailable).
        drums_stem = feedpak_dir / "audio" / "stems" / "drums.wav"
        if drums_stem.is_file():
            try:
                from aural_ingest.drum_onset_align import align_drum_tab_to_onsets

                drum_tab_doc, _ = align_drum_tab_to_onsets(drum_tab_doc, drums_stem)
            except Exception:  # noqa: BLE001 — alignment must never break import
                pass
        (feedpak_dir / "drum_tab.json").write_text(
            json.dumps(drum_tab_doc, indent=2), encoding="utf-8"
        )

    vocal_pitch_rel: str | None = None
    vocal_pitch_src = _first_existing_feature(auralsong_dir, ("vocal_pitch.json",))
    if vocal_pitch_src is not None:
        vocal_pitch_rel = "vocal_pitch.json"
        _copy_tree(vocal_pitch_src, feedpak_dir / vocal_pitch_rel)
    elif vocal_pitch_doc is not None:
        vocal_pitch_rel = "vocal_pitch.json"
        (feedpak_dir / vocal_pitch_rel).write_text(
            json.dumps(vocal_pitch_doc, indent=2), encoding="utf-8"
        )

    vocal_pitch_contour_rel: str | None = None
    vocal_pitch_contour_src = _first_existing_feature(
        auralsong_dir,
        ("vocal_pitch_contour.json", "pitch_contour.json"),
    )
    if vocal_pitch_contour_src is not None:
        vocal_pitch_contour_rel = "vocal_pitch_contour.json"
        _copy_tree(vocal_pitch_contour_src, feedpak_dir / vocal_pitch_contour_rel)

    # --- aural_* artifacts (copied + referenced) ------------------------
    aural_ext: dict[str, Any] = {}

    if notes_rel and (auralsong_dir / notes_rel).exists():
        _copy_tree(auralsong_dir / notes_rel, aural_dir / "notes.mid")
        aural_ext["aural_notes_mid"] = "aural/notes.mid"

    # spectrogram lives at features/spectrogram (per role); copy the whole dir.
    spectro_src = auralsong_dir / "features" / "spectrogram"
    if spectro_src.exists():
        _copy_tree(spectro_src, aural_dir / "spectrogram")
        aural_ext["aural_spectrogram"] = "aural/spectrogram"

    # refine_candidates.<role>.json files.
    refine_map: dict[str, str] = {}
    for src in sorted(auralsong_dir.glob("features/refine_candidates.*.json")):
        dst_rel = f"aural/{src.name}"
        _copy_tree(src, feedpak_dir / dst_rel)
        # role is the middle component: refine_candidates.<role>.json
        role = src.name.split(".")[1] if src.name.count(".") >= 2 else src.stem
        refine_map[role] = dst_rel
    if refine_map:
        aural_ext["aural_refine_candidates"] = refine_map

    # fingering.<role>.json files carry string/fret note metadata that cannot
    # be represented in MIDI. Runtime merges these back onto notes.mid tracks.
    fingering_map: dict[str, str] = {}
    for src in sorted(auralsong_dir.glob("features/fingering.*.json")):
        dst_rel = f"aural/{src.name}"
        _copy_tree(src, feedpak_dir / dst_rel)
        role = src.name.split(".")[1] if src.name.count(".") >= 2 else src.stem
        fingering_map[role] = dst_rel
    if fingering_map:
        aural_ext["aural_fingering"] = fingering_map

    benchmark_src = auralsong_dir / "features" / "benchmark"
    if benchmark_src.exists():
        _copy_tree(benchmark_src, aural_dir / "benchmark")
        aural_ext["aural_benchmark"] = "aural/benchmark"

    if isinstance(manifest.get("pipeline"), dict):
        aural_ext["aural_pipeline"] = manifest["pipeline"]

    # --- manifest --------------------------------------------------------
    title = manifest.get("title") or song_stem
    artist = manifest.get("artist") or "Unknown"
    fp_manifest: dict[str, Any] = {
        "feedpak_version": FEEDPAK_VERSION,
        "title": str(title),
        "artist": str(artist),
        "duration": duration,
        "arrangements": arrangement_entries,
        "stems": stem_entries,
        "song_timeline": "song_timeline.json",
    }
    # Only when the song actually has one. An empty string would filter as a
    # real genre named "" and quietly collect every untagged song under it.
    genre = (manifest.get("genre") or "").strip()
    if genre:
        fp_manifest["genre"] = genre
    if key_analysis is not None:
        fp_manifest["keys"] = "keys.json"
        fp_manifest["harmony"] = "harmony.json"
        # AuralPrimer compatibility extension: current HUD fallback reads
        # top-level key/mode from manifest_raw. The spec-defined `keys` and
        # `harmony` fields remain relpath pointers above.
        fp_manifest["key"] = key_analysis["key"]
        fp_manifest["mode"] = key_analysis["mode"]
    if drum_tab_doc is not None:
        fp_manifest["drum_tab"] = "drum_tab.json"
    if vocal_pitch_rel is not None:
        fp_manifest["vocal_pitch"] = vocal_pitch_rel
    if vocal_pitch_contour_rel is not None:
        fp_manifest["vocal_pitch_contour"] = vocal_pitch_contour_rel
    if vocal_pitch_rel is not None or vocal_pitch_contour_rel is not None:
        transcription = manifest.get("pipeline", {}).get("transcription", {})
        method = "vocal_notes"
        if isinstance(transcription, dict):
            by_instrument = transcription.get("instrument_melodic_methods_used")
            if isinstance(by_instrument, dict) and isinstance(by_instrument.get("vocals"), str):
                method = by_instrument["vocals"]
            elif isinstance(transcription.get("melodic_method_used"), str):
                method = transcription["melodic_method_used"]
        fp_manifest["pitch_extraction"] = {
            "engine": "aural_ingest",
            "model": str(method),
            "version": "1.0.0",
        }
    fp_manifest.update(aural_ext)

    (feedpak_dir / "manifest.yaml").write_text(
        yaml.safe_dump(fp_manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "feedpak_dir": feedpak_dir,
        "manifest": fp_manifest,
        "arrangements": [a["id"] for a in arrangement_entries],
        "notation_files": list(notation_files),
        "has_drum_tab": drum_tab_doc is not None,
        "roundtrip_notes": roundtrip_notes,
        "stems": [s["id"] for s in stem_entries],
    }
