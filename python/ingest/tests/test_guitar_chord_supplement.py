"""Tests for the guitar chord-tone supplement.

Uses synthesised strummed-chord audio: each strum is the sum of sine
tones at the target MIDI pitches with a pluck-style envelope and a
tiny amount of broadband noise so librosa's onset detector has real
signal to pick.

If numpy / librosa / soundfile aren't installed the whole module fails
to import and ``conftest.py``'s collect-ignore skips it.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def _midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _write_strum_wav(
    path: Path,
    *,
    sr: int = 22_050,
    duration_sec: float = 2.5,
    strums: list[tuple[float, list[int], float]] | None = None,
    noise_amp: float = 0.001,
) -> None:
    """Write a synthetic strummed-chord WAV.

    Each strum is (onset_sec, list of MIDI pitches, amplitude). Every
    tone has a fast attack + slow decay envelope so onset detection
    sees a clean transient and the FFT window has enough steady state
    to lock the pitches.
    """
    import random

    random.seed(0)
    total = int(sr * duration_sec)
    y = [0.0] * total
    strums = strums or []

    for onset_sec, pitches, amp in strums:
        start = max(0, int(onset_sec * sr))
        # Each strum rings for ~0.7 s so the 200 ms analysis window is
        # always inside sustain.
        ring_len = int(0.7 * sr)
        for frame in range(ring_len):
            idx = start + frame
            if idx >= total:
                break
            t = frame / float(sr)
            # Fast attack (5 ms) + exponential decay.
            attack = min(1.0, t / 0.005)
            decay = math.exp(-2.5 * t)
            env = attack * decay
            sample = 0.0
            for pitch in pitches:
                f = _midi_to_hz(pitch)
                # First two harmonics -- keeps peaks distinct without
                # smearing across neighbouring bins.
                sample += math.sin(2.0 * math.pi * f * t)
                sample += 0.35 * math.sin(2.0 * math.pi * f * 2.0 * t)
            y[idx] += amp * env * sample

    # Sprinkle a floor of broadband noise so librosa's spectral flux
    # isn't looking at exact-zero silence between strums.
    for i in range(total):
        y[i] += noise_amp * (random.random() * 2.0 - 1.0)

    peak = max((abs(s) for s in y), default=1.0)
    if peak > 0:
        scale = 0.9 / peak
        y = [s * scale for s in y]

    with wave.open(str(path), "wb") as h:
        h.setnchannels(1)
        h.setsampwidth(2)
        h.setframerate(sr)
        for s in y:
            clipped = max(-1.0, min(1.0, s))
            h.writeframesraw(struct.pack("<h", int(clipped * 32767.0)))


def _write_silent_wav(path: Path, *, sr: int = 22_050, duration_sec: float = 1.0) -> None:
    total = int(sr * duration_sec)
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1)
        h.setsampwidth(2)
        h.setframerate(sr)
        for _ in range(total):
            h.writeframesraw(struct.pack("<h", 0))


# ---------------------------------------------------------------------------
# detect_strum_onsets
# ---------------------------------------------------------------------------


def test_detect_strum_onsets_finds_synthetic_strums(tmp_path: Path) -> None:
    import librosa

    from aural_ingest.algorithms.guitar_chord_supplement import (
        GuitarChordSupplementConfig,
        detect_strum_onsets,
    )

    stem = tmp_path / "strums.wav"
    _write_strum_wav(
        stem,
        duration_sec=3.0,
        strums=[
            (0.20, [48, 52, 55, 60, 64], 0.5),
            (1.20, [48, 52, 55, 60, 64], 0.5),
            (2.20, [48, 52, 55, 60, 64], 0.5),
        ],
    )
    y, sr = librosa.load(str(stem), sr=None, mono=True)
    onsets = detect_strum_onsets(y, sr, GuitarChordSupplementConfig())

    # Each of the three strums should have a detected onset within a
    # reasonable tolerance. Extra decay-tail onsets are acceptable --
    # they're a property of the sustaining synthetic envelope and
    # would be handled by the supplement's dedupe stage in practice.
    assert len(onsets) >= 3, onsets
    for expected in (0.20, 1.20, 2.20):
        matches = [t for t in onsets if abs(t - expected) < 0.08]
        assert matches, (expected, onsets)


def test_detect_strum_onsets_silent_audio_returns_empty(tmp_path: Path) -> None:
    import librosa

    from aural_ingest.algorithms.guitar_chord_supplement import detect_strum_onsets

    stem = tmp_path / "silent.wav"
    _write_silent_wav(stem)
    y, sr = librosa.load(str(stem), sr=None, mono=True)
    assert detect_strum_onsets(y, sr) == []


# ---------------------------------------------------------------------------
# estimate_chord_tones_at
# ---------------------------------------------------------------------------


def test_estimate_chord_tones_detects_c_major_triad(tmp_path: Path) -> None:
    import librosa

    from aural_ingest.algorithms.guitar_chord_supplement import (
        GuitarChordSupplementConfig,
        estimate_chord_tones_at,
    )

    stem = tmp_path / "cmaj.wav"
    _write_strum_wav(
        stem,
        duration_sec=1.5,
        strums=[(0.10, [48, 52, 55], 0.6)],  # C3 E3 G3
    )
    y, sr = librosa.load(str(stem), sr=None, mono=True)
    tones = estimate_chord_tones_at(y, sr, 0.10, GuitarChordSupplementConfig())

    assert set([48, 52, 55]).issubset(set(tones)), tones


# ---------------------------------------------------------------------------
# supplement_rhythm_guitar
# ---------------------------------------------------------------------------


def _c_major_strum(tmp_path: Path) -> Path:
    stem = tmp_path / "cmaj_strum.wav"
    _write_strum_wav(
        stem,
        duration_sec=2.0,
        strums=[
            (0.15, [48, 52, 55, 60, 64], 0.55),
            (1.15, [48, 52, 55, 60, 64], 0.55),
        ],
    )
    return stem


def test_supplement_adds_missing_fifth(tmp_path: Path) -> None:
    """Neural base emits C and E at the strum, missing the G. The
    supplement must add G (MIDI 55) at that onset.
    """
    from aural_ingest.algorithms.guitar_chord_supplement import (
        supplement_rhythm_guitar,
    )
    from aural_ingest.transcription import MelodicNote

    stem = _c_major_strum(tmp_path)

    base = [
        # First strum: C3, E3 -- missing G3 (55)
        MelodicNote(t_on=0.15, t_off=0.55, pitch=48, velocity=80, instrument="guitar"),
        MelodicNote(t_on=0.15, t_off=0.55, pitch=52, velocity=80, instrument="guitar"),
        # Second strum: full chord already provided
        MelodicNote(t_on=1.15, t_off=1.55, pitch=48, velocity=80, instrument="guitar"),
        MelodicNote(t_on=1.15, t_off=1.55, pitch=52, velocity=80, instrument="guitar"),
        MelodicNote(t_on=1.15, t_off=1.55, pitch=55, velocity=80, instrument="guitar"),
    ]

    result = supplement_rhythm_guitar(base, stem)

    # G3 should now be present at the first strum.
    first = [n for n in result if abs(n.t_on - 0.15) < 0.05 and n.pitch == 55]
    assert first, (
        "supplement did not add the missing G3 at the first strum: "
        f"{sorted((n.t_on, n.pitch) for n in result)}"
    )
    # Base notes still present (no removal).
    for base_note in base:
        assert base_note in result, base_note


def test_supplement_no_duplicates_when_chord_complete(tmp_path: Path) -> None:
    """If every chord tone is already in the base, the supplement must
    not add duplicates of any of those pitches.
    """
    from aural_ingest.algorithms.guitar_chord_supplement import (
        supplement_rhythm_guitar,
    )
    from aural_ingest.transcription import MelodicNote

    stem = _c_major_strum(tmp_path)

    # Provide C, E, G, C, E at both strums -- full base coverage of
    # the detected chord tones.
    base: list[MelodicNote] = []
    for onset in (0.15, 1.15):
        for pitch in (48, 52, 55, 60, 64):
            base.append(
                MelodicNote(
                    t_on=onset, t_off=onset + 0.4,
                    pitch=pitch, velocity=80, instrument="guitar",
                )
            )

    result = supplement_rhythm_guitar(base, stem)

    # No pitch that was already in the base should have a second copy
    # emitted at the same onset.
    for onset in (0.15, 1.15):
        for pitch in (48, 52, 55, 60, 64):
            hits = [
                n for n in result
                if abs(n.t_on - onset) < 0.05 and n.pitch == pitch
            ]
            assert len(hits) == 1, (onset, pitch, hits)


def test_supplement_silent_audio_returns_input_unchanged(tmp_path: Path) -> None:
    from aural_ingest.algorithms.guitar_chord_supplement import (
        supplement_rhythm_guitar,
    )
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "silent.wav"
    _write_silent_wav(stem, duration_sec=2.0)

    base = [
        MelodicNote(t_on=0.10, t_off=0.30, pitch=60, velocity=80, instrument="guitar"),
        MelodicNote(t_on=0.10, t_off=0.30, pitch=64, velocity=80, instrument="guitar"),
    ]
    result = supplement_rhythm_guitar(base, stem)

    # Same set of notes (order shouldn't matter, but the base is
    # already sorted so it must equal exactly).
    assert result == sorted(base, key=lambda n: (n.t_on, n.pitch))


def test_supplement_is_deterministic(tmp_path: Path) -> None:
    from aural_ingest.algorithms.guitar_chord_supplement import (
        supplement_rhythm_guitar,
    )
    from aural_ingest.transcription import MelodicNote

    stem = _c_major_strum(tmp_path)

    base = [
        MelodicNote(t_on=0.15, t_off=0.55, pitch=48, velocity=80, instrument="guitar"),
    ]
    a = supplement_rhythm_guitar(base, stem)
    b = supplement_rhythm_guitar(base, stem)
    assert a == b
