"""Tatum-conditioned decode prior for the drum CRNN.

Ports the beat/tatum-conditioned decoding idea from arXiv 2010.03749 /
2105.05791 (RWC 3-class F 70.8 -> 81.6): snapping decode toward
musically-plausible tatum positions raises recall with only a small
precision cost. Those papers regularize a discrete onset decision; here the
model outputs per-frame class PROBABILITIES, so the natural port is to nudge
the probability field itself, upward near a tatum grid point and untouched
elsewhere, BEFORE the existing per-class :func:`decode.decode_events`
threshold runs. This directly targets run-2/3's observed shape --
precision-heavy, recall-limited (P ~0.7-0.9, R ~0.4-0.5 at the calibrated
per-class thresholds) -- by recovering onsets that land just under threshold
at a metrically-plausible position, without lowering the threshold globally
(which would also let through off-grid noise).

The boost is applied UNIFORMLY across all 5 classes (rhythmic plausibility
isn't class-specific -- a kick and a hi-hat are equally likely to land on a
sixteenth-note grid point) and is a small additive Gaussian bump, not a hard
gate, so an off-grid onset (a legitimate ghost note, a swung hit) is never
blocked -- it just doesn't get the assist.

Pure numpy; no audio I/O, no model. The beat grid itself is expected to come
from ``meter_tracker.track_meter`` (Beat This!) elsewhere in the pipeline;
this module only consumes plain beat times.
"""
from __future__ import annotations

import numpy as np

from .config import FeatureConfig


def build_tatum_grid(
    beat_times_sec: list[float],
    *,
    subdivisions: int = 4,
    duration_sec: float | None = None,
) -> list[float]:
    """Subdivide each inter-beat interval into ``subdivisions`` equal tatum
    positions (default 4 -> a sixteenth-note grid under a quarter-note beat).

    Beat times are assumed sorted-ish and deduplicated by the caller
    (``meter_tracker`` already does this); this function is defensive about
    NaN and out-of-order input regardless. Extrapolates one interval's worth
    of grid PAST the last detected beat (using the final inter-beat
    interval's duration) so the tail of the song still gets tatum coverage
    when ``duration_sec`` is given -- beat trackers routinely under-detect
    the last bar. Returns a sorted, deduplicated list of tatum times.
    """
    beats = sorted(t for t in beat_times_sec if t == t)  # drop NaN
    if len(beats) < 2:
        return [round(t, 6) for t in beats]
    if subdivisions < 1:
        subdivisions = 1

    tatums: list[float] = []
    for i in range(len(beats) - 1):
        interval = beats[i + 1] - beats[i]
        if interval <= 0:
            continue
        step = interval / subdivisions
        for k in range(subdivisions):
            tatums.append(beats[i] + k * step)
    tatums.append(beats[-1])

    if duration_sec is not None and duration_sec > beats[-1]:
        trail_interval = beats[-1] - beats[-2]
        if trail_interval > 0:
            step = trail_interval / subdivisions
            t = beats[-1] + step
            while t <= duration_sec:
                tatums.append(t)
                t += step

    return sorted({round(t, 6) for t in tatums if t == t})


def apply_tatum_prior(
    probs: np.ndarray,
    feat: FeatureConfig,
    tatum_times_sec: list[float],
    *,
    boost: float = 0.12,
    sigma_sec: float = 0.025,
) -> np.ndarray:
    """Nudge per-frame class probabilities toward tatum-grid positions.

    Adds a small Gaussian-shaped bump (peak height ``boost``, std-dev
    ``sigma_sec``) centred on each tatum time, applied identically across all
    classes, then clips back to ``[0, 1]``. A frame several sigma from every
    tatum point is untouched. Returns a NEW array; ``probs`` is not mutated.

    No-ops (returns ``probs`` unchanged) when there's no grid or no frames --
    this is the fallback path when a beat grid isn't available (e.g. Beat
    This! didn't resolve), so tatum-conditioning is opt-in, never required.
    """
    if not tatum_times_sec or probs.size == 0:
        return probs
    hop = float(feat.hop_length)
    sr = float(feat.sample_rate)
    if hop <= 0 or sr <= 0:
        return probs
    n_frames = probs.shape[0]
    sigma_frames = max(1e-6, sigma_sec * sr / hop)

    boosted = probs.astype(np.float32, copy=True)
    for t in tatum_times_sec:
        center = t * sr / hop
        lo = max(0, int(center - 3 * sigma_frames))
        hi = min(n_frames, int(center + 3 * sigma_frames) + 1)
        if lo >= hi:
            continue
        idxs = np.arange(lo, hi, dtype=np.float32)
        gauss = boost * np.exp(-0.5 * ((idxs - center) / sigma_frames) ** 2)
        boosted[lo:hi, :] += gauss[:, None]

    return np.clip(boosted, 0.0, 1.0)


def uniform_beat_grid(
    bpm: float,
    *,
    duration_sec: float,
    phase_sec: float = 0.0,
) -> list[float]:
    """A constant-tempo beat grid at ``bpm`` starting at ``phase_sec``.

    Useful as a stand-in for a real tracked beat grid when evaluating on
    corpora recorded to a metronome at a known, constant BPM (e.g. E-GMD's
    CSV ``bpm`` column) -- NOT a substitute for real beat tracking on
    variable-tempo material such as the actual Suno/Psalms import targets.
    """
    if bpm <= 0 or duration_sec <= 0:
        return []
    period = 60.0 / bpm
    beats: list[float] = []
    t = max(0.0, phase_sec % period if period > 0 else 0.0)
    while t <= duration_sec:
        beats.append(round(t, 6))
        t += period
    return beats
