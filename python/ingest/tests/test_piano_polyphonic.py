import math
import struct
import wave
from pathlib import Path


def _write_polyphonic_fixture(
    path: Path,
    *,
    sr: int = 24_000,
    duration_sec: float = 1.0,
    notes: list[tuple[float, float, int, float]] | None = None,
) -> None:
    total_frames = int(sr * duration_sec)
    samples = [0.0 for _ in range(total_frames)]

    for start_sec, note_duration_sec, pitch, amplitude in notes or []:
        start = max(0, int(start_sec * sr))
        synth_len = int((note_duration_sec + 0.12) * sr)
        freq = 440.0 * (2.0 ** ((float(pitch) - 69.0) / 12.0))
        for frame_index in range(synth_len):
            idx = start + frame_index
            if idx >= total_frames:
                break
            t = frame_index / float(sr)
            attack = min(1.0, t / 0.01)
            decay = math.exp(-3.2 * (t / max(0.1, note_duration_sec)))
            if t > note_duration_sec:
                decay *= math.exp(-18.0 * (t - note_duration_sec))

            sample = 0.0
            for harmonic, gain in ((1, 1.0), (2, 0.43), (3, 0.24), (4, 0.14)):
                sample += gain * math.sin(2.0 * math.pi * freq * harmonic * t)
            samples[idx] += amplitude * attack * decay * sample

    peak = max((abs(sample) for sample in samples), default=1.0)
    if peak > 0:
        samples = [sample / peak * 0.92 for sample in samples]

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        for sample in samples:
            clipped = max(-1.0, min(1.0, sample))
            handle.writeframesraw(struct.pack("<h", int(clipped * 32767.0)))


def test_piano_polyphonic_detects_simple_major_triad(tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_polyphonic

    stem = tmp_path / "triad.wav"
    _write_polyphonic_fixture(
        stem,
        duration_sec=0.9,
        notes=[
            (0.05, 0.42, 60, 0.75),
            (0.05, 0.42, 64, 0.68),
            (0.05, 0.42, 67, 0.64),
        ],
    )

    detected = piano_polyphonic.transcribe(stem, instrument="keys", max_polyphony=6)
    assert detected

    early_notes = [note for note in detected if note.t_on <= 0.16]
    assert early_notes

    for expected_pitch in (60, 64, 67):
        assert any(abs(note.pitch - expected_pitch) <= 1 for note in early_notes)


def test_piano_polyphonic_splits_repeated_same_pitch_attacks(tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_polyphonic

    stem = tmp_path / "retrigger.wav"
    _write_polyphonic_fixture(
        stem,
        duration_sec=0.95,
        notes=[
            (0.05, 0.18, 60, 0.78),
            (0.35, 0.18, 60, 0.82),
        ],
    )

    detected = piano_polyphonic.transcribe(stem, instrument="keys", max_polyphony=4)
    repeated = [note for note in detected if abs(note.pitch - 60) <= 1]

    assert len(repeated) >= 2
    onsets = sorted(note.t_on for note in repeated)
    assert onsets[0] <= 0.12
    assert any(onset >= 0.28 for onset in onsets[1:])


def test_piano_polyphonic_restarts_repeated_chord_attacks(tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_polyphonic

    stem = tmp_path / "repeated_chord.wav"
    _write_polyphonic_fixture(
        stem,
        duration_sec=1.0,
        notes=[
            (0.05, 0.20, 60, 0.72),
            (0.05, 0.20, 64, 0.68),
            (0.05, 0.20, 67, 0.64),
            (0.36, 0.20, 60, 0.78),
            (0.36, 0.20, 64, 0.73),
            (0.36, 0.20, 67, 0.68),
        ],
    )

    detected = piano_polyphonic.transcribe(stem, instrument="keys", max_polyphony=6)
    restarted = 0
    for expected_pitch in (60, 64, 67):
        pitch_notes = [note for note in detected if abs(note.pitch - expected_pitch) <= 1]
        if pitch_notes and any(note.t_on >= 0.28 for note in pitch_notes[1:]):
            restarted += 1

    assert restarted >= 2
