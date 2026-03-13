"""YIN-octave + HPS-pitch hybrid melodic transcription.

Uses each method where it's strongest:
- **YIN (autocorrelation)** determines the correct *octave* — because
  autocorrelation naturally finds the period of the waveform, which
  corresponds to the fundamental frequency, not harmonics.
- **HPS (FFT)** provides the fine *pitch class* (chroma) within that
  octave — because HPS has better frequency resolution than YIN.

This directly addresses the #1 weakness across all current algorithms:
octave errors caused by harmonics being stronger than the fundamental.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_onset_yin import _detect_onsets
from aural_ingest.algorithms.melodic_fft_hps import _hps_pitch
from aural_ingest.algorithms.melodic_yin import _yin_pitch_np, _HAS_NUMPY
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
except ImportError:
    np = None


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def _freq_from_midi(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _yin_octave_hps_pitch(
    samples: list[float],
    samples_np,  # numpy array or None
    sr: int,
    frame_start: int,
    frame_len: int,
    *,
    min_freq: float,
    max_freq: float,
    yin_threshold: float = 0.15,
    use_np: bool = False,
) -> float | None:
    """Hybrid pitch: YIN for octave, HPS for chroma.

    Strategy:
    1. Run both YIN and HPS on the frame
    2. If both agree (same MIDI note ±1), use HPS (better resolution)
    3. If they disagree by exactly 12 semitones (octave), trust YIN's
       octave but use HPS's pitch class
    4. If only one returns a result, use that one
    5. For larger disagreements, prefer YIN (more robust to harmonics)
    """
    hps_freq = _hps_pitch(
        samples, sr, frame_start, frame_len,
        min_freq=min_freq, max_freq=max_freq,
        harmonics=4,
    )

    yin_freq = None
    if use_np and samples_np is not None:
        end = frame_start + frame_len
        if end <= len(samples):
            # Use a longer window for YIN when possible (better for low freqs)
            yin_len = min(len(samples) - frame_start, int(frame_len * 1.5))
            yin_seg = samples_np[frame_start: frame_start + yin_len]
            yin_freq = _yin_pitch_np(
                yin_seg, sr,
                threshold=yin_threshold,
                min_freq=min_freq,
                max_freq=max_freq,
            )

    # --- Decision logic ---

    # Neither worked
    if hps_freq is None and yin_freq is None:
        return None

    # Only one worked — use it
    if hps_freq is None:
        return yin_freq
    if yin_freq is None:
        return hps_freq

    # Both worked — compare
    hps_midi = _midi_from_freq(hps_freq)
    yin_midi = _midi_from_freq(yin_freq)
    diff = abs(hps_midi - yin_midi)

    if diff <= 1:
        # They agree — use HPS for better frequency resolution
        return hps_freq

    if diff == 12 or diff == 11 or diff == 13:
        # Octave disagreement — trust YIN's octave, use HPS's pitch class
        # Extract pitch class (0-11) from HPS, octave from YIN
        hps_chroma = hps_midi % 12
        yin_octave = yin_midi // 12
        corrected_midi = yin_octave * 12 + hps_chroma

        # Sanity check: result should be in range
        corrected_freq = _freq_from_midi(corrected_midi)
        if min_freq <= corrected_freq <= max_freq:
            return corrected_freq
        # If not in range, just trust YIN entirely
        return yin_freq

    if diff == 24 or diff == 23 or diff == 25:
        # Two-octave disagreement — definitely trust YIN
        return yin_freq

    # Other disagreement — prefer YIN (more robust to harmonics)
    # but only if YIN's result is lower (less likely to be a harmonic)
    if yin_freq < hps_freq:
        return yin_freq
    else:
        # HPS is lower, which is unusual — might be a subharmonic in YIN
        # Trust HPS in this case
        return hps_freq


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.06,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.06,
    onset_ratio: float = 3.0,
    yin_threshold: float = 0.15,
) -> list[MelodicNote]:
    """YIN-octave + HPS-pitch hybrid transcription."""
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    # Use longer frames for bass instruments (need more cycles for YIN)
    if instrument == "bass":
        frame_sec = max(frame_sec, 0.08)

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    frame = max(128, int(sr * max(0.008, frame_sec)))
    hop = max(32, int(sr * max(0.002, hop_sec)))
    if len(samples) < frame:
        return []

    use_np = _HAS_NUMPY and np is not None
    samples_np = None
    if use_np:
        samples_np = np.array(samples, dtype=np.float64)

    # --- Frame-wise analysis ---
    frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples):
        if use_np:
            seg = samples_np[i: i + frame]
            rms = float(np.sqrt(np.mean(seg * seg)))
        else:
            seg = samples[i: i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        freq = _yin_octave_hps_pitch(
            samples, samples_np, sr, i, frame,
            min_freq=freq_lo, max_freq=freq_hi,
            yin_threshold=yin_threshold,
            use_np=use_np,
        )

        midi = _midi_from_freq(freq) if freq is not None else None
        t = i / float(sr)
        frames.append((t, midi, rms))
        i += hop

    if not frames:
        return []

    # --- Onset detection ---
    rms_vals = [f[2] for f in frames]
    onset_frames = _detect_onsets(rms_vals, ratio_threshold=onset_ratio)

    # --- Voicing floor with hysteresis ---
    rms_sorted = sorted(v for v in rms_vals if v > 0)
    if rms_sorted:
        floor_on = max(0.005, rms_sorted[len(rms_sorted) // 6])
        floor_sustain = floor_on * 0.6
    else:
        floor_on = 0.005
        floor_sustain = 0.003

    # --- Note segmentation ---
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

    for frame_idx, (t, midi, rms) in enumerate(frames):
        threshold = floor_sustain if is_sustaining else floor_on
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

    if frames:
        flush(frames[-1][0] + (hop / float(sr)))

    return out
