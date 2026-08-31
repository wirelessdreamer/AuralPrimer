"""Unit tests for MIDI render preparation.

The flatten moves a performance out of the tempo map and into note positions so
Ableton, which cannot import tempo maps, plays it correctly. Two properties
carry the whole thing: the notes must not move, and the check that says so must
measure the notes rather than the file.

That second one is not hypothetical. The previous gate compared
``MidiFile.length``, which includes trailing silence, and reported 71 seconds
of drift on a Bach file whose notes had not moved at all -- a false alarm loud
enough to hide a real one.
"""
from __future__ import annotations

import json
from pathlib import Path

import mido
import pytest

from aural_ingest.midi_render_prep import MAX_NOTE_DRIFT_SEC, flatten, prepare_render


def _rubato_midi(path: Path, *, trailing_silence_beats: int = 0) -> None:
    """A file whose timing lives in its tempo map, as piano-midi.de files do."""
    mid = mido.MidiFile(ticks_per_beat=480)

    meta = mido.MidiTrack()
    # Wildly varying tempo: this is the expression that has to survive.
    for tick, bpm in ((0, 60), (480, 132), (960, 40), (1440, 90)):
        meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm),
                                     time=tick - (0 if tick == 0 else tick - 480)))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Piano", time=0))
    for _ in range(4):
        track.append(mido.Message("note_on", note=60, velocity=90, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    if trailing_silence_beats:
        track.append(mido.MetaMessage("marker", text="tail",
                                      time=480 * trailing_silence_beats))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    mid.save(str(path))


def test_notes_do_not_move(tmp_path):
    """The point of the exercise: same wall-clock onsets, different tempo map."""
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    result = flatten(src, tmp_path / "flat" / "rubato.mid")

    assert result.ok, result.warnings
    assert result.max_note_drift_sec <= MAX_NOTE_DRIFT_SEC
    assert result.tempo_events_removed >= 3


def test_output_carries_a_single_tempo(tmp_path):
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    out = tmp_path / "flat" / "rubato.mid"
    flatten(src, out)

    tempos = [m for t in mido.MidiFile(out).tracks for m in t if m.type == "set_tempo"]
    assert len(tempos) == 1


def test_trailing_silence_is_not_drift(tmp_path):
    """A long tail after the last note is not a defect.

    The old gate compared file lengths and failed a Bach file by 71 seconds
    for exactly this. Notes are what must be preserved; silence afterwards is
    not part of the performance.
    """
    src = tmp_path / "tail.mid"
    _rubato_midi(src, trailing_silence_beats=120)
    result = flatten(src, tmp_path / "flat" / "tail.mid")

    assert result.ok, result.warnings
    assert result.max_note_drift_sec <= MAX_NOTE_DRIFT_SEC


def test_a_flatten_that_moved_notes_is_reported_not_silently_written(tmp_path, monkeypatch):
    """The gate has to fail loudly, or a bad render source ships."""
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    monkeypatch.setattr("aural_ingest.midi_render_prep.MAX_NOTE_DRIFT_SEC", 0.0)
    result = flatten(src, tmp_path / "flat" / "rubato.mid")

    assert result.ok is False
    assert result.warnings
    assert "moved" in result.warnings[0]


def test_prepare_render_records_which_file_is_the_chart(tmp_path):
    """The pairing must not be left to memory.

    Importing the flattened file as the chart is the mistake this module
    exists to prevent, so the render note says outright which is which.
    """
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    payload = prepare_render(src, tmp_path / "out")

    assert Path(payload["chart_source"]) == src
    assert Path(payload["render_source"]) != src
    assert "import-midi" in payload["note"]

    sidecar = json.loads((tmp_path / "out" / "rubato.render.json").read_text(encoding="utf-8"))
    assert sidecar["chart_source"] == str(src)


def test_lead_in_shifts_the_notes(tmp_path):
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    prepare_render(src, tmp_path / "none", lead_in_sec=0.0)
    prepare_render(src, tmp_path / "lead", lead_in_sec=2.0)

    from aural_ingest.midi_feedpak import read_midi_roles

    plain, _, _ = read_midi_roles(tmp_path / "none" / "rubato.mid")
    lead, _, _ = read_midi_roles(tmp_path / "lead" / "rubato.mid")
    first_plain = min(n.t_on for v in plain.values() for n in v)
    first_lead = min(n.t_on for v in lead.values() for n in v)

    assert first_lead - first_plain == pytest.approx(2.0, abs=0.01)


def test_lead_in_defaults_to_zero(tmp_path):
    """An offset nobody recorded is how the packs came out misaligned."""
    src = tmp_path / "rubato.mid"
    _rubato_midi(src)
    payload = prepare_render(src, tmp_path / "out")
    assert payload["lead_in_sec"] == 0.0
