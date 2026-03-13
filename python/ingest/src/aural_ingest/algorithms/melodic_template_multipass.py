"""Multi-pass template melodic transcription.

Inspired by the drum spectral_template_multipass approach.  The key insight
is that every song/stem has a unique harmonic profile for its instrument,
so learning that profile first allows better pitch estimation.

Pass 1 — Spectral Profiling:
  1. Broad pitch detection (HPS + YIN, lower voicing threshold)
  2. Collect detected pitches and their harmonic energy structure
  3. Learn the actual pitch range used in this stem
  4. Build a harmonic weight template (fundamental-to-overtone ratios)

Pass 2 — Refined Pitch Estimation:
  1. Re-estimate pitch using learned harmonic weights for HPS
  2. Apply song-specific octave correction (learned range, not static)
  3. Adaptive voicing threshold from Pass 1 statistics
  4. Onset detection + note segmentation
"""
from __future__ import annotations

import cmath
import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_onset_yin import _detect_onsets
from aural_ingest.algorithms.melodic_fft_hps import _fft, _midi_from_freq
from aural_ingest.algorithms.melodic_yin import _yin_pitch_np, _HAS_NUMPY
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
except ImportError:
    np = None


# --------------------------------------------------------------------------
# Weighted HPS: uses learned harmonic weights instead of uniform multiplication
# --------------------------------------------------------------------------
def _weighted_hps_pitch(
    samples: list[float],
    sr: int,
    frame_start: int,
    frame_len: int,
    *,
    min_freq: float = 55.0,
    max_freq: float = 1600.0,
    harmonics: int = 5,
    harmonic_weights: list[float] | None = None,
    hps_threshold: float = 0.008,
) -> float | None:
    """HPS pitch estimation with per-harmonic weighting.

    If harmonic_weights is provided, each downsampled harmonic product is
    raised to the corresponding weight (0 = ignore, 1 = standard, >1 = boost).
    This lets us emphasize harmonics that are strong in this instrument.
    """
    end = frame_start + frame_len
    if end > len(samples):
        return None

    # Apply Hann window
    windowed = []
    for i in range(frame_len):
        w = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (frame_len - 1)))
        windowed.append(complex(samples[frame_start + i] * w))

    spectrum = _fft(windowed)
    n = len(spectrum)
    half = n // 2

    mag = [abs(spectrum[k]) for k in range(half)]

    # Normalize magnitude to avoid numerical issues
    peak_mag = max(mag) if mag else 1.0
    if peak_mag > 0:
        mag = [m / peak_mag for m in mag]

    # Weighted HPS
    hps = list(mag)
    weights = harmonic_weights or [1.0] * harmonics

    for h_idx in range(2, harmonics + 1):
        hps_len = half // h_idx
        w = weights[h_idx - 1] if h_idx - 1 < len(weights) else 1.0
        if w <= 0.01:
            continue  # Skip this harmonic entirely
        for k in range(hps_len):
            # Use weighted multiplication: mag[k*h]^w instead of mag[k*h]
            harmonic_val = mag[k * h_idx]
            if w == 1.0:
                hps[k] *= harmonic_val
            else:
                hps[k] *= max(1e-15, harmonic_val) ** w
        for k in range(hps_len, half):
            hps[k] = 0.0

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

    # Parabolic interpolation
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


# --------------------------------------------------------------------------
# Extract harmonic energy profile for a detected note
# --------------------------------------------------------------------------
def _extract_harmonic_profile(
    samples: list[float],
    sr: int,
    frame_start: int,
    frame_len: int,
    fundamental_freq: float,
    num_harmonics: int = 6,
) -> list[float]:
    """Extract relative energy at each harmonic of the fundamental.

    Returns a list of num_harmonics float values representing the energy
    at [f0, 2*f0, 3*f0, ..., num_harmonics*f0] relative to the total.
    """
    end = frame_start + frame_len
    if end > len(samples) or fundamental_freq <= 0:
        return [1.0] + [0.0] * (num_harmonics - 1)

    # Apply Hann window and FFT
    windowed = []
    for i in range(frame_len):
        w = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (frame_len - 1)))
        windowed.append(complex(samples[frame_start + i] * w))

    spectrum = _fft(windowed)
    n = len(spectrum)
    half = n // 2
    mag = [abs(spectrum[k]) for k in range(half)]

    # Measure energy at each harmonic (+/- 1 bin)
    profile = []
    for h in range(1, num_harmonics + 1):
        harmonic_freq = fundamental_freq * h
        bin_idx = int(round(harmonic_freq * n / sr))
        if bin_idx >= half - 1:
            profile.append(0.0)
            continue

        # Take max of bin and neighbors for robustness
        lo = max(0, bin_idx - 1)
        hi = min(half - 1, bin_idx + 1)
        energy = max(mag[lo:hi + 1])
        profile.append(energy)

    # Normalize: make fundamental = 1.0
    f0_energy = profile[0] if profile else 1.0
    if f0_energy > 1e-9:
        profile = [p / f0_energy for p in profile]
    else:
        profile = [1.0] + [0.0] * (num_harmonics - 1)

    return profile


# --------------------------------------------------------------------------
# Main multi-pass transcription
# --------------------------------------------------------------------------
def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.06,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.06,
    onset_ratio: float = 3.0,
    num_harmonics: int = 5,
) -> list[MelodicNote]:
    """Multi-pass template melodic transcription."""
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

    use_np = _HAS_NUMPY and np is not None
    if use_np:
        samples_np = np.array(samples, dtype=np.float64)

    # ==================================================================
    # PASS 1: Broad pitch detection + harmonic profiling
    # ==================================================================

    # Use lower voicing threshold to catch more notes for profiling
    pass1_frames: list[tuple[float, float | None, float, int]] = []  # (time, freq, rms, sample_idx)
    i = 0
    while i + frame <= len(samples):
        if use_np:
            seg = samples_np[i: i + frame]
            rms = float(np.sqrt(np.mean(seg * seg)))
        else:
            seg = samples[i: i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        # HPS with standard uniform weights
        freq = _weighted_hps_pitch(
            samples, sr, i, frame,
            min_freq=freq_lo, max_freq=freq_hi,
            harmonics=num_harmonics,
            harmonic_weights=None,  # uniform weights for Pass 1
        )

        # YIN fallback
        if freq is None and use_np:
            freq = _yin_pitch_np(
                samples_np[i: i + frame], sr,
                threshold=0.12,  # Slightly looser for profiling
                min_freq=freq_lo, max_freq=freq_hi,
            )

        pass1_frames.append((i / float(sr), freq, rms, i))
        i += hop

    if not pass1_frames:
        return []

    # Compute voicing floor for Pass 1 (lower threshold to capture more)
    rms_vals = [f[2] for f in pass1_frames]
    rms_sorted = sorted(v for v in rms_vals if v > 0)
    if rms_sorted:
        pass1_floor = max(0.003, rms_sorted[len(rms_sorted) // 8])  # Lower than normal
    else:
        pass1_floor = 0.003

    # Collect voiced frames for profiling
    voiced_freqs: list[float] = []
    voiced_midis: list[int] = []
    harmonic_profiles: list[list[float]] = []

    for t, freq, rms, sample_idx in pass1_frames:
        if freq is None or rms < pass1_floor:
            continue

        midi = _midi_from_freq(freq)
        voiced_freqs.append(freq)
        voiced_midis.append(midi)

        # Extract harmonic structure
        profile = _extract_harmonic_profile(
            samples, sr, sample_idx, frame, freq, num_harmonics,
        )
        harmonic_profiles.append(profile)

    if len(voiced_freqs) < 5:
        # Not enough data for profiling — fall back to standard combined
        from aural_ingest.algorithms.melodic_combined import transcribe as _combined
        return _combined(stem_path, instrument=instrument)

    # ==================================================================
    # Build learned templates from Pass 1
    # ==================================================================

    # 1. Learn actual pitch range
    midi_sorted = sorted(voiced_midis)
    # Use 5th–95th percentile to exclude outliers
    idx_lo = max(0, len(midi_sorted) // 20)
    idx_hi = min(len(midi_sorted) - 1, len(midi_sorted) * 19 // 20)
    learned_midi_lo = midi_sorted[idx_lo]
    learned_midi_hi = midi_sorted[idx_hi]
    learned_midi_center = (learned_midi_lo + learned_midi_hi) // 2

    # Expand range slightly for tolerance
    learned_freq_lo = 440.0 * (2.0 ** ((learned_midi_lo - 3 - 69) / 12.0))
    learned_freq_hi = 440.0 * (2.0 ** ((learned_midi_hi + 3 - 69) / 12.0))
    # Clamp to instrument range
    learned_freq_lo = max(freq_lo * 0.8, learned_freq_lo)
    learned_freq_hi = min(freq_hi * 1.2, learned_freq_hi)

    # 2. Learn harmonic weight template
    mean_profile = [0.0] * num_harmonics
    for profile in harmonic_profiles:
        for h in range(min(num_harmonics, len(profile))):
            mean_profile[h] += profile[h]
    n_profiles = float(len(harmonic_profiles))
    mean_profile = [p / n_profiles for p in mean_profile]

    # Convert to HPS weights: emphasize harmonics that are strong
    # Weight = 0.3 + 0.7 * (relative_strength / max_strength)
    # This boosts harmonics that are actually present in this instrument
    max_h = max(mean_profile[1:]) if len(mean_profile) > 1 else 1.0
    if max_h < 0.01:
        max_h = 1.0

    harmonic_weights = [1.0]  # Fundamental always weight 1.0
    for h in range(1, num_harmonics):
        relative = mean_profile[h] / max_h if h < len(mean_profile) else 0.0
        weight = 0.3 + 0.7 * min(1.0, relative)
        harmonic_weights.append(weight)

    # 3. Learn voicing statistics
    if rms_sorted:
        pass2_floor_on = max(0.004, rms_sorted[len(rms_sorted) // 6])
        pass2_floor_sustain = pass2_floor_on * 0.55
    else:
        pass2_floor_on = 0.004
        pass2_floor_sustain = 0.002

    # ==================================================================
    # PASS 2: Refined pitch estimation with learned templates
    # ==================================================================
    pass2_frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples):
        if use_np:
            seg = samples_np[i: i + frame]
            rms = float(np.sqrt(np.mean(seg * seg)))
        else:
            seg = samples[i: i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        # Weighted HPS with learned harmonic template
        freq = _weighted_hps_pitch(
            samples, sr, i, frame,
            min_freq=learned_freq_lo,
            max_freq=learned_freq_hi,
            harmonics=num_harmonics,
            harmonic_weights=harmonic_weights,
        )

        # YIN fallback with learned range
        if freq is None and use_np:
            freq = _yin_pitch_np(
                samples_np[i: i + frame], sr,
                threshold=0.15,
                min_freq=learned_freq_lo,
                max_freq=learned_freq_hi,
            )

        midi = None
        if freq is not None:
            midi = _midi_from_freq(freq)

            # Song-specific octave correction using learned range
            midi = _adaptive_octave_correct(
                midi, learned_midi_lo, learned_midi_hi, learned_midi_center,
            )

        t = i / float(sr)
        pass2_frames.append((t, midi, rms))
        i += hop

    if not pass2_frames:
        return []

    # --- Onset detection ---
    rms_vals2 = [f[2] for f in pass2_frames]
    onset_frames = _detect_onsets(rms_vals2, ratio_threshold=onset_ratio)

    # --- Note segmentation with onset awareness + hysteresis ---
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_rms = 0.0
    cur_count = 0
    is_sustaining = False

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_rms, cur_count, is_sustaining
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
        is_sustaining = False

    for frame_idx, (t, midi, rms) in enumerate(pass2_frames):
        threshold = pass2_floor_sustain if is_sustaining else pass2_floor_on
        voiced = midi is not None and rms >= threshold

        if not voiced:
            flush(t)
            continue

        assert midi is not None

        is_onset = frame_idx in onset_frames
        if is_onset and cur_pitch is not None:
            flush(t)

        if cur_pitch is None:
            cur_pitch = midi
            cur_start = t
            cur_rms = rms
            cur_count = 1
            is_sustaining = True
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
        is_sustaining = True

    if pass2_frames:
        flush(pass2_frames[-1][0] + (hop / float(sr)))

    # --- Median filter for pitch outliers ---
    out = _median_filter_pitches(out, window=5)

    return out


def _adaptive_octave_correct(
    midi: int,
    learned_lo: int,
    learned_hi: int,
    learned_center: int,
) -> int:
    """Octave correction using learned pitch range (not static instrument range)."""
    # If within learned range, accept
    if learned_lo <= midi <= learned_hi:
        return midi

    # If an octave shift would bring it into range, apply it
    if midi > learned_hi:
        candidate = midi - 12
        if learned_lo <= candidate <= learned_hi:
            return candidate
        # Try another octave
        candidate = midi - 24
        if learned_lo <= candidate <= learned_hi:
            return candidate
    elif midi < learned_lo:
        candidate = midi + 12
        if learned_lo <= candidate <= learned_hi:
            return candidate
        candidate = midi + 24
        if learned_lo <= candidate <= learned_hi:
            return candidate

    # If one octave above but within extended range, pull down
    if midi > learned_hi and midi <= learned_hi + 14:
        candidate = midi - 12
        if abs(candidate - learned_center) < abs(midi - learned_center):
            return candidate

    # If one octave below but within extended range, push up
    if midi < learned_lo and midi >= learned_lo - 14:
        candidate = midi + 12
        if abs(candidate - learned_center) < abs(midi - learned_center):
            return candidate

    return midi


def _median_filter_pitches(
    notes: list[MelodicNote],
    window: int = 5,
) -> list[MelodicNote]:
    """Remove pitch outliers using a median filter."""
    if len(notes) < 3:
        return notes

    pitches = [n.pitch for n in notes]
    corrected: list[MelodicNote] = []

    for i, note in enumerate(notes):
        start = max(0, i - window // 2)
        end = min(len(pitches), i + window // 2 + 1)
        neighborhood = sorted(pitches[start:end])
        median_pitch = neighborhood[len(neighborhood) // 2]

        pitch = note.pitch
        if abs(pitch - median_pitch) > 7:
            pitch = median_pitch

        corrected.append(
            MelodicNote(
                t_on=note.t_on,
                t_off=note.t_off,
                pitch=pitch,
                velocity=note.velocity,
                instrument=note.instrument,
            )
        )

    return corrected
