"""NMF Spectral Decomposition drum transcription.

Paradigm shift: instead of detect-then-classify, decompose the
spectrogram V ≈ W·H where W = basis spectra, H = activations.
Peak-picking each activation row gives onsets AND drum class identity
simultaneously.

Key insight: NMF naturally separates additive spectral sources. Each
drum has a characteristic spectral shape (basis) and each hit is an
activation event. The factorization learns per-song basis spectra,
adapting to the recording's actual timbres.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_hat_or_cymbal,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent

# FFT parameters
N_FFT = 2048
HOP = 512
N_COMPONENTS = 5  # kick, snare, hat, crash/ride, tom
N_ITERATIONS = 100
EPS = 1e-10

# Seed spectral shapes — freq bin ranges for 44100 Hz, n_fft=2048
# bin = freq * n_fft / sr, so bin_per_hz = 2048/44100 ≈ 0.0464
_BIN_PER_HZ = N_FFT / 44100.0

_SEED_SPECS = {
    "kick": (30.0, 180.0, 1.0),     # strong low energy
    "snare": (180.0, 4000.0, 0.8),   # mid + crack
    "hh_closed": (5000.0, 16000.0, 0.7),  # high freq
    "crash": (3000.0, 12000.0, 0.6),  # broad high
    "tom_low": (60.0, 400.0, 0.5),   # low-mid
}

_COMPONENT_LABELS = list(_SEED_SPECS.keys())


def _build_seed_basis(n_bins: int) -> list[list[float]]:
    """Build initial W matrix (n_bins × n_components) with seeded spectral shapes."""
    W: list[list[float]] = []
    for _ in range(n_bins):
        W.append([0.1] * N_COMPONENTS)

    for comp_idx, (label, (lo_hz, hi_hz, peak_val)) in enumerate(_SEED_SPECS.items()):
        lo_bin = max(0, int(lo_hz * _BIN_PER_HZ))
        hi_bin = min(n_bins - 1, int(hi_hz * _BIN_PER_HZ))
        center = (lo_bin + hi_bin) // 2
        width = max(1, (hi_bin - lo_bin) // 2)
        for b in range(n_bins):
            if lo_bin <= b <= hi_bin:
                # Gaussian-ish shape centered in band
                dist = abs(b - center) / max(1.0, float(width))
                W[b][comp_idx] = peak_val * max(0.05, math.exp(-0.5 * dist * dist))
            else:
                W[b][comp_idx] = 0.01 + 0.02 * (comp_idx / float(N_COMPONENTS))

    return W


def _stft_magnitude(samples: list[float], n_fft: int, hop: int) -> list[list[float]]:
    """Compute magnitude spectrogram (n_bins × n_frames).

    Uses a simple DFT with Hann window. Returns only positive frequencies.
    """
    n = len(samples)
    n_bins = n_fft // 2 + 1
    frames: list[list[float]] = []

    # Precompute Hann window
    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_fft - 1))) for i in range(n_fft)]

    pos = 0
    while pos + n_fft <= n:
        # Windowed segment
        seg = [samples[pos + i] * window[i] for i in range(n_fft)]

        # DFT magnitude for positive frequencies
        mags: list[float] = []
        for k in range(n_bins):
            re = 0.0
            im = 0.0
            w = -2.0 * math.pi * k / n_fft
            for t in range(n_fft):
                angle = w * t
                re += seg[t] * math.cos(angle)
                im += seg[t] * math.sin(angle)
            mags.append(math.sqrt(re * re + im * im))
        frames.append(mags)
        pos += hop

    if not frames:
        return []

    # Transpose to n_bins × n_frames
    n_frames = len(frames)
    result: list[list[float]] = []
    for b in range(n_bins):
        result.append([frames[f][b] for f in range(n_frames)])

    return result


def _nmf_multiplicative_update(
    V: list[list[float]],
    W: list[list[float]],
    H: list[list[float]],
    n_iter: int,
) -> tuple[list[list[float]], list[list[float]]]:
    """Run multiplicative update NMF: V ≈ W·H.

    V: n_bins × n_frames
    W: n_bins × n_components
    H: n_components × n_frames
    """
    n_bins = len(V)
    n_frames = len(V[0]) if V else 0
    n_comp = len(W[0]) if W else 0

    for _ in range(n_iter):
        # Compute W·H
        WH: list[list[float]] = []
        for b in range(n_bins):
            row: list[float] = []
            for f in range(n_frames):
                val = EPS
                for c in range(n_comp):
                    val += W[b][c] * H[c][f]
                row.append(val)
            WH.append(row)

        # Update H: H *= (W^T · V) / (W^T · WH)
        for c in range(n_comp):
            for f in range(n_frames):
                num = EPS
                den = EPS
                for b in range(n_bins):
                    num += W[b][c] * V[b][f]
                    den += W[b][c] * WH[b][f]
                H[c][f] *= num / den

        # Recompute W·H after H update
        for b in range(n_bins):
            for f in range(n_frames):
                val = EPS
                for c in range(n_comp):
                    val += W[b][c] * H[c][f]
                WH[b][f] = val

        # Update W: W *= (V · H^T) / (WH · H^T)
        for b in range(n_bins):
            for c in range(n_comp):
                num = EPS
                den = EPS
                for f in range(n_frames):
                    num += V[b][f] * H[c][f]
                    den += WH[b][f] * H[c][f]
                W[b][c] *= num / den

    return W, H


def _label_components(
    W: list[list[float]],
    sr: int,
) -> list[str]:
    """Label NMF components by spectral centroid and energy distribution."""
    n_bins = len(W)
    n_comp = len(W[0]) if W else 0
    bin_to_hz = float(sr) / float(N_FFT)

    centroids: list[float] = []
    low_energies: list[float] = []
    high_energies: list[float] = []

    for c in range(n_comp):
        total = EPS
        weighted = 0.0
        low_e = 0.0
        high_e = 0.0
        for b in range(n_bins):
            freq = b * bin_to_hz
            total += W[b][c]
            weighted += freq * W[b][c]
            if freq < 300:
                low_e += W[b][c]
            if freq > 4000:
                high_e += W[b][c]
        centroids.append(weighted / total)
        low_energies.append(low_e / total)
        high_energies.append(high_e / total)

    # Sort components by centroid
    indexed = list(enumerate(centroids))
    indexed.sort(key=lambda x: x[1])

    labels = [""] * n_comp

    # Assign roles based on sorted centroid order
    if n_comp >= 3:
        # Lowest centroid = kick
        labels[indexed[0][0]] = "kick"
        # Highest centroid = hi-hat
        labels[indexed[-1][0]] = "hh_closed"
        # Second highest = crash/ride if centroid > 3000, else hat
        if n_comp >= 4 and indexed[-2][1] > 3000:
            labels[indexed[-2][0]] = "crash"
        # Middle = snare
        mid_idx = len(indexed) // 2
        for i, (comp_idx, cent) in enumerate(indexed):
            if not labels[comp_idx]:
                if 200 < cent < 5000:
                    labels[comp_idx] = "snare"
                elif cent < 400:
                    labels[comp_idx] = "tom_low"
                else:
                    labels[comp_idx] = "crash"

    # Fill any unlabeled
    for c in range(n_comp):
        if not labels[c]:
            labels[c] = "tom_low"

    return labels


class NmfDecompositionAlgorithm(TranscriptionAlgorithm):
    name = "nmf_decomposition"

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

        # Step 1: Compute magnitude spectrogram
        V = _stft_magnitude(samples, N_FFT, HOP)
        if not V or not V[0]:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        n_bins = len(V)
        n_frames = len(V[0])

        # Step 2: Initialize W with seeded basis, H randomly
        W = _build_seed_basis(n_bins)
        H: list[list[float]] = []
        for c in range(N_COMPONENTS):
            row: list[float] = []
            for f in range(n_frames):
                # Initialize with small positive values
                row.append(0.1 + 0.01 * ((c * n_frames + f) % 17))
            H.append(row)

        # Step 3: Run NMF
        W, H = _nmf_multiplicative_update(V, W, H, N_ITERATIONS)

        # Step 4: Label components
        labels = _label_components(W, sr)

        # Step 5: Peak-pick each activation row
        candidates: list[DrumCandidate] = []
        for c in range(N_COMPONENTS):
            drum_class = labels[c]
            activation = normalize_series(H[c])

            # Instrument-specific peak picking parameters
            if drum_class == "kick":
                peaks = adaptive_peak_pick(
                    activation, hop_sec=hop_sec, k=2.20,
                    min_gap_sec=0.085, window_sec=0.30, percentile=0.80,
                )
            elif drum_class == "snare":
                peaks = adaptive_peak_pick(
                    activation, hop_sec=hop_sec, k=2.40,
                    min_gap_sec=0.080, window_sec=0.30, percentile=0.82,
                )
            elif drum_class in ("hh_closed", "hh_open"):
                peaks = adaptive_peak_pick(
                    activation, hop_sec=hop_sec, k=1.80,
                    min_gap_sec=0.040, window_sec=0.24, percentile=0.72,
                )
            else:
                peaks = adaptive_peak_pick(
                    activation, hop_sec=hop_sec, k=2.50,
                    min_gap_sec=0.070, window_sec=0.30, percentile=0.84,
                )

            for idx, strength in peaks:
                t = frame_to_time(idx, HOP, sr)
                final_class = drum_class

                # Refine hi-hat/cymbal classification using timbral features
                if drum_class in ("hh_closed", "crash"):
                    tf = timbral_features(samples, sr, t)
                    final_class = classify_hat_or_cymbal(tf)

                candidates.append(DrumCandidate(
                    time=round(t, 6),
                    drum_class=final_class,
                    strength=float(strength),
                    confidence=0.7,
                    source="nmf_decomposition",
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
    algo = NmfDecompositionAlgorithm()
    return algo.transcribe(stem_path)
