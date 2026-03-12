"""YIN-based monophonic pitch estimator for melodic transcription.

Uses the Cumulative Mean Normalized Difference Function (CMNDF) from the
YIN algorithm (de Cheveigné & Kawahara, 2002).  Numpy-accelerated for
practical speed on full-length stems, with pure-Python fallback.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# YIN core (numpy-accelerated)
# ---------------------------------------------------------------------------

def _yin_pitch_np(
    frame_arr,   # numpy array of the frame
    sr: int,
    *,
    threshold: float = 0.15,
    min_freq: float = 55.0,
    max_freq: float = 1600.0,
) -> float | None:
    """Numpy-accelerated YIN pitch estimation for a single frame."""
    frame_len = len(frame_arr)
    min_lag = max(2, int(sr / max_freq))
    max_lag = min(frame_len // 2, int(sr / min_freq) + 1)
    if max_lag <= min_lag + 2:
        return None

    # 1. Squared-difference function via autocorrelation trick
    # d(τ) = Σ (x[j] - x[j+τ])² = r(0,0) + r(τ,τ) - 2·r(0,τ)
    # where r(a,b) = Σ x[a+j]·x[b+j]
    # But simpler: compute differences directly using vectorized ops
    d = np.zeros(max_lag + 1)
    for tau in range(1, max_lag + 1):
        diff = frame_arr[:frame_len - tau] - frame_arr[tau:frame_len]
        d[tau] = float(np.sum(diff * diff))

    # 2. CMNDF
    cmndf = np.ones(max_lag + 1)
    running = 0.0
    for tau in range(1, max_lag + 1):
        running += d[tau]
        cmndf[tau] = d[tau] / (running / tau) if running > 0 else 1.0

    # 3. Pick first τ below threshold
    best_tau = None
    for tau in range(min_lag, max_lag):
        if cmndf[tau] < threshold:
            while tau + 1 < max_lag and cmndf[tau + 1] < cmndf[tau]:
                tau += 1
            best_tau = tau
            break

    if best_tau is None:
        min_idx = int(np.argmin(cmndf[min_lag:max_lag + 1])) + min_lag
        if cmndf[min_idx] <= 0.5:
            best_tau = min_idx
        else:
            return None

    # 4. Parabolic interpolation
    tau = best_tau
    if 1 <= tau < max_lag:
        s0, s1, s2 = float(cmndf[tau - 1]), float(cmndf[tau]), float(cmndf[tau + 1])
        denom = 2.0 * s1 - s0 - s2
        shift = (s0 - s2) / (2.0 * denom) if abs(denom) > 1e-12 else 0.0
        tau_refined = tau + shift
    else:
        tau_refined = float(tau)

    if tau_refined <= 0:
        return None
    freq = sr / tau_refined
    return freq if min_freq <= freq <= max_freq else None


def _yin_pitch(
    samples: list[float],
    sr: int,
    *,
    frame_start: int,
    frame_len: int,
    threshold: float = 0.15,
    min_freq: float = 55.0,
    max_freq: float = 1600.0,
) -> float | None:
    """Pure-Python fallback YIN (used by other modules too)."""
    min_lag = max(2, int(sr / max_freq))
    max_lag = min(frame_len // 2, int(sr / min_freq) + 1)
    if max_lag <= min_lag + 2:
        return None
    end = frame_start + frame_len
    if end > len(samples):
        return None

    d = [0.0] * (max_lag + 1)
    for tau in range(1, max_lag + 1):
        acc = 0.0
        for j in range(frame_len - tau):
            diff = samples[frame_start + j] - samples[frame_start + j + tau]
            acc += diff * diff
        d[tau] = acc

    cmndf = [0.0] * (max_lag + 1)
    cmndf[0] = 1.0
    running = 0.0
    for tau in range(1, max_lag + 1):
        running += d[tau]
        cmndf[tau] = d[tau] / (running / tau) if running > 0 else 1.0

    best_tau = None
    for tau in range(min_lag, max_lag):
        if cmndf[tau] < threshold:
            while tau + 1 < max_lag and cmndf[tau + 1] < cmndf[tau]:
                tau += 1
            best_tau = tau
            break

    if best_tau is None:
        min_val = 2.0
        for tau in range(min_lag, max_lag + 1):
            if cmndf[tau] < min_val:
                min_val = cmndf[tau]
                best_tau = tau
        if best_tau is None or min_val > 0.5:
            return None

    tau = best_tau
    if 1 <= tau < max_lag:
        s0, s1, s2 = cmndf[tau - 1], cmndf[tau], cmndf[tau + 1]
        denom = 2.0 * s1 - s0 - s2
        shift = (s0 - s2) / (2.0 * denom) if abs(denom) > 1e-12 else 0.0
        tau_refined = tau + shift
    else:
        tau_refined = float(tau)

    if tau_refined <= 0:
        return None
    freq = sr / tau_refined
    return freq if min_freq <= freq <= max_freq else None


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.05,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.07,
    yin_threshold: float = 0.15,
) -> list[MelodicNote]:
    """Transcribe a WAV stem using the YIN pitch estimator."""
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    frame = max(96, int(sr * max(0.004, frame_sec)))
    hop = max(32, int(sr * max(0.002, hop_sec)))
    if len(samples) < frame:
        return []

    # Convert to numpy if available for ~100× speedup
    use_np = _HAS_NUMPY
    if use_np:
        samples_np = np.array(samples, dtype=np.float64)

    # --- Frame-wise analysis ---
    frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples):
        if use_np:
            seg = samples_np[i : i + frame]
            rms = float(np.sqrt(np.mean(seg * seg)))
            freq = _yin_pitch_np(
                seg, sr,
                threshold=yin_threshold,
                min_freq=freq_lo, max_freq=freq_hi,
            )
        else:
            seg = samples[i : i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))
            freq = _yin_pitch(
                samples, sr,
                frame_start=i, frame_len=frame,
                threshold=yin_threshold,
                min_freq=freq_lo, max_freq=freq_hi,
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
    if rms_sorted:
        floor = max(0.004, rms_sorted[len(rms_sorted) // 6])
    else:
        floor = 0.004

    # --- Note segmentation ---
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_rms = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_rms, cur_count
        if cur_pitch is None or cur_count <= 0:
            cur_pitch = None
            cur_start = t_end
            cur_rms = 0.0
            cur_count = 0
            return
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
