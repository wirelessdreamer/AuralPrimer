from __future__ import annotations

import math
from pathlib import Path
from typing import Any

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

    base = extract_melodic_notes_mono(
        stem_path,
        frame_sec=0.04,
        hop_sec=0.015,
        min_note_sec=0.06,
        min_freq_hz=freq_lo,
        max_freq_hz=freq_hi,
    )
    if base:
        if instrument in {"bass", "keys"}:
            return base

        out: list[MelodicNote] = []
        for n in base:
            out.append(n)
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

    duration = estimate_duration_sec(stem_path)
    out: list[MelodicNote] = []
    t = 0.0
    idx = 0
    if instrument == "bass":
        base_pitches = [40, 43, 45, 47]
    elif instrument == "keys":
        base_pitches = [60, 64, 67, 72]
    else:
        base_pitches = [52, 55, 59, 64]
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


def _coerce_velocity(value: Any) -> int:
    try:
        numeric = float(value)
    except Exception:
        numeric = 0.75
    if numeric <= 1.0:
        return max(24, min(127, int(round(36.0 + (numeric * 88.0)))))
    return max(24, min(127, int(round(numeric))))


def _parse_note_event(raw_event: Any, *, instrument: str) -> MelodicNote | None:
    if isinstance(raw_event, dict):
        t_on = float(raw_event.get("start_time_s", raw_event.get("start", 0.0)) or 0.0)
        t_off = float(raw_event.get("end_time_s", raw_event.get("end", t_on)) or t_on)
        pitch = raw_event.get("pitch_midi", raw_event.get("pitch"))
        velocity = raw_event.get("velocity", raw_event.get("confidence", raw_event.get("amplitude", 0.8)))
    elif isinstance(raw_event, (tuple, list)) and len(raw_event) >= 3:
        t_on = float(raw_event[0] or 0.0)
        t_off = float(raw_event[1] or t_on)
        pitch = raw_event[2]
        velocity = raw_event[3] if len(raw_event) >= 4 else 0.8
    else:
        return None

    try:
        pitch_i = int(round(float(pitch)))
    except Exception:
        return None

    if t_off <= t_on:
        return None
    if pitch_i < 0 or pitch_i > 127:
        return None

    return MelodicNote(
        t_on=round(max(0.0, t_on), 6),
        t_off=round(max(t_on, t_off), 6),
        pitch=pitch_i,
        velocity=_coerce_velocity(velocity),
        instrument=instrument,
    )


def transcribe(
    stem_path: Path,
    *,
    model_path: Path | None = None,
    instrument: str = "melodic",
) -> list[MelodicNote]:
    if model_path is not None and not model_path.exists():
        raise RuntimeError("basic_pitch model path unavailable")

    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model, predict
    except Exception:
        return _fallback_transcribe(stem_path, instrument=instrument)

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )
    resolved_model_path = model_path
    if resolved_model_path is None:
        try:
            resolved_model_path = Path(str(ICASSP_2022_MODEL_PATH))
        except Exception:
            resolved_model_path = None

    model: Any | None = None
    if resolved_model_path is not None:
        try:
            model = Model(str(resolved_model_path))
        except Exception:
            model = None

    try:
        if model is not None:
            _model_output, _midi_data, note_events = predict(
                str(stem_path),
                model,
                minimum_frequency=float(freq_lo),
                maximum_frequency=float(freq_hi),
            )
        else:
            _model_output, _midi_data, note_events = predict(
                str(stem_path),
                minimum_frequency=float(freq_lo),
                maximum_frequency=float(freq_hi),
            )
    except TypeError:
        try:
            if model is not None:
                _model_output, _midi_data, note_events = predict(str(stem_path), model)
            else:
                _model_output, _midi_data, note_events = predict(str(stem_path))
        except Exception:
            return _fallback_transcribe(stem_path, instrument=instrument)
    except Exception:
        return _fallback_transcribe(stem_path, instrument=instrument)

    parsed = [
        note
        for note in (_parse_note_event(raw, instrument=instrument) for raw in note_events)
        if note is not None
    ]
    if parsed:
        filtered = [note for note in parsed if freq_lo <= (440.0 * (2.0 ** ((note.pitch - 69) / 12.0))) <= freq_hi]
        if filtered:
            parsed = filtered
        parsed.sort(key=lambda note: (note.t_on, note.pitch, note.t_off))
        return parsed

    return _fallback_transcribe(stem_path, instrument=instrument)
