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
      audio/
        stems/<role>.wav             # copied from the .auralsong stems
      aural/                         # our authoring artifacts (aural_* exts)
        notes.mid
        spectrogram/<role>/...
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
import shutil
from pathlib import Path
from typing import Any

import pretty_midi
import yaml

FEEDPAK_VERSION = "1.11.0"

# Roles that engrave on a grand (treble+bass) staff. Everything else gets a
# single treble staff.
_PIANO_FAMILY_ROLES = {"keys", "piano", "synth", "melodic"}

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
    drum_tab_doc: dict[str, Any] | None = None
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
            arr_type = "piano" if role.lower() in _PIANO_FAMILY_ROLES else role
            arrangement_entries.append(
                {
                    "id": role,
                    "name": role.replace("_", " ").title(),
                    "type": arr_type,
                    "notation": rel,
                }
            )

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

    song_timeline = _build_song_timeline(tempo_map, beats, sections)
    (feedpak_dir / "song_timeline.json").write_text(
        json.dumps(song_timeline, indent=2), encoding="utf-8"
    )

    if drum_tab_doc is not None:
        (feedpak_dir / "drum_tab.json").write_text(
            json.dumps(drum_tab_doc, indent=2), encoding="utf-8"
        )

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
    if drum_tab_doc is not None:
        fp_manifest["drum_tab"] = "drum_tab.json"
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
