"""YIN pitch estimator with energy-based onset detection.

Adds a dedicated onset detector on top of the YIN pitch core so that
repeated notes at the same pitch are properly segmented.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_yin import _yin_pitch, _yin_pitch_np, _midi_from_freq, _HAS_NUMPY
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
except ImportError:
    pass


def _detect_onsets(
    rms_vals: list[float],
    *,
    ratio_threshold: float = 3.0,
    median_window: int = 7,
) -> set[int]:
    """Return frame indices where we detect an energy onset."""
    onsets: set[int] = set()
    for i in range(1, len(rms_vals)):
        if rms_vals[i] <= 0:
            continue
        # Local median from a short window behind
        start = max(0, i - median_window)
        window = sorted(rms_vals[start:i])
        local_med = window[len(window) // 2] if window else 0.0
        if local_med > 0 and rms_vals[i] / local_med > ratio_threshold:
            onsets.add(i)
        elif i > 0 and rms_vals[i - 1] > 0 and rms_vals[i] / rms_vals[i - 1] > ratio_threshold:
            onsets.add(i)
    return onsets


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.05,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.06,
    yin_threshold: float = 0.15,
    onset_ratio: float = 3.0,
) -> list[MelodicNote]:
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
                seg, sr, threshold=yin_threshold,
                min_freq=freq_lo, max_freq=freq_hi,
            )
        else:
            seg = samples[i : i + frame]
            rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))
            freq = _yin_pitch(
                samples, sr, frame_start=i, frame_len=frame,
                threshold=yin_threshold,
                min_freq=freq_lo, max_freq=freq_hi,
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

    # --- Voicing floor (15th percentile with hysteresis) ---
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

        # Force note boundary at onset detection points
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
