"""HPSS + combined approach — harmonic preprocessing before onset+HPS.

Applies librosa HPSS to isolate the harmonic content, then runs the
combined onset+HPS pitch estimation on the cleaned signal.
This should reduce false detections from percussive transients.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_onset_yin import _detect_onsets
from aural_ingest.algorithms.melodic_fft_hps import _hps_pitch, _midi_from_freq
from aural_ingest.algorithms.melodic_yin import _yin_pitch_np, _HAS_NUMPY
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
    import librosa as _librosa
except ImportError:
    np = None
    _librosa = None


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.06,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.06,
    onset_ratio: float = 3.0,
) -> list[MelodicNote]:
    """HPSS preprocessing + combined onset+HPS approach."""
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    # Apply HPSS to isolate harmonic content
    if np is not None and _librosa is not None:
        y = np.array(samples, dtype=np.float32)
        harmonic, _ = _librosa.effects.hpss(y)
        samples_clean = harmonic.tolist()
    else:
        samples_clean = samples

    frame = max(128, int(sr * max(0.008, frame_sec)))
    hop = max(32, int(sr * max(0.002, hop_sec)))
    if len(samples_clean) < frame:
        return []

    use_np = _HAS_NUMPY and np is not None
    if use_np:
        samples_np = np.array(samples_clean, dtype=np.float64)

    # --- Frame-wise analysis using HPS for pitch on HPSS-cleaned signal ---
    frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples_clean):
        if use_np:
            seg = samples_np[i : i + frame]
            rms = float(np.sqrt(np.mean(seg * seg)))
        else:
            seg = samples_clean[i : i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        # Use HPS for pitch on cleaned signal
        freq = _hps_pitch(
            samples_clean, sr, i, frame,
            min_freq=freq_lo, max_freq=freq_hi,
            harmonics=4,
        )

        # If HPS fails, try YIN as fallback
        if freq is None and use_np:
            freq_val = _yin_pitch_np(
                samples_np[i : i + frame], sr,
                threshold=0.15,
                min_freq=freq_lo, max_freq=freq_hi,
            )
            freq = freq_val

        midi = _midi_from_freq(freq) if freq is not None else None
        t = i / float(sr)
        frames.append((t, midi, rms))
        i += hop

    if not frames:
        return []

    # --- Onset detection on HPSS-cleaned signal ---
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

    # --- Note segmentation with onset awareness ---
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
