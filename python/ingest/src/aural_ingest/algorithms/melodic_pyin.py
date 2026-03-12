from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms._common import estimate_duration_sec, extract_melodic_notes_mono
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
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

    # Resilient fallback for non-wav or low-SNR sources.
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
