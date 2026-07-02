"""Decode per-frame class probabilities into drum onset events.

Turns the model's ``(n_frames, num_classes)`` sigmoid output into a list of
canonical :class:`DrumEvent`s (the same type the benchmark harness consumes),
so a trained model can drop into the evaluation pipeline later. Each class is
peak-picked independently: a frame is an onset if it is a local maximum above
``threshold`` and far enough (``min_gap_sec``) from the previous accepted peak
in that class.
"""
from __future__ import annotations

import numpy as np

from aural_ingest.transcription import DrumEvent

from .config import CLASSES, DRUM_5CLASS_TO_MIDI_FALLBACK, FeatureConfig


def _peak_pick_channel(
    prob: np.ndarray,
    threshold: float,
    min_gap_frames: int,
) -> list[int]:
    """Return frame indices that are local maxima above ``threshold``."""
    peaks: list[int] = []
    last = -(10**9)
    n = prob.shape[0]
    for i in range(n):
        v = prob[i]
        if v < threshold:
            continue
        left = prob[i - 1] if i > 0 else -np.inf
        right = prob[i + 1] if i + 1 < n else -np.inf
        if v < left or v < right:
            continue
        if i - last < min_gap_frames:
            # Keep the stronger of two too-close peaks.
            if peaks and v > prob[peaks[-1]]:
                peaks[-1] = i
                last = i
            continue
        peaks.append(i)
        last = i
    return peaks


def decode_events(
    probs: np.ndarray,
    feat: FeatureConfig,
    *,
    threshold: float = 0.5,
    min_gap_sec: float = 0.03,
) -> list[DrumEvent]:
    """Decode ``(n_frames, num_classes)`` probabilities to ``DrumEvent``s.

    ``probs`` are post-sigmoid (0..1). Frame index -> time via the inverse of
    the target builder's mapping (``frame * hop / sr``). Velocity is a coarse
    map of peak height into 1..127 so downstream MIDI has plausible dynamics.
    """
    if probs.ndim != 2 or probs.shape[1] != len(CLASSES):
        raise ValueError(f"expected (n_frames, {len(CLASSES)}) probs, got {probs.shape}")
    hop = float(feat.hop_length)
    sr = float(feat.sample_rate)
    min_gap_frames = max(1, int(round(min_gap_sec * sr / hop)))

    events: list[DrumEvent] = []
    for cls_idx, cls_name in enumerate(CLASSES):
        channel = probs[:, cls_idx]
        for frame in _peak_pick_channel(channel, threshold, min_gap_frames):
            t = frame * hop / sr
            height = float(channel[frame])
            velocity = int(np.clip(round(1 + height * 126), 1, 127))
            events.append(
                DrumEvent(
                    time=round(t, 6),
                    note=int(DRUM_5CLASS_TO_MIDI_FALLBACK[cls_name]),
                    velocity=velocity,
                )
            )
    events.sort(key=lambda e: (e.time, e.note))
    return events
