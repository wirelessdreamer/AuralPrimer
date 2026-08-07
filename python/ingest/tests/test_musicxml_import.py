"""Unit tests for the stdlib MusicXML -> MelodicNote parser."""
from __future__ import annotations

from pathlib import Path

from aural_ingest.musicxml_import import parse_musicxml, role_for_part_name

# A minimal 2-part score exercising: divisions, an explicit tempo, a chord
# (shared onset, no cursor advance), a tie (start+stop merged into one note),
# a rest, and part-name -> role mapping.
_XML = """<?xml version="1.0"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1"><part-name>Acoustic Piano</part-name></score-part>
    <score-part id="P2"><part-name>Voice</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions></attributes>
      <sound tempo="120"/>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>
        <tie type="start"/></note>
      <note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration></note>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>
        <tie type="stop"/></note>
      <note><rest/><duration>2</duration></note>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes><divisions>1</divisions></attributes>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration></note>
    </measure>
  </part>
</score-partwise>"""


def _parse(tmp_path: Path):
    f = tmp_path / "s.musicxml"
    f.write_text(_XML, encoding="utf-8")
    return parse_musicxml(f)


def test_roles_and_counts(tmp_path: Path) -> None:
    out = _parse(tmp_path)
    assert set(out) == {"keys", "vocals"}
    # C4 (tied start+stop -> one note) + E4 chord note = 2 keys notes.
    assert len(out["keys"]) == 2
    assert len(out["vocals"]) == 1


def test_pitches(tmp_path: Path) -> None:
    out = _parse(tmp_path)
    pitches = sorted(n.pitch for n in out["keys"])
    assert pitches == [60, 64]  # C4, E4
    assert out["vocals"][0].pitch == 67  # G4


def test_chord_shares_onset(tmp_path: Path) -> None:
    out = _parse(tmp_path)
    c4 = next(n for n in out["keys"] if n.pitch == 60)
    e4 = next(n for n in out["keys"] if n.pitch == 64)
    assert abs(c4.t_on - e4.t_on) < 1e-6  # chord: same onset


def test_tie_merges_into_one_sustained_note(tmp_path: Path) -> None:
    out = _parse(tmp_path)
    c4 = next(n for n in out["keys"] if n.pitch == 60)
    # tie start (dur 1) + tie stop (dur 1) at 120 BPM (0.5 s/quarter) -> ~1.0 s.
    assert abs((c4.t_off - c4.t_on) - 1.0) < 1e-6


def test_tempo_to_seconds(tmp_path: Path) -> None:
    out = _parse(tmp_path)
    # At 120 BPM a quarter note is 0.5 s; the chord starts at division 0.
    assert abs(out["keys"][0].t_on) < 1e-6
    # The 3rd keys event (tie-stop) merged, so the E4 chord note onset is 0.
    e4 = next(n for n in out["keys"] if n.pitch == 64)
    assert abs(e4.t_on) < 1e-6


def test_role_for_part_name() -> None:
    assert role_for_part_name("Acoustic Piano") == "keys"
    assert role_for_part_name("Voice") == "vocals"
    assert role_for_part_name("Electric Bass") == "bass"
    assert role_for_part_name("Drum Kit") == "drums"
    assert role_for_part_name(None) == "keys"  # lone unnamed melodic part
