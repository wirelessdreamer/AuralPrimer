"""MFCC Cepstral Classification drum transcription.

From speech recognition: uses Mel-Frequency Cepstral Coefficients as
a compact 13-dimensional timbral fingerprint at each onset.  MFCCs
capture the spectral *envelope shape* rather than raw band energies,
making classification more robust to EQ/mixing variations.

Algorithm:
1. Broad onset detection using spectral flux
2. At each onset, extract 50ms window → compute 13 MFCCs
3. K-means cluster all onset MFCCs (k=3-5)
4. Label clusters by centroid features
5. Classify each onset by nearest cluster centroid (cosine distance)
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
    compute_band_envelopes,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent

HOP = 320
FRAME = 1024
N_MFCC = 13
N_MEL_FILTERS = 26
MFCC_WINDOW_SEC = 0.050
N_CLUSTERS_MIN = 3
N_CLUSTERS_MAX = 5
KMEANS_ITERATIONS = 30

# Onset detection bands
ONSET_BANDS = {
    "kick_low": (35.0, 120.0),
    "snare_mid": (200.0, 2200.0),
    "hat_main": (5000.0, 12000.0),
}


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _build_mel_filterbank(
    n_filters: int,
    n_fft: int,
    sr: int,
    fmin: float = 20.0,
    fmax: float | None = None,
) -> list[list[float]]:
    """Build triangular Mel filterbank matrix (n_filters × n_fft//2+1)."""
    if fmax is None:
        fmax = sr / 2.0

    n_bins = n_fft // 2 + 1
    mel_min = _hz_to_mel(fmin)
    mel_max = _hz_to_mel(fmax)

    # n_filters + 2 points for triangular filters
    mel_points = [mel_min + i * (mel_max - mel_min) / (n_filters + 1) for i in range(n_filters + 2)]
    hz_points = [_mel_to_hz(m) for m in mel_points]
    bin_points = [int(round(h * n_fft / sr)) for h in hz_points]

    filterbank: list[list[float]] = []
    for m in range(n_filters):
        filt = [0.0] * n_bins
        left = bin_points[m]
        center = bin_points[m + 1]
        right = bin_points[m + 2]

        for k in range(left, center + 1):
            if 0 <= k < n_bins and center > left:
                filt[k] = (k - left) / max(1, center - left)
        for k in range(center, right + 1):
            if 0 <= k < n_bins and right > center:
                filt[k] = (right - k) / max(1, right - center)

        filterbank.append(filt)

    return filterbank


def _dft_power(segment: list[float], n_fft: int) -> list[float]:
    """Compute power spectrum for a single frame."""
    n_bins = n_fft // 2 + 1
    # Zero-pad or truncate
    padded = segment[:n_fft] + [0.0] * max(0, n_fft - len(segment))

    # Hann window
    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_fft - 1))) for i in range(n_fft)]
    windowed = [padded[i] * window[i] for i in range(n_fft)]

    power: list[float] = []
    for k in range(n_bins):
        re = 0.0
        im = 0.0
        w = -2.0 * math.pi * k / n_fft
        for t in range(n_fft):
            angle = w * t
            re += windowed[t] * math.cos(angle)
            im += windowed[t] * math.sin(angle)
        power.append(re * re + im * im)
    return power


def _compute_mfcc(
    segment: list[float],
    sr: int,
    n_fft: int = 512,
    n_mfcc: int = N_MFCC,
    n_mel: int = N_MEL_FILTERS,
) -> list[float]:
    """Compute MFCCs for a single audio segment."""
    if len(segment) < 64:
        return [0.0] * n_mfcc

    # Power spectrum
    power = _dft_power(segment, n_fft)

    # Mel filterbank
    filterbank = _build_mel_filterbank(n_mel, n_fft, sr)

    # Apply filterbank
    mel_energies: list[float] = []
    for filt in filterbank:
        energy = sum(filt[k] * power[k] for k in range(min(len(filt), len(power))))
        mel_energies.append(max(1e-10, energy))

    # Log
    log_mel = [math.log(e) for e in mel_energies]

    # DCT (Type-II)
    mfcc: list[float] = []
    n = len(log_mel)
    for k in range(n_mfcc):
        val = 0.0
        for i in range(n):
            val += log_mel[i] * math.cos(math.pi * k * (2 * i + 1) / (2 * n))
        mfcc.append(val)

    return mfcc


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Cosine distance (1 - cosine similarity)."""
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    dot = sum(a[i] * b[i] for i in range(n))
    norm_a = math.sqrt(sum(x * x for x in a[:n]))
    norm_b = math.sqrt(sum(x * x for x in b[:n]))
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _euclidean_distance(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)))


def _kmeans(
    data: list[list[float]],
    k: int,
    max_iter: int = KMEANS_ITERATIONS,
) -> tuple[list[list[float]], list[int]]:
    """Simple K-means clustering. Returns (centroids, assignments)."""
    if not data or k <= 0:
        return [], []

    n = len(data)
    dim = len(data[0])
    k = min(k, n)

    # Initialize centroids with evenly-spaced data points
    step = max(1, n // k)
    centroids = [data[i * step][:] for i in range(k)]

    assignments = [0] * n

    for _ in range(max_iter):
        # Assign
        changed = False
        for i in range(n):
            best_c = 0
            best_dist = float("inf")
            for c in range(k):
                d = _euclidean_distance(data[i], centroids[c])
                if d < best_dist:
                    best_dist = d
                    best_c = c
            if assignments[i] != best_c:
                changed = True
            assignments[i] = best_c

        if not changed:
            break

        # Update centroids
        for c in range(k):
            members = [data[i] for i in range(n) if assignments[i] == c]
            if members:
                centroids[c] = [
                    sum(m[d] for m in members) / len(members)
                    for d in range(dim)
                ]

    return centroids, assignments


def _label_cluster_centroids(
    centroids: list[list[float]],
    onset_times: list[float],
    assignments: list[int],
    samples: list[float],
    sr: int,
) -> list[str]:
    """Label each cluster centroid as a drum class using timbral features."""
    k = len(centroids)
    labels = [""] * k

    # For each cluster, get average timbral features of its members
    cluster_features: list[dict[str, float]] = []
    for c in range(k):
        member_indices = [i for i, a in enumerate(assignments) if a == c]
        if not member_indices:
            cluster_features.append({"centroid": 5000.0, "low": 0.0})
            continue

        avg_features: dict[str, float] = {}
        for idx in member_indices:
            if idx < len(onset_times):
                tf = timbral_features(samples, sr, onset_times[idx])
                for key, val in tf.items():
                    avg_features[key] = avg_features.get(key, 0.0) + val

        n_members = len(member_indices)
        for key in avg_features:
            avg_features[key] /= n_members
        cluster_features.append(avg_features)

    # MFCC[0] is log-energy, MFCC[1] correlates with spectral tilt
    # But better to use timbral features for labeling
    centroid_values = [f.get("centroid", 5000.0) for f in cluster_features]
    low_values = [f.get("low", 0.0) + f.get("sub", 0.0) for f in cluster_features]
    high_values = [f.get("high", 0.0) + f.get("air", 0.0) for f in cluster_features]

    # Sort by centroid frequency
    indexed = sorted(range(k), key=lambda i: centroid_values[i])

    # Lowest centroid = kick
    if k >= 1:
        labels[indexed[0]] = "kick"
    # Highest centroid = hi-hat
    if k >= 2:
        labels[indexed[-1]] = "hh_closed"
    # Middle = snare
    if k >= 3:
        labels[indexed[1]] = "snare"
    # Extra clusters
    for i in range(k):
        if not labels[i]:
            if centroid_values[i] > 4000:
                labels[i] = "crash"
            elif centroid_values[i] < 400:
                labels[i] = "tom_low"
            else:
                labels[i] = "snare"

    return labels


class MfccCepstralAlgorithm(TranscriptionAlgorithm):
    name = "mfcc_cepstral"

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

        hop_sec = HOP / float(sr)

        # Step 1: Broad onset detection
        env = compute_band_envelopes(
            samples, sr, ONSET_BANDS, hop_size=HOP, frame_size=FRAME,
        )
        if not env:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Combine all bands for broad onset detection
        min_len = min(len(v) for v in env.values())
        if min_len < 4:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        combined = normalize_series([
            sum(env[b][i] for b in env) for i in range(min_len)
        ])
        novelty = normalize_series(onset_novelty(combined))

        # Broad peak picking — lower threshold to catch more onsets
        all_peaks = adaptive_peak_pick(
            novelty, hop_sec=hop_sec, k=1.60, min_gap_sec=0.035,
            window_sec=0.28, percentile=0.65, density_boost=0.04,
        )

        if len(all_peaks) < 3:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Step 2: Extract MFCCs at each onset
        onset_times: list[float] = []
        onset_strengths: list[float] = []
        mfcc_vectors: list[list[float]] = []
        window_samples = max(64, int(MFCC_WINDOW_SEC * sr))

        for idx, strength in all_peaks:
            t = frame_to_time(idx, HOP, sr)
            onset_times.append(t)
            onset_strengths.append(strength)

            start = max(0, int(t * sr))
            end = min(len(samples), start + window_samples)
            segment = samples[start:end]
            mfcc = _compute_mfcc(segment, sr)
            mfcc_vectors.append(mfcc)

        # Step 3: K-means clustering
        n_onsets = len(mfcc_vectors)
        k = min(N_CLUSTERS_MAX, max(N_CLUSTERS_MIN, n_onsets // 15))
        k = min(k, n_onsets)

        centroids, assignments = _kmeans(mfcc_vectors, k)

        # Step 4: Label clusters
        cluster_labels = _label_cluster_centroids(
            centroids, onset_times, assignments, samples, sr,
        )

        # Step 5: Classify each onset and build candidates
        candidates: list[DrumCandidate] = []
        for i in range(n_onsets):
            drum_class = cluster_labels[assignments[i]]
            t = onset_times[i]
            strength = onset_strengths[i]

            # Refine hat/cymbal classification
            if drum_class in ("hh_closed", "crash"):
                tf = timbral_features(samples, sr, t)
                drum_class = classify_hat_or_cymbal(tf)

            candidates.append(DrumCandidate(
                time=round(t, 6),
                drum_class=drum_class,
                strength=float(strength),
                confidence=0.65,
                source="mfcc_cepstral",
            ))

        if not candidates:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        events = candidates_to_events(candidates, stem_path=stem_path)
        if events:
            return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed"],
            step_sec=0.082, velocity_base=87,
        )


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = MfccCepstralAlgorithm()
    return algo.transcribe(stem_path)
