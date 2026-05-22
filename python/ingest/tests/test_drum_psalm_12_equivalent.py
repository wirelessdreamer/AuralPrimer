"""Realistic synthetic Psalm-12-equivalent regression for combined_filter.

Approximates the failure mode documented in `wip.md`: a kick-heavy real
song (`Psalm 12`) where the default `combined_filter` path produced
hallucinated cymbal/tom hits. We cannot ship the original copyrighted
audio. This fixture instead synthesizes:

  * a sparse kick pattern (4 quarters per bar, 4 bars at ~96 BPM),
  * realistic kick body with attack noise + 65 Hz fundamental + low
    decay tail (broader spectrum than a 58 Hz pure sine),
  * low-level pink-ish ambient noise floor across the stem to simulate
    bleed and room ambience,
  * a short reverb-tail decay after each kick to simulate close-mic'd
    drums in a real room.

This better triggers the broadband-transient → cymbal misclassification
than a clean sine kick, while staying fully synthetic and shippable.

Bound: at least 60% of detected events must be kick (note 36); fewer
than 25% may be crash/ride. These are looser than the synthetic-sine
fixture in `test_quality_02_*` because the realistic ambient noise
genuinely adds spectral content that any DSP classifier might
reasonably read as cymbal energy.

If the bound is exceeded, the fix is one of:
  * Tighten `combined_filter` low-band guard threshold (currently 0.55).
  * Promote `beat_conditioned_multiband_decoder` or
    `spectral_flux_multiband` to the legacy `combined_filter` slot.
  * Integrate ADTOF / YourMT3+ as the production drum default
    (path 2 of docs/research-deep-dive-adt-2026-05-07.md).
"""
from __future__ import annotations

import math
import struct
import wave
from collections import Counter
from pathlib import Path


class _RNG:
    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFF
    def _next(self) -> int:
        self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
        return self._state
    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * (self._next() / float(0xFFFFFFFF))


def _kick_with_body(samples, start, sr, rng):
    """Synthesize a more realistic kick: attack noise + 65 Hz body + tail."""
    pulse_len = int(round(0.12 * sr))
    for i in range(pulse_len):
        if start + i >= len(samples):
            return
        t = i / float(sr)
        # Attack: 5ms of pink-ish noise
        attack_env = max(0.0, 1.0 - i / float(int(0.005 * sr))) if i < int(0.005 * sr) else 0.0
        attack = rng.uniform(-1.0, 1.0) * 0.4 * attack_env

        # Body: 65 Hz + 130 Hz overtone with exponential decay
        body_env = math.exp(-7.0 * (i / float(pulse_len)))
        body = (
            0.7 * math.sin(2.0 * math.pi * 65.0 * t)
            + 0.18 * math.sin(2.0 * math.pi * 130.0 * t)
        ) * body_env

        # Subtle tail noise simulating room ambience after the hit
        tail_env = math.exp(-3.0 * (i / float(pulse_len))) * 0.04
        tail = rng.uniform(-1.0, 1.0) * tail_env

        samples[start + i] += attack + body + tail


def _ambient_noise(samples, sr, rng, *, level: float = 0.012):
    """Add low-level pink-ish ambient noise simulating room/bleed."""
    # Simple low-passed white via 2-tap moving average (cheap pink-ish).
    prev = 0.0
    for i in range(len(samples)):
        n = rng.uniform(-1.0, 1.0)
        # 1-pole low-pass to dim the high end
        prev = 0.92 * prev + 0.08 * n
        samples[i] += level * prev


def _write_realistic_kick_stem(path: Path) -> None:
    sr = 48_000
    bpm = 96.0
    duration_sec = 10.0  # 4 bars at 96 BPM ~= 10s
    n = int(round(sr * duration_sec))
    samples = [0.0] * n

    # Kick on every beat (quarter notes at 96 BPM).
    beat_period_sec = 60.0 / bpm
    rng = _RNG(seed=20260507)
    t = 0.5
    while t < duration_sec - 0.5:
        _kick_with_body(samples, int(round(t * sr)), sr, rng)
        t += beat_period_sec

    # Ambient bleed across the whole stem.
    _ambient_noise(samples, sr, rng, level=0.012)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for s in samples:
            v = max(-1.0, min(1.0, s))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def test_combined_filter_psalm_12_equivalent_fixture(tmp_path: Path) -> None:
    """Approximates the Psalm-12 broadband-transient kick fixture.

    Looser bounds than the clean-sine fixture because the realistic
    ambient noise genuinely creates spectral content that DSP classifiers
    can reasonably misread. Bound: kick fraction >= 60%, crash+ride
    fraction <= 25%. Tighten only after a real-fixture validation."""

    from aural_ingest.algorithms import combined_filter

    fixture = tmp_path / "psalm_12_equivalent.wav"
    _write_realistic_kick_stem(fixture)

    events = combined_filter.transcribe(fixture)
    assert events, "combined_filter detected zero events on a kick-heavy stem"

    counts = Counter(e.note for e in events)
    total = sum(counts.values())
    kick_frac = counts.get(36, 0) / float(total)
    crash_ride_frac = (counts.get(49, 0) + counts.get(51, 0)) / float(total)

    assert kick_frac >= 0.60, (
        f"kick fraction {kick_frac:.2f} below 0.60 on a realistic kick fixture; "
        f"distribution: {dict(counts)}"
    )
    assert crash_ride_frac <= 0.25, (
        f"crash+ride fraction {crash_ride_frac:.2f} above 0.25 on a kick fixture; "
        f"distribution: {dict(counts)}"
    )


def test_combined_filter_psalm_12_equivalent_event_density_bounded(tmp_path: Path) -> None:
    """Density guard: a 10-second stem with ~16 ground-truth kicks must not
    produce hundreds of events (which would indicate the dense fallback
    grid was triggered)."""

    from aural_ingest.algorithms import combined_filter

    fixture = tmp_path / "psalm_12_density.wav"
    _write_realistic_kick_stem(fixture)

    events = combined_filter.transcribe(fixture)
    # Ground truth ~= 16 kicks. Allow 4x slack for ambient-noise-induced
    # extra detections, but anything over 64 events on a 10s stem is the
    # dense fallback path firing.
    assert 4 <= len(events) <= 64, (
        f"event count {len(events)} outside [4, 64] on a kick-heavy stem; "
        "may indicate dense fallback grid or detection collapse"
    )
