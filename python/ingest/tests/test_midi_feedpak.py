"""Unit tests for the MIDI-source feedpak path.

The load-bearing property is that note times come out of a pack exactly as they
went into it. The classical set was imported through MusicXML, which carries
note VALUES rather than milliseconds, so an unquantised performance was snapped
to the nearest notated subdivision and the error accumulated -- Fur Elise ended
5.8s long, Debussy 36s. These tests exist so that cannot come back quietly.
"""
from __future__ import annotations

import json
from pathlib import Path

import mido
import pytest

from aural_ingest.midi_feedpak import (
    build_feedpak_from_midi,
    read_midi_roles,
    role_for_track,
    tempo_map,
)


def _write_midi(path: Path, notes, *, tpb=480, tempos=((0, 500000),), track_name="Piano"):
    """A MIDI with the given (start_tick, dur_ticks, pitch) notes."""
    mid = mido.MidiFile(ticks_per_beat=tpb)

    meta = mido.MidiTrack()
    prev = 0
    for tick, tempo in tempos:
        meta.append(mido.MetaMessage("set_tempo", tempo=tempo, time=tick - prev))
        prev = tick
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    events = []
    for start, dur, pitch in notes:
        events.append((start, 1, pitch))
        events.append((start + dur, 0, pitch))
    events.sort(key=lambda e: (e[0], e[1]))
    prev = 0
    for tick, kind, pitch in events:
        track.append(mido.Message("note_on" if kind else "note_off",
                                  note=pitch, velocity=96 if kind else 0,
                                  time=tick - prev, channel=0))
        prev = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    mid.save(str(path))
    return mid


def _write_wav(path: Path, seconds=2.0, rate=8000):
    import numpy as np
    import soundfile as sf

    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    sf.write(str(path), (0.1 * np.sin(2 * np.pi * 220 * t)).astype("float32"), rate)


# ---------------------------------------------------------------------------
# role mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Piano", "keys"),
        ("PIANO RH", "keys"),
        ("Lead Guitar", "lead_guitar"),
        ("Rhythm Guitar", "rhythm_guitar"),
        # Bare "guitar" must not be swallowed by the lead/rhythm hints, and
        # must not fall through to the keys default either.
        ("Guitar", "rhythm_guitar"),
        ("Bass Gtr", "bass"),
        ("Lead Vocal", "vocals"),
        ("Drums", "drums"),
        ("", "keys"),
        ("Untitled", "keys"),
    ],
)
def test_role_for_track(name, expected):
    assert role_for_track(name) == expected


# ---------------------------------------------------------------------------
# timing -- the reason this module exists
# ---------------------------------------------------------------------------


def test_note_times_survive_the_round_trip_exactly(tmp_path):
    """Times out == times in. This is the property MusicXML lost."""
    src = tmp_path / "src.mid"
    # Deliberately off-grid: 1/3 and 1/7 of a beat land between any notated
    # subdivision, which is exactly what a human performance looks like and
    # exactly what notation cannot hold.
    _write_midi(src, [(0, 240, 60), (160, 240, 64), (411, 137, 67)])
    _write_wav(tmp_path / "src.wav")

    roles, duration, _ = read_midi_roles(src)
    times = sorted(n.t_on for n in roles["keys"])

    build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")
    pack = tmp_path / "out" / "src.feedpak"
    pm_notes = mido.MidiFile(pack / "aural" / "notes.mid")

    tmap = tempo_map(pm_notes)
    tpb = pm_notes.ticks_per_beat
    out_times = []
    for track in pm_notes.tracks:
        if track.name != "Keys":
            continue
        now = 0
        for msg in track:
            now += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                base_tick, base_sec, tempo = tmap[0]
                out_times.append(base_sec + mido.tick2second(now - base_tick, tpb, tempo))

    assert len(out_times) == len(times)
    for want, got in zip(times, sorted(out_times)):
        # A millisecond of tick quantisation is unavoidable; the failure this
        # guards against was seconds.
        assert got == pytest.approx(want, abs=0.002)


def test_tempo_changes_are_kept_not_averaged(tmp_path):
    """A rubato file must not be flattened to one bpm on the way in."""
    src = tmp_path / "rubato.mid"
    _write_midi(src, [(0, 240, 60), (480, 240, 62), (960, 240, 64)],
                tempos=((0, 500000), (480, 750000), (960, 400000)))
    _write_wav(tmp_path / "rubato.wav")

    result = build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")
    assert result["tempo_segments"] == 3

    tempo_json = json.loads(
        (Path(result["feedpak"]) / "song_timeline.json").read_text(encoding="utf-8")
    )
    assert tempo_json  # writer produced a timeline rather than dropping it


def test_beats_follow_the_tempo_map(tmp_path):
    """Beat spacing has to change where the tempo changes, not stay uniform."""
    src = tmp_path / "bend.mid"
    _write_midi(src, [(0, 240, 60), (1920, 240, 62)],
                tempos=((0, 500000), (960, 1000000)))
    # The tempo halves at beat 2, so the last note lands at 3s: the render has
    # to cover that or the coverage gate rejects it, quite correctly.
    _write_wav(tmp_path / "bend.wav", seconds=3.5)
    build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")

    beats = json.loads((tmp_path / "out" / "bend.feedpak" / "song_timeline.json")
                       .read_text(encoding="utf-8"))
    assert beats


# ---------------------------------------------------------------------------
# refusals -- a wrong pack is worse than no pack
# ---------------------------------------------------------------------------


def test_refuses_a_midi_with_no_notes(tmp_path):
    src = tmp_path / "empty.mid"
    _write_midi(src, [])
    _write_wav(tmp_path / "empty.wav")
    with pytest.raises(ValueError, match="no notes"):
        build_feedpak_from_midi(src, tmp_path / "out")


def test_refuses_when_no_audio_can_be_found(tmp_path):
    src = tmp_path / "lonely.mid"
    _write_midi(src, [(0, 240, 60)])
    with pytest.raises(ValueError, match="no audio"):
        build_feedpak_from_midi(src, tmp_path / "out")


def test_finds_the_render_beside_the_midi(tmp_path):
    src = tmp_path / "paired.mid"
    _write_midi(src, [(0, 240, 60)])
    _write_wav(tmp_path / "paired.wav")
    result = build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")
    assert result["audio_attached"] is True


def test_unmatched_note_off_is_dropped_not_guessed(tmp_path):
    """A release with nothing holding it is a malformed file, not a note."""
    src = tmp_path / "orphan.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Piano", time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=100))
    track.append(mido.Message("note_on", note=64, velocity=90, time=0))
    track.append(mido.Message("note_off", note=64, velocity=0, time=240))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    mid.save(str(src))

    roles, _, _ = read_midi_roles(src)
    assert [n.pitch for n in roles["keys"]] == [64]


# ---------------------------------------------------------------------------
# studio renders -- audio bounced by hand, which is where it goes wrong
# ---------------------------------------------------------------------------


def test_refuses_a_render_that_stops_before_the_last_note(tmp_path):
    """A short bounce is a stall in Wait mode, so it fails at import instead."""
    src = tmp_path / "truncated.mid"
    # Last note starts at beat 20 == 10s at 120bpm; the render is 2s.
    _write_midi(src, [(0, 240, 60), (9600, 240, 72)])
    _write_wav(tmp_path / "truncated.wav", seconds=2.0)

    with pytest.raises(ValueError, match="stops short"):
        build_feedpak_from_midi(src, tmp_path / "out")


def test_accepts_a_render_that_ends_just_after_the_last_onset(tmp_path):
    """The final note is released into a fade; that is not a truncated render."""
    src = tmp_path / "tight.mid"
    _write_midi(src, [(0, 240, 60), (960, 480, 64)])   # last onset at 1.0s
    _write_wav(tmp_path / "tight.wav", seconds=1.4)

    result = build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")
    assert result["ok"] is True
    assert result["last_note_onset_sec"] == pytest.approx(1.0, abs=0.01)


def test_unreadable_audio_header_is_not_treated_as_evidence(tmp_path, monkeypatch):
    """Not being able to measure the render is not the same as it being wrong."""
    from aural_ingest import midi_feedpak

    src = tmp_path / "opaque.mid"
    _write_midi(src, [(0, 240, 60), (9600, 240, 72)])
    _write_wav(tmp_path / "opaque.wav", seconds=2.0)
    monkeypatch.setattr(midi_feedpak, "audio_duration_sec", lambda _p: None)

    result = build_feedpak_from_midi(src, tmp_path / "out", title="T", artist="A")
    assert result["ok"] is True
    assert result["audio_sec"] is None
