"""Tests for the MusicXML -> feedpak import flow."""
from __future__ import annotations

from pathlib import Path

import pytest

from aural_ingest.musicxml_feedpak import build_feedpak_from_musicxml
from aural_ingest.musicxml_import import parse_musicxml_timeline

# Two measures of 3/4 at 60 BPM (1 s/quarter -> a 3 s bar), piano + voice.
_XML = """<?xml version="1.0"?>
<score-partwise version="3.1">
  <movement-title>G major</movement-title>
  <part-list>
    <score-part id="P1"><part-name>Acoustic Piano</part-name></score-part>
    <score-part id="P2"><part-name>Voice</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>3</beats><beat-type>4</beat-type></time></attributes>
      <sound tempo="60"/>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration></note>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration></note>
    </measure>
    <measure number="2">
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>3</duration></note>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>3</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>3</duration></note>
    </measure>
    <measure number="2">
      <note><pitch><step>A</step><octave>4</octave></pitch><duration>3</duration></note>
    </measure>
  </part>
</score-partwise>"""


def _write_silence(path: Path, seconds: float = 6.0, sr: int = 22050) -> None:
    sf = pytest.importorskip("soundfile")
    import numpy as np

    sf.write(str(path), np.zeros(int(seconds * sr), dtype="float32"), sr)


@pytest.fixture()
def score(tmp_path: Path) -> Path:
    f = tmp_path / "song.musicxml"
    f.write_text(_XML, encoding="utf-8")
    # A feedpak needs audio; Mirelo drops a render beside the score, so mirror
    # that — a sibling .wav with the same stem gets auto-attached.
    _write_silence(tmp_path / "song.wav")
    return f


def test_timeline_extraction(score: Path) -> None:
    tl = parse_musicxml_timeline(score)
    assert tl.tempo_bpm == 60.0
    assert tl.time_signature == (3, 4)
    # two measures; bar 2 starts at 3 s (three 1-s quarters).
    assert len(tl.measure_onsets_sec) == 2
    assert abs(tl.measure_onsets_sec[1] - 3.0) < 1e-6
    assert tl.movement_title == "G major"


def test_builds_valid_feedpak(score: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = build_feedpak_from_musicxml(score, out, title="My Song", artist="Me")
    assert result["ok"] is True
    assert result["title"] == "My Song"  # NOT the "G major" movement-title
    assert result["movement_title_ignored"] == "G major"
    assert result["roles"] == {"keys": 4, "vocals": 2}
    assert result["time_signature"] == "3/4"

    feedpak = Path(result["feedpak"])
    assert feedpak.is_dir()
    assert (feedpak / "manifest.yaml").exists()
    assert (feedpak / "aural" / "notes.mid").exists()
    assert (feedpak / "song_timeline.json").exists()
    # The sibling render was auto-attached as the pack's stem.
    assert result["audio_attached"] is True
    assert (feedpak / "audio" / "stems" / "mix.wav").exists()


def test_requires_audio(tmp_path: Path) -> None:
    # A score with no render beside it and no --audio cannot make a valid
    # feedpak (feedpaks require an audio stem).
    f = tmp_path / "lonely.musicxml"
    f.write_text(_XML, encoding="utf-8")
    with pytest.raises(ValueError, match="needs an audio stem"):
        build_feedpak_from_musicxml(f, tmp_path / "out")


def test_feedpak_manifest_schema_valid(score: Path, tmp_path: Path) -> None:
    import yaml

    from aural_ingest import feedpak_validate as fv

    result = build_feedpak_from_musicxml(score, tmp_path / "out", title="S", artist="A")
    manifest = yaml.safe_load((Path(result["feedpak"]) / "manifest.yaml").read_text("utf-8"))
    assert fv.iter_errors(manifest, "manifest.schema.json") == []
    assert manifest["title"] == "S"


def test_notes_mid_has_both_roles(score: Path, tmp_path: Path) -> None:
    pretty_midi = pytest.importorskip("pretty_midi")
    result = build_feedpak_from_musicxml(score, tmp_path / "out")
    pm = pretty_midi.PrettyMIDI(str(Path(result["feedpak"]) / "aural" / "notes.mid"))
    names = {i.name for i in pm.instruments}
    assert "Keys" in names and "Vocals" in names


def test_title_defaults_to_stem_not_movement_title(score: Path) -> None:
    # The whole point: Mirelo's exporter stamps the key as movement-title.
    result = build_feedpak_from_musicxml(score, score.parent / "out")
    assert result["title"] == "song"  # the file stem, not "G major"


def test_spectrogram_explains_mix_only_pack(score: Path, tmp_path: Path, capsys) -> None:
    """A MusicXML pack has only a `mix` stem, so a spectrogram build can't run.
    Regression: it must say WHY (naming the stems, and that score notes are
    authoritative) rather than returning a bare ok:false with no message.
    """
    import argparse
    import json

    from aural_ingest import cli

    result = build_feedpak_from_musicxml(score, tmp_path / "out", title="S", artist="A")
    args = argparse.Namespace(auralsong_dir=result["feedpak"], instrument=["keys"])
    rc = cli.cmd_build_spectrogram(args)

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1
    assert payload["ok"] is False
    assert payload["available_stems"] == ["mix"]
    assert payload["requested_roles"] == ["keys"]
    # The reason is human-readable and points at the real cause.
    err = payload["error"].lower()
    assert "mix" in err
    assert "stem" in err and "score" in err
