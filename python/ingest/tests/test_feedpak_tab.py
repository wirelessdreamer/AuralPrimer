"""Tests for aural_ingest.feedpak_tab (feedpak tabs as a source of truth).

All charts here are small SYNTHETIC arrangements: full-difficulty flattening
from the phrase ladder, chord expansion (per-chord notes vs template only),
sustains, technique keys, open strings (offset vs absolute tuning, bass
octave, manifest override), the capo rule, and dir-vs-zip pack reading.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aural_ingest.feedpak_tab import (
    CHORD_MAX_SUS_SEC,
    MIN_NOTE_SEC,
    STANDARD,
    full_difficulty_events,
    is_bass,
    open_strings_midi,
    read_pack,
    tab_notes,
)


def _lead() -> dict:
    return {
        "name": "Lead",
        "tuning": [0, 0, 0, 0, 0, 0],
        "capo": 0,
        # Sparse top-level summary: must be ignored when a ladder exists.
        "notes": [{"t": 0.5, "s": 5, "f": 12}],
        "chords": [],
        "templates": [
            {"name": "Em", "frets": [0, 2, 2, -1, -1, -1]},
            {"name": "A5", "frets": [-1, 0, 2, 2, -1, -1]},
        ],
        "phrases": [
            {
                "start_time": 0.0,
                "end_time": 1.6,
                "max_difficulty": 1,
                "levels": [
                    {"difficulty": 0, "notes": [{"t": 0.5, "s": 5, "f": 12}], "chords": []},
                    {
                        "difficulty": 1,
                        "notes": [
                            {"t": 0.5, "s": 0, "f": 3, "sus": 0.0, "pm": 1, "ho": 0, "sl": -1},
                            {
                                "t": 1.0,
                                "s": 2,
                                "f": 7,
                                "sus": 0.4,
                                "sl": 9,
                                "bn": 2.0,
                                "sd": 0,
                                "ig": False,
                            },
                        ],
                        # Per-chord notes win over the template (Em would add s2 f2).
                        "chords": [
                            {
                                "t": 1.5,
                                "id": 0,
                                "notes": [{"s": 0, "f": 0, "sus": 0.8, "ac": 1}, {"s": 1, "f": 2}],
                            }
                        ],
                    },
                ],
            },
            {
                "start_time": 1.6,
                "end_time": 4.0,
                "max_difficulty": 0,
                # Template-only chord: expands the A5 template's frets.
                "levels": [{"difficulty": 0, "notes": [], "chords": [{"t": 1.6, "id": 1}]}],
            },
        ],
    }


def _bass() -> dict:
    return {
        "name": "Bass",
        "tuning": [0, 0, 0, 0, 0, 0],
        "notes": [{"t": 0.0, "s": 0, "f": 0}, {"t": 1.0, "s": 1, "f": 3, "sus": 0.5}],
    }


MANIFEST = (
    "title: Synthetic\n"
    "artist: Tester\n"
    "duration: 4.0\n"
    "arrangements:\n"
    "- id: lead\n  name: Lead\n  type: guitar\n  file: arrangements/lead.json\n"
    "  tuning: [0, 0, 0, 0, 0, 0]\n  capo: 0\n"
    "- id: bass\n  name: Bass\n  type: bass\n  file: arrangements/bass.json\n"
    "  tuning: [-1, -1, -1, -1, -1, -1]\n"
    "stems:\n- id: full\n  file: stems/full.ogg\n"
)


def _write_pack(root: Path) -> Path:
    (root / "arrangements").mkdir(parents=True)
    (root / "arrangements" / "lead.json").write_text(json.dumps(_lead()), encoding="utf-8")
    (root / "arrangements" / "bass.json").write_text(json.dumps(_bass()), encoding="utf-8")
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


def _zip_pack(src: Path, dst: Path, prefix: str = "") -> Path:
    with zipfile.ZipFile(dst, "w") as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, prefix + path.relative_to(src).as_posix())
    return dst


# --- full-difficulty flattening ---------------------------------------------


def test_full_difficulty_takes_each_phrases_hardest_level():
    notes, chords = full_difficulty_events(_lead())
    assert [(n["t"], n["s"], n["f"]) for n in notes] == [(0.5, 0, 3), (1.0, 2, 7)]
    assert [(c["t"], c["id"]) for c in chords] == [(1.5, 0), (1.6, 1)]


def test_no_ladder_falls_back_to_top_level():
    arr = _lead()
    del arr["phrases"]
    notes, chords = full_difficulty_events(arr)
    assert [(n["s"], n["f"]) for n in notes] == [(5, 12)]
    assert chords == []


def test_missing_max_difficulty_takes_hardest_level():
    arr = _lead()
    del arr["phrases"][0]["max_difficulty"]
    notes, _ = full_difficulty_events(arr)
    assert len(notes) == 2


# --- tab notes ---------------------------------------------------------------


def test_tab_notes_full_chart_positions_and_pitches():
    notes = tab_notes(_lead())
    assert [(n["t"], n["s"], n["f"], n["midi"]) for n in notes] == [
        (0.5, 0, 3, 43),
        (1.0, 2, 7, 57),
        (1.5, 0, 0, 40),
        (1.5, 1, 2, 47),
        (1.6, 1, 0, 45),
        (1.6, 2, 2, 52),
        (1.6, 3, 2, 57),
    ]
    # The top-level-only note (s5 f12 -> 76) is not part of the full chart.
    assert 76 not in {n["midi"] for n in notes}


def test_tab_notes_sustains():
    by_pos = {(n["t"], n["s"]): n["t_off"] - n["t"] for n in tab_notes(_lead())}
    assert by_pos[(0.5, 0)] == pytest.approx(MIN_NOTE_SEC)  # sus 0 -> floor
    assert by_pos[(1.0, 2)] == pytest.approx(0.4)  # single note's own sus
    assert by_pos[(1.5, 0)] == pytest.approx(0.8)  # chord note's own sus
    assert by_pos[(1.5, 1)] == pytest.approx(0.1)  # no sus -> gap to next onset
    assert by_pos[(1.6, 1)] == pytest.approx(CHORD_MAX_SUS_SEC)  # last event -> cap


def test_tab_notes_chord_id_and_name():
    notes = tab_notes(_lead())
    singles = [n for n in notes if n["t"] < 1.5]
    assert all("chord_id" not in n and "chord_name" not in n for n in singles)
    assert {(n["chord_id"], n["chord_name"]) for n in notes if n["t"] == 1.5} == {(0, "Em")}
    assert {(n["chord_id"], n["chord_name"]) for n in notes if n["t"] == 1.6} == {(1, "A5")}


def test_tab_notes_technique_keys():
    notes = {(n["t"], n["s"]): n for n in tab_notes(_lead())}
    first = notes[(0.5, 0)]
    assert first["pm"] == 1
    assert "ho" not in first  # falsy -> absent
    assert "sl" not in first  # -1 = no slide
    second = notes[(1.0, 2)]
    assert (second["sl"], second["bn"], second["sd"]) == (9, 2.0, 0)  # sd 0 is a real degree
    assert "ig" not in second
    assert notes[(1.5, 0)]["ac"] == 1


def test_tab_notes_drop_unplayable_positions():
    arr = {
        "tuning": [28, 33, 38, 43],  # absolute 4-string bass
        "notes": [
            {"t": 0.0, "s": 3, "f": 2},
            {"t": 0.5, "s": 4, "f": 0},  # no fifth string
            {"t": 1.0, "s": 0, "f": -1},  # negative fret
        ],
    }
    assert [(n["s"], n["midi"]) for n in tab_notes(arr, {"type": "bass"})] == [(3, 45)]


# --- open strings / capo -----------------------------------------------------


def test_open_strings_offsets_from_standard():
    assert open_strings_midi({"tuning": [0] * 6}) == STANDARD
    assert open_strings_midi({"tuning": [-2, 0, 0, 0, 0, 0]}) == [38, 45, 50, 55, 59, 64]
    assert open_strings_midi({}) == STANDARD  # no tuning -> standard


def test_open_strings_bass_is_an_octave_below_guitar():
    # Rocksmith-style 6-entry bass tuning: E1 A1 D2 G2 (+ unused strings).
    assert open_strings_midi({"tuning": [0] * 6}, {"type": "bass"})[:4] == [28, 33, 38, 43]
    assert open_strings_midi({"tuning": [-1] * 4}, {"id": "bass"}) == [27, 32, 37, 42]


def test_open_strings_absolute_tuning_used_as_is():
    # Our own feedpak_writer tab_<role>.json carries absolute MIDI open strings.
    assert open_strings_midi({"tuning": [28, 33, 38, 43]}, {"type": "bass"}) == [28, 33, 38, 43]
    assert open_strings_midi({"tuning": STANDARD}) == STANDARD


def test_manifest_tuning_and_capo_override_the_json():
    arr = {"tuning": [0] * 6, "capo": 0, "notes": [{"t": 0.0, "s": 0, "f": 0}]}
    entry = {"tuning": [-2] * 6, "capo": 3}
    assert open_strings_midi(arr, entry)[0] == 38
    assert tab_notes(arr, entry)[0]["midi"] == 41  # D2 open under capo 3


def test_capo_raises_open_strings_only():
    arr = {"notes": [{"t": 0.0, "s": 0, "f": 0}, {"t": 1.0, "s": 1, "f": 5}]}
    capo2 = [n["midi"] for n in tab_notes(arr, {"capo": 2})]
    no_capo = [n["midi"] for n in tab_notes(arr, {"capo": 0})]
    assert capo2 == [42, 50]  # open low E under capo 2 -> 42; fret 5 on A stays 50
    assert no_capo == [40, 50]


def test_is_bass_prefers_manifest_type():
    assert is_bass({}, {"type": "bass", "id": "x"})
    assert not is_bass({"name": "Bass"}, {"type": "guitar", "id": "bass"})
    assert is_bass({"name": "Bass"}, {"id": "low"})
    assert not is_bass({"name": "Lead"}, None)


# --- pack reading (dir vs zip) ------------------------------------------------


def _summary(pack: Path) -> tuple[str, list[tuple[str, list[tuple[float, int, int, int]]]]]:
    manifest, arrangements = read_pack(pack)
    return manifest["title"], [
        (a["entry"]["id"], [(n["t"], n["s"], n["f"], n["midi"]) for n in a["notes"]])
        for a in arrangements
    ]


def test_read_pack_dir_and_zip_agree(tmp_path: Path):
    pack_dir = _write_pack(tmp_path / "synthetic.feedpak")
    flat_zip = _zip_pack(pack_dir, tmp_path / "flat.feedpak")
    nested_zip = _zip_pack(pack_dir, tmp_path / "nested.feedpak", prefix="Synthetic/")

    expected = _summary(pack_dir)
    assert _summary(flat_zip) == expected
    assert _summary(nested_zip) == expected

    title, arrangements = expected
    assert title == "Synthetic"
    assert [arr_id for arr_id, _ in arrangements] == ["lead", "bass"]
    # Manifest tuning (Eb, -1) wins over the bass JSON's standard tuning, an
    # octave below guitar: open E1-1 = 27, A1-1 + 3 = 35.
    assert [n[3] for n in arrangements[1][1]] == [27, 35]


def test_tab_truth_cli_dumps_every_arrangement(tmp_path: Path):
    from aural_ingest import cli

    pack = _zip_pack(_write_pack(tmp_path / "synthetic.feedpak"), tmp_path / "packed.feedpak")
    out = tmp_path / "truth.json"
    assert cli.main(["tab-truth", str(pack), "--out", str(out)]) == 0

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert (doc["title"], doc["artist"]) == ("Synthetic", "Tester")
    lead, bass = doc["arrangements"]
    assert (lead["id"], lead["type"], lead["role"], lead["capo"]) == (
        "lead",
        "guitar",
        "lead_guitar",
        0,
    )
    assert lead["open_strings_midi"] == STANDARD
    assert len(lead["notes"]) == 7
    assert (bass["role"], bass["open_strings_midi"]) == ("bass", [27, 32, 37, 42, 46, 51])
