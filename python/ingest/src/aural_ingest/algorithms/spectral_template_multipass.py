"""Multi-pass spectral template drum transcription.

Pass 1 — Spectral Profiling:
  1. Run broad onset detection (energy-based)
  2. For each onset, extract a short FFT window (~50ms)
  3. Compute spectral features: band energies, centroid, bandwidth
  4. Cluster onsets by spectral similarity (k-means on features)
  5. Label clusters using heuristics: lowest centroid → kick, broadband mid → snare, high centroid → hi-hat

Pass 2 — Refined Classification with Song-Specific Templates:
  1. Build a spectral template (mean band energy profile) for each cluster
  2. Re-scan the audio with onset detection
  3. Classify each onset by correlation against the learned templates
  4. Use tighter, song-specific confidence thresholds

The key insight is that every song uses different drum samples/kits,
so learning the spectral signature per-song allows for much better
classification than fixed frequency bands.
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
    classify_tom,
    clamp,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_rms_series,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


# --------------------------------------------------------------------------
# Spectral analysis bands — finer-grained than spectral_flux_multiband
# for better template construction
# --------------------------------------------------------------------------
ANALYSIS_BANDS = {
    "sub_bass":    (20.0, 60.0),
    "bass":        (60.0, 150.0),
    "low_mid":     (150.0, 400.0),
    "mid":         (400.0, 1200.0),
    "upper_mid":   (1200.0, 2800.0),
    "crack":       (2800.0, 5000.0),
    "presence":    (5000.0, 8000.0),
    "brilliance":  (8000.0, 12000.0),
    "air":         (12000.0, 18000.0),
}

# Standard onset detection bands — same as spectral_flux_multiband
ONSET_BANDS = {
    "kick_low":    (35.0, 120.0),
    "kick_high":   (120.0, 200.0),
    "snare_mid":   (200.0, 2200.0),
    "snare_crack": (1800.0, 4500.0),
    "hat_main":    (5000.0, 12000.0),
    "hat_air":     (7000.0, 16000.0),
}

HOP = 320
FRAME = 1024
PROFILE_WINDOW_SEC = 0.05  # 50ms window for spectral profiling


class SpectralTemplateMultipassAlgorithm(TranscriptionAlgorithm):
    name = "spectral_template_multipass"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events
        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed", "kick", "hh_open", "crash", "tom_low"],
            step_sec=0.082,
            velocity_base=87,
        )


# --------------------------------------------------------------------------
# Spectral feature extraction for a single onset
# --------------------------------------------------------------------------
def _extract_onset_features(
    samples: list[float],
    sr: int,
    onset_time: float,
    window_sec: float = PROFILE_WINDOW_SEC,
) -> dict[str, float]:
    """Extract band energy features for a single onset."""
    start = max(0, int(onset_time * sr))
    length = max(64, int(window_sec * sr))
    end = min(len(samples), start + length)

    if end - start < 32:
        return {}

    window = samples[start:end]
    features: dict[str, float] = {}

    total_energy = 0.0
    for band_name, (lo, hi) in ANALYSIS_BANDS.items():
        filtered = band_pass_one_pole(window, sr, lo, hi)
        if not filtered:
            features[band_name] = 0.0
            continue
        energy = math.sqrt(sum(x * x for x in filtered) / float(len(filtered)))
        features[band_name] = energy
        total_energy += energy

    # Normalize to relative energies
    if total_energy > 1e-9:
        for k in features:
            features[k] /= total_energy

    # Add spectral centroid as weighted sum
    centroid_weights = {
        "sub_bass": 40, "bass": 105, "low_mid": 275, "mid": 800,
        "upper_mid": 2000, "crack": 3900, "presence": 6500,
        "brilliance": 10000, "air": 15000,
    }
    centroid = sum(features.get(k, 0.0) * w for k, w in centroid_weights.items())
    features["centroid"] = centroid

    # Add temporal sharpness (attack sharpness)
    peak = max((abs(x) for x in window), default=0.0)
    rms = math.sqrt(sum(x * x for x in window) / float(len(window))) if window else 0.0
    features["sharpness"] = peak / max(1e-6, rms)

    return features


# --------------------------------------------------------------------------
# Simple k-means clustering on spectral features
# --------------------------------------------------------------------------
def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Cosine distance between two feature vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))


def _euclidean_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _features_to_vector(features: dict[str, float], keys: list[str]) -> list[float]:
    return [features.get(k, 0.0) for k in keys]


def _kmeans(
    vectors: list[list[float]],
    k: int,
    max_iter: int = 20,
) -> list[int]:
    """Simple k-means clustering. Returns cluster labels."""
    if not vectors or k <= 0:
        return []
    n = len(vectors)
    dim = len(vectors[0])
    if n <= k:
        return list(range(n))

    # Initialize centroids: pick k evenly-spaced points
    centroids = [vectors[i * n // k][:] for i in range(k)]
    labels = [0] * n

    for _ in range(max_iter):
        # Assign
        changed = False
        for i, v in enumerate(vectors):
            best_c = 0
            best_d = float("inf")
            for c in range(k):
                d = _euclidean_distance(v, centroids[c])
                if d < best_d:
                    best_d = d
                    best_c = c
            if labels[i] != best_c:
                labels[i] = best_c
                changed = True

        if not changed:
            break

        # Update centroids
        for c in range(k):
            members = [vectors[i] for i in range(n) if labels[i] == c]
            if members:
                centroids[c] = [
                    sum(m[d] for m in members) / float(len(members))
                    for d in range(dim)
                ]

    return labels


# --------------------------------------------------------------------------
# Cluster labeling: assign drum classes based on spectral characteristics
# --------------------------------------------------------------------------
def _label_clusters(
    cluster_profiles: list[dict[str, float]],
) -> list[str]:
    """Label each cluster with a drum class based on its average spectral profile."""
    if not cluster_profiles:
        return []

    n = len(cluster_profiles)
    labels: list[str] = ["unknown"] * n

    # Compute centroid and low/high energy ratio for each cluster
    centroids = [p.get("centroid", 5000.0) for p in cluster_profiles]
    low_energies = [p.get("sub_bass", 0.0) + p.get("bass", 0.0) for p in cluster_profiles]
    high_energies = [p.get("presence", 0.0) + p.get("brilliance", 0.0) + p.get("air", 0.0) for p in cluster_profiles]
    mid_energies = [p.get("mid", 0.0) + p.get("upper_mid", 0.0) + p.get("crack", 0.0) for p in cluster_profiles]

    # Sort by centroid to assign roles
    sorted_indices = sorted(range(n), key=lambda i: centroids[i])

    assigned = set()

    # Lowest centroid cluster → kick (if has strong low energy)
    for idx in sorted_indices:
        if idx not in assigned and low_energies[idx] > 0.15:
            labels[idx] = "kick"
            assigned.add(idx)
            break

    # Highest centroid cluster → hi-hat (if has strong high energy)
    for idx in reversed(sorted_indices):
        if idx not in assigned and high_energies[idx] > 0.15:
            labels[idx] = "hi_hat"
            assigned.add(idx)
            break

    # Remaining cluster with highest mid energy → snare
    for idx in sorted_indices:
        if idx not in assigned and mid_energies[idx] > 0.10:
            labels[idx] = "snare"
            assigned.add(idx)
            break

    # Any remaining clusters: label by closest spectral match
    for idx in range(n):
        if idx not in assigned:
            if low_energies[idx] > high_energies[idx]:
                if centroids[idx] < 3000:
                    labels[idx] = "kick"
                else:
                    labels[idx] = "snare"
            else:
                labels[idx] = "hi_hat"

    return labels


# --------------------------------------------------------------------------
# Template matching: compare an onset's features against learned templates
# --------------------------------------------------------------------------
def _match_template(
    onset_features: dict[str, float],
    templates: dict[str, dict[str, float]],
    feature_keys: list[str],
) -> tuple[str, float]:
    """Return (drum_class, confidence) for the best matching template."""
    onset_vec = _features_to_vector(onset_features, feature_keys)
    best_class = "kick"
    best_dist = float("inf")

    for drum_class, template in templates.items():
        template_vec = _features_to_vector(template, feature_keys)
        dist = _euclidean_distance(onset_vec, template_vec)
        if dist < best_dist:
            best_dist = dist
            best_class = drum_class

    # Convert distance to confidence (0–1)
    confidence = max(0.0, 1.0 - best_dist * 2.0)
    return best_class, confidence


# --------------------------------------------------------------------------
# Main multi-pass detection
# --------------------------------------------------------------------------
def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    """Multi-pass spectral template drum detection."""
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.94,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    duration_sec = len(samples) / float(sr)
    if duration_sec < 0.1:
        return []

    hop_sec = HOP / float(sr)

    # ===================================================================
    # PASS 1: Initial onset detection + spectral profiling
    # ===================================================================

    # Step 1a: Compute band envelopes for onset detection
    env = compute_band_envelopes(
        samples, sr, ONSET_BANDS, hop_size=HOP, frame_size=FRAME,
    )
    if not env:
        return []

    kick_env = normalize_series([
        0.7 * env["kick_low"][i] + 0.3 * env["kick_high"][i]
        for i in range(min(len(env["kick_low"]), len(env["kick_high"])))
    ])
    snare_env = normalize_series([
        0.45 * env["snare_mid"][i] + 1.0 * env["snare_crack"][i]
        for i in range(min(len(env["snare_mid"]), len(env["snare_crack"])))
    ])
    hat_env = normalize_series([
        0.82 * env["hat_main"][i] + 0.18 * env["hat_air"][i]
        for i in range(min(len(env["hat_main"]), len(env["hat_air"])))
    ])

    n = min(len(kick_env), len(snare_env), len(hat_env))
    if n < 4:
        return []

    kick_novelty = normalize_series(onset_novelty(kick_env[:n]))
    snare_novelty = normalize_series(onset_novelty(snare_env[:n]))
    hat_novelty = normalize_series(onset_novelty(hat_env[:n]))

    # Full-band composite for general onset detection
    full_novelty = normalize_series([
        0.35 * kick_novelty[i] + 0.35 * snare_novelty[i] + 0.30 * hat_novelty[i]
        for i in range(n)
    ])

    # Step 1b: Broad onset detection (lower thresholds to catch more events for profiling)
    all_peaks = adaptive_peak_pick(
        full_novelty,
        hop_sec=hop_sec,
        k=1.50,  # Lower threshold for profiling — catch more events
        min_gap_sec=0.035,
        window_sec=0.28,
        percentile=0.65,
        density_boost=0.04,
    )

    if len(all_peaks) < 3:
        # Not enough onsets for clustering — fall back to standard approach
        return _fallback_standard_detection(
            samples, sr, kick_novelty, snare_novelty, hat_novelty,
            full_novelty, n, hop_sec,
        )

    # Step 1c: Extract spectral features for each onset
    feature_keys = list(ANALYSIS_BANDS.keys()) + ["centroid", "sharpness"]
    onset_data: list[tuple[float, float, dict[str, float]]] = []

    for idx, strength in all_peaks:
        t = frame_to_time(idx, HOP, sr)
        features = _extract_onset_features(samples, sr, t, PROFILE_WINDOW_SEC)
        if features:
            onset_data.append((t, strength, features))

    if len(onset_data) < 3:
        return _fallback_standard_detection(
            samples, sr, kick_novelty, snare_novelty, hat_novelty,
            full_novelty, n, hop_sec,
        )

    # Step 1d: Cluster onsets by spectral similarity
    vectors = [_features_to_vector(od[2], feature_keys) for od in onset_data]
    k = min(5, max(3, len(vectors) // 8))  # 3-5 clusters
    cluster_labels = _kmeans(vectors, k)

    # Build average profile per cluster
    cluster_profiles: list[dict[str, float]] = []
    for c in range(k):
        members = [onset_data[i][2] for i in range(len(onset_data)) if cluster_labels[i] == c]
        if members:
            avg = {}
            for key in feature_keys:
                avg[key] = sum(m.get(key, 0.0) for m in members) / float(len(members))
            cluster_profiles.append(avg)
        else:
            cluster_profiles.append({key: 0.0 for key in feature_keys})

    # Step 1e: Label clusters with drum classes
    drum_labels = _label_clusters(cluster_profiles)

    # Build templates: drum_class → average spectral profile
    templates: dict[str, dict[str, float]] = {}
    template_counts: dict[str, int] = {}
    for c, label in enumerate(drum_labels):
        if label not in templates:
            templates[label] = cluster_profiles[c].copy()
            template_counts[label] = 1
        else:
            # Merge into existing template
            for key in feature_keys:
                templates[label][key] += cluster_profiles[c].get(key, 0.0)
            template_counts[label] += 1

    for label in templates:
        count = template_counts[label]
        if count > 1:
            for key in feature_keys:
                templates[label][key] /= float(count)

    # ===================================================================
    # PASS 2: Refined detection using learned templates
    # ===================================================================

    # Step 2a: Per-band peak picking (tighter thresholds for the actual output)
    kick_peaks = adaptive_peak_pick(
        kick_novelty,
        hop_sec=hop_sec,
        k=2.00,
        min_gap_sec=0.085,
        window_sec=0.32,
        percentile=0.80,
        density_boost=0.08,
    )
    snare_peaks_raw = adaptive_peak_pick(
        snare_novelty,
        hop_sec=hop_sec,
        k=2.50,
        min_gap_sec=0.080,
        window_sec=0.32,
        percentile=0.84,
        density_boost=0.05,
    )
    # Cross-band filter for snare (same as spectral_flux_multiband)
    snare_peaks = []
    for idx, strength in snare_peaks_raw:
        sev = snare_novelty[idx] if idx < len(snare_novelty) else 0.0
        kev = kick_novelty[idx] if idx < len(kick_novelty) else 0.0
        kick_ratio = kev / max(1e-9, sev)
        if kick_ratio < 0.60 or sev > 0.35:
            snare_peaks.append((idx, strength))

    hat_peaks = adaptive_peak_pick(
        hat_novelty,
        hop_sec=hop_sec,
        k=1.85,
        min_gap_sec=0.038,
        window_sec=0.24,
        percentile=0.74,
        density_boost=0.05,
    )

    # Step 2b: Collect all raw onsets with initial band-based labels
    raw_onsets: list[tuple[float, str, float]] = []
    for idx, strength in kick_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "kick", float(strength)))
    for idx, strength in snare_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "snare", float(strength)))
    for idx, strength in hat_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "hh_closed", float(strength)))

    if not raw_onsets:
        return []

    raw_onsets.sort(key=lambda x: x[0])

    # Step 2c: Template-refine each onset's classification
    # For each onset, extract features and compare against templates
    refined_onsets: list[tuple[float, str, float]] = []
    for t, initial_class, strength in raw_onsets:
        features = _extract_onset_features(samples, sr, t, PROFILE_WINDOW_SEC)
        if not features:
            refined_onsets.append((t, initial_class, strength))
            continue

        # Match against templates
        matched_class, confidence = _match_template(features, templates, feature_keys)

        # Decision logic: use template match if confident, else trust band detection
        if confidence > 0.55:
            # High confidence template match — use it
            final_class = matched_class
        elif confidence > 0.35:
            # Medium confidence — prefer band detection but allow template override
            # if the initial class seems wrong
            if matched_class == initial_class:
                final_class = initial_class
            else:
                # Check if the template match is more plausible
                centroid = features.get("centroid", 5000.0)
                if initial_class == "kick" and centroid > 4000:
                    final_class = matched_class  # Band said kick but centroid is too high
                elif initial_class == "hh_closed" and centroid < 2000:
                    final_class = matched_class  # Band said hi-hat but centroid is too low
                else:
                    final_class = initial_class
        else:
            # Low confidence — trust band detection
            final_class = initial_class

        # Map template classes to specific drum classes
        if final_class == "hi_hat":
            final_class = "hh_closed"
        elif final_class == "unknown":
            final_class = initial_class

        refined_onsets.append((t, final_class, strength))

    # Step 2d: Cluster refined onsets within 25ms windows
    clusters: list[list[tuple[float, str, float]]] = []
    current_cluster: list[tuple[float, str, float]] = [refined_onsets[0]]
    for onset in refined_onsets[1:]:
        cluster_center = sum(o[0] for o in current_cluster) / float(len(current_cluster))
        if abs(onset[0] - cluster_center) <= 0.025:
            current_cluster.append(onset)
        else:
            clusters.append(current_cluster)
            current_cluster = [onset]
    if current_cluster:
        clusters.append(current_cluster)

    # Step 2e: Tempo estimation for soft grid
    period_sec, beat_conf = estimate_tempo_from_onset_env(full_novelty, hop_sec)
    grid_step = period_sec / 2.0 if beat_conf >= 0.15 and period_sec > 0.0 else 0.0
    grid_tolerance = min(0.028, grid_step * 0.2) if grid_step > 0.0 else 0.0

    # Step 2f: Decode clusters into drum candidates
    decoded: list[DrumCandidate] = []

    for cluster in clusters:
        if not cluster:
            continue

        weight_sum = sum(s for _, _, s in cluster)
        if weight_sum <= 0.0:
            weight_sum = 1.0
        cluster_time = sum(t * s for t, _, s in cluster) / weight_sum

        # Soft grid alignment
        if grid_step > 0.0:
            snapped = snap_time_to_grid(
                cluster_time, anchor=0.0, step=grid_step, tolerance=grid_tolerance,
            )
            cluster_time = (cluster_time * 0.82) + (snapped * 0.18)

        cluster_time = max(0.0, cluster_time)

        # Collect band/class info
        band_classes: dict[str, float] = {}
        for _, cls, strength in cluster:
            if cls in band_classes:
                band_classes[cls] = max(band_classes[cls], strength)
            else:
                band_classes[cls] = strength

        # Use template-refined timbral features for final classification
        tf = timbral_features(samples, sr, cluster_time)

        # Determine dominant class
        if "kick" in band_classes and band_classes["kick"] >= 0.3:
            if tf.get("sub", 0.0) >= tf.get("mid", 0.0) * 0.5:
                decoded.append(DrumCandidate(
                    time=round(cluster_time, 6),
                    drum_class="kick",
                    strength=band_classes["kick"],
                    confidence=0.8,
                    source="spectral_template",
                ))

        if "snare" in band_classes and band_classes["snare"] >= 0.25:
            decoded.append(DrumCandidate(
                time=round(cluster_time, 6),
                drum_class="snare",
                strength=band_classes["snare"],
                confidence=0.75,
                source="spectral_template",
            ))

        if "hh_closed" in band_classes and band_classes["hh_closed"] >= 0.20:
            hat_class = classify_hat_or_cymbal(tf)
            decoded.append(DrumCandidate(
                time=round(cluster_time, 6),
                drum_class=hat_class,
                strength=band_classes["hh_closed"],
                confidence=0.7,
                source="spectral_template",
            ))

        # If no band class fired but we have a confidence from the cluster
        if not decoded or decoded[-1].time != round(cluster_time, 6):
            # Use timbral features as fallback
            low = tf.get("low", 0.0)
            mid = tf.get("mid", 0.0)
            high = tf.get("high", 0.0)
            best_strength = max(s for _, _, s in cluster)

            if low > mid and low > high:
                decoded.append(DrumCandidate(
                    time=round(cluster_time, 6),
                    drum_class="kick",
                    strength=best_strength,
                    confidence=0.5,
                    source="spectral_template",
                ))
            elif high > mid:
                hat_class = classify_hat_or_cymbal(tf)
                decoded.append(DrumCandidate(
                    time=round(cluster_time, 6),
                    drum_class=hat_class,
                    strength=best_strength,
                    confidence=0.5,
                    source="spectral_template",
                ))
            else:
                decoded.append(DrumCandidate(
                    time=round(cluster_time, 6),
                    drum_class="snare",
                    strength=best_strength,
                    confidence=0.5,
                    source="spectral_template",
                ))

    return decoded


def _fallback_standard_detection(
    samples: list[float],
    sr: int,
    kick_novelty: list[float],
    snare_novelty: list[float],
    hat_novelty: list[float],
    full_novelty: list[float],
    n: int,
    hop_sec: float,
) -> list[DrumCandidate]:
    """Standard detection without template matching — used when too few onsets for clustering."""
    kick_peaks = adaptive_peak_pick(
        kick_novelty, hop_sec=hop_sec, k=2.10, min_gap_sec=0.085,
        window_sec=0.32, percentile=0.82, density_boost=0.08,
    )
    snare_peaks = adaptive_peak_pick(
        snare_novelty, hop_sec=hop_sec, k=2.65, min_gap_sec=0.080,
        window_sec=0.32, percentile=0.86, density_boost=0.05,
    )
    hat_peaks = adaptive_peak_pick(
        hat_novelty, hop_sec=hop_sec, k=1.85, min_gap_sec=0.038,
        window_sec=0.24, percentile=0.74, density_boost=0.05,
    )

    decoded: list[DrumCandidate] = []
    for idx, strength in kick_peaks:
        t = frame_to_time(idx, HOP, sr)
        decoded.append(DrumCandidate(time=round(t, 6), drum_class="kick",
                                     strength=strength, confidence=0.6,
                                     source="spectral_template_fallback"))
    for idx, strength in snare_peaks:
        t = frame_to_time(idx, HOP, sr)
        decoded.append(DrumCandidate(time=round(t, 6), drum_class="snare",
                                     strength=strength, confidence=0.55,
                                     source="spectral_template_fallback"))
    for idx, strength in hat_peaks:
        t = frame_to_time(idx, HOP, sr)
        tf = timbral_features(samples, sr, t)
        hat_class = classify_hat_or_cymbal(tf)
        decoded.append(DrumCandidate(time=round(t, 6), drum_class=hat_class,
                                     strength=strength, confidence=0.5,
                                     source="spectral_template_fallback"))
    return decoded


# Module-level transcribe function for registry compatibility
def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = SpectralTemplateMultipassAlgorithm()
    return algo.transcribe(stem_path)
