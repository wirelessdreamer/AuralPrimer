"""Template matching with cross-correlation drum transcription (Theory 3).

Instead of Euclidean distance (as in spectral_template_multipass), uses
normalized cross-correlation to match learned spectral templates against
the audio.  Cross-correlation is more robust to amplitude variations and
produces a continuous match signal that can be peak-picked.
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


def _extract_spectral_profile(
    samples: list[float],
    sr: int,
    onset_time: float,
    window_sec: float = TEMPLATE_WINDOW_SEC,
) -> list[float]:
    """Extract normalized band-energy vector for a single onset."""
    start = max(0, int(onset_time * sr))
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


def _normalized_xcorr(template: list[float], signal: list[float]) -> float:
    """Compute normalized cross-correlation between template and signal."""
    n = min(len(template), len(signal))
    if n == 0:
        return 0.0

    t_mean = sum(template[:n]) / n
    s_mean = sum(signal[:n]) / n

    num = 0.0
    t_var = 0.0
    s_var = 0.0
    for i in range(n):
        td = template[i] - t_mean
        sd = signal[i] - s_mean
        num += td * sd
        t_var += td * td
        s_var += sd * sd

    denom = math.sqrt(t_var * s_var)
    if denom < 1e-12:
        return 0.0
    return num / denom


def _sliding_xcorr(
    template: list[float],
    samples: list[float],
    sr: int,
    hop_size: int,
    frame_size: int,
    window_sec: float = TEMPLATE_WINDOW_SEC,
) -> list[float]:
    """Compute sliding cross-correlation of a spectral template against audio.

    For each frame position, extract the spectral profile and correlate
    with the template. Returns a correlation time series.
    """
    n_samples = len(samples)
    corr_signal: list[float] = []
    pos = 0
    win_len = max(64, int(window_sec * sr))

    while pos + frame_size <= n_samples:
        # Extract spectral profile at this position
        end = min(n_samples, pos + win_len)
        if end - pos < 32:
            corr_signal.append(0.0)
            pos += hop_size
            continue

        window = samples[pos:end]
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

        corr = _normalized_xcorr(template, energies)
        corr_signal.append(max(0.0, corr))
        pos += hop_size

    return corr_signal


class TemplateXcorrAlgorithm(TranscriptionAlgorithm):
    name = "template_xcorr"

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

        # ---- Pass 1: Initial onset detection for template learning ----
        env = compute_band_envelopes(
            samples, sr, ONSET_BANDS, hop_size=HOP, frame_size=FRAME,
        )
        if not env:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

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
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        kick_novelty = normalize_series(onset_novelty(kick_env[:n]))
        snare_novelty = normalize_series(onset_novelty(snare_env[:n]))
        hat_novelty = normalize_series(onset_novelty(hat_env[:n]))

        # Pick initial hits for template extraction
        kick_peaks = adaptive_peak_pick(
            kick_novelty, hop_sec=hop_sec, k=1.80, min_gap_sec=0.085,
            window_sec=0.28, percentile=0.70, density_boost=0.06,
        )
        snare_peaks = adaptive_peak_pick(
            snare_novelty, hop_sec=hop_sec, k=2.00, min_gap_sec=0.080,
            window_sec=0.28, percentile=0.75, density_boost=0.05,
        )
        hat_peaks = adaptive_peak_pick(
            hat_novelty, hop_sec=hop_sec, k=1.50, min_gap_sec=0.038,
            window_sec=0.24, percentile=0.65, density_boost=0.05,
        )

        # ---- Extract templates from strongest initial hits ----
        templates: dict[str, list[float]] = {}
        for label, peaks in [("kick", kick_peaks), ("snare", snare_peaks), ("hh_closed", hat_peaks)]:
            # Take top 5 strongest hits for template
            sorted_peaks = sorted(peaks, key=lambda p: -p[1])[:5]
            profiles: list[list[float]] = []
            for idx, _strength in sorted_peaks:
                t = frame_to_time(idx, HOP, sr)
                prof = _extract_spectral_profile(samples, sr, t)
                if prof:
                    profiles.append(prof)

            if profiles:
                n_bands = len(profiles[0])
                avg = [
                    sum(p[b] for p in profiles) / float(len(profiles))
                    for b in range(n_bands)
                ]
                templates[label] = avg

        if len(templates) < 2:
            # Not enough templates — fall back to standard band detection
            return self._band_fallback(
                samples, sr, kick_novelty, snare_novelty, hat_novelty, hop_sec,
            )

        # ---- Pass 2: Cross-correlation detection ----
        candidates: list[DrumCandidate] = []

        for drum_class, template in templates.items():
            # Compute sliding correlation
            corr = _sliding_xcorr(template, samples, sr, HOP, FRAME)
            if not corr:
                continue

            corr_norm = normalize_series(corr)

            # Instrument-specific peak picking on correlation signal
            if drum_class == "kick":
                peaks = adaptive_peak_pick(
                    corr_norm, hop_sec=hop_sec, k=2.00, min_gap_sec=0.085,
                    window_sec=0.32, percentile=0.78, density_boost=0.08,
                )
            elif drum_class == "snare":
                peaks = adaptive_peak_pick(
                    corr_norm, hop_sec=hop_sec, k=2.30, min_gap_sec=0.080,
                    window_sec=0.32, percentile=0.82, density_boost=0.05,
                )
            else:  # hi-hat
                peaks = adaptive_peak_pick(
                    corr_norm, hop_sec=hop_sec, k=1.75, min_gap_sec=0.038,
                    window_sec=0.24, percentile=0.72, density_boost=0.05,
                )

            for idx, strength in peaks:
                t = frame_to_time(idx, HOP, sr)
                # Get actual correlation value as confidence
                conf = corr_norm[idx] if idx < len(corr_norm) else 0.5

                final_class = drum_class
                if drum_class == "hh_closed":
                    tf = timbral_features(samples, sr, t)
                    final_class = classify_hat_or_cymbal(tf)

                candidates.append(DrumCandidate(
                    time=round(t, 6),
                    drum_class=final_class,
                    strength=float(strength),
                    confidence=float(conf),
                    source="template_xcorr",
                ))

        if not candidates:
            return self._band_fallback(
                samples, sr, kick_novelty, snare_novelty, hat_novelty, hop_sec,
            )

        events = candidates_to_events(candidates, stem_path=stem_path)
        if events:
            return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed"],
            step_sec=0.082, velocity_base=87,
        )

    def _band_fallback(
        self,
        samples: list[float],
        sr: int,
        kick_novelty: list[float],
        snare_novelty: list[float],
        hat_novelty: list[float],
        hop_sec: float,
    ) -> list[DrumEvent]:
        """Standard band-based detection fallback."""
        candidates: list[DrumCandidate] = []

        for label, novelty, k, gap in [
            ("kick", kick_novelty, 2.10, 0.085),
            ("snare", snare_novelty, 2.65, 0.080),
            ("hh_closed", hat_novelty, 1.85, 0.038),
        ]:
            peaks = adaptive_peak_pick(
                novelty, hop_sec=hop_sec, k=k, min_gap_sec=gap,
                window_sec=0.32, percentile=0.82, density_boost=0.06,
            )
            for idx, strength in peaks:
                t = frame_to_time(idx, HOP, sr)
                final_class = label
                if label == "hh_closed":
                    tf = timbral_features(samples, sr, t)
                    final_class = classify_hat_or_cymbal(tf)
                candidates.append(DrumCandidate(
                    time=round(t, 6),
                    drum_class=final_class,
                    strength=float(strength),
                    confidence=0.55,
                    source="template_xcorr_fallback",
                ))

        return candidates_to_events(candidates, stem_path=Path(""))


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = TemplateXcorrAlgorithm()
    return algo.transcribe(stem_path)
