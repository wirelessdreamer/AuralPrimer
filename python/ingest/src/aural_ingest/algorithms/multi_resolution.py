"""Multi-resolution FFT drum transcription (Theory 2).

Uses 3 simultaneous FFT window sizes to capture different drum types
at their optimal spectral resolution:
  - 512 samples  (~12ms @44.1k) for hi-hats — fast transients
  - 2048 samples (~46ms @44.1k) for snares  — mid-frequency resolution
  - 4096 samples (~93ms @44.1k) for kicks   — low-frequency resolution

Detections from each resolution are merged with instrument-specific weights.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    band_pass_one_pole,
    candidates_to_events,
    classify_hat_or_cymbal,
    clamp,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent

# ---------------------------------------------------------------------------
# Resolution configurations
# ---------------------------------------------------------------------------
RESOLUTIONS = {
    "fast":   {"frame": 512,  "hop": 128,  "label": "hi-hat"},
    "medium": {"frame": 2048, "hop": 320,  "label": "snare"},
    "slow":   {"frame": 4096, "hop": 512,  "label": "kick"},
}

# Frequency band definitions per resolution
BANDS_FAST = {
    "hat_main": (5000.0, 12000.0),
    "hat_air":  (7000.0, 16000.0),
}
BANDS_MEDIUM = {
    "snare_body": (200.0, 2200.0),
    "snare_crack": (1800.0, 4500.0),
}
BANDS_SLOW = {
    "kick_low":  (35.0, 120.0),
    "kick_high": (120.0, 200.0),
}


def _compute_band_envelope(
    samples: list[float],
    sr: int,
    band: tuple[float, float],
    frame_size: int,
    hop_size: int,
) -> list[float]:
    """Compute RMS envelope for a frequency band at a given resolution."""
    lo, hi = band
    env: list[float] = []
    pos = 0
    n = len(samples)
    while pos + frame_size <= n:
        seg = samples[pos : pos + frame_size]
        filtered = band_pass_one_pole(seg, sr, lo, hi)
        if filtered:
            rms = math.sqrt(sum(x * x for x in filtered) / float(len(filtered)))
        else:
            rms = 0.0
        env.append(rms)
        pos += hop_size
    return env


def _detect_at_resolution(
    samples: list[float],
    sr: int,
    bands: dict[str, tuple[float, float]],
    frame_size: int,
    hop_size: int,
    drum_class: str,
    peak_k: float,
    min_gap_sec: float,
    percentile: float,
) -> list[DrumCandidate]:
    """Run onset detection at a specific resolution for given bands."""
    hop_sec = hop_size / float(sr)

    envs = {}
    for name, band in bands.items():
        envs[name] = _compute_band_envelope(samples, sr, band, frame_size, hop_size)

    if not envs:
        return []

    # Combine band envelopes
    band_names = list(envs.keys())
    min_len = min(len(envs[b]) for b in band_names)
    if min_len < 4:
        return []

    combined = [0.0] * min_len
    for b in band_names:
        for i in range(min_len):
            combined[i] += envs[b][i]

    combined = normalize_series(combined)
    novelty = normalize_series(onset_novelty(combined))

    peaks = adaptive_peak_pick(
        novelty,
        hop_sec=hop_sec,
        k=peak_k,
        min_gap_sec=min_gap_sec,
        window_sec=0.30,
        percentile=percentile,
        density_boost=0.06,
    )

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        t = frame_to_time(idx, hop_size, sr)
        candidates.append(DrumCandidate(
            time=round(t, 6),
            drum_class=drum_class,
            strength=float(strength),
            confidence=0.7,
            source="multi_resolution",
        ))
    return candidates


def _merge_multi_res(
    all_candidates: list[DrumCandidate],
    samples: list[float],
    sr: int,
    window_sec: float = 0.030,
) -> list[DrumCandidate]:
    """Merge candidates from different resolutions, resolving conflicts."""
    if not all_candidates:
        return []

    sorted_c = sorted(all_candidates, key=lambda c: c.time)
    merged: list[DrumCandidate] = []
    i = 0

    while i < len(sorted_c):
        cluster = [sorted_c[i]]
        j = i + 1
        while j < len(sorted_c) and sorted_c[j].time - cluster[0].time <= window_sec:
            cluster.append(sorted_c[j])
            j += 1

        # Group by drum class
        by_class: dict[str, list[DrumCandidate]] = {}
        for c in cluster:
            by_class.setdefault(c.drum_class, []).append(c)

        # If multiple classes detected at same time, use timbral features to decide
        cluster_time = sum(c.time for c in cluster) / len(cluster)

        if len(by_class) > 1:
            tf = timbral_features(samples, sr, cluster_time)
            low = tf.get("low", 0.0) + tf.get("sub", 0.0)
            mid = tf.get("mid", 0.0)
            high = tf.get("high", 0.0)

            # Allow simultaneous kick+hat or kick+snare
            for cls, cands in by_class.items():
                best = max(cands, key=lambda c: c.strength)
                # Timbral validation
                if cls == "kick" and low < mid * 0.3:
                    continue  # skip kick if no low energy
                if cls == "hh_closed" and high < mid * 0.2:
                    continue  # skip hat if no high energy
                merged.append(best)
        else:
            for cls, cands in by_class.items():
                best = max(cands, key=lambda c: c.strength)
                merged.append(best)

        i = j

    return merged


class MultiResolutionAlgorithm(TranscriptionAlgorithm):
    name = "multi_resolution"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        samples, sr = preprocess_audio(
            stem_path,
            target_sr=44_100,
            pre_emphasis_coeff=0.94,
            high_pass_hz=35.0,
        )
        if not samples or sr <= 0 or len(samples) / float(sr) < 0.1:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Detect at each resolution with instrument-optimized parameters
        kick_candidates = _detect_at_resolution(
            samples, sr, BANDS_SLOW,
            frame_size=4096, hop_size=512,
            drum_class="kick",
            peak_k=2.10, min_gap_sec=0.085, percentile=0.80,
        )
        snare_candidates = _detect_at_resolution(
            samples, sr, BANDS_MEDIUM,
            frame_size=2048, hop_size=320,
            drum_class="snare",
            peak_k=2.50, min_gap_sec=0.080, percentile=0.84,
        )
        hat_candidates_raw = _detect_at_resolution(
            samples, sr, BANDS_FAST,
            frame_size=512, hop_size=128,
            drum_class="hh_closed",
            peak_k=1.85, min_gap_sec=0.038, percentile=0.74,
        )

        # Refine hat classification using timbral features
        hat_candidates: list[DrumCandidate] = []
        for c in hat_candidates_raw:
            tf = timbral_features(samples, sr, c.time)
            hat_class = classify_hat_or_cymbal(tf)
            hat_candidates.append(DrumCandidate(
                time=c.time,
                drum_class=hat_class,
                strength=c.strength,
                confidence=c.confidence,
                source=c.source,
            ))

        all_candidates = kick_candidates + snare_candidates + hat_candidates
        if not all_candidates:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        merged = _merge_multi_res(all_candidates, samples, sr)
        events = candidates_to_events(merged, stem_path=stem_path)
        if events:
            return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed"],
            step_sec=0.082, velocity_base=87,
        )


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = MultiResolutionAlgorithm()
    return algo.transcribe(stem_path)
