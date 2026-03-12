"""CREPE neural pitch estimator wrapper.

Uses the ``crepe`` library for high-accuracy monophonic f0 estimation.
Falls back gracefully to None if crepe is not installed.
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
    confidence_threshold: float = 0.5,
    hop_sec: float = 0.01,
) -> list[MelodicNote]:
    try:
        import numpy as np
        import crepe
    except ImportError:
        return []  # Graceful fallback

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    audio = np.array(samples, dtype=np.float32)

    # CREPE returns (time, frequency, confidence, activation)
    step_size = max(5, int(hop_sec * 1000))  # ms
    time_arr, freq_arr, conf_arr, _act = crepe.predict(
        audio, sr, viterbi=True, step_size=step_size,
    )

    # --- Build note events from frame estimates ---
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_conf_sum = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_conf_sum, cur_count
        if cur_pitch is not None and cur_count > 0:
            dur = t_end - cur_start
            if dur >= min_note_sec:
                mean_conf = cur_conf_sum / cur_count
                vel = int(40 + mean_conf * 87)
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
        cur_conf_sum = 0.0
        cur_count = 0

    for i in range(len(time_arr)):
        t = float(time_arr[i])
        f = float(freq_arr[i])
        c = float(conf_arr[i])

        voiced = c >= confidence_threshold and freq_lo <= f <= freq_hi
        if not voiced:
            flush(t)
            continue

        midi = _midi_from_freq(f)
        if cur_pitch is None:
            cur_pitch = midi
            cur_start = t
            cur_conf_sum = c
            cur_count = 1
            continue

        if abs(midi - cur_pitch) <= 1:
            cur_conf_sum += c
            cur_count += 1
            continue

        flush(t)
        cur_pitch = midi
        cur_start = t
        cur_conf_sum = c
        cur_count = 1

    if len(time_arr) > 0:
        flush(float(time_arr[-1]) + hop_sec)

    return out
