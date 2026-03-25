"""Multi-resolution + cross-correlation template combined approach (Theories 2+3+5).

The strongest combination:
1. Multi-resolution spectral flux (Theory 2) for onset detection
2. Cross-correlation template matching (Theory 3) for classification
3. Probabilistic beat-position filtering (Theory 5) as post-processing
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
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
    DRUM_CLASS_TO_MIDI,
)
from aural_ingest.transcription import DrumEvent

HOP = 320
FRAME = 1024

ONSET_BANDS = {
    "kick_low":    (35.0, 120.0),
    "kick_high":   (120.0, 200.0),
    "snare_mid":   (200.0, 2200.0),
    "snare_crack": (1800.0, 4500.0),
    "hat_main":    (5000.0, 12000.0),
    "hat_air":     (7000.0, 16000.0),
}

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

TEMPLATE_WINDOW_SEC = 0.05

# Multi-resolution configs
RES_FAST   = {"frame": 512,  "hop": 128}
RES_MED    = {"frame": 2048, "hop": 320}
RES_SLOW   = {"frame": 4096, "hop": 512}


def _band_env_at_res(
    samples: list[float],
    sr: int,
    band: tuple[float, float],
    frame_size: int,
    hop_size: int,
) -> list[float]:
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


def _extract_profile(
    samples: list[float], sr: int, t: float, window_sec: float = TEMPLATE_WINDOW_SEC,
) -> list[float]:
    start = max(0, int(t * sr))
    length = max(64, int(window_sec * sr))
    end = min(len(samples), start + length)
    if end - start < 32:
        return []
    window = samples[start:end]
    energies: list[float] = []
    total = 0.0
    for _name, (lo, hi) in ANALYSIS_BANDS.items():
        filtered = band_pass_one_pole(window, sr, lo, hi)
        if filtered:
            e = math.sqrt(sum(x * x for x in filtered) / float(len(filtered)))
        else:
            e = 0.0
        energies.append(e)
        total += e
    if total > 1e-9:
        energies = [e / total for e in energies]
    return energies


def _xcorr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    a_mean = sum(a[:n]) / n
    b_mean = sum(b[:n]) / n
    num = sum((a[i] - a_mean) * (b[i] - b_mean) for i in range(n))
    a_var = sum((a[i] - a_mean) ** 2 for i in range(n))
    b_var = sum((b[i] - b_mean) ** 2 for i in range(n))
    denom = math.sqrt(a_var * b_var)
    return num / denom if denom > 1e-12 else 0.0


# Beat-position probabilities (from Theory 5)
_KICK_PROBS = {0.00: 0.95, 0.25: 0.30, 0.50: 0.70, 0.75: 0.25}
_SNARE_PROBS = {0.00: 0.10, 0.25: 0.90, 0.50: 0.10, 0.75: 0.90}
_HAT_PROBS = {0.00: 0.85, 0.125: 0.70, 0.25: 0.85, 0.375: 0.70, 0.50: 0.85, 0.625: 0.70, 0.75: 0.85, 0.875: 0.70}

_MIDI_TO_PATTERN = {36: "kick", 38: "snare", 42: "hat", 46: "hat", 49: "hat", 51: "hat", 50: "snare", 47: "snare", 41: "kick"}

def _beat_prob(time_sec: float, beat_period: float, pattern: str) -> float:
    if beat_period <= 0:
        return 0.5
    probs = {"kick": _KICK_PROBS, "snare": _SNARE_PROBS, "hat": _HAT_PROBS}.get(pattern, _HAT_PROBS)
    bar_period = beat_period * 4
    pos = (time_sec % bar_period) / bar_period
    best_prob = 0.3
    best_dist = float("inf")
    for dp, prob in probs.items():
        dist = min(abs(pos - dp), abs(pos - dp + 1.0), abs(pos - dp - 1.0))
        if dist < best_dist:
            best_dist = dist
            best_prob = prob
    return best_prob


class MultiResolutionTemplateAlgorithm(TranscriptionAlgorithm):
    name = "multi_resolution_template"

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

        # ====================================================================
        # Stage 1: Multi-resolution onset detection (Theory 2)
        # ====================================================================

        # Kick: slow resolution (4096)
        kick_env_slow = normalize_series(_band_env_at_res(
            samples, sr, (35.0, 200.0), RES_SLOW["frame"], RES_SLOW["hop"],
        ))
        kick_hop_sec = RES_SLOW["hop"] / float(sr)

        # Snare: medium resolution (2048)
        snare_env_med = normalize_series(_band_env_at_res(
            samples, sr, (200.0, 4500.0), RES_MED["frame"], RES_MED["hop"],
        ))
        snare_hop_sec = RES_MED["hop"] / float(sr)

        # Hi-hat: fast resolution (512)
        hat_env_fast = normalize_series(_band_env_at_res(
            samples, sr, (5000.0, 16000.0), RES_FAST["frame"], RES_FAST["hop"],
        ))
        hat_hop_sec = RES_FAST["hop"] / float(sr)

        # Onset novelty at each resolution
        kick_novelty = normalize_series(onset_novelty(kick_env_slow)) if len(kick_env_slow) >= 4 else []
        snare_novelty = normalize_series(onset_novelty(snare_env_med)) if len(snare_env_med) >= 4 else []
        hat_novelty = normalize_series(onset_novelty(hat_env_fast)) if len(hat_env_fast) >= 4 else []

        # Initial peak detection for template learning
        kick_init_peaks = adaptive_peak_pick(
            kick_novelty, hop_sec=kick_hop_sec, k=1.80, min_gap_sec=0.085,
            window_sec=0.28, percentile=0.70, density_boost=0.06,
        ) if kick_novelty else []

        snare_init_peaks = adaptive_peak_pick(
            snare_novelty, hop_sec=snare_hop_sec, k=2.00, min_gap_sec=0.080,
            window_sec=0.28, percentile=0.75, density_boost=0.05,
        ) if snare_novelty else []

        hat_init_peaks = adaptive_peak_pick(
            hat_novelty, hop_sec=hat_hop_sec, k=1.50, min_gap_sec=0.038,
            window_sec=0.24, percentile=0.65, density_boost=0.05,
        ) if hat_novelty else []

        # ====================================================================
        # Stage 2: Learn spectral templates (Theory 3)
        # ====================================================================
        templates: dict[str, list[float]] = {}
        for label, peaks, hop_size in [
            ("kick", kick_init_peaks, RES_SLOW["hop"]),
            ("snare", snare_init_peaks, RES_MED["hop"]),
            ("hh_closed", hat_init_peaks, RES_FAST["hop"]),
        ]:
            sorted_peaks = sorted(peaks, key=lambda p: -p[1])[:5]
            profiles: list[list[float]] = []
            for idx, _s in sorted_peaks:
                t = frame_to_time(idx, hop_size, sr)
                prof = _extract_profile(samples, sr, t)
                if prof:
                    profiles.append(prof)
            if profiles:
                n_bands = len(profiles[0])
                templates[label] = [
                    sum(p[b] for p in profiles) / float(len(profiles))
                    for b in range(n_bands)
                ]

        # ====================================================================
        # Stage 3: Template-refined detection with cross-correlation
        # ====================================================================
        candidates: list[DrumCandidate] = []

        # Re-pick at tighter thresholds
        kick_main_peaks = adaptive_peak_pick(
            kick_novelty, hop_sec=kick_hop_sec, k=2.10, min_gap_sec=0.085,
            window_sec=0.32, percentile=0.80, density_boost=0.08,
        ) if kick_novelty else []
        snare_main_peaks = adaptive_peak_pick(
            snare_novelty, hop_sec=snare_hop_sec, k=2.50, min_gap_sec=0.080,
            window_sec=0.32, percentile=0.84, density_boost=0.05,
        ) if snare_novelty else []
        hat_main_peaks = adaptive_peak_pick(
            hat_novelty, hop_sec=hat_hop_sec, k=1.85, min_gap_sec=0.038,
            window_sec=0.24, percentile=0.74, density_boost=0.05,
        ) if hat_novelty else []

        for label, peaks, hop_size, initial_class in [
            ("kick", kick_main_peaks, RES_SLOW["hop"], "kick"),
            ("snare", snare_main_peaks, RES_MED["hop"], "snare"),
            ("hh_closed", hat_main_peaks, RES_FAST["hop"], "hh_closed"),
        ]:
            for idx, strength in peaks:
                t = frame_to_time(idx, hop_size, sr)
                prof = _extract_profile(samples, sr, t)

                final_class = initial_class
                confidence = 0.65

                if prof and templates:
                    # Cross-correlate with all templates to find best match
                    best_cls = initial_class
                    best_corr = -1.0
                    for tmpl_cls, tmpl in templates.items():
                        corr = _xcorr(prof, tmpl)
                        if corr > best_corr:
                            best_corr = corr
                            best_cls = tmpl_cls

                    confidence = max(0.3, (best_corr + 1.0) / 2.0)

                    # Use template match if confident, else trust resolution
                    if best_corr > 0.6:
                        final_class = best_cls
                    elif best_corr > 0.35 and best_cls == initial_class:
                        final_class = initial_class
                        confidence = max(confidence, 0.7)

                # Hat sub-classification
                if final_class in ("hh_closed", "hi_hat"):
                    tf = timbral_features(samples, sr, t)
                    final_class = classify_hat_or_cymbal(tf)

                candidates.append(DrumCandidate(
                    time=round(t, 6),
                    drum_class=final_class,
                    strength=float(strength),
                    confidence=float(confidence),
                    source="multi_res_template",
                ))

        if not candidates:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        events = candidates_to_events(candidates, stem_path=stem_path)
        if not events:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # ====================================================================
        # Stage 4: Probabilistic pattern filtering (Theory 5)
        # ====================================================================

        # Estimate tempo using medium-resolution envelope
        if snare_novelty:
            beat_period, beat_conf = estimate_tempo_from_onset_env(snare_novelty, snare_hop_sec)
        else:
            beat_period, beat_conf = 0.0, 0.0

        if beat_conf < 0.10 or beat_period <= 0:
            return events  # no reliable beat grid

        filtered_events: list[DrumEvent] = []
        for ev in events:
            pattern = _MIDI_TO_PATTERN.get(ev.note, "hat")
            prob = _beat_prob(ev.time, beat_period, pattern)
            vel_factor = ev.velocity / 127.0
            threshold = max(0.08, 0.30 - vel_factor * 0.18)

            if prob >= threshold:
                vel_boost = 1.0 + (prob - 0.5) * 0.12
                new_vel = int(min(127, max(1, ev.velocity * vel_boost)))
                filtered_events.append(DrumEvent(
                    time=ev.time, note=ev.note, velocity=new_vel, duration=ev.duration,
                ))

        if not filtered_events:
            return events

        return sorted(filtered_events, key=lambda e: e.time)


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = MultiResolutionTemplateAlgorithm()
    return algo.transcribe(stem_path)
