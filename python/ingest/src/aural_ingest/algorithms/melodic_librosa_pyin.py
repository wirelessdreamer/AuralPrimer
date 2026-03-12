"""librosa pYIN pitch estimator wrapper.

Uses ``librosa.pyin()`` for probabilistic YIN with HMM smoothing,
providing good accuracy with octave-error resistance.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    min_note_sec: float = 0.06,
    frame_length: int = 2048,
    hop_length: int = 512,
) -> list[MelodicNote]:
    try:
        import numpy as np
        import librosa
    except ImportError:
        return []

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    audio = np.array(samples, dtype=np.float32)

    # librosa.pyin returns (f0, voiced_flag, voiced_probs)
    f0, voiced_flag, voiced_probs = librosa.pyin(
        audio,
        fmin=freq_lo,
        fmax=freq_hi,
        sr=sr,
        frame_length=frame_length,
        hop_length=hop_length,
    )

    times = librosa.times_like(f0, sr=sr, hop_length=hop_length)
    hop_sec = hop_length / float(sr)

    # --- Build note events ---
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_prob_sum = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_prob_sum, cur_count
        if cur_pitch is not None and cur_count > 0:
            dur = t_end - cur_start
            if dur >= min_note_sec:
                mean_prob = cur_prob_sum / cur_count
                vel = int(40 + mean_prob * 87)
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
        cur_prob_sum = 0.0
        cur_count = 0

    for i in range(len(f0)):
        t = float(times[i])
        is_voiced = bool(voiced_flag[i])
        freq = float(f0[i]) if not np.isnan(f0[i]) else 0.0
        prob = float(voiced_probs[i])

        if not is_voiced or freq < freq_lo or freq > freq_hi:
            flush(t)
            continue

        midi = _midi_from_freq(freq)
        if cur_pitch is None:
            cur_pitch = midi
            cur_start = t
            cur_prob_sum = prob
            cur_count = 1
            continue

        if abs(midi - cur_pitch) <= 1:
            cur_prob_sum += prob
            cur_count += 1
            continue

        flush(t)
        cur_pitch = midi
        cur_start = t
        cur_prob_sum = prob
        cur_count = 1

    if len(f0) > 0:
        flush(float(times[-1]) + hop_sec)

    return out
