"""Octave-correcting post-processor on top of melodic_combined.

Analyzes output notes for systematic octave errors using the instrument
frequency range. If a note's pitch is an octave above or below the
expected range centroid, it gets corrected.

Also applies a median-filter smoothing pass: if a note's pitch is an
outlier relative to its neighbors, it gets corrected or removed.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms.melodic_combined import transcribe as _transcribe_combined
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def _freq_from_midi(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def _octave_correct(
    notes: list[MelodicNote],
    instrument: str,
) -> list[MelodicNote]:
    """Fix systematic octave errors based on instrument range."""
    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )

    # Compute the expected MIDI range
    midi_lo = _midi_from_freq(freq_lo)
    midi_hi = _midi_from_freq(freq_hi)
    midi_center = (midi_lo + midi_hi) // 2

    corrected: list[MelodicNote] = []
    for note in notes:
        pitch = note.pitch
        freq = _freq_from_midi(pitch)

        # Check if pitch is outside the expected range
        if freq < freq_lo * 0.8:
            # Too low — try octave up
            if freq_lo <= _freq_from_midi(pitch + 12) <= freq_hi:
                pitch = pitch + 12
        elif freq > freq_hi * 1.2:
            # Too high — try octave down
            if freq_lo <= _freq_from_midi(pitch - 12) <= freq_hi:
                pitch = pitch - 12
        else:
            # Within range — check for systematic octave doubling
            if pitch > midi_center + 12:
                candidate = pitch - 12
                if midi_lo <= candidate <= midi_hi:
                    if abs(candidate - midi_center) < abs(pitch - midi_center):
                        pitch = candidate
            elif pitch < midi_center - 12:
                candidate = pitch + 12
                if midi_lo <= candidate <= midi_hi:
                    if abs(candidate - midi_center) < abs(pitch - midi_center):
                        pitch = candidate

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


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **kwargs,
) -> list[MelodicNote]:
    """Combined approach with octave correction and median smoothing."""
    notes = _transcribe_combined(stem_path, instrument=instrument, **kwargs)

    if not notes:
        return notes

    notes = _octave_correct(notes, instrument)
    notes = _median_filter_pitches(notes, window=5)

    return notes
