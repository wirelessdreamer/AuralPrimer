"""Top-10 ingest pipeline quality regression battery.

Each test exercises one quality dimension with a small synthetic fixture and
asserts a bound. Bounds are intentionally generous - they catch gross
regressions without being brittle to small DSP tuning changes. Tighten only
once a tighter operating point is benchmarked end-to-end.
"""
from __future__ import annotations

import math
import struct
import wave
from collections import Counter
from pathlib import Path

import pytest


# ----- fixture builders --------------------------------------------------


def _write_mono_int16(path: Path, samples, sr: int) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for s in samples:
            v = max(-1.0, min(1.0, s))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def _kick_pulse(samples, start: int, sr: int) -> None:
    pulse_len = int(round(0.08 * sr))
    for i in range(pulse_len):
        if start + i >= len(samples):
            return
        env = math.exp(-6.0 * (i / float(pulse_len)))
        samples[start + i] += 0.85 * math.sin(2.0 * math.pi * 58.0 * (i / float(sr))) * env


class _RNG:
    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFF
    def _next(self) -> int:
        self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
        return self._state
    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * (self._next() / float(0xFFFFFFFF))


def _snare_pulse(samples, start: int, sr: int, *, rng_seed: int = 42) -> None:
    pulse_len = int(round(0.05 * sr))
    rng = _RNG(rng_seed + start)
    for i in range(pulse_len):
        if start + i >= len(samples):
            return
        env = math.exp(-12.0 * (i / float(pulse_len)))
        noise = rng.uniform(-1.0, 1.0) * 0.7
        crack = math.sin(2.0 * math.pi * 2000.0 * (i / float(sr))) * 0.25
        samples[start + i] += (noise + crack) * env


def _hat_pulse(samples, start: int, sr: int, *, rng_seed: int = 7) -> None:
    pulse_len = int(round(0.02 * sr))
    rng = _RNG(rng_seed + start)
    for i in range(pulse_len):
        if start + i >= len(samples):
            return
        env = math.exp(-25.0 * (i / float(pulse_len)))
        noise = rng.uniform(-1.0, 1.0) * 0.45
        bright = math.sin(2.0 * math.pi * 7500.0 * (i / float(sr))) * 0.2
        samples[start + i] += (noise + bright) * env


def _sustained_sine(samples, start: int, dur_samples: int, freq: float, sr: int, amp: float = 0.6) -> None:
    for i in range(dur_samples):
        if start + i >= len(samples):
            return
        a = min(i / max(1, int(0.01 * sr)), 1.0)
        r = min((dur_samples - i) / max(1, int(0.01 * sr)), 1.0)
        env = max(0.0, min(1.0, a)) * max(0.0, min(1.0, r))
        samples[start + i] += amp * env * math.sin(2.0 * math.pi * freq * (i / float(sr)))


def _drum_class_counts(events) -> Counter:
    return Counter(e.note for e in events)


def _events_in_window(times, target: float, window: float) -> int:
    return sum(1 for t in times if abs(t - target) <= window)


# ----- dimension 1 -----


def test_quality_01_sparse_source_snare_precision(tmp_path, monkeypatch) -> None:
    """Snare-only sparse stem must not trigger the dense fallback grid."""
    from aural_ingest.algorithms import combined_filter

    sr = 48_000
    n = int(round(sr * 5.0))
    samples = [0.0] * n
    for t in (0.5, 1.5, 2.5, 3.5):
        _snare_pulse(samples, int(round(t * sr)), sr)
    fixture = tmp_path / "snare_only.wav"
    _write_mono_int16(fixture, samples, sr)

    fallback_calls = []
    real_fb = combined_filter.fallback_events_from_classes
    monkeypatch.setattr(combined_filter, "fallback_events_from_classes",
        lambda *a, **k: (fallback_calls.append(1) or real_fb(*a, **k)))

    events = combined_filter.transcribe(fixture)
    assert not fallback_calls
    assert 1 <= len(events) <= 12


# ----- dimension 2 -----
# Previously xfail (documented Psalm 12-style regression). Fixed 2026-05-07
# by:
#   (a) dropping the stale `kick + centroid > 520 -> tom_floor` post-classifier
#       rule (centroid thresholds are unreliable on real recordings — see
#       2018 Wu/Lerch survey, 2026 Towards-Realistic-Synthetic-Data paper),
#   (b) adding a low-band-energy guard that prevents bass-dominant onsets
#       (low_dom > 0.55) from being classified as crash / ride / hh_open,
#       and
#   (c) adding a stem-level unanimous-detector boost so a class-consistent
#       detector (e.g. aural_onset emitting only kicks on a kick-only stem)
#       can overrule the louder dsp_bandpass_improved when its candidates
#       disagree.
# See docs/research-deep-dive-adt-2026-05-07.md paths 5 and 6.


def test_quality_02_kick_stem_not_classified_as_crash_or_ride(tmp_path) -> None:
    from aural_ingest.algorithms import combined_filter

    sr = 48_000
    n = int(round(sr * 6.0))
    samples = [0.0] * n
    for t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
        _kick_pulse(samples, int(round(t * sr)), sr)
    fixture = tmp_path / "kick_only.wav"
    _write_mono_int16(fixture, samples, sr)

    events = combined_filter.transcribe(fixture)
    counts = _drum_class_counts(events)
    crash_ride = counts.get(49, 0) + counts.get(51, 0)
    total = sum(counts.values())
    if total > 0:
        assert crash_ride <= max(2, total // 3), f"crash+ride={crash_ride}/{total} dist={dict(counts)}"

    # Tighter bound now that the Phase 1 fix is in place: at least half of
    # the events must be kick (note 36) on a pure-kick stem.
    kick_count = counts.get(36, 0)
    if total > 0:
        assert kick_count / float(total) >= 0.5, (
            f"kick fraction {kick_count}/{total} below 0.5 on a pure kick stem; "
            f"distribution: {dict(counts)}"
        )


def test_quality_02_alt_engines_classify_kick_correctly(tmp_path) -> None:
    """Iteration result: gameplay_default's first two engines pass where
    combined_filter fails."""
    from aural_ingest.transcription import build_default_drum_algorithm_registry

    sr = 48_000
    n = int(round(sr * 6.0))
    samples = [0.0] * n
    for t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
        _kick_pulse(samples, int(round(t * sr)), sr)
    fixture = tmp_path / "kick_only_alt.wav"
    _write_mono_int16(fixture, samples, sr)

    registry = build_default_drum_algorithm_registry()
    for engine_id in ("beat_conditioned_multiband_decoder", "spectral_flux_multiband"):
        events = registry[engine_id](fixture)
        counts = _drum_class_counts(events)
        total = sum(counts.values()) or 1
        kick_frac = counts.get(36, 0) / float(total)
        assert kick_frac >= 0.7, f"{engine_id} kick_frac={kick_frac:.2f} dist={dict(counts)}"


# ----- dimension 3 -----


def test_quality_03_hat_stem_dominated_by_hats(tmp_path) -> None:
    from aural_ingest.algorithms import combined_filter

    sr = 48_000
    n = int(round(sr * 4.0))
    samples = [0.0] * n
    for t in [0.125 * i for i in range(2, 30)]:
        _hat_pulse(samples, int(round(t * sr)), sr)
    fixture = tmp_path / "hats.wav"
    _write_mono_int16(fixture, samples, sr)

    events = combined_filter.transcribe(fixture)
    counts = _drum_class_counts(events)
    hats = counts.get(42, 0) + counts.get(46, 0)
    nonhats = sum(v for k, v in counts.items() if k not in (42, 46))
    assert hats >= 1, f"zero hats on hat stream: {dict(counts)}"
    if hats + nonhats > 0:
        assert hats / float(hats + nonhats) >= 0.3, f"hats={hats} nonhats={nonhats}"


# ----- dimension 4 -----


def test_quality_04_drum_onset_timing_accuracy(tmp_path) -> None:
    from aural_ingest.algorithms import combined_filter

    sr = 48_000
    n = int(round(sr * 4.0))
    samples = [0.0] * n
    truth = [0.5, 1.5, 2.5, 3.5]
    for t in truth:
        _kick_pulse(samples, int(round(t * sr)), sr)
    fixture = tmp_path / "kicks_for_timing.wav"
    _write_mono_int16(fixture, samples, sr)

    events = combined_filter.transcribe(fixture)
    times = [e.time for e in events]
    matched = sum(_events_in_window(times, t, 0.020) >= 1 for t in truth)
    assert matched >= len(truth) - 1, f"timing match: {matched}/{len(truth)} times={times}"


# ----- dimension 5 -----


def test_quality_05_multiclass_recall(tmp_path) -> None:
    from aural_ingest.algorithms import combined_filter

    sr = 48_000
    n = int(round(sr * 6.0))
    samples = [0.0] * n
    for k in (0.5, 1.5, 2.5, 3.5, 4.5):
        _kick_pulse(samples, int(round(k * sr)), sr)
    for s in (1.0, 2.0, 3.0, 4.0, 5.0):
        _snare_pulse(samples, int(round(s * sr)), sr)
    for h in [0.25 * i for i in range(2, 22)]:
        _hat_pulse(samples, int(round(h * sr)), sr)
    fixture = tmp_path / "multiclass.wav"
    _write_mono_int16(fixture, samples, sr)

    events = combined_filter.transcribe(fixture)
    notes = {e.note for e in events}
    core = {n for n in (36, 38, 42) if n in notes}
    assert len(core) >= 2, f"core class recall: {sorted(core)} of {sorted(notes)}"


# ----- dimension 6 -----


def test_quality_06_no_chatter_within_refractory(tmp_path) -> None:
    from aural_ingest.algorithms import combined_filter
    from aural_ingest.algorithms._common import CLASS_REFRACTORY_SEC, DRUM_CLASS_TO_MIDI

    sr = 48_000
    n = int(round(sr * 6.0))
    samples = [0.0] * n
    for k in (0.5, 1.5, 2.5, 3.5):
        _kick_pulse(samples, int(round(k * sr)), sr)
    for s in (1.0, 2.0, 3.0, 4.0):
        _snare_pulse(samples, int(round(s * sr)), sr)
    for h in [0.25 * i for i in range(2, 22)]:
        _hat_pulse(samples, int(round(h * sr)), sr)
    fixture = tmp_path / "chatter_check.wav"
    _write_mono_int16(fixture, samples, sr)

    events = sorted(combined_filter.transcribe(fixture), key=lambda e: e.time)
    midi_to_class = {v: k for k, v in DRUM_CLASS_TO_MIDI.items()}
    last_time_by_class = {}
    violations = []
    for e in events:
        cls = midi_to_class.get(e.note)
        if cls is None:
            continue
        refract = CLASS_REFRACTORY_SEC.get(cls, 0.05)
        prev = last_time_by_class.get(e.note)
        if prev is not None and (e.time - prev) < (refract * 0.9):
            violations.append((e.note, prev, e.time))
        last_time_by_class[e.note] = e.time
    assert not violations, f"refractory violations: {violations[:5]}"


# ----- dimension 7 -----


def test_quality_07_melodic_pyin_tracks_sustained_sine(tmp_path) -> None:
    from aural_ingest.algorithms import melodic_pyin

    sr = 48_000
    n = int(round(sr * 2.0))
    samples = [0.0] * n
    _sustained_sine(samples, int(0.2 * sr), int(1.5 * sr), 220.0, sr, amp=0.7)
    fixture = tmp_path / "sine_a3.wav"
    _write_mono_int16(fixture, samples, sr)

    notes = melodic_pyin.transcribe(fixture)
    assert notes
    targets = {56, 57, 58}
    matches = [m for m in notes if m.pitch in targets]
    assert matches, f"pyin missed A3; pitches={[m.pitch for m in notes]}"


# ----- dimension 8 -----


def test_quality_08_melodic_no_blanket_octave_error(tmp_path) -> None:
    from aural_ingest.algorithms import melodic_pyin

    sr = 48_000
    n = int(round(sr * 2.0))
    samples = [0.0] * n
    _sustained_sine(samples, int(0.2 * sr), int(1.5 * sr), 220.0, sr, amp=0.7)
    fixture = tmp_path / "sine_a3_oct.wav"
    _write_mono_int16(fixture, samples, sr)

    notes = melodic_pyin.transcribe(fixture)
    if not notes:
        pytest.skip("no notes; covered by dimension 7")
    in_band = sum(1 for m in notes if 55 <= m.pitch <= 59)
    octave_off = sum(1 for m in notes if abs(m.pitch - 57) >= 12)
    assert in_band >= octave_off, f"octave errors: in={in_band} off={octave_off}"


# ----- dimension 9 -----


def test_quality_09_beat_estimation_120bpm(tmp_path) -> None:
    """Skipped when soundfile / cli is unavailable in the test environment."""
    try:
        from aural_ingest.cli import _estimate_bpm_from_wav
    except ImportError as exc:
        pytest.skip(f"aural_ingest.cli optional dep missing: {exc}")
        return

    sr = 48_000
    duration_sec = 4.0
    n = int(round(sr * duration_sec))
    period = int(round((60.0 / 120.0) * sr))
    samples = [0.0] * n
    for i in range(0, n, period):
        for j in range(int(0.01 * sr)):
            if i + j >= n:
                break
            env = math.exp(-30.0 * (j / float(int(0.01 * sr))))
            samples[i + j] += 0.9 * env
    fixture = tmp_path / "click_120.wav"
    _write_mono_int16(fixture, samples, sr)

    bpm = _estimate_bpm_from_wav(fixture)
    assert 115.0 <= bpm <= 125.0, f"expected ~120 BPM, got {bpm:.2f}"


# ----- dimension 10 -----


def test_quality_10_unknown_drum_filter_falls_back_with_warning() -> None:
    from aural_ingest.transcription import resolve_drum_filter, drum_fallback_chain

    normalized, warnings = resolve_drum_filter("not_a_real_filter_id")
    assert normalized == "combined_filter"
    assert any("not_a_real_filter_id" in w for w in warnings)

    chain = drum_fallback_chain("not_a_real_filter_id")
    assert "combined_filter" in chain
    assert chain[0] in ("combined_filter", "not_a_real_filter_id")
