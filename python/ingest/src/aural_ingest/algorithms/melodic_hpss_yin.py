"""HPSS preprocessing + YIN pitch estimation.

Applies harmonic-percussive source separation via librosa to extract the
harmonic component, then runs YIN on the cleaner signal.
"""
from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_yin import _yin_pitch, _yin_pitch_np, _midi_from_freq, _HAS_NUMPY
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote

try:
    import numpy as np
    import librosa as _librosa
except ImportError:
    np = None
    _librosa = None


def _apply_hpss(samples: list[float], sr: int) -> list[float]:
    """Apply HPSS via librosa and return the harmonic component."""
    if np is None or _librosa is None:
        return samples  # fallback to raw if librosa unavailable

    y = np.array(samples, dtype=np.float32)
    harmonic, _percussive = _librosa.effects.hpss(y)
    return harmonic.tolist()


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    frame_sec: float = 0.05,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.07,
    yin_threshold: float = 0.15,
) -> list[MelodicNote]:
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    # Apply HPSS to get cleaner harmonic content
    samples = _apply_hpss(samples, sr)

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
