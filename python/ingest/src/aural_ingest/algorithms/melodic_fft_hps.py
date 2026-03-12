"""FFT + Harmonic Product Spectrum pitch estimator.

Uses the magnitude spectrum with downsampled harmonic products to find
the fundamental frequency.  Pure-Python with no new dependencies.
"""
from __future__ import annotations

import cmath
import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


# ---------------------------------------------------------------------------
# Minimal FFT (Cooley-Tukey radix-2)
# ---------------------------------------------------------------------------

def _fft(x: list[complex]) -> list[complex]:
    """Radix-2 decimation-in-time FFT."""
    n = len(x)
    if n <= 1:
        return x
    if n & (n - 1):
        # Pad to next power of 2
        m = 1
        while m < n:
            m <<= 1
        x = x + [0j] * (m - n)
        n = m
    if n <= 1:
        return x
    even = _fft(x[0::2])
    odd = _fft(x[1::2])
    half = n // 2
    result = [0j] * n
    for k in range(half):
        w = cmath.exp(-2j * cmath.pi * k / n) * odd[k]
        result[k] = even[k] + w
        result[k + half] = even[k] - w
    return result


def _hps_pitch(
    samples: list[float],
    sr: int,
    frame_start: int,
    frame_len: int,
    *,
    min_freq: float = 55.0,
    max_freq: float = 1600.0,
    harmonics: int = 4,
    hps_threshold: float = 0.01,
) -> float | None:
    """Harmonic Product Spectrum pitch estimation for one frame."""
    end = frame_start + frame_len
    if end > len(samples):
        return None

    # Apply Hann window
    windowed = []
    for i in range(frame_len):
        w = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (frame_len - 1)))
        windowed.append(complex(samples[frame_start + i] * w))

    # FFT
    spectrum = _fft(windowed)
    n = len(spectrum)
    half = n // 2

    # Magnitude spectrum (positive frequencies only)
    mag = [abs(spectrum[k]) for k in range(half)]

    # HPS: multiply downsampled copies
    hps = list(mag)
    for h in range(2, harmonics + 1):
        hps_len = half // h
        for k in range(hps_len):
            hps[k] *= mag[k * h]
        # Zero out beyond valid range
        for k in range(hps_len, half):
            hps[k] = 0.0

    # Find peak in valid frequency range
    min_bin = max(1, int(min_freq * n / sr))
    max_bin = min(half - 1, int(max_freq * n / sr) + 1)

    if min_bin >= max_bin:
        return None

    best_bin = min_bin
    best_val = hps[min_bin]
    for k in range(min_bin + 1, max_bin + 1):
        if k < half and hps[k] > best_val:
            best_val = hps[k]
            best_bin = k

    if best_val < hps_threshold:
        return None

    # Parabolic interpolation for sub-bin accuracy
    if 1 <= best_bin < half - 1:
        alpha = hps[best_bin - 1]
        beta = hps[best_bin]
        gamma = hps[best_bin + 1]
        denom = alpha - 2 * beta + gamma
        if abs(denom) > 1e-12:
            shift = 0.5 * (alpha - gamma) / denom
        else:
            shift = 0.0
        refined_bin = best_bin + shift
    else:
        refined_bin = float(best_bin)

    freq = refined_bin * sr / n
    if freq < min_freq or freq > max_freq:
        return None
    return freq


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.06,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.07,
    harmonics: int = 4,
) -> list[MelodicNote]:
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    frame = max(128, int(sr * max(0.008, frame_sec)))
    hop = max(32, int(sr * max(0.002, hop_sec)))
    if len(samples) < frame:
        return []

    # --- Frame-wise analysis ---
    frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples):
        seg = samples[i : i + frame]
        rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        freq = _hps_pitch(
            samples, sr, i, frame,
            min_freq=freq_lo, max_freq=freq_hi,
            harmonics=harmonics,
        )
        midi = _midi_from_freq(freq) if freq is not None else None
        t = i / float(sr)
        frames.append((t, midi, rms))
        i += hop

    if not frames:
        return []

    # --- Voicing floor ---
    rms_vals = [f[2] for f in frames]
    rms_sorted = sorted(v for v in rms_vals if v > 0)
    floor = max(0.004, rms_sorted[len(rms_sorted) // 6]) if rms_sorted else 0.004

    # --- Note segmentation ---
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_rms = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_rms, cur_count
        if cur_pitch is not None and cur_count > 0:
            dur = max(0.0, t_end - cur_start)
            if dur >= min_note_sec:
                mean_rms = cur_rms / float(cur_count)
                vel = int(42 + mean_rms * 85.0)
                vel = max(24, min(127, vel))
                out.append(
                    MelodicNote(
                        t_on=round(cur_start, 6),
                        t_off=round(t_end, 6),
                        pitch=int(cur_pitch),
                        velocity=vel,
                        instrument=instrument,
                    )
                )
        cur_pitch = None
        cur_start = t_end
        cur_rms = 0.0
        cur_count = 0

    for t, midi, rms in frames:
        voiced = midi is not None and rms >= floor
        if not voiced:
            flush(t)
            continue

        assert midi is not None
        if cur_pitch is None:
            cur_pitch = midi
            cur_start = t
            cur_rms = rms
            cur_count = 1
            continue

        if abs(midi - cur_pitch) <= 1:
            cur_rms += rms
            cur_count += 1
            continue

        flush(t)
        cur_pitch = midi
        cur_start = t
        cur_rms = rms
        cur_count = 1

    if frames:
        flush(frames[-1][0] + (hop / float(sr)))

    return out
