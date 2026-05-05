import math
import struct
import wave
from pathlib import Path


def _write_level_burst_wav(
    path: Path,
    *,
    sr: int = 24_000,
    duration_sec: float = 1.0,
    bursts: list[tuple[float, float, float]] | None = None,
) -> None:
    total_frames = int(sr * duration_sec)
    samples = [0.0 for _ in range(total_frames)]
    for start_sec, burst_duration_sec, amplitude in bursts or []:
        start = max(0, int(start_sec * sr))
        length = max(1, int(burst_duration_sec * sr))
        for frame in range(length):
            idx = start + frame
            if idx >= total_frames:
                break
            samples[idx] += amplitude * math.sin(2.0 * math.pi * 440.0 * (frame / sr))

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        for sample in samples:
            clipped = max(-1.0, min(1.0, sample))
            handle.writeframesraw(struct.pack("<h", int(clipped * 32767.0)))


def _write_note_burst_wav(
    path: Path,
    *,
    sr: int = 24_000,
    duration_sec: float = 1.0,
    bursts: list[tuple[float, float, int, float]] | None = None,
) -> None:
    total_frames = int(sr * duration_sec)
    samples = [0.0 for _ in range(total_frames)]
    for start_sec, burst_duration_sec, pitch, amplitude in bursts or []:
        start = max(0, int(start_sec * sr))
        length = max(1, int(burst_duration_sec * sr))
        freq = 440.0 * (2.0 ** ((float(pitch) - 69.0) / 12.0))
        for frame in range(length):
            idx = start + frame
            if idx >= total_frames:
                break
            t = frame / sr
            env = math.exp(-3.8 * (t / max(0.03, burst_duration_sec)))
            sample = (
                math.sin(2.0 * math.pi * freq * t)
                + 0.36 * math.sin(2.0 * math.pi * freq * 2.0 * t)
                + 0.18 * math.sin(2.0 * math.pi * freq * 3.0 * t)
            )
            samples[idx] += amplitude * env * sample

    peak = max((abs(sample) for sample in samples), default=1.0)
    if peak > 0.0:
        samples = [sample / peak * 0.9 for sample in samples]

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        for sample in samples:
            clipped = max(-1.0, min(1.0, sample))
            handle.writeframesraw(struct.pack("<h", int(clipped * 32767.0)))


def test_cleanup_notes_dedupes_merges_and_clamps_piano_range() -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.0, t_off=0.2, pitch=18, velocity=70),
            MelodicNote(t_on=0.1, t_off=0.14, pitch=60, velocity=38),
            MelodicNote(t_on=0.118, t_off=0.26, pitch=60, velocity=74),
            MelodicNote(t_on=0.5, t_off=0.56, pitch=64, velocity=80),
                MelodicNote(t_on=0.575, t_off=0.7, pitch=64, velocity=82),
            MelodicNote(t_on=0.8, t_off=0.82, pitch=67, velocity=26),
        ],
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [60, 64]
    assert cleaned[0].t_on == 0.1
    assert cleaned[0].velocity == 74
    assert cleaned[0].t_off > 0.26
    assert cleaned[1].t_on == 0.5
    assert cleaned[1].t_off > 0.7
    assert all(note.instrument == "keys" for note in cleaned)


def test_cleanup_notes_blends_velocity_from_audio_energy(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "piano.wav"
    _write_level_burst_wav(
        stem,
        bursts=[
            (0.1, 0.03, 0.12),
            (0.4, 0.03, 0.85),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.24, pitch=60, velocity=70),
            MelodicNote(t_on=0.4, t_off=0.55, pitch=67, velocity=70),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert len(cleaned) == 2
    assert cleaned[1].velocity > cleaned[0].velocity


def test_cleanup_notes_pitch_aware_velocity_does_not_overboost_bass(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "same_amp_low_high.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.18, 36, 0.8),
            (0.45, 0.18, 84, 0.8),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.28, pitch=36, velocity=82),
            MelodicNote(t_on=0.45, t_off=0.63, pitch=84, velocity=82),
        ],
        stem_path=stem,
        instrument="keys",
    )

    by_pitch = {note.pitch: note.velocity for note in cleaned}
    assert by_pitch[84] >= by_pitch[36] - 4


def test_cleanup_notes_splits_long_note_at_audio_reattack(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "reattack.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.95,
        bursts=[
            (0.08, 0.18, 60, 0.85),
            (0.42, 0.18, 60, 0.92),
        ],
    )

    cleaned = cleanup_notes(
        [MelodicNote(t_on=0.07, t_off=0.62, pitch=60, velocity=78)],
        stem_path=stem,
        instrument="keys",
    )

    assert len(cleaned) >= 2
    assert cleaned[0].t_on <= 0.09
    assert cleaned[0].t_off < 0.42
    assert any(note.t_on >= 0.38 for note in cleaned[1:])


def test_cleanup_notes_prunes_false_low_octave_shadow_when_audio_lacks_lower_note(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "upper_only.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.22, 60, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=48, velocity=74),
            MelodicNote(t_on=0.1, t_off=0.34, pitch=60, velocity=78),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [60]


def test_cleanup_notes_keeps_real_low_octave_when_audio_contains_both_notes(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "real_octave.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.22, 48, 0.82),
            (0.1, 0.22, 60, 0.76),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=48, velocity=76),
            MelodicNote(t_on=0.1, t_off=0.34, pitch=60, velocity=74),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [48, 60]


def test_cleanup_notes_merges_interleaved_same_pitch_chatter() -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.22, pitch=60, velocity=72),
            MelodicNote(t_on=0.13, t_off=0.3, pitch=64, velocity=70),
            MelodicNote(t_on=0.17, t_off=0.36, pitch=60, velocity=74),
        ],
        instrument="keys",
    )

    assert [(note.pitch, note.t_on, note.t_off) for note in cleaned] == [
        (60, 0.1, cleaned[0].t_off),
        (64, 0.13, cleaned[1].t_off),
    ]
    assert cleaned[0].t_off > 0.36


def test_cleanup_notes_keeps_interleaved_same_pitch_audio_reattack(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "interleaved_reattack.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.2, 60, 0.8),
            (0.33, 0.2, 60, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.31, pitch=60, velocity=72),
            MelodicNote(t_on=0.18, t_off=0.42, pitch=64, velocity=70),
            MelodicNote(t_on=0.33, t_off=0.5, pitch=60, velocity=74),
        ],
        stem_path=stem,
        instrument="keys",
    )

    repeated = [note for note in cleaned if note.pitch == 60]
    assert len(repeated) >= 2
    assert repeated[0].t_off <= 0.322
    assert repeated[1].t_on >= 0.3


def test_cleanup_notes_prunes_dense_high_harmonic_shadows() -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.45, pitch=pitch, velocity=82)
            for pitch in (60, 64, 67, 72, 79, 84, 96, 103)
        ],
        instrument="keys",
    )

    pitches = [note.pitch for note in cleaned]
    assert len(pitches) <= 6
    assert 96 not in pitches
    assert 103 not in pitches


def test_cleanup_notes_prunes_unsupported_extreme_high_pitch(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "middle_only.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 60, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=60, velocity=82),
            MelodicNote(t_on=0.1, t_off=0.34, pitch=104, velocity=84),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [60]


def test_cleanup_notes_keeps_audio_supported_extreme_high_pitch(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "high_true.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 104, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [MelodicNote(t_on=0.1, t_off=0.34, pitch=104, velocity=82)],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [104]


def test_cleanup_notes_prunes_unsupported_two_octave_low_shadow(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "upper_bass_only.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 48, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=24, velocity=80),
            MelodicNote(t_on=0.1, t_off=0.34, pitch=48, velocity=82),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [48]


def test_cleanup_notes_keeps_audio_supported_extreme_low_pitch(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "low_true.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 24, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [MelodicNote(t_on=0.1, t_off=0.34, pitch=24, velocity=82)],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [24]


def test_cleanup_notes_prunes_unsupported_low_extreme_near_bass_boundary(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "middle_bass_only.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 48, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=31, velocity=86),
            MelodicNote(t_on=0.1, t_off=0.34, pitch=48, velocity=84),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [48]


def test_cleanup_notes_keeps_supported_low_extreme_near_bass_boundary(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "low_boundary_true.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 35, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [MelodicNote(t_on=0.1, t_off=0.34, pitch=35, velocity=86)],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [35]


def test_cleanup_notes_extends_sustain_while_pitch_band_decays(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "sustain_tail.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=1.1,
        bursts=[
            (0.1, 0.68, 60, 0.9),
        ],
    )

    cleaned = cleanup_notes(
        [MelodicNote(t_on=0.1, t_off=0.24, pitch=60, velocity=84)],
        stem_path=stem,
        instrument="keys",
    )

    assert len(cleaned) == 1
    assert cleaned[0].t_off > 0.45
    assert cleaned[0].t_off <= 1.1


def test_cleanup_notes_does_not_smear_sustain_across_next_same_pitch(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "same_pitch_restrike.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=1.0,
        bursts=[
            (0.1, 0.46, 60, 0.82),
            (0.46, 0.28, 60, 0.88),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.24, pitch=60, velocity=80),
            MelodicNote(t_on=0.46, t_off=0.58, pitch=60, velocity=84),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert len(cleaned) >= 2
    assert cleaned[0].t_off <= 0.452


def test_cleanup_notes_prunes_extreme_high_spray_near_midrange_attack(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_cleanup import cleanup_notes
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "midrange_attack.wav"
    _write_note_burst_wav(
        stem,
        duration_sec=0.8,
        bursts=[
            (0.1, 0.24, 67, 0.8),
            (0.1, 0.24, 79, 0.7),
        ],
    )

    cleaned = cleanup_notes(
        [
            MelodicNote(t_on=0.1, t_off=0.34, pitch=67, velocity=82),
            MelodicNote(t_on=0.11, t_off=0.32, pitch=79, velocity=78),
            MelodicNote(t_on=0.12, t_off=0.26, pitch=98, velocity=76),
            MelodicNote(t_on=0.13, t_off=0.27, pitch=102, velocity=74),
            MelodicNote(t_on=0.14, t_off=0.28, pitch=105, velocity=72),
        ],
        stem_path=stem,
        instrument="keys",
    )

    assert [note.pitch for note in cleaned] == [67, 79]
