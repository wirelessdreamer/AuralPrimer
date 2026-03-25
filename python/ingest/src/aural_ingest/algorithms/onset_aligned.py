"""Onset-aligned drum transcription (Theory 1).

Addresses timing offset issues by:
1. Running base detection (spectral_template_with_grid)
2. Computing onset envelope from audio
3. Cross-correlating predicted onset times with audio onset envelope
4. Estimating and correcting systematic timing offset
5. Re-aligning all predicted event times
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    TranscriptionAlgorithm,
    compute_band_envelopes,
    fallback_events_from_classes,
    normalize_series,
    onset_novelty,
    preprocess_audio,
)
from aural_ingest.algorithms.spectral_template_with_grid import (
    SpectralTemplateWithGridAlgorithm,
)
from aural_ingest.transcription import DrumEvent

HOP = 320
FRAME = 1024

# Maximum offset correction in seconds
MAX_OFFSET_SEC = 0.060
# Search range for cross-correlation in frames
SEARCH_FRAMES = 30


def _events_to_onset_env(
    events: list[DrumEvent],
    duration_sec: float,
    hop_sec: float,
) -> list[float]:
    """Convert drum events into a synthetic onset envelope for cross-correlation."""
    n_frames = max(1, int(duration_sec / hop_sec))
    env = [0.0] * n_frames

    for ev in events:
        frame_idx = int(ev.time / hop_sec)
        if 0 <= frame_idx < n_frames:
            # Gaussian pulse centered at event time
            vel_weight = ev.velocity / 127.0
            for offset in range(-3, 4):
                idx = frame_idx + offset
                if 0 <= idx < n_frames:
                    gauss = math.exp(-0.5 * (offset ** 2))
                    env[idx] = max(env[idx], vel_weight * gauss)

    return env


def _cross_correlate_offset(
    audio_env: list[float],
    pred_env: list[float],
    max_lag: int,
) -> int:
    """Find the lag that maximizes cross-correlation between two envelopes.

    Returns lag in frames (positive = predicted events are ahead of audio).
    """
    n = min(len(audio_env), len(pred_env))
    if n < 4:
        return 0

    best_lag = 0
    best_corr = -float("inf")

    for lag in range(-max_lag, max_lag + 1):
        corr = 0.0
        count = 0
        for i in range(n):
            j = i + lag
            if 0 <= j < n:
                corr += audio_env[i] * pred_env[j]
                count += 1
        if count > 0:
            corr /= count
            if corr > best_corr:
                best_corr = corr
                best_lag = lag

    return best_lag


class OnsetAlignedAlgorithm(TranscriptionAlgorithm):
    name = "onset_aligned"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        # Step 1: Run base detection
        base_algo = SpectralTemplateWithGridAlgorithm()
        base_events = base_algo.transcribe(stem_path)
        if not base_events:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Step 2: Compute audio onset envelope
        samples, sr = preprocess_audio(
            stem_path,
            target_sr=44_100,
            pre_emphasis_coeff=0.94,
            high_pass_hz=35.0,
        )
        if not samples or sr <= 0:
            return base_events

        hop_sec = HOP / float(sr)
        duration_sec = len(samples) / float(sr)

        onset_bands = {
            "kick_low":    (35.0, 120.0),
            "kick_high":   (120.0, 200.0),
            "snare_mid":   (200.0, 2200.0),
            "snare_crack": (1800.0, 4500.0),
            "hat_main":    (5000.0, 12000.0),
        }
        env = compute_band_envelopes(
            samples, sr, onset_bands, hop_size=HOP, frame_size=FRAME,
        )
        if not env:
            return base_events

        min_len = min(len(v) for v in env.values())
        if min_len < 4:
            return base_events

        # Composite audio onset envelope
        combined = normalize_series([
            sum(env[b][i] for b in env) for i in range(min_len)
        ])
        audio_novelty = normalize_series(onset_novelty(combined))

        # Step 3: Create predicted onset envelope
        pred_env = _events_to_onset_env(base_events, duration_sec, hop_sec)

        # Step 4: Cross-correlate to find timing offset
        max_lag = min(SEARCH_FRAMES, int(MAX_OFFSET_SEC / hop_sec))
        lag_frames = _cross_correlate_offset(audio_novelty, pred_env, max_lag)
        offset_sec = lag_frames * hop_sec

        # Clamp offset
        offset_sec = max(-MAX_OFFSET_SEC, min(MAX_OFFSET_SEC, offset_sec))

        if abs(offset_sec) < 0.002:
            return base_events  # no significant offset

        # Step 5: Re-align all events
        aligned_events: list[DrumEvent] = []
        for ev in base_events:
            new_time = max(0.0, ev.time - offset_sec)
            aligned_events.append(DrumEvent(
                time=round(new_time, 6),
                note=ev.note,
                velocity=ev.velocity,
                duration=ev.duration,
            ))

        return sorted(aligned_events, key=lambda e: e.time)


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = OnsetAlignedAlgorithm()
    return algo.transcribe(stem_path)
