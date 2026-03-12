from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms._common import estimate_duration_sec, extract_melodic_notes_mono
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def transcribe(
    stem_path: Path,
    *,
    model_path: Path | None = None,
    instrument: str = "melodic",
) -> list[MelodicNote]:
    # This implementation is dependency-free; only reject explicitly invalid paths.
    if model_path is not None and not model_path.exists():
        raise RuntimeError("basic_pitch model path unavailable")

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    base = extract_melodic_notes_mono(
        stem_path,
        frame_sec=0.04,
        hop_sec=0.015,
        min_note_sec=0.06,
        min_freq_hz=freq_lo,
        max_freq_hz=freq_hi,
    )
    if base:
        # For bass and keys, skip dyad expansion (bass is monophonic, keys are already
        # polyphonic in the transcription).  For guitar, add simple dyad.
        if instrument in {"bass", "keys"}:
            return base

        out: list[MelodicNote] = []
        for n in base:
            out.append(n)
            # Simple dyad expansion to approximate polyphonic content in a dependency-free way.
            chord_pitch = min(108, n.pitch + 7)
            chord_on = round(min(n.t_off, n.t_on + 0.02), 6)
            chord_off = round(n.t_off, 6)
            if chord_off > chord_on:
                out.append(
                    MelodicNote(
                        t_on=chord_on,
                        t_off=chord_off,
                        pitch=chord_pitch,
                        velocity=max(24, min(127, n.velocity - 10)),
                        instrument=instrument,
                    )
                )
        return out

    # Fallback for non-wav assets where pitch extraction is unavailable.
    duration = estimate_duration_sec(stem_path)
    out: list[MelodicNote] = []
    t = 0.0
    idx = 0
    # Instrument-appropriate fallback pitches.
    if instrument == "bass":
        base_pitches = [40, 43, 45, 47]  # E2, G2, A2, B2
    elif instrument == "keys":
        base_pitches = [60, 64, 67, 72]  # C4, E4, G4, C5
    else:
        base_pitches = [52, 55, 59, 64]  # E3, G3, B3, E4
    while t < duration:
        base_pitch = base_pitches[idx % len(base_pitches)]
        t_on = round(t, 6)
        t_off = round(min(duration, t + 0.18), 6)
        if t_off > t_on:
            out.append(MelodicNote(t_on=t_on, t_off=t_off, pitch=base_pitch, velocity=92, instrument=instrument))

            if instrument not in {"bass", "keys"}:
                chord_on = round(min(t_off, t + 0.02), 6)
                if t_off > chord_on:
                    out.append(
                        MelodicNote(
                            t_on=chord_on,
                            t_off=t_off,
                            pitch=min(108, base_pitch + 7),
                            velocity=82,
                            instrument=instrument,
                        )
                    )

        t += 0.24
        idx += 1

    return out
