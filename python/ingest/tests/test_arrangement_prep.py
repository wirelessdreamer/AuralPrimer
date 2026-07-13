"""Tests for aural_ingest.arrangement_prep.

Pitch math (capo-0 / capo-2 / fret-0, tuning offsets), template expansion +
per-string overrides, song_timeline sub-beat carry-forward + sections
passthrough + first-arrangement-only, role assignment, and the end-to-end
prep (MIDI re-read for track names + pitches; drums-only skips notes.mid).

The MIDI-building tests require pretty_midi; the pure pitch / timeline /
role-assignment tests do not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aural_ingest.arrangement_prep import (
    STANDARD,
    assign_roles,
    build_notes_mid,
    build_role_notes,
    build_song_timeline,
    note_midi,
    prep_arrangements,
)

FIXTURE_SLOPPAK = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "sloppak"
    / "fixtures"
    / "minimal.sloppak"
)


def _load(rel: str) -> dict:
    return json.loads((FIXTURE_SLOPPAK / rel).read_text(encoding="utf-8"))


# --- pitch math (CONFIRMED capo rule) --------------------------------------


def test_note_midi_open_string_no_capo():
    # Open low E, standard tuning, no capo -> 40.
    assert note_midi(0, 0, [0, 0, 0, 0, 0, 0], 0) == 40


def test_note_midi_open_string_capo_2_raises_open():
    # The fixture capo test note: open string (f==0) under capo 2 sounds 2
    # semitones above its nominal open pitch. STANDARD[0]=40 -> 42.
    assert note_midi(0, 0, [0, 0, 0, 0, 0, 0], 2) == 42


def test_note_midi_fretted_ignores_capo():
    # A fretted note (f>0) sounds at its absolute fret regardless of capo.
    # String 1 (A2=45), fret 5, capo 2 -> 45 + 5 = 50 (NOT 45+2+5).
    assert note_midi(1, 5, [0, 0, 0, 0, 0, 0], 2) == 50
    # Same with capo 0 — identical, confirming capo doesn't touch fretted notes.
    assert note_midi(1, 5, [0, 0, 0, 0, 0, 0], 0) == 50


def test_note_midi_tuning_offset():
    # Drop-D (string 0 down a semitone) shifts every note on that string.
    assert note_midi(0, 0, [-2, 0, 0, 0, 0, 0], 0) == 38
    assert note_midi(0, 3, [-2, 0, 0, 0, 0, 0], 0) == 41


def test_standard_tuning_array():
    assert STANDARD == [40, 45, 50, 55, 59, 64]


# --- build_role_notes: fixture lead arrangement ----------------------------


def test_build_role_notes_lead_fixture_pitches():
    lead = _load("arrangements/lead.json")
    # manifest_entry supplies tuning/capo; here use the arrangement's own.
    entry = {"tuning": [0, 0, 0, 0, 0, 0], "capo": 2}
    triples = build_role_notes(lead, entry)
    midis = [m for _, _, m in triples]
    # notes[0] open s=0 f=0 capo=2 -> 42.
    assert 42 in midis
    # notes[1] s=1 f=5 -> 50.
    assert 50 in midis
    # notes[2] s=2 f=7 -> STANDARD[2]=50 + 7 = 57.
    assert 57 in midis
    # notes[3] s=3 f=3 -> 55 + 3 = 58; notes[4] s=3 f=5 -> 55 + 5 = 60.
    assert 58 in midis
    assert 60 in midis


def test_build_role_notes_sustain_floor():
    arr = {"notes": [{"t": 0.0, "s": 0, "f": 0, "sus": 0.0}], "chords": [], "templates": []}
    triples = build_role_notes(arr, {"tuning": [0] * 6, "capo": 0})
    t_on, t_off, _ = triples[0]
    # sus 0 -> floored to MIN_NOTE_SEC (0.05).
    assert t_off - t_on == pytest.approx(0.05)


def test_build_role_notes_explicit_sustain():
    arr = {"notes": [{"t": 1.0, "s": 2, "f": 7, "sus": 0.4}], "chords": [], "templates": []}
    triples = build_role_notes(arr, {"tuning": [0] * 6, "capo": 0})
    t_on, t_off, _ = triples[0]
    assert t_off - t_on == pytest.approx(0.4)


# --- chord template expansion + per-string overrides -----------------------


def test_chord_template_expansion():
    lead = _load("arrangements/lead.json")
    # templates[0] Em: frets [0,2,2,-1,-1,-1]. capo 2.
    entry = {"tuning": [0] * 6, "capo": 2}
    triples = build_role_notes(lead, entry)
    # The chord at t=2.0 expands the Em template (strings 0,1,2 played).
    chord_midis = sorted(m for t, _, m in triples if abs(t - 2.0) < 1e-6)
    # string 0 fret 0 (open) under capo 2 -> 40+2 = 42.
    # string 1 fret 2 -> 45+2 = 47.
    # string 2 fret 2 -> 50+2 = 52.
    assert chord_midis == [42, 47, 52]


def test_chord_per_string_override_wins():
    arr = {
        "notes": [],
        "chords": [{"t": 0.0, "id": 0, "notes": [{"s": 0, "f": 5}]}],
        "templates": [{"frets": [0, 2, 2, -1, -1, -1]}],
    }
    triples = build_role_notes(arr, {"tuning": [0] * 6, "capo": 0})
    midis = sorted(m for _, _, m in triples)
    # Override string 0 to fret 5 -> 40+5 = 45 (not open 40); strings 1,2 from
    # template -> 45+2=47, 50+2=52.
    assert midis == [45, 47, 52]


def test_chord_skip_string_minus_one():
    arr = {
        "notes": [],
        "chords": [{"t": 0.0, "id": 0}],
        "templates": [{"frets": [-1, -1, -1, 0, 0, 0]}],
    }
    triples = build_role_notes(arr, {"tuning": [0] * 6, "capo": 0})
    # Only strings 3,4,5 played (open) -> 55, 59, 64.
    assert sorted(m for _, _, m in triples) == [55, 59, 64]


# --- song_timeline: sub-beat carry-forward + sections ----------------------


def test_song_timeline_carry_forward_and_downbeats():
    lead = _load("arrangements/lead.json")
    tl = build_song_timeline(lead)
    beats = tl["beats"]
    assert len(beats) == 8
    # First beat downbeat measure 1; sub-beats carry it forward.
    assert beats[0] == {"time": 0.0, "measure": 1}
    assert beats[1]["measure"] == 1  # was -1, carried forward
    assert beats[3]["measure"] == 1
    # time 2.0 -> measure 2, then sub-beats carry 2 forward.
    assert beats[4] == {"time": 2.0, "measure": 2}
    assert beats[5]["measure"] == 2
    assert beats[7]["measure"] == 2


def test_song_timeline_time_signature_modal():
    lead = _load("arrangements/lead.json")
    tl = build_song_timeline(lead)
    # Two downbeats, 4 beats each -> 4/4.
    assert tl["time_signatures"] == [{"time": 0.0, "ts": [4, 4]}]


def test_song_timeline_sections_passthrough():
    lead = _load("arrangements/lead.json")
    tl = build_song_timeline(lead)
    assert tl["sections"] == [
        {"name": "intro", "number": 1, "time": 0.0},
        {"name": "verse", "number": 1, "time": 2.0},
    ]


def test_song_timeline_tempo_from_interval():
    lead = _load("arrangements/lead.json")
    tl = build_song_timeline(lead)
    # Beats every 0.5s -> 120 bpm.
    assert tl["tempos"][0]["bpm"] == pytest.approx(120.0, abs=0.01)


def test_song_timeline_bass_has_no_beats():
    # bass.json has no beats/sections -> timeline is minimal.
    bass = _load("arrangements/bass.json")
    tl = build_song_timeline(bass)
    assert "beats" not in tl
    assert "sections" not in tl
    assert tl["version"] == 1


# --- role assignment --------------------------------------------------------


def test_assign_roles_lead_and_bass_first_wins():
    lead_entry = {"id": "lead", "name": "Lead"}
    bass_entry = {"id": "bass", "name": "Bass"}
    assigned = assign_roles([(lead_entry, {}), (bass_entry, {})])
    roles = [r for r, _, _ in assigned]
    assert roles == ["lead_guitar", "bass"]


def test_assign_roles_vocals_kept():
    assigned = assign_roles([({"id": "vocals"}, {}), ({"id": "bass"}, {})])
    roles = [r for r, _, _ in assigned]
    assert roles == ["vocals", "bass"]


def test_assign_roles_duplicate_role_dropped():
    # Two bass arrangements -> only the first claims the bass role.
    assigned = assign_roles([({"id": "bass"}, {"name": "B1"}), ({"id": "bass"}, {"name": "B2"})])
    assert len(assigned) == 1
    assert assigned[0][0] == "bass"


def test_assign_roles_rhythm_lead_keys():
    entries = [
        ({"id": "rhythm"}, {}),
        ({"id": "lead"}, {}),
        ({"type": "piano"}, {}),
    ]
    assigned = assign_roles(entries)
    assert [r for r, _, _ in assigned] == ["rhythm_guitar", "lead_guitar", "keys"]


def test_build_notes_mid_keeps_vocals_track():
    arr = {"notes": [{"t": 0.1, "s": 0, "f": 5, "sus": 0.25}]}
    pm = build_notes_mid([("vocals", {"id": "vocals"}, arr)])

    assert pm is not None
    assert [inst.name for inst in pm.instruments] == ["Vocals"]
    assert [note.pitch for note in pm.instruments[0].notes] == [45]


# --- end-to-end (needs pretty_midi) ----------------------------------------
#
# These tests lazily import pretty_midi inside the body; on a lightweight env
# without it the ModuleNotFoundError is converted to a skip by the suite's
# conftest (pytest_runtest_makereport), so the pure tests above still run.


def _copy_sloppak(tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "minimal.sloppak"
    shutil.copytree(FIXTURE_SLOPPAK, dst)
    return dst


def test_prep_writes_notes_mid_and_timeline(tmp_path: Path):
    pack = _copy_sloppak(tmp_path)
    status = prep_arrangements(pack)
    assert status["ok"] is True
    notes_mid = pack / "aural" / "notes.mid"
    timeline = pack / "song_timeline.json"
    assert notes_mid.is_file()
    assert timeline.is_file()

    # Re-read MIDI: track names + role layout (CONTRACT C3). lead -> Lead
    # Guitar, bass -> Bass. First-wins insertion order is bass then lead_guitar.
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(notes_mid))
    names = [inst.name for inst in pm.instruments]
    assert "Bass" in names
    assert "Lead Guitar" in names
    # No drum track.
    assert not any(inst.is_drum for inst in pm.instruments)
    # Insertion order follows ROLE_ORDER: Bass before Lead Guitar.
    assert names.index("Bass") < names.index("Lead Guitar")

    # Lead Guitar carries the capo-2 open-string pitch 42.
    lead_inst = next(i for i in pm.instruments if i.name == "Lead Guitar")
    lead_pitches = {n.pitch for n in lead_inst.notes}
    assert 42 in lead_pitches  # open string under capo 2


def test_prep_stamps_manifest_keys(tmp_path: Path):
    pack = _copy_sloppak(tmp_path)
    prep_arrangements(pack)
    from aural_ingest.pack_paths import load_pack_manifest

    mf = load_pack_manifest(pack)
    assert mf["aural_notes_mid"] == "aural/notes.mid"
    assert mf["song_timeline"] == "song_timeline.json"
    # drum_tab already present in the fixture manifest — unchanged, still there.
    assert mf["drum_tab"] == "drum_tab.json"
    # Unknown key preserved.
    assert mf["slopsmith_version"] == "0.9.0"


def test_prep_skips_existing_without_force(tmp_path: Path):
    pack = _copy_sloppak(tmp_path)
    prep_arrangements(pack)
    notes_mid = pack / "aural" / "notes.mid"
    mtime = notes_mid.stat().st_mtime_ns
    # Second run without force must not rewrite.
    status = prep_arrangements(pack)
    assert status["notes_mid"] == "skipped_exists"
    assert notes_mid.stat().st_mtime_ns == mtime


def test_prep_empty_vocals_skips_notes_mid(tmp_path: Path):
    # A pack with an empty vocals arrangement recognizes the role but still
    # writes no bogus notes.mid / aural_notes_mid key.
    pack = tmp_path / "drumsonly.sloppak"
    (pack / "arrangements").mkdir(parents=True)
    (pack / "arrangements" / "vox.json").write_text(
        json.dumps({"name": "Vocals", "notes": [], "chords": [], "templates": []}),
        encoding="utf-8",
    )
    (pack / "drum_tab.json").write_text(json.dumps({"version": 1, "hits": []}), encoding="utf-8")
    (pack / "manifest.yaml").write_text(
        "title: D\n"
        "artist: A\n"
        "duration: 4.0\n"
        "arrangements:\n"
        "- id: vocals\n  file: arrangements/vox.json\n"
        "drum_tab: drum_tab.json\n",
        encoding="utf-8",
    )
    status = prep_arrangements(pack)
    assert status["ok"] is True
    assert status["roles"] == {"vocals": "Vocals"}
    assert status["notes_mid"] == "skipped_no_notes"
    assert not (pack / "aural" / "notes.mid").exists()
    from aural_ingest.pack_paths import load_pack_manifest

    mf = load_pack_manifest(pack)
    assert "aural_notes_mid" not in mf
    # Timeline still written (from the first/only arrangement, which has no
    # beats -> minimal timeline) and stamped.
    assert (pack / "song_timeline.json").is_file()
    assert mf["song_timeline"] == "song_timeline.json"
