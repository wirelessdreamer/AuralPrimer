"""Unit tests for the tatum-conditioned decode prior.

Covers the three load-bearing contracts:

  (a) grid construction -- inter-beat subdivision + tail extrapolation;
  (b) probability boosting -- a bump lands at (and only near) tatum times,
      is bounded to [0, 1], and doesn't mutate the input;
  (c) the no-op / fallback paths -- empty grid, empty probs.

Pure numpy; no torch/librosa/onnx dependency, so this module runs even in
lightweight CI (unlike test_drum_crnn_training.py, which conftest.py skips
without the full ML runtime).
"""
from __future__ import annotations

import numpy as np
import pytest

from aural_ingest.training.drum_crnn.config import CLASSES, FeatureConfig
from aural_ingest.training.drum_crnn.tatum import (
    apply_tatum_prior,
    build_tatum_grid,
    uniform_beat_grid,
)


# --------------------------------------------------------------------------- #
# (a) grid construction
# --------------------------------------------------------------------------- #

def test_build_tatum_grid_subdivides_each_beat_interval() -> None:
    # Two beats 1 second apart, subdivisions=4 -> tatums at 0, 0.25, 0.5,
    # 0.75, plus the final beat itself.
    grid = build_tatum_grid([0.0, 1.0], subdivisions=4)
    assert grid == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])


def test_build_tatum_grid_handles_multiple_beats() -> None:
    grid = build_tatum_grid([0.0, 1.0, 2.0], subdivisions=2)
    assert grid == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])


def test_build_tatum_grid_extrapolates_tail_when_duration_given() -> None:
    # Beats stop at 2.0 s but the song runs to 3.0 s -- the grid should keep
    # going using the last inter-beat interval's spacing (beat trackers
    # routinely under-detect the final bar).
    grid = build_tatum_grid([0.0, 1.0, 2.0], subdivisions=2, duration_sec=3.0)
    assert grid[-1] > 2.0
    assert max(grid) <= 3.0
    # Spacing after 2.0 should match the pre-2.0 spacing (0.5s tatums).
    tail = [t for t in grid if t > 2.0]
    assert tail == pytest.approx([2.5, 3.0])


def test_build_tatum_grid_no_extrapolation_without_duration() -> None:
    grid = build_tatum_grid([0.0, 1.0, 2.0], subdivisions=2)
    assert max(grid) == pytest.approx(2.0)


def test_build_tatum_grid_fewer_than_two_beats_returns_input() -> None:
    assert build_tatum_grid([], subdivisions=4) == []
    assert build_tatum_grid([1.5], subdivisions=4) == [1.5]


def test_build_tatum_grid_drops_nan_and_sorts_unordered_input() -> None:
    grid = build_tatum_grid([1.0, float("nan"), 0.0], subdivisions=1)
    assert grid == pytest.approx([0.0, 1.0])


def test_build_tatum_grid_ignores_non_positive_intervals() -> None:
    # A duplicate/out-of-order beat pair (interval <= 0) is skipped, not a
    # crash or a bogus negative-step grid.
    grid = build_tatum_grid([0.0, 0.0, 1.0], subdivisions=2)
    assert grid == pytest.approx([0.0, 0.5, 1.0])


# --------------------------------------------------------------------------- #
# (b) probability boosting
# --------------------------------------------------------------------------- #

def test_apply_tatum_prior_boosts_frames_near_a_tatum_time() -> None:
    feat = FeatureConfig()
    n_frames = 200
    probs = np.zeros((n_frames, len(CLASSES)), dtype=np.float32)
    tatum_sec = 0.5
    center_frame = round(tatum_sec * feat.sample_rate / feat.hop_length)

    boosted = apply_tatum_prior(probs, feat, [tatum_sec], boost=0.2, sigma_sec=0.02)
    assert boosted[center_frame, 0] > 0.0
    assert boosted[center_frame, 0] == pytest.approx(0.2, abs=1e-3)
    # Applied uniformly across every class, not just one.
    assert np.all(boosted[center_frame, :] == pytest.approx(0.2, abs=1e-3))


def test_apply_tatum_prior_leaves_far_frames_untouched() -> None:
    feat = FeatureConfig()
    probs = np.zeros((500, len(CLASSES)), dtype=np.float32)
    boosted = apply_tatum_prior(probs, feat, [0.05], boost=0.2, sigma_sec=0.01)
    # Frame near the end of a 500-frame (~5s) clip is far from a 0.05s tatum.
    assert boosted[400, :].sum() == 0.0


def test_apply_tatum_prior_clips_to_valid_probability_range() -> None:
    feat = FeatureConfig()
    n_frames = 50
    probs = np.full((n_frames, len(CLASSES)), 0.95, dtype=np.float32)
    boosted = apply_tatum_prior(probs, feat, [0.1], boost=0.3, sigma_sec=0.02)
    assert boosted.max() <= 1.0
    assert boosted.min() >= 0.0


def test_apply_tatum_prior_does_not_mutate_input() -> None:
    feat = FeatureConfig()
    probs = np.zeros((100, len(CLASSES)), dtype=np.float32)
    original = probs.copy()
    apply_tatum_prior(probs, feat, [0.2], boost=0.2)
    assert np.array_equal(probs, original)


def test_apply_tatum_prior_multiple_tatums_each_contribute() -> None:
    feat = FeatureConfig()
    probs = np.zeros((300, len(CLASSES)), dtype=np.float32)
    boosted = apply_tatum_prior(probs, feat, [0.2, 0.6, 1.0], boost=0.15, sigma_sec=0.015)
    for t in (0.2, 0.6, 1.0):
        frame = round(t * feat.sample_rate / feat.hop_length)
        assert boosted[frame, 0] > 0.0


# --------------------------------------------------------------------------- #
# (c) fallback / no-op paths
# --------------------------------------------------------------------------- #

def test_apply_tatum_prior_empty_grid_is_noop() -> None:
    feat = FeatureConfig()
    probs = np.random.default_rng(0).random((80, len(CLASSES))).astype(np.float32)
    result = apply_tatum_prior(probs, feat, [])
    assert result is probs  # early-return, not a copy


def test_apply_tatum_prior_empty_probs_is_noop() -> None:
    feat = FeatureConfig()
    probs = np.zeros((0, len(CLASSES)), dtype=np.float32)
    result = apply_tatum_prior(probs, feat, [0.1, 0.2])
    assert result.shape == (0, len(CLASSES))


# --------------------------------------------------------------------------- #
# uniform_beat_grid (E-GMD-style constant-tempo evaluation stand-in)
# --------------------------------------------------------------------------- #

def test_uniform_beat_grid_matches_bpm_period() -> None:
    # 120 bpm -> beats every 0.5s.
    grid = uniform_beat_grid(120.0, duration_sec=2.0)
    assert grid == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])


def test_uniform_beat_grid_respects_phase() -> None:
    grid = uniform_beat_grid(120.0, duration_sec=1.5, phase_sec=0.1)
    assert grid[0] == pytest.approx(0.1)
    assert grid == pytest.approx([0.1, 0.6, 1.1])


def test_uniform_beat_grid_invalid_inputs_return_empty() -> None:
    assert uniform_beat_grid(0.0, duration_sec=10.0) == []
    assert uniform_beat_grid(120.0, duration_sec=0.0) == []
    assert uniform_beat_grid(-5.0, duration_sec=10.0) == []
