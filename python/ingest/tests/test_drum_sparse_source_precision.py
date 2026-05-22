"""Sparse-source drum precision regression for `combined_filter`.

Guards the failure mode documented in `wip.md`: on a real sparse-source song
(reported as Psalm 12), the default `combined_filter` path was producing
hallucinated drum hits, with false-positive pressure attributed to
three-detector fusion, expanded-kit remaps, permissive refractory settings,
and the dense synthetic grid that `fallback_events_from_classes` emits when
candidate recovery fails.

The test cannot ship copyrighted Psalm 12 audio. Instead it runs the actual
`combined_filter` against a synthetic kick-only stem with four well-spaced
hits and asserts:

* the algorithm does NOT fall through to `fallback_events_from_classes`
  (the dense grid emits hundreds of events across nine drum classes), and
* the event count stays within a generous upper bound near the ground truth
  (catches gross over-liberal regressions without being brittle to small
  DSP tuning changes).

Bounds are intentionally conservative; tighten them only after a wider
benchmark (`benchmarks/quality/...`) confirms a tighter operating point.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest


def _write_sparse_kick_only_stem(
    path: Path,
    *,
    sr: int = 48_000,
    duration_sec: float = 5.0,
    kick_times_sec: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5),
) -> None:
    """Write a mono 16-bit WAV stem with `len(kick_times_sec)` kick hits.

    Each kick is a short 58 Hz sine pulse with an exponential decay envelope
    matching the rebuild fixture used elsewhere in the algorithm test suite.
    The rest of the stem is silence.
    """

    n = int(round(sr * duration_sec))
    samples = [0.0] * n

    pulse_len = int(round(0.08 * sr))
    for kt in kick_times_sec:
        start = int(round(kt * sr))
        for i in range(pulse_len):
            idx = start + i
            if idx >= n:
                break
            env = math.exp(-6.0 * (i / float(pulse_len)))
            samples[idx] += 0.85 * math.sin(2.0 * math.pi * 58.0 * (i / float(sr))) * env

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for s in samples:
            v = max(-1.0, min(1.0, s))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def test_combined_filter_sparse_source_does_not_hallucinate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """combined_filter must not emit a dense-fallback or hallucinated burst
    on a clear sparse-source drum stem (Psalm 12-style regression guard)."""

    from aural_ingest.algorithms import combined_filter

    fixture = tmp_path / "sparse_kicks.wav"
    _write_sparse_kick_only_stem(fixture)

    fallback_calls: list[tuple[object, ...]] = []
    real_fallback = combined_filter.fallback_events_from_classes

    def trap_fallback(*args: object, **kwargs: object):
        fallback_calls.append((args, kwargs))
        return real_fallback(*args, **kwargs)

    monkeypatch.setattr(combined_filter, "fallback_events_from_classes", trap_fallback)

    events = combined_filter.transcribe(fixture)

    assert not fallback_calls, (
        "combined_filter fell through to fallback_events_from_classes on a "
        "sparse-source stem; the dense synthetic grid is the documented "
        "Psalm 12 hallucination path."
    )

    # Ground truth = 4 kick hits. The bound is intentionally generous to
    # tolerate small classification artifacts from the multiband fusion path
    # while still catching a regression that turns 4 hits into dozens or
    # hundreds.
    assert 1 <= len(events) <= 12, (
        f"Expected 1..12 events on a 4-kick sparse stem, got {len(events)}. "
        "Out-of-range counts indicate either silent regression (no detection) "
        "or hallucinated bursts."
    )

    # Sanity: timestamps stay monotonic (algorithm contract).
    times = [e.time for e in events]
    assert times == sorted(times), "combined_filter emitted out-of-order events"


def test_combined_filter_silent_stem_falls_back_explicitly(tmp_path: Path) -> None:
    """A truly silent stem has no transients, so the fallback path is the
    expected (and only) source of events. This complements the sparse-source
    test above by pinning the documented behavior at the other end of the
    spectrum: the test above guards against unintended fallback; this test
    documents that fallback IS taken when there is genuinely nothing to
    detect."""

    from aural_ingest.algorithms import combined_filter

    fixture = tmp_path / "silent.wav"
    sr = 48_000
    duration_sec = 1.5
    n = int(round(sr * duration_sec))
    with wave.open(str(fixture), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for _ in range(n):
            wav_file.writeframesraw(struct.pack("<h", 0))

    events = combined_filter.transcribe(fixture)

    # Fallback emits a dense grid across nine classes; just assert the
    # algorithm produced *something* (does not crash on silence) and that
    # whatever it produced is well-formed.
    assert events, "combined_filter must always emit at least the fallback grid"
    times = [e.time for e in events]
    assert times == sorted(times)
