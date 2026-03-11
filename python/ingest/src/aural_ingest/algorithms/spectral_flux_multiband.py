from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    centroid_trajectory,
    classify_hat_or_cymbal,
    classify_tom,
    clamp,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    high_decay_fine,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class SpectralFluxMultibandAlgorithm(TranscriptionAlgorithm):
    name = "spectral_flux_multiband"

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


# ---------------------------------------------------------------------------
# Band definitions for envelope-based multiband onset detection
# ---------------------------------------------------------------------------

ONSET_BANDS = {
    "kick_low": (35.0, 120.0),
    "kick_high": (120.0, 200.0),
    "snare_mid": (200.0, 2200.0),
    "snare_crack": (1800.0, 4500.0),
    "hat_main": (5000.0, 12000.0),
    "hat_air": (7000.0, 16000.0),
    "cym": (3500.0, 10000.0),
}

HOP = 320
FRAME = 1024


# ---------------------------------------------------------------------------
# Core detection pipeline
# ---------------------------------------------------------------------------

def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    """Multi-band onset detection with overlap resolution and decay-gated hat classification."""
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

    # -----------------------------------------------------------------------
    # Step 1: Per-band envelope computation and onset novelty
    # -----------------------------------------------------------------------
    env = compute_band_envelopes(
        samples, sr, ONSET_BANDS, hop_size=HOP, frame_size=FRAME,
    )
    if not env:
        return []

    # Combine sub-bands into three composite onset novelty curves
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
    cym_env = normalize_series(env.get("cym", []))

    n = min(len(kick_env), len(snare_env), len(hat_env), len(cym_env))
    if n < 4:
        return []

    # Per-band onset novelty (half-wave rectified first differences)
    kick_novelty = normalize_series(onset_novelty(kick_env[:n]))
    snare_novelty = normalize_series(onset_novelty(snare_env[:n]))
    hat_novelty = normalize_series(onset_novelty(hat_env[:n]))

    # -----------------------------------------------------------------------
    # Step 2: Independent peak-picking per band
    # Tightened thresholds to reduce false positives on real-world audio.
    # -----------------------------------------------------------------------
    kick_peaks = adaptive_peak_pick(
        kick_novelty,
        hop_sec=hop_sec,
        k=2.10,
        min_gap_sec=0.085,
        window_sec=0.32,
        percentile=0.82,
        density_boost=0.08,
    )
    snare_peaks = adaptive_peak_pick(
        snare_novelty,
        hop_sec=hop_sec,
        k=2.40,
        min_gap_sec=0.080,
        window_sec=0.32,
        percentile=0.84,
        density_boost=0.06,
    )
    hat_peaks = adaptive_peak_pick(
        hat_novelty,
        hop_sec=hop_sec,
        k=1.85,
        min_gap_sec=0.038,
        window_sec=0.24,
        percentile=0.74,
        density_boost=0.05,
    )

    # Full-band composite for fills and rare events — very strict
    full_novelty = normalize_series([
        0.35 * kick_novelty[i] + 0.30 * snare_novelty[i]
        + 0.20 * hat_novelty[i] + 0.15 * normalize_series(onset_novelty(cym_env[:n]))[i]
        for i in range(n)
    ])
    full_peaks = adaptive_peak_pick(
        full_novelty,
        hop_sec=hop_sec,
        k=2.30,
        min_gap_sec=0.065,
        window_sec=0.32,
        percentile=0.85,
        density_boost=0.10,
    )

    # -----------------------------------------------------------------------
    # Step 3: Estimate tempo for soft grid alignment
    # -----------------------------------------------------------------------
    period_sec, beat_conf = estimate_tempo_from_onset_env(full_novelty, hop_sec)
    grid_step = period_sec / 2.0 if beat_conf >= 0.15 and period_sec > 0.0 else 0.0
    grid_tolerance = min(0.028, grid_step * 0.2) if grid_step > 0.0 else 0.0

    # -----------------------------------------------------------------------
    # Step 4: Build onset events per band and assemble
    # -----------------------------------------------------------------------
    raw_onsets: list[tuple[float, str, float]] = []

    for idx, strength in kick_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "kick", float(strength)))
    for idx, strength in snare_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "snare", float(strength)))
    for idx, strength in hat_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "hh_closed", float(strength)))
    for idx, strength in full_peaks:
        raw_onsets.append((frame_to_time(idx, HOP, sr), "_full", float(strength)))

    if not raw_onsets:
        return []

    raw_onsets.sort(key=lambda x: x[0])

    # -----------------------------------------------------------------------
    # Step 5: Cluster onsets within 25ms windows (multi-label assembly)
    # -----------------------------------------------------------------------
    clusters: list[list[tuple[float, str, float]]] = []
    current_cluster: list[tuple[float, str, float]] = [raw_onsets[0]]
    for onset in raw_onsets[1:]:
        cluster_center = sum(o[0] for o in current_cluster) / float(len(current_cluster))
        if abs(onset[0] - cluster_center) <= 0.025:
            current_cluster.append(onset)
        else:
            clusters.append(current_cluster)
            current_cluster = [onset]
    if current_cluster:
        clusters.append(current_cluster)

    # -----------------------------------------------------------------------
    # Step 6: Decode each cluster into drum candidates
    # -----------------------------------------------------------------------
    decoded: list[DrumCandidate] = []

    for cluster in clusters:
        if not cluster:
            continue

        # Weighted cluster time
        weight_sum = sum(s for _, _, s in cluster)
        if weight_sum <= 0.0:
            weight_sum = 1.0
        cluster_time = sum(t * s for t, _, s in cluster) / weight_sum

        # Soft grid alignment (preserve microtiming)
        if grid_step > 0.0:
            snapped = snap_time_to_grid(
                cluster_time, anchor=0.0, step=grid_step, tolerance=grid_tolerance,
            )
            cluster_time = (cluster_time * 0.82) + (snapped * 0.18)

        cluster_time = max(0.0, cluster_time)

        # Collect which bands fired in this cluster
        band_classes: dict[str, float] = {}
        for _, cls, strength in cluster:
            if cls in band_classes:
                band_classes[cls] = max(band_classes[cls], strength)
            else:
                band_classes[cls] = strength

        # Get timbral features at this time
        feat = timbral_features(samples, sr, cluster_time)
        total_energy = max(
            1e-9,
            feat["low"] + feat["sub"] + feat["mid"] + feat["snare_crack"]
            + feat["high"] + feat["air"],
        )
        low_dom = clamp((feat["low"] + 0.55 * feat["sub"]) / total_energy, 0.0, 1.0)
        snare_dom = clamp(
            (feat["snare_crack"] + 0.4 * feat["mid"]) / total_energy, 0.0, 1.0,
        )
        high_dom = clamp((feat["high"] + 0.3 * feat["air"]) / total_energy, 0.0, 1.0)
        high_decay_val = clamp(feat["high_decay"], 0.0, 1.0)
        sharp = clamp((feat["sharpness"] - 1.25) / 1.75, 0.0, 1.0)
        zcr = clamp((feat["zcr"] - 0.05) / 0.18, 0.0, 1.0)

        # Fine-grained decay for hat-vs-snare-vs-crash disambiguation
        hd_0_5, hd_5_15, hd_15_30 = high_decay_fine(samples, sr, cluster_time)
        decay_ratio_fast = (hd_5_15 / max(1e-6, hd_0_5)) if hd_0_5 > 1e-6 else 1.0
        fast_decay = decay_ratio_fast < 0.55
        # Crash detection: sustained high-frequency energy (slow decay)
        sustained_high = decay_ratio_fast > 0.72 and hd_15_30 > hd_0_5 * 0.35

        # Centroid trajectory for hat-vs-snare
        centroids = centroid_trajectory(samples, sr, cluster_time)
        centroid_falling = False
        if len(centroids) >= 3 and centroids[0] > 1000.0:
            centroid_falling = centroids[2] < centroids[0] * 0.75

        # Envelope-level evidence at this frame
        frame_idx = max(0, min(n - 1, int(round(cluster_time * sr / HOP))))
        kick_ev = kick_novelty[frame_idx] if frame_idx < len(kick_novelty) else 0.0
        snare_ev = snare_novelty[frame_idx] if frame_idx < len(snare_novelty) else 0.0
        hat_ev = hat_novelty[frame_idx] if frame_idx < len(hat_novelty) else 0.0

        # Which bands fired
        has_kick = "kick" in band_classes
        has_snare = "snare" in band_classes
        has_hat = "hh_closed" in band_classes
        has_full = "_full" in band_classes

        # ------ Multi-label decode ------
        _decode_cluster(
            decoded,
            cluster_time=cluster_time,
            band_classes=band_classes,
            feat=feat,
            low_dom=low_dom,
            snare_dom=snare_dom,
            high_dom=high_dom,
            high_decay_val=high_decay_val,
            sharp=sharp,
            zcr=zcr,
            fast_decay=fast_decay,
            sustained_high=sustained_high,
            centroid_falling=centroid_falling,
            kick_ev=kick_ev,
            snare_ev=snare_ev,
            hat_ev=hat_ev,
            has_kick=has_kick,
            has_snare=has_snare,
            has_hat=has_hat,
            has_full=has_full,
        )

    return decoded


# ---------------------------------------------------------------------------
# Cluster decoding logic
# ---------------------------------------------------------------------------

def _decode_cluster(
    decoded: list[DrumCandidate],
    *,
    cluster_time: float,
    band_classes: dict[str, float],
    feat: dict[str, float],
    low_dom: float,
    snare_dom: float,
    high_dom: float,
    high_decay_val: float,
    sharp: float,
    zcr: float,
    fast_decay: bool,
    sustained_high: bool,
    centroid_falling: bool,
    kick_ev: float,
    snare_ev: float,
    hat_ev: float,
    has_kick: bool,
    has_snare: bool,
    has_hat: bool,
    has_full: bool,
) -> None:
    """Decode a cluster into one or more DrumCandidate events (multi-label)."""

    # --- Simultaneous core + hat events ---
    if has_hat and (has_kick or has_snare):
        # Emit the core instrument(s)
        if has_kick and low_dom >= 0.18:
            _emit(decoded, cluster_time, "kick",
                  score=clamp(0.4 + 0.28 * low_dom + 0.18 * sharp + 0.14 * kick_ev, 0.0, 1.0),
                  strength=band_classes.get("kick", 0.5))
        if has_snare and snare_dom >= 0.18:
            # Guard: is this actually a hat leaking into snare band?
            if fast_decay and centroid_falling and high_dom >= snare_dom * 0.8:
                pass  # suppress -- this is a hat, not a snare
            # Guard: is this actually a kick leaking into snare band?
            elif low_dom > snare_dom * 1.3:
                pass  # suppress -- low-freq dominant, this is a kick not a snare
            else:
                _emit(decoded, cluster_time, "snare",
                      score=clamp(0.4 + 0.28 * snare_dom + 0.16 * zcr + 0.16 * snare_ev, 0.0, 1.0),
                      strength=band_classes.get("snare", 0.5))

        # Emit hat only when high-freq evidence is strong AND it's actually
        # a transient hit, not sustained cymbal/crash bleed
        if not sustained_high and high_dom >= 0.15 and hat_ev >= 0.20:
            hat_class = _classify_hat(feat, high_decay_val, fast_decay, sustained_high, centroid_falling)
            _emit(decoded, cluster_time, hat_class,
                  score=clamp(0.4 + 0.25 * high_dom + 0.15 * hat_ev, 0.0, 1.0),
                  strength=band_classes.get("hh_closed", 0.4))
        return

    # --- Pure hat detection ---
    if has_hat and not has_kick and not has_snare:
        if high_dom >= 0.12 or (fast_decay and band_classes.get("hh_closed", 0.0) >= 0.40):
            hat_class = _classify_hat(feat, high_decay_val, fast_decay, sustained_high, centroid_falling)
            _emit(decoded, cluster_time, hat_class,
                  score=clamp(0.5 + 0.3 * high_dom + 0.2 * hat_ev, 0.0, 1.0),
                  strength=band_classes.get("hh_closed", 0.5))
        return

    # --- Kick only ---
    if has_kick and not has_snare:
        if low_dom >= 0.18 or (band_classes.get("kick", 0.0) >= 0.45 and low_dom >= 0.12):
            _emit(decoded, cluster_time, "kick",
                  score=clamp(0.4 + 0.3 * low_dom + 0.15 * sharp + 0.15 * kick_ev, 0.0, 1.0),
                  strength=band_classes.get("kick", 0.5))
        return

    # --- Snare only ---
    if has_snare and not has_kick:
        # Check if this is a hat that leaked into snare band
        if fast_decay and centroid_falling and high_dom >= snare_dom * 0.8:
            hat_class = _classify_hat(feat, high_decay_val, fast_decay, sustained_high, centroid_falling)
            _emit(decoded, cluster_time, hat_class,
                  score=clamp(0.5 + 0.3 * high_dom, 0.0, 1.0),
                  strength=band_classes.get("snare", 0.5))
        # Reclassify as kick when low-freq energy dominates
        elif low_dom > snare_dom * 1.4 and low_dom >= 0.22:
            _emit(decoded, cluster_time, "kick",
                  score=clamp(0.4 + 0.3 * low_dom + 0.15 * sharp, 0.0, 1.0),
                  strength=band_classes.get("snare", 0.5))
        elif snare_dom >= 0.20 and low_dom < snare_dom * 1.2 and (zcr >= 0.12 or snare_ev >= 0.25):
            _emit(decoded, cluster_time, "snare",
                  score=clamp(0.4 + 0.3 * snare_dom + 0.15 * zcr + 0.15 * snare_ev, 0.0, 1.0),
                  strength=band_classes.get("snare", 0.5))
        elif band_classes.get("snare", 0.0) >= 0.55 and snare_dom >= 0.16 and low_dom < snare_dom * 1.1:
            _emit(decoded, cluster_time, "snare",
                  score=clamp(0.4 + 0.3 * snare_dom + 0.15 * zcr + 0.15 * snare_ev, 0.0, 1.0),
                  strength=band_classes.get("snare", 0.5))
        return

    # --- Both kick and snare fired (no hat) ---
    if has_kick and has_snare:
        if low_dom > snare_dom * 1.2 and sharp >= 0.12:
            _emit(decoded, cluster_time, "kick",
                  score=clamp(0.45 + 0.3 * low_dom, 0.0, 1.0),
                  strength=band_classes.get("kick", 0.5))
        elif snare_dom > low_dom * 1.0 and zcr >= 0.18:
            _emit(decoded, cluster_time, "snare",
                  score=clamp(0.45 + 0.3 * snare_dom, 0.0, 1.0),
                  strength=band_classes.get("snare", 0.5))
        else:
            if low_dom >= 0.20:
                _emit(decoded, cluster_time, "kick",
                      score=clamp(0.4 + 0.25 * low_dom, 0.0, 1.0),
                      strength=band_classes.get("kick", 0.5))
            if snare_dom >= 0.18:
                _emit(decoded, cluster_time, "snare",
                      score=clamp(0.4 + 0.25 * snare_dom, 0.0, 1.0),
                      strength=band_classes.get("snare", 0.5))
        return

    # --- Full-band peak only (fill/rare class) ---
    if has_full:
        full_str = band_classes.get("_full", 0.5)
        if low_dom >= max(snare_dom, high_dom) * 1.15:
            _emit(decoded, cluster_time, "kick",
                  score=clamp(0.35 + 0.3 * low_dom, 0.0, 1.0),
                  strength=full_str)
        elif snare_dom >= max(low_dom, high_dom) * 1.05:
            _emit(decoded, cluster_time, "snare",
                  score=clamp(0.35 + 0.3 * snare_dom, 0.0, 1.0),
                  strength=full_str)
        elif high_dom >= max(low_dom, snare_dom) * 1.0 and not sustained_high:
            hat_class = _classify_hat(feat, high_decay_val, fast_decay, sustained_high, centroid_falling)
            _emit(decoded, cluster_time, hat_class,
                  score=clamp(0.35 + 0.3 * high_dom, 0.0, 1.0),
                  strength=full_str)
        else:
            tom_class = classify_tom(feat)
            _emit(decoded, cluster_time, tom_class,
                  score=clamp(0.3 + 0.2 * (low_dom + snare_dom), 0.0, 1.0),
                  strength=full_str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit(
    out: list[DrumCandidate],
    time_sec: float,
    drum_class: str,
    score: float,
    strength: float,
) -> None:
    out.append(
        DrumCandidate(
            time=max(0.0, float(time_sec)),
            drum_class=drum_class,
            strength=clamp(max(strength, score), 0.0, 1.0),
            confidence=clamp(score, 0.0, 1.0),
            source="spectral_flux_multiband",
        )
    )


def _classify_hat(
    feat: dict[str, float],
    high_decay_val: float,
    fast_decay: bool,
    sustained_high: bool,
    centroid_falling: bool,
) -> str:
    """Classify hi-hat type: closed, open, crash, or ride."""
    # Crash: sustained high energy means this is a cymbal hit, not a hat
    if sustained_high:
        return "crash"
    if high_decay_val >= 0.58 and feat.get("high", 0.0) > feat.get("snare_crack", 0.0) * 1.1:
        return classify_hat_or_cymbal(feat, prefer_ride_when_on_grid=False, on_grid=False)
    if high_decay_val >= 0.28 and not fast_decay:
        return "hh_open"
    return "hh_closed"


ALGORITHM = SpectralFluxMultibandAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
