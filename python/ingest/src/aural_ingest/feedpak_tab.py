"""Read feedpak/sloppak arrangement tabs (string/fret) as a source of truth.

A feedpak arrangement JSON is Rocksmith-style: ``notes[{t, s, f, sus, ...}]``,
``chords[{t, id, notes[{s, f, sus, ...}]}]`` and ``templates[{name, frets}]``,
with ``s = 0`` the LOWEST-pitched string. This module turns one into a flat,
time-sorted list of *tab notes* -- the authored string/fret position of every
note plus its MIDI pitch -- without losing anything the chart says.

Full difficulty
---------------
When the arrangement has a difficulty ladder (``phrases[].levels``), the full
chart is, for each phrase, the level whose ``difficulty == max_difficulty``.
The top-level ``notes``/``chords`` are then only a sparse summary and are NOT
used. Without a ladder the top-level notes/chords ARE the chart.

Open strings
------------
``tuning`` (manifest entry wins over the JSON's own) is, per the spec, a
semitone offset per string from standard ``E2 A2 D3 G3 B3 E4``; a bass sits an
octave lower (``E1 A1 D2 G2``). Our own ``feedpak_writer`` writes ABSOLUTE MIDI
open strings instead, so an array with any value > 24 is read as absolute.
The spec gives no reference pitches past six strings, so strings beyond the
sixth of an offset array are left unresolved (their notes are dropped).

Capo rule (see arrangement_prep): frets are absolute from the nut; a capo
raises only open strings, ``midi = open[s] + (capo if f == 0 else f)``.
``open_strings_midi`` is the nut tuning, without the capo.

Sustains
--------
* single note: ``t_off = t + max(sus, MIN_NOTE_SEC)``
* chord note:  its own ``sus`` if > 0, else ``min(gap to next onset, 0.25s)``,
  floored at ``MIN_NOTE_SEC``.

Chords expand from their per-chord ``notes[]``; the template's ``frets``
(``-1`` = string not played) fill in only when a chord has no notes.

Pure stdlib (manifest.yaml parsing lazily uses the sidecar's existing PyYAML).
"""

from __future__ import annotations

import json
import zipfile
from bisect import bisect_right
from pathlib import Path, PurePosixPath
from typing import Any, Callable

# Standard 6-string guitar tuning, low-E first: E2 A2 D3 G3 B3 E4.
STANDARD: list[int] = [40, 45, 50, 55, 59, 64]
# Bass open strings are an octave below the guitar's (E1 A1 D2 G2).
BASS_OCTAVE = 12
# Minimum note length so a zero-sustain note is still an audible/visible blip.
MIN_NOTE_SEC = 0.05
# Cap on a chord note's implied duration when no per-note sustain is given.
CHORD_MAX_SUS_SEC = 0.25

# Note technique keys (feedpak spec section 6). A key is kept when truthy --
# except the ones whose "unset" value is -1, which are kept unless -1.
TECHNIQUE_KEYS: tuple[str, ...] = tuple(
    "pm mt fhm ho po tp sl slu bn bt bnv vb tr ac hm hp ln plk slp rh pkd ig fg ch sd".split()
)
_UNSET_IS_MINUS_ONE = frozenset({"sl", "slu", "rh", "pkd", "fg", "ch", "sd"})


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Tuning / capo.
# ---------------------------------------------------------------------------


def is_bass(arr: dict[str, Any], entry: dict[str, Any] | None = None) -> bool:
    """Bass arrangement? The manifest ``type`` decides when present, else id/name."""
    entry = entry or {}
    kind = entry.get("type")
    if isinstance(kind, str) and kind.strip():
        return "bass" in kind.lower()
    names = (entry.get("id"), entry.get("name"), arr.get("name"))
    return any(isinstance(n, str) and "bass" in n.lower() for n in names)


def _setting(arr: dict[str, Any], entry: dict[str, Any] | None, key: str) -> Any:
    """Manifest-level value wins over the arrangement JSON's own (spec)."""
    for src in (entry, arr):
        if isinstance(src, dict) and src.get(key) is not None:
            return src[key]
    return None


def capo_of(arr: dict[str, Any], entry: dict[str, Any] | None = None) -> int:
    return _int(_setting(arr, entry, "capo")) or 0


def open_strings_midi(arr: dict[str, Any], entry: dict[str, Any] | None = None) -> list[int]:
    """Absolute MIDI pitch of each open string (low string first), capo excluded."""
    raw = _setting(arr, entry, "tuning")
    tuning = [_int(x) or 0 for x in raw] if isinstance(raw, list) and raw else [0] * 6
    if any(x > 24 for x in tuning):  # already absolute MIDI (our feedpak_writer)
        return tuning
    base = [p - BASS_OCTAVE for p in STANDARD] if is_bass(arr, entry) else STANDARD
    return [b + off for b, off in zip(base, tuning)]


# ---------------------------------------------------------------------------
# Chart flattening.
# ---------------------------------------------------------------------------


def _hardest_levels(arr: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Each phrase's hardest level, or ``None`` when there is no ladder."""
    hardest: list[dict[str, Any]] = []
    has_ladder = False
    for phrase in _dicts(arr.get("phrases")):
        levels = _dicts(phrase.get("levels"))
        if not levels:
            continue
        has_ladder = True
        want = phrase.get("max_difficulty")
        level = next((lv for lv in levels if lv.get("difficulty") == want), None)
        if level is None:
            level = max(levels, key=lambda lv: _num(lv.get("difficulty")) or 0.0)
        hardest.append(level)
    return hardest if has_ladder else None


def _full_difficulty_list(arr: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """``arr[key]`` of the full chart: from the hardest levels, else top-level."""
    levels = _hardest_levels(arr)
    if levels is None:
        return _dicts(arr.get(key))
    return [item for level in levels for item in _dicts(level.get(key))]


def full_difficulty_events(
    arr: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(notes, chords)`` of the full chart (each phrase's hardest level).

    Falls back to the top-level ``notes``/``chords`` when there is no ladder.
    """
    return _full_difficulty_list(arr, "notes"), _full_difficulty_list(arr, "chords")


def _template_name(template: dict[str, Any]) -> str:
    name = template.get("name") or template.get("displayName")
    return name.strip() if isinstance(name, str) else ""


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [n if (n := _int(x)) is not None else -1 for x in value]


def hand_positions(arr: dict[str, Any]) -> list[dict[str, Any]]:
    """Authored hand positions (anchors) of the full chart, sorted by time.

    Each is ``{t, fret, width}``: the index finger sits at ``fret`` and the hand
    spans ``width`` frets (4 when the chart leaves it out).
    """
    out: list[dict[str, Any]] = []
    for anchor in _full_difficulty_list(arr, "anchors"):
        t = _num(anchor.get("time"))
        fret = _int(anchor.get("fret"))
        if t is None or fret is None:
            continue
        out.append({"t": t, "fret": fret, "width": _int(anchor.get("width")) or 4})
    out.sort(key=lambda a: a["t"])
    return out


def chord_shapes(arr: dict[str, Any]) -> list[dict[str, Any]]:
    """Authored chord shapes held over time (handshapes), sorted by time.

    Each is ``{t, t_end, name, frets, fingers}`` with ``frets``/``fingers``
    lowest string first (``-1`` = string not used / no finger), from the
    handshape's template. Covers arpeggios too, where the notes are singles.
    """
    templates = _dicts(arr.get("templates"))
    out: list[dict[str, Any]] = []
    for shape in _full_difficulty_list(arr, "handshapes"):
        t = _num(shape.get("start_time"))
        tid = _int(shape.get("chord_id"))
        if t is None or tid is None or not 0 <= tid < len(templates):
            continue
        template = templates[tid]
        t_end = _num(shape.get("end_time"))
        out.append(
            {
                "t": t,
                "t_end": t_end if t_end is not None and t_end >= t else t,
                "name": _template_name(template),
                "frets": _int_list(template.get("frets")),
                "fingers": _int_list(template.get("fingers")),
            }
        )
    out.sort(key=lambda h: h["t"])
    return out


def _techniques(note: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in TECHNIQUE_KEYS:
        value = note.get(key)
        if value is None or value is False:
            continue
        if (value != -1) if key in _UNSET_IS_MINUS_ONE else bool(value):
            out[key] = value
    return out


def _tab_note(
    t: float, raw: dict[str, Any], open_midi: list[int], capo: int
) -> dict[str, Any] | None:
    s = _int(raw.get("s"))
    f = _int(raw.get("f"))
    if s is None or f is None or not 0 <= s < len(open_midi) or f < 0:
        return None
    midi = open_midi[s] + (capo if f == 0 else f)
    return {"t": t, "t_off": t, "s": s, "f": f, "midi": midi}


def tab_notes(arr: dict[str, Any], entry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Normalized tab notes of an arrangement's full chart, sorted by (t, s).

    Each note: ``{t, t_off, s, f, midi}`` plus ``chord_id`` / ``chord_name``
    (and ``finger``, from the template, when it names one) when part of a
    chord, plus its present technique keys. Notes on a string
    the tuning doesn't cover, or with a negative fret, are dropped.
    """
    open_midi = open_strings_midi(arr, entry)
    capo = capo_of(arr, entry)
    templates = _dicts(arr.get("templates"))
    singles, chords = full_difficulty_events(arr)

    onsets = sorted({_num(ev.get("t")) for ev in singles + chords} - {None})

    out: list[dict[str, Any]] = []
    for raw in singles:
        t = _num(raw.get("t"))
        note = _tab_note(t, raw, open_midi, capo) if t is not None else None
        if note is None:
            continue
        note["t_off"] = t + max(_num(raw.get("sus")) or 0.0, MIN_NOTE_SEC)
        note.update(_techniques(raw))
        out.append(note)

    for chord in chords:
        t = _num(chord.get("t"))
        if t is None:
            continue
        tid = _int(chord.get("id"))
        template = templates[tid] if tid is not None and 0 <= tid < len(templates) else {}
        name = _template_name(template)
        fingers = _int_list(template.get("fingers"))
        members = _dicts(chord.get("notes"))
        if not members:
            frets = template.get("frets") if isinstance(template.get("frets"), list) else []
            members = [{"s": s, "f": f} for s, f in enumerate(frets)]
        i = bisect_right(onsets, t + 1e-9)
        implied = min(onsets[i] - t, CHORD_MAX_SUS_SEC) if i < len(onsets) else CHORD_MAX_SUS_SEC
        for raw in members:
            note = _tab_note(t, raw, open_midi, capo)
            if note is None:
                continue
            sus = _num(raw.get("sus")) or 0.0
            note["t_off"] = t + max(sus if sus > 0 else implied, MIN_NOTE_SEC)
            if tid is not None:
                note["chord_id"] = tid
            if name:
                note["chord_name"] = name
            if note["s"] < len(fingers) and fingers[note["s"]] > 0:
                note["finger"] = fingers[note["s"]]
            note.update(_techniques(raw))
            out.append(note)

    out.sort(key=lambda n: (n["t"], n["s"]))
    return out


# ---------------------------------------------------------------------------
# Pack reading (directory or zip).
# ---------------------------------------------------------------------------


def read_pack(pack: Path | str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a feedpak/sloppak (directory or zip) -> ``(manifest, arrangements)``.

    Each arrangement is ``{entry, arrangement, notes}``: the manifest entry,
    the wire JSON, and its :func:`tab_notes`. Entries without a readable
    ``file`` (e.g. notation-only) are skipped. A zip may keep the pack under
    one top-level folder.
    """
    path = Path(pack)
    if path.is_dir():

        def read_dir_text(rel: str) -> str | None:
            p = path / rel
            return p.read_text(encoding="utf-8-sig") if p.is_file() else None

        return _read_pack(path, read_dir_text)

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        manifests = sorted((n for n in names if n.split("/")[-1] == "manifest.yaml"), key=len)
        prefix = PurePosixPath(manifests[0]).parent if manifests else PurePosixPath(".")

        def read_zip_text(rel: str) -> str | None:
            name = (prefix / rel).as_posix()
            return zf.read(name).decode("utf-8-sig") if name in names else None

        return _read_pack(path, read_zip_text)


def _read_pack(
    path: Path, read_text: Callable[[str], str | None]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import yaml  # existing sidecar dependency; lazy so the helpers stay stdlib-only

    text = read_text("manifest.yaml")
    manifest = yaml.safe_load(text) if text is not None else None
    if not isinstance(manifest, dict):
        raise ValueError(f"no readable manifest.yaml in {path}")

    arrangements: list[dict[str, Any]] = []
    for entry in _dicts(manifest.get("arrangements")):
        rel = entry.get("file")
        raw = read_text(rel) if isinstance(rel, str) and rel else None
        arr = json.loads(raw) if raw is not None else None
        if isinstance(arr, dict):
            arrangements.append(
                {"entry": entry, "arrangement": arr, "notes": tab_notes(arr, entry)}
            )
    return manifest, arrangements
