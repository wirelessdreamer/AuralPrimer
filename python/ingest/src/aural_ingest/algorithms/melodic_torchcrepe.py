"""torchcrepe monophonic pitch transcription adapter.

This is an optional research adapter. It is intentionally strict about missing
dependencies so benchmark reports can distinguish "model unavailable" from a
real transcription result.
"""
from __future__ import annotations

import importlib
import math
import os
from pathlib import Path
from typing import Any, Iterable

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def _as_float_list(values: Any) -> list[float]:
    if hasattr(values, "detach"):
        values = values.detach()
    if hasattr(values, "cpu"):
        values = values.cpu()
    if hasattr(values, "numpy"):
        values = values.numpy()
    if hasattr(values, "tolist"):
        values = values.tolist()
    if values and isinstance(values, list) and values and isinstance(values[0], list):
        values = values[0]
    try:
        return [float(v) for v in values]
    except Exception:
        return []


def _iter_notes_from_frames(
    freqs: Iterable[float],
    confidences: Iterable[float],
    *,
    hop_sec: float,
    freq_lo: float,
    freq_hi: float,
    min_note_sec: float,
    confidence_threshold: float,
    instrument: str,
) -> list[MelodicNote]:
    freq_list = [float(freq) for freq in freqs]
    confidence_list = [float(conf) for conf in confidences]
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_conf_sum = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_conf_sum, cur_count
        if cur_pitch is not None and cur_count > 0 and t_end - cur_start >= min_note_sec:
            conf = cur_conf_sum / float(cur_count)
            out.append(
                MelodicNote(
                    t_on=round(max(0.0, cur_start), 6),
                    t_off=round(max(cur_start, t_end), 6),
                    pitch=int(cur_pitch),
                    velocity=max(24, min(127, int(round(36.0 + (conf * 88.0))))),
                    instrument=instrument,
                )
            )
        cur_pitch = None
        cur_start = t_end
        cur_conf_sum = 0.0
        cur_count = 0

    for idx, (freq, conf) in enumerate(zip(freq_list, confidence_list)):
        t = float(idx) * hop_sec
        voiced = conf >= confidence_threshold and freq_lo <= freq <= freq_hi
        if not voiced:
            flush(t)
            continue

        pitch = _midi_from_freq(freq)
        if cur_pitch is None:
            cur_pitch = pitch
            cur_start = t
            cur_conf_sum = conf
            cur_count = 1
            continue

        if abs(pitch - cur_pitch) <= 1:
            cur_conf_sum += conf
            cur_count += 1
            continue

        flush(t)
        cur_pitch = pitch
        cur_start = t
        cur_conf_sum = conf
        cur_count = 1

    flush(float(len(freq_list)) * hop_sec)
    return sorted(out, key=lambda n: (n.t_on, n.pitch, n.t_off))


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    min_note_sec: float = 0.06,
    confidence_threshold: float = 0.55,
    hop_sec: float = 0.01,
) -> list[MelodicNote]:
    try:
        torchcrepe = importlib.import_module("torchcrepe")
        torch = importlib.import_module("torch")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "torchcrepe melodic method requires optional packages 'torchcrepe' and 'torch'"
        ) from exc

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    freq_lo, freq_hi = INSTRUMENT_FREQ_RANGES.get(
        instrument, INSTRUMENT_FREQ_RANGES["melodic"]
    )
    hop_length = max(1, int(round(float(hop_sec) * float(sr))))
    device = os.environ.get("AURAL_TORCHCREPE_DEVICE", "cpu").strip() or "cpu"
    model = os.environ.get("AURAL_TORCHCREPE_MODEL", "tiny").strip() or "tiny"
    batch_size = int(os.environ.get("AURAL_TORCHCREPE_BATCH_SIZE", "2048") or "2048")

    audio = torch.tensor(samples, dtype=torch.float32).unsqueeze(0)
    try:
        frequency, periodicity = torchcrepe.predict(
            audio,
            sr,
            hop_length,
            float(freq_lo),
            float(freq_hi),
            model=model,
            batch_size=batch_size,
            device=device,
            return_periodicity=True,
            pad=True,
        )
    except Exception as exc:  # pragma: no cover - depends on optional model runtime
        raise RuntimeError(f"torchcrepe inference failed: {exc}") from exc

    freqs = _as_float_list(frequency)
    confidences = _as_float_list(periodicity)
    if not freqs or not confidences:
        return []

    return _iter_notes_from_frames(
        freqs,
        confidences,
        hop_sec=hop_length / float(sr),
        freq_lo=float(freq_lo),
        freq_hi=float(freq_hi),
        min_note_sec=float(min_note_sec),
        confidence_threshold=float(confidence_threshold),
        instrument=instrument,
    )
