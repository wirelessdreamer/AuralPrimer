"""Convert sloppak/feedpak arrangement wire JSONs into game-ready artifacts.

The Slopsmith (``.sloppak``) song format ships melodic parts as Rocksmith-style
string/fret wire JSONs (one per arrangement, listed in ``manifest.yaml``'s
``arrangements[].file``). AuralPrimer's game charts melodic notes from
``aural/notes.mid`` (apps/game/src/chartLoader.ts). This module bridges the two:
it reads the arrangement wire JSONs and writes

  * ``aural/notes.mid``       — pretty_midi, one named Instrument per melodic
                                role in the exact layout the game requires.
  * ``aural/fingering.<role>.json`` — for bass / guitar roles, the AUTHORED
                                string/fret of every note (aural-fingering
                                schema), t_on snapped to the notes.mid tick
                                grid so the game's merge keys match.
  * ``song_timeline.json``    — beats / sections / tempos / time_signatures,
                                matching the feedpak_writer shape.

and stamps the corresponding ``aural_notes_mid`` / ``aural_fingering`` /
``song_timeline`` / ``drum_tab`` keys into the manifest (order-preserving).
Drums are NOT charted here — the game charts drums from the pack-root
``drum_tab.json`` shared with Slopsmith.

The tab itself is read by ``feedpak_tab``: the full chart (each phrase's
hardest difficulty level, not the sparse top-level notes), open strings from
the tuning (offsets from standard, a bass an octave lower, or absolute MIDI),
chord expansion and sustains.

Pitch / capo rule (RISK R1 — CONFIRMED)
---------------------------------------
Standard tuning base MIDI (string 0 = low E to string 5 = high E)::

    STANDARD = [40, 45, 50, 55, 59, 64]   # E2 A2 D3 G3 B3 E4

A note is ``{s: string_index, f: fret, sus: seconds}``; tuning is a per-string
semitone offset array; capo is an integer fret. The MIDI pitch is::

    midi = open[s] + (capo if f == 0 else f)
    open[s] = STANDARD[s] + tuning[s]          # guitar
    open[s] = STANDARD[s] - 12 + tuning[s]     # bass (E1 A1 D2 G2)

i.e. a capo raises the OPEN string (``f == 0``) to the capo fret, while a
fretted note (``f > 0``) sounds at its absolute fret-from-nut regardless of the
capo (you cannot fret behind the capo). This is the standard Rocksmith reading:
the sloppak spec defines ``f`` as ``0 = open, 24 = max`` (absolute, from the
nut) and says the capo overrides embedded values but gives no MIDI formula.

VERIFIED against Slopsmith's own source (github.com/mikey0000/slopsmith): its
renderer (``static/highway.js``) draws frets visually and it retunes audio via
a pitch-shift factor (``lib/retune.py``) — it NEVER converts fret/capo to a
MIDI number. There is therefore no upstream MIDI formula to adopt; this module
defines the canonical AuralPrimer reading (absolute fret + capo-raises-open),
consistent with the fixture's capo test note (open string under capo 2 sounds
2 semitones higher).

Chords
------
A chord is ``{t, id, notes: [...]}``. Its per-chord ``notes[]`` are the
played strings; when empty, ``id`` indexes ``templates[]`` and the template's
``frets`` array (``-1`` = skip string) is expanded instead. Chord note-off =
per-note ``sus`` if given, else ``min(gap to next event, 0.25s)``.

Technique flags (``pm``/``ho``/``po``/...) are ignored for MIDI. ``ig``
(ignore/muted) notes are still charted.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .feedpak_tab import (
    MIN_NOTE_SEC,
    TECHNIQUE_KEYS,
    chord_shapes,
    hand_positions,
    open_strings_midi,
    tab_notes,
)
from .pack_paths import load_pack_manifest, pack_feature_dirname, update_manifest_keys

if TYPE_CHECKING:  # pragma: no cover — type-only; avoids a hard runtime dep.
    import pretty_midi


# Single constant tempo for the emitted MIDI. Note times are absolute seconds
# (the game reads seconds via its own tempo map); this only sets the tick grid.
CONSTANT_BPM = 120.0

# CONTRACT C3: melodic role -> (track name, insertion order). Instruments are
# inserted in this order so pretty_midi assigns program/channels 0..5 (skipping
# the drum channel 9) and the game's CHANNEL_TO_ROLE fallback lines up, while
# the track NAME is the primary detection key.
ROLE_TRACK_NAME: dict[str, str] = {
    "bass": "Bass",
    "rhythm_guitar": "Rhythm Guitar",
    "lead_guitar": "Lead Guitar",
    "keys": "Keys",
    "melodic": "Melodic",
    "vocals": "Vocals",
}
ROLE_ORDER: list[str] = ["bass", "rhythm_guitar", "lead_guitar", "keys", "melodic", "vocals"]

# Fretted roles that get an authored fingering.<role>.json sidecar.
FINGERING_ROLES: tuple[str, ...] = ("bass", "rhythm_guitar", "lead_guitar")


# ---------------------------------------------------------------------------
# Pure note helpers (no pretty_midi needed — unit-testable directly).
# ---------------------------------------------------------------------------


def build_role_notes(
    arr: dict[str, Any], manifest_entry: dict[str, Any] | None
) -> list[tuple[float, float, int]]:
    """Flatten an arrangement's full chart to ``(t_on, t_off, midi)`` triples.

    Pure — no pretty_midi. A pitch-only view of ``feedpak_tab.tab_notes``
    (full-difficulty chart, tuning / bass octave / capo, chord expansion):
      * single note: ``t_off = t + max(sus, MIN_NOTE_SEC)``.
      * chord note: ``t_off = t + (its own sus if given else
        min(gap-to-next, 0.25))``, floored at ``MIN_NOTE_SEC``.
    Technique flags are ignored; ``ig`` notes are still emitted.
    """
    out = [(n["t"], n["t_off"], n["midi"]) for n in tab_notes(arr, manifest_entry)]
    out.sort(key=lambda x: (x[0], x[2]))
    return out


# ---------------------------------------------------------------------------
# Role mapping — assign each arrangement to at most one melodic role.
# ---------------------------------------------------------------------------


def _arrangement_kind(entry: dict[str, Any], arr: dict[str, Any]) -> str:
    """Best-effort lowercase kind for role mapping, from type/id/name.

    A generic ``type: guitar`` does not say lead or rhythm, so the id/name
    decide then (feedpak packs put the part there: ``lead``, ``rhythm-2``).
    """
    found: list[str] = []
    for v in (*(entry.get(k) for k in ("type", "id", "name")), arr.get("name")):
        if isinstance(v, str) and v.strip():
            found.append(v.strip().lower())
    for kind in found:
        if kind != "guitar":
            return kind
    return found[0] if found else ""


def assign_roles(
    arrangements: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Assign each ``(manifest_entry, wire_json)`` to a melodic role (first-wins).

    Mapping (no merging; the first arrangement claiming a role keeps it):
      * ``bass``                  -> ``bass``
      * ``rhythm*``               -> ``rhythm_guitar``
      * ``lead*``                 -> ``lead_guitar``
      * ``piano`` | ``keys``      -> ``keys``
      * ``combo``                 -> first empty of lead_guitar/rhythm_guitar,
                                     else ``melodic``
      * ``vocals``                -> ``vocals``
      * anything else             -> first empty of lead/rhythm, else ``melodic``

    Returns ``[(role, manifest_entry, wire_json)]`` in arrangement order.
    Roles already taken are skipped (the arrangement is dropped rather than
    merged).
    """
    taken: set[str] = set()
    result: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def _first_empty(*roles: str) -> str | None:
        for r in roles:
            if r not in taken:
                return r
        return None

    for entry, arr in arrangements:
        kind = _arrangement_kind(entry, arr)
        role: str | None = None
        if "vocal" in kind:
            role = "vocals"
        elif "bass" in kind:
            role = "bass"
        elif "rhythm" in kind:
            role = "rhythm_guitar"
        elif "lead" in kind:
            role = "lead_guitar"
        elif "piano" in kind or "key" in kind:
            role = "keys"
        elif "combo" in kind:
            role = _first_empty("lead_guitar", "rhythm_guitar", "melodic")
        else:
            role = _first_empty("lead_guitar", "rhythm_guitar", "melodic")

        if role is None or role in taken:
            continue
        taken.add(role)
        result.append((role, entry, arr))

    return result


# ---------------------------------------------------------------------------
# MIDI assembly.
# ---------------------------------------------------------------------------


def build_notes_mid(
    assigned: list[tuple[str, dict[str, Any], dict[str, Any]]]
) -> pretty_midi.PrettyMIDI | None:
    """Build a PrettyMIDI with one named Instrument per melodic role (CONTRACT C3).

    Instruments are inserted in ROLE_ORDER (bass, rhythm_guitar, lead_guitar,
    keys, melodic, vocals) — only for roles present in ``assigned`` — each
    named "Bass"/"Rhythm Guitar"/"Lead Guitar"/"Keys"/"Melodic"/"Vocals".
    No drum track; single constant tempo. Returns ``None`` when nothing
    melodic is present.
    """
    import pretty_midi  # lazy: keeps the pure helpers importable without it.

    by_role = {role: (entry, arr) for role, entry, arr in assigned}
    if not by_role:
        return None

    pm = pretty_midi.PrettyMIDI(initial_tempo=CONSTANT_BPM)
    any_notes = False
    for role in ROLE_ORDER:
        if role not in by_role:
            continue
        entry, arr = by_role[role]
        inst = pretty_midi.Instrument(program=0, is_drum=False, name=ROLE_TRACK_NAME[role])
        for t_on, t_off, midi in build_role_notes(arr, entry):
            if not (0 <= midi <= 127):
                continue
            end = max(t_off, t_on + MIN_NOTE_SEC)
            inst.notes.append(
                pretty_midi.Note(velocity=100, pitch=int(midi), start=float(t_on), end=float(end))
            )
            any_notes = True
        pm.instruments.append(inst)

    if not any_notes:
        return None
    return pm


def _snap_to_midi_grid(times: list[float]) -> list[float]:
    """Snap seconds onto the notes.mid tick grid exactly as pretty_midi writes it.

    Same tempo + default resolution as ``build_notes_mid``, so a snapped time
    equals the note onset read back from the written notes.mid.
    """
    import pretty_midi  # lazy: keeps the pure helpers importable without it.

    pm = pretty_midi.PrettyMIDI(initial_tempo=CONSTANT_BPM)
    if times:  # size pretty_midi's tick->time table once, not per note
        pm.tick_to_time(int(pm.time_to_tick(max(times))) + 1)
    return [float(pm.tick_to_time(int(pm.time_to_tick(t)))) for t in times]


def build_fingering_doc(
    role: str, arr: dict[str, Any], manifest_entry: dict[str, Any] | None
) -> dict[str, Any] | None:
    """aural-fingering sidecar doc from the AUTHORED string/fret positions.

    Times are snapped to the notes.mid tick grid so each ``t_on`` equals the
    onset the game reads back from notes.mid — its merge key is
    ``(round(t_on * 1e4), pitch)``. Notes the game's validator would reject
    (pitch outside 0..127, string > 8, fret > 36, negative time) are left out.
    ``tuning`` is the absolute MIDI open strings (capo excluded). Returns
    ``None`` when no note survives.

    The rest of what the tab says rides along for the guitar views: per note
    ``tech`` (technique keys), ``chord`` (chord name) and ``finger`` (from the
    chord template); top-level ``anchors`` (hand positions) and
    ``handshapes`` (chord shapes held over time, arpeggios included).
    """
    kept = [
        n
        for n in tab_notes(arr, manifest_entry)
        if 0 <= n["midi"] <= 127 and n["s"] <= 8 and n["f"] <= 36 and n["t"] >= 0
    ]
    if not kept:
        return None
    t_on = _snap_to_midi_grid([n["t"] for n in kept])
    t_off = _snap_to_midi_grid([n["t_off"] for n in kept])
    notes = []
    for n, on, off in zip(kept, t_on, t_off):
        note: dict[str, Any] = {
            "t_on": on,
            "t_off": off,
            "pitch": n["midi"],
            "velocity": 100,
            "string": n["s"],
            "fret": n["f"],
        }
        tech = {k: n[k] for k in TECHNIQUE_KEYS if k in n}
        if tech:
            note["tech"] = tech
        if n.get("chord_name"):
            note["chord"] = n["chord_name"]
        if n.get("finger"):
            note["finger"] = n["finger"]
        notes.append(note)
    notes.sort(key=lambda x: (x["t_on"], x["pitch"], x["string"]))
    doc: dict[str, Any] = {
        "version": "1.0.0",
        "instrument": role,
        "tuning": open_strings_midi(arr, manifest_entry),
        "notes": notes,
    }
    anchors = hand_positions(arr)
    if anchors:
        doc["anchors"] = anchors
    shapes = chord_shapes(arr)
    if shapes:
        doc["handshapes"] = shapes
    return doc


# ---------------------------------------------------------------------------
# song_timeline.json from the first arrangement's beats/sections (CONTRACT C4).
# ---------------------------------------------------------------------------


def build_song_timeline(arr: dict[str, Any]) -> dict[str, Any]:
    """Build a song_timeline.json dict from an arrangement's beats + sections.

    Matches the feedpak_writer shape (CONTRACT C4):
    ``{version, tempos, time_signatures, beats, sections}`` with beat rows
    ``{time, measure}`` and section rows ``{name, number, time}``.

    Sloppak beats carry ``measure == -1`` for sub-beats; we carry forward the
    last real measure number so every emitted beat has a concrete measure (the
    downbeat detection keys off measure-value CHANGES, so carried-forward
    sub-beats correctly read as non-downbeats). ``time_signatures`` is derived
    from the modal beats-per-downbeat; ``tempos`` from the median beat interval.
    Sections pass through unchanged.
    """
    timeline: dict[str, Any] = {"version": 1}

    raw_beats = arr.get("beats") if isinstance(arr.get("beats"), list) else []
    beats: list[dict[str, Any]] = []
    current_measure = 1
    for row in raw_beats:
        if not isinstance(row, dict):
            continue
        t = row.get("time")
        if not isinstance(t, (int, float)):
            continue
        m = row.get("measure")
        if isinstance(m, int) and m != -1:
            current_measure = m
        beats.append({"time": float(t), "measure": int(current_measure)})

    # Derive downbeats (a measure-value change) to compute meter + tempo.
    downbeat_times: list[float] = []
    prev_measure: int | None = None
    for b in beats:
        if b["measure"] != prev_measure:
            downbeat_times.append(b["time"])
            prev_measure = b["measure"]

    # time_signatures: modal beats-per-downbeat interval (numerator), /4 denom.
    if len(downbeat_times) >= 2 and beats:
        per_bar_counts: list[int] = []
        for i in range(len(downbeat_times) - 1):
            start_t = downbeat_times[i]
            end_t = downbeat_times[i + 1]
            count = sum(1 for b in beats if start_t - 1e-9 <= b["time"] < end_t - 1e-9)
            if count > 0:
                per_bar_counts.append(count)
        numerator = 4
        if per_bar_counts:
            numerator = Counter(per_bar_counts).most_common(1)[0][0]
        timeline["time_signatures"] = [{"time": 0.0, "ts": [int(numerator), 4]}]
    elif beats:
        timeline["time_signatures"] = [{"time": 0.0, "ts": [4, 4]}]

    # tempos: from the median beat interval (bpm = 60 / interval).
    times_sorted = sorted(b["time"] for b in beats)
    intervals = [
        times_sorted[i + 1] - times_sorted[i]
        for i in range(len(times_sorted) - 1)
        if times_sorted[i + 1] - times_sorted[i] > 1e-6
    ]
    if intervals:
        intervals.sort()
        median = intervals[len(intervals) // 2]
        if median > 1e-6:
            bpm = 60.0 / median
            timeline["tempos"] = [{"time": 0.0, "bpm": float(round(bpm, 3))}]

    if beats:
        timeline["beats"] = beats

    raw_sections = arr.get("sections") if isinstance(arr.get("sections"), list) else []
    sections: list[dict[str, Any]] = []
    for i, row in enumerate(raw_sections, start=1):
        if not isinstance(row, dict):
            continue
        t = row.get("time")
        if not isinstance(t, (int, float)):
            continue
        name = row.get("name")
        number = row.get("number")
        sections.append(
            {
                "name": str(name) if name is not None else f"section_{i}",
                "number": int(number) if isinstance(number, int) else i,
                "time": float(t),
            }
        )
    if sections:
        timeline["sections"] = sections

    return timeline


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def _load_arrangement_json(pack_root: Path, rel: str) -> dict[str, Any] | None:
    path = pack_root / rel
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None
    return doc if isinstance(doc, dict) else None


def prep_arrangements(pack_root: Path | str, *, force: bool = False) -> dict[str, Any]:
    """Read manifest arrangements, write aural/notes.mid + fingering + song_timeline.json.

    Returns a status dict ``{ok, roles, wrote, skipped, ...}``. Existing output
    files are SKIPPED unless ``force`` (protects cleanup anchors). Updates the
    manifest with ``aural_notes_mid`` / ``aural_fingering`` (merged into any
    existing role map) / ``song_timeline`` / ``drum_tab`` keys (the latter
    only if ``drum_tab.json`` exists and the key is missing).

    A drums-only pack (zero melodic arrangements) does NOT get a bogus
    ``aural_notes_mid`` key — ``roles`` is empty and notes.mid is skipped.
    """
    root = Path(pack_root)
    status: dict[str, Any] = {"ok": False, "roles": {}, "wrote": [], "skipped": []}

    manifest = load_pack_manifest(root)
    if manifest is None:
        status["error"] = f"no readable manifest.yaml in {root}"
        return status

    arr_entries = manifest.get("arrangements")
    if not isinstance(arr_entries, list) or not arr_entries:
        status["error"] = "manifest has no arrangements[]"
        return status

    # Load every arrangement wire JSON, preserving manifest order.
    loaded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in arr_entries:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("file")
        if not isinstance(rel, str) or not rel:
            continue
        arr = _load_arrangement_json(root, rel)
        if arr is None:
            continue
        loaded.append((entry, arr))

    if not loaded:
        status["error"] = "no arrangement JSONs could be loaded"
        return status

    feat_dir = pack_feature_dirname(root)
    notes_rel = f"{feat_dir}/notes.mid"
    timeline_rel = "song_timeline.json"
    notes_path = root / feat_dir / "notes.mid"
    timeline_path = root / timeline_rel

    manifest_updates: dict[str, Any] = {}

    # --- notes.mid ------------------------------------------------------
    assigned = assign_roles(loaded)
    roles_map = {role: ROLE_TRACK_NAME[role] for role, _, _ in assigned}
    status["roles"] = roles_map

    if not assigned:
        # Drums-only (or vocals-only) pack: no melodic highway. Do NOT stamp a
        # bogus aural_notes_mid key.
        status["notes_mid"] = "skipped_no_melodic"
    elif notes_path.exists() and not force:
        status["notes_mid"] = "skipped_exists"
        status["skipped"].append(notes_rel)
        # The file exists; still ensure the manifest points at it.
        if manifest.get("aural_notes_mid") != notes_rel:
            manifest_updates["aural_notes_mid"] = notes_rel
    else:
        pm = build_notes_mid(assigned)
        if pm is None:
            status["notes_mid"] = "skipped_no_notes"
        else:
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            pm.write(str(notes_path))
            status["notes_mid"] = "written"
            status["wrote"].append(notes_rel)
            manifest_updates["aural_notes_mid"] = notes_rel

    # --- fingering.<role>.json (authored string/fret, bass + guitars) ----
    fingering_status: dict[str, str] = {}
    fingering_map: dict[str, str] = {}
    for role, entry, arr in assigned:
        if role not in FINGERING_ROLES:
            continue
        rel = f"{feat_dir}/fingering.{role}.json"
        path = root / rel
        if path.exists() and not force:
            fingering_status[role] = "skipped_exists"
            status["skipped"].append(rel)
            fingering_map[role] = rel
            continue
        doc = build_fingering_doc(role, arr, entry)
        if doc is None:
            fingering_status[role] = "skipped_no_notes"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        fingering_status[role] = "written"
        status["wrote"].append(rel)
        fingering_map[role] = rel
    if fingering_status:
        status["fingering"] = fingering_status
    existing_fingering = manifest.get("aural_fingering")
    existing_fingering = existing_fingering if isinstance(existing_fingering, dict) else {}
    merged_fingering = {**existing_fingering, **fingering_map}
    if merged_fingering != existing_fingering:
        manifest_updates["aural_fingering"] = merged_fingering

    # --- song_timeline.json (FIRST arrangement's beats/sections) --------
    first_arr = loaded[0][1]
    if timeline_path.exists() and not force:
        status["song_timeline"] = "skipped_exists"
        status["skipped"].append(timeline_rel)
        if manifest.get("song_timeline") != timeline_rel:
            manifest_updates["song_timeline"] = timeline_rel
    else:
        timeline = build_song_timeline(first_arr)
        timeline_path.write_text(json.dumps(timeline, indent=2), encoding="utf-8")
        status["song_timeline"] = "written"
        status["wrote"].append(timeline_rel)
        manifest_updates["song_timeline"] = timeline_rel

    # --- drum_tab key (if the file exists but the manifest lacks the key) --
    if (root / "drum_tab.json").is_file() and not manifest.get("drum_tab"):
        manifest_updates["drum_tab"] = "drum_tab.json"

    # --- stamp the manifest (order-preserving, unknown keys survive) ----
    if manifest_updates:
        try:
            update_manifest_keys(root, manifest_updates)
            status["manifest_updated"] = sorted(manifest_updates.keys())
        except Exception as exc:  # noqa: BLE001
            status["manifest_error"] = str(exc)

    status["ok"] = True
    return status
