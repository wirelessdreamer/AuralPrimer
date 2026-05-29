"""Tests for the plausibility scorer and the scored fallback gate."""
import struct
import wave
from pathlib import Path

from aural_ingest.transcription import (
    DrumEvent,
    MelodicNote,
    MIN_TRANSCRIPTION_SCORE,
    score_transcription,
    transcribe_drums_dsp,
    transcribe_melodic,
)


def _write_sustained_tone_wav(path: Path, *, sr: int = 16_000, duration_sec: float = 2.0) -> None:
    """A loud, fully-active stem (no silent regions)."""
    import math

    total = int(sr * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(total):
            sample = int(20000 * math.sin(2.0 * math.pi * 220.0 * (i / sr)))
            w.writeframesraw(struct.pack("<h", sample))


def _plausible_notes() -> list[MelodicNote]:
    # ~2 notes/sec over 2s, spanning ~1 octave -- a sane melodic line.
    return [
        MelodicNote(t_on=0.1, t_off=0.4, pitch=60, velocity=90),
        MelodicNote(t_on=0.5, t_off=0.8, pitch=62, velocity=88),
        MelodicNote(t_on=1.0, t_off=1.3, pitch=67, velocity=90),
        MelodicNote(t_on=1.5, t_off=1.9, pitch=64, velocity=86),
    ]


def _hallucination_notes() -> list[MelodicNote]:
    # Hundreds of onsets crammed into a fraction of a second, sprayed across
    # the whole keyboard -- the onset-spam failure mode.
    out: list[MelodicNote] = []
    for i in range(400):
        t = 0.001 * i
        pitch = 21 + (i * 7) % 87  # full 88-key spread
        out.append(MelodicNote(t_on=t, t_off=t + 0.01, pitch=pitch, velocity=80))
    return out


def test_empty_transcription_scores_zero() -> None:
    assert score_transcription([]) == 0.0


def test_plausible_notes_score_high() -> None:
    score = score_transcription(_plausible_notes())
    assert score >= MIN_TRANSCRIPTION_SCORE


def test_hallucination_notes_score_low() -> None:
    score = score_transcription(_hallucination_notes())
    assert score < MIN_TRANSCRIPTION_SCORE


def test_notes_in_silent_stem_regions_score_low(tmp_path: Path) -> None:
    # A 2s stem: loud for the first 0.6s (30% active -> reliable energy
    # reference), silent afterward.
    import math

    sr = 16_000
    stem = tmp_path / "stem.wav"
    with wave.open(str(stem), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(sr * 2):
            t = i / sr
            sample = int(20000 * math.sin(2.0 * math.pi * 220.0 * t)) if t < 0.6 else 0
            w.writeframesraw(struct.pack("<h", sample))

    # All onsets land in the silent tail (0.7s .. 1.65s).
    silent_region_notes = [
        MelodicNote(t_on=0.7 + 0.05 * i, t_off=0.7 + 0.05 * i + 0.02, pitch=60 + (i % 5), velocity=80)
        for i in range(20)
    ]
    score = score_transcription(silent_region_notes, stem)
    assert score < MIN_TRANSCRIPTION_SCORE


def test_drum_events_are_scorable() -> None:
    events = [DrumEvent(time=0.1 * i, note=36 + (i % 3), velocity=100) for i in range(8)]
    score = score_transcription(events)
    assert 0.0 <= score <= 1.0


def test_low_scoring_first_producer_does_not_shadow_high_scoring_later(tmp_path: Path) -> None:
    """The scored gate must skip a hallucination-like first result in favor of
    a later plausible one, instead of returning the first non-empty result."""
    stem = tmp_path / "stem.wav"
    _write_sustained_tone_wav(stem)

    calls: list[str] = []

    def hallucinating(_p: Path) -> list[MelodicNote]:
        calls.append("basic_pitch")
        return _hallucination_notes()

    def plausible(_p: Path) -> list[MelodicNote]:
        calls.append("pyin")
        return _plausible_notes()

    result = transcribe_melodic(
        stem,
        requested_method="basic_pitch",
        algorithm_registry={"basic_pitch": hallucinating, "pyin": plausible},
    )

    # First non-empty result would have been basic_pitch; the gate must reject
    # its low score and select the higher-scoring pyin.
    assert calls == ["basic_pitch", "pyin"]
    assert result.used_method == "pyin"
    assert result.notes == _plausible_notes()
    assert result.used_score is not None and result.used_score >= MIN_TRANSCRIPTION_SCORE
    assert "basic_pitch" in result.attempt_scores
    assert "pyin" in result.attempt_scores
    assert result.attempt_scores["basic_pitch"] < result.attempt_scores["pyin"]


def test_best_scoring_kept_when_none_clear_threshold(tmp_path: Path) -> None:
    """When no producer clears the threshold, the best-scoring non-empty
    result is returned rather than the last-tried or empty."""
    stem = tmp_path / "stem.wav"
    _write_sustained_tone_wav(stem)

    first_bad = _hallucination_notes()
    second_bad = _hallucination_notes()

    def first_fn(_p: Path) -> list[MelodicNote]:
        return first_bad

    def second_fn(_p: Path) -> list[MelodicNote]:
        return second_bad

    result = transcribe_melodic(
        stem,
        requested_method="basic_pitch",
        algorithm_registry={"basic_pitch": first_fn, "pyin": second_fn},
    )

    # Neither clears the threshold, so a best-scoring non-empty result is kept
    # (never empty when at least one producer returned notes).
    assert result.used_method is not None
    assert result.notes
    assert result.used_score is not None
    assert result.used_score < MIN_TRANSCRIPTION_SCORE
    # used_score equals the max across attempts.
    assert result.used_score == max(result.attempt_scores.values())


def test_melodic_exceptions_captured_into_warnings(tmp_path: Path) -> None:
    stem = tmp_path / "stem.wav"
    _write_sustained_tone_wav(stem)

    def boom(_p: Path) -> list[MelodicNote]:
        raise RuntimeError("model exploded")

    def ok(_p: Path) -> list[MelodicNote]:
        return _plausible_notes()

    result = transcribe_melodic(
        stem,
        requested_method="basic_pitch",
        algorithm_registry={"basic_pitch": boom, "pyin": ok},
    )

    assert result.used_method == "pyin"
    assert any("RuntimeError('model exploded')" in w for w in result.warnings)


def test_drum_gate_records_scores_and_captures_exceptions(tmp_path: Path) -> None:
    stem = tmp_path / "stem.wav"
    _write_sustained_tone_wav(stem)

    def boom(_p: Path) -> list[DrumEvent]:
        raise RuntimeError("drum boom")

    def ok(_p: Path) -> list[DrumEvent]:
        return [DrumEvent(time=0.2 * i, note=36, velocity=100) for i in range(6)]

    result = transcribe_drums_dsp(
        stem,
        requested_filter="combined_filter",
        algorithm_registry={"combined_filter": boom, "dsp_bandpass_improved": ok},
    )

    assert result.used_algorithm == "dsp_bandpass_improved"
    assert any("RuntimeError('drum boom')" in w for w in result.warnings)
    assert "dsp_bandpass_improved" in result.meta.get("attempt_scores", {})
    assert result.meta.get("used_score") is not None
