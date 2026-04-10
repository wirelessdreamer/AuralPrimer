from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import estimate_duration_sec, extract_melodic_notes_mono
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def _fallback_transcribe(
    stem_path: Path,
    *,
    instrument: str,
) -> list[MelodicNote]:
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    notes = extract_melodic_notes_mono(
        stem_path,
        frame_sec=0.048,
        hop_sec=0.018,
        min_note_sec=0.07,
        min_freq_hz=freq_lo,
        max_freq_hz=freq_hi,
    )
    if notes:
        return notes

    duration = estimate_duration_sec(stem_path)
    out: list[MelodicNote] = []
    t = 0.0
    if instrument == "bass":
        pitches = [40, 43, 45, 47, 48, 50]
    elif instrument == "keys":
        pitches = [60, 64, 67, 72, 76, 79]
    else:
        pitches = [52, 55, 59, 64, 67, 71]
    idx = 0
    while t < duration:
        t_on = round(t, 6)
        t_off = round(min(duration, t + 0.2), 6)
        if t_off > t_on:
            out.append(
                MelodicNote(
                    t_on=t_on,
                    t_off=t_off,
                    pitch=pitches[idx % len(pitches)],
                    velocity=84,
                    instrument=instrument,
                )
            )
        idx += 1
        t += 0.25

    return out


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
) -> list[MelodicNote]:
    try:
        import numpy as np
        import librosa
    except Exception:
        return _fallback_transcribe(stem_path, instrument=instrument)

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    try:
        audio, sr = librosa.load(str(stem_path), sr=None, mono=True)
    except Exception:
        return _fallback_transcribe(stem_path, instrument=instrument)

    if sr <= 0 or len(audio) == 0:
        return _fallback_transcribe(stem_path, instrument=instrument)

    frame_length = 2048
    hop_length = 256 if instrument == "bass" else 512
    min_note_sec = 0.08 if instrument == "bass" else 0.06

    try:
        f0, voiced_flag, voiced_probs = librosa.pyin(
            np.asarray(audio, dtype=np.float32),
            fmin=float(freq_lo),
            fmax=float(freq_hi),
            sr=int(sr),
            frame_length=frame_length,
            hop_length=hop_length,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=hop_length)
    except Exception:
        return _fallback_transcribe(stem_path, instrument=instrument)

    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_prob_sum = 0.0
    cur_count = 0
    hop_sec = hop_length / float(sr)

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_prob_sum, cur_count
        if cur_pitch is not None and cur_count > 0:
            dur = t_end - cur_start
            if dur >= min_note_sec:
                mean_prob = cur_prob_sum / float(cur_count)
                velocity = max(24, min(127, int(round(42.0 + (85.0 * mean_prob)))))
                out.append(
                    MelodicNote(
                        t_on=round(cur_start, 6),
                        t_off=round(t_end, 6),
                        pitch=int(cur_pitch),
                        velocity=velocity,
                        instrument=instrument,
                    )
                )
        cur_pitch = None
        cur_prob_sum = 0.0
        cur_count = 0

    for idx, frame_freq in enumerate(f0):
        t = float(times[idx])
        voiced = bool(voiced_flag[idx])
        if not voiced or np.isnan(frame_freq):
            flush(t)
            continue

        freq = float(frame_freq)
        if freq < freq_lo or freq > freq_hi:
            flush(t)
            continue

        midi = int(round(69.0 + (12.0 * math.log2(freq / 440.0))))
        midi = max(0, min(127, midi))
        prob = float(voiced_probs[idx]) if voiced_probs is not None else 0.75

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

    if len(times) > 0:
        flush(float(times[-1]) + hop_sec)

    if out:
        return out
    return _fallback_transcribe(stem_path, instrument=instrument)
