"""HPSS Percussive Isolation drum transcription.

From computational auditory scene analysis: uses Harmonic-Percussive
Source Separation (HPSS) as preprocessing to extract a percussive-only
signal, then runs multi-resolution onset detection on the cleaned signal.

HPSS works by median-filtering the spectrogram:
- Horizontal median → harmonic component (sustained tones)
- Vertical median → percussive component (transients)
- Soft masking separates them

This removes pitched instrument bleed that causes false positives in
drum detection, especially when processing full-mix audio rather than
isolated drum stems.
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
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent

N_FFT = 2048
HOP = 512
FRAME = 1024
MEDIAN_KERNEL = 17  # frames for median filter
EPS = 1e-10

# Band definitions for multi-resolution detection on percussive signal
BANDS = {
    "kick_low": (35.0, 140.0),
    "kick_high": (140.0, 250.0),
    "snare_body": (200.0, 2200.0),
    "snare_crack": (1800.0, 4500.0),
    "hat_main": (5000.0, 12000.0),
    "hat_air": (7000.0, 16000.0),
}


def _stft_complex(
    samples: list[float],
    n_fft: int,
    hop: int,
) -> tuple[list[list[float]], list[list[float]]]:
    """Compute STFT, returning (real_parts, imag_parts) each n_bins × n_frames."""
    n = len(samples)
    n_bins = n_fft // 2 + 1

    # Hann window
    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_fft - 1))) for i in range(n_fft)]

    real_frames: list[list[float]] = []
    imag_frames: list[list[float]] = []

    pos = 0
    while pos + n_fft <= n:
        seg = [samples[pos + i] * window[i] for i in range(n_fft)]

        re_row: list[float] = []
        im_row: list[float] = []
        for k in range(n_bins):
            re = 0.0
            im = 0.0
            w = -2.0 * math.pi * k / n_fft
            for t in range(n_fft):
                angle = w * t
                re += seg[t] * math.cos(angle)
                im += seg[t] * math.sin(angle)
            re_row.append(re)
            im_row.append(im)

        real_frames.append(re_row)
        imag_frames.append(im_row)
        pos += hop

    if not real_frames:
        return [], []

    # Transpose to n_bins × n_frames
    n_frames = len(real_frames)
    real_t: list[list[float]] = []
    imag_t: list[list[float]] = []
    for b in range(n_bins):
        real_t.append([real_frames[f][b] for f in range(n_frames)])
        imag_t.append([imag_frames[f][b] for f in range(n_frames)])

    return real_t, imag_t


def _istft(
    real: list[list[float]],
    imag: list[list[float]],
    n_fft: int,
    hop: int,
    length: int,
) -> list[float]:
    """Inverse STFT via overlap-add."""
    if not real or not real[0]:
        return [0.0] * length

    n_bins = len(real)
    n_frames = len(real[0])

    # Hann window for synthesis
    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_fft - 1))) for i in range(n_fft)]

    out = [0.0] * length
    win_sum = [0.0] * length

    for f in range(n_frames):
        # iDFT for this frame
        frame = [0.0] * n_fft
        for t in range(n_fft):
            val = 0.0
            for k in range(n_bins):
                w = 2.0 * math.pi * k * t / n_fft
                val += real[k][f] * math.cos(w) - imag[k][f] * math.sin(w)
                # Mirror (conjugate symmetry)
                if k > 0 and k < n_bins - 1:
                    val += real[k][f] * math.cos(w) - imag[k][f] * math.sin(w)
            frame[t] = val / n_fft

        # Overlap-add with window
        pos = f * hop
        for t in range(n_fft):
            if pos + t < length:
                out[pos + t] += frame[t] * window[t]
                win_sum[pos + t] += window[t] * window[t]

    # Normalize by window overlap
    for i in range(length):
        if win_sum[i] > 1e-8:
            out[i] /= win_sum[i]

    return out


def _median_filter_1d(data: list[float], kernel: int) -> list[float]:
    """1D median filter."""
    n = len(data)
    if n == 0 or kernel <= 1:
        return data[:]
    half = kernel // 2
    out = [0.0] * n
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        window = sorted(data[start:end])
        out[i] = window[len(window) // 2]
    return out


def _hpss(
    magnitude: list[list[float]],
    real: list[list[float]],
    imag: list[list[float]],
    kernel: int = MEDIAN_KERNEL,
) -> tuple[list[list[float]], list[list[float]]]:
    """Harmonic-Percussive Source Separation via median filtering.

    Returns (percussive_real, percussive_imag) masked STFT.
    """
    n_bins = len(magnitude)
    n_frames = len(magnitude[0]) if magnitude else 0

    if n_bins == 0 or n_frames == 0:
        return real, imag

    # Horizontal median → harmonic estimate (along time axis)
    harmonic_mag: list[list[float]] = []
    for b in range(n_bins):
        harmonic_mag.append(_median_filter_1d(magnitude[b], kernel))

    # Vertical median → percussive estimate (along frequency axis)
    percussive_mag: list[list[float]] = [[] for _ in range(n_bins)]
    for f in range(n_frames):
        col = [magnitude[b][f] for b in range(n_bins)]
        filtered = _median_filter_1d(col, kernel)
        for b in range(n_bins):
            percussive_mag[b].append(filtered[b])

    # Soft masking: P_mask = P² / (H² + P² + ε)
    perc_real: list[list[float]] = [[] for _ in range(n_bins)]
    perc_imag: list[list[float]] = [[] for _ in range(n_bins)]

    for b in range(n_bins):
        for f in range(n_frames):
            h2 = harmonic_mag[b][f] ** 2
            p2 = percussive_mag[b][f] ** 2
            mask = p2 / (h2 + p2 + EPS)
            perc_real[b].append(real[b][f] * mask)
            perc_imag[b].append(imag[b][f] * mask)

    return perc_real, perc_imag


def _compute_magnitude(real: list[list[float]], imag: list[list[float]]) -> list[list[float]]:
    """Compute magnitude from real and imaginary STFT parts."""
    n_bins = len(real)
    n_frames = len(real[0]) if real else 0
    mag: list[list[float]] = []
    for b in range(n_bins):
        row: list[float] = []
        for f in range(n_frames):
            row.append(math.sqrt(real[b][f] ** 2 + imag[b][f] ** 2))
        mag.append(row)
    return mag


class HpssPercussiveAlgorithm(TranscriptionAlgorithm):
    name = "hpss_percussive"

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

        # Step 1: STFT
        real, imag = _stft_complex(samples, N_FFT, HOP)
        if not real or not real[0]:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Step 2: Compute magnitude
        magnitude = _compute_magnitude(real, imag)

        # Step 3: HPSS — extract percussive component
        perc_real, perc_imag = _hpss(magnitude, real, imag)

        # Step 4: iSTFT → percussive time-domain signal
        perc_samples = _istft(perc_real, perc_imag, N_FFT, HOP, len(samples))

        # Normalize percussive signal
        peak = max((abs(x) for x in perc_samples), default=1.0)
        if peak > 1e-8:
            perc_samples = [x / peak for x in perc_samples]

        # Step 5: Multi-band onset detection on percussive signal
        from aural_ingest.algorithms._common import compute_band_envelopes

        env = compute_band_envelopes(
            perc_samples, sr, BANDS, hop_size=HOP, frame_size=FRAME,
        )
        if not env:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Build instrument-specific envelopes
        kick_env = normalize_series([
            0.7 * env.get("kick_low", [0.0])[i] + 0.3 * env.get("kick_high", [0.0])[i]
            for i in range(min(
                len(env.get("kick_low", [])),
                len(env.get("kick_high", [])),
            ))
        ])
        snare_env = normalize_series([
            0.4 * env.get("snare_body", [0.0])[i] + 1.0 * env.get("snare_crack", [0.0])[i]
            for i in range(min(
                len(env.get("snare_body", [])),
                len(env.get("snare_crack", [])),
            ))
        ])
        hat_env = normalize_series([
            0.8 * env.get("hat_main", [0.0])[i] + 0.2 * env.get("hat_air", [0.0])[i]
            for i in range(min(
                len(env.get("hat_main", [])),
                len(env.get("hat_air", [])),
            ))
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

        # Step 6: Peak-pick per instrument
        candidates: list[DrumCandidate] = []

        kick_peaks = adaptive_peak_pick(
            kick_novelty, hop_sec=hop_sec, k=2.00, min_gap_sec=0.085,
            window_sec=0.30, percentile=0.78, density_boost=0.06,
        )
        for idx, strength in kick_peaks:
            t = frame_to_time(idx, HOP, sr)
            candidates.append(DrumCandidate(
                time=round(t, 6), drum_class="kick",
                strength=float(strength), confidence=0.75,
                source="hpss_percussive",
            ))

        snare_peaks = adaptive_peak_pick(
            snare_novelty, hop_sec=hop_sec, k=2.30, min_gap_sec=0.080,
            window_sec=0.30, percentile=0.80, density_boost=0.05,
        )
        for idx, strength in snare_peaks:
            t = frame_to_time(idx, HOP, sr)
            candidates.append(DrumCandidate(
                time=round(t, 6), drum_class="snare",
                strength=float(strength), confidence=0.75,
                source="hpss_percussive",
            ))

        hat_peaks = adaptive_peak_pick(
            hat_novelty, hop_sec=hop_sec, k=1.75, min_gap_sec=0.038,
            window_sec=0.24, percentile=0.70, density_boost=0.05,
        )
        for idx, strength in hat_peaks:
            t = frame_to_time(idx, HOP, sr)
            # Use original (non-HPSS) samples for timbral refinement
            tf = timbral_features(samples, sr, t)
            hat_class = classify_hat_or_cymbal(tf)
            candidates.append(DrumCandidate(
                time=round(t, 6), drum_class=hat_class,
                strength=float(strength), confidence=0.70,
                source="hpss_percussive",
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
    algo = HpssPercussiveAlgorithm()
    return algo.transcribe(stem_path)
