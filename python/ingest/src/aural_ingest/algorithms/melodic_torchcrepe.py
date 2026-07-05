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
from aural_ingest.device import select_device
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote


# torchcrepe's CREPE model covers C1..B7; the lowest representable pitch is
# C1 = 32.70319566 Hz. Passing a lower fmin yields degenerate (all-zero)
# periodicity rather than an error.
_TORCHCREPE_MIN_FMIN = 32.70319566

# Instrument-specific upper-freq clamps applied INSIDE the torchcrepe adapter
# only. torchcrepe latches strongly onto strong upper harmonics -- on quiet /
# per-string debleeded low-string audio, the CREPE model is happy to report
# the second harmonic (2*f0) as the pitch, i.e. an exact octave-up jump. That
# is invisible to the octave-forgiving F1 metric but doubles the strict-F1
# false-positive count on the GuitarSet low-string corpus.
#
# We can't tighten INSTRUMENT_FREQ_RANGES["bass"] globally because YIN / pyIN
# / basic_pitch / melodic_combined are all tuned to the wider range and their
# strict F1s are actually helped by having room above E3 for a genuine
# fretted high-A-string note. So we clamp inside this adapter only.
#
# Empirically (benchmarks/bass/gt_runs/bass_hexdebleed_20_fmax_sweep.log):
#   fmax=400 Hz (production default) strict-F1 = 0.197, fp = 828
#   fmax=200 Hz                       strict-F1 = 0.233, fp = 114  (-86%)
# 200 Hz sits just above G3 (196 Hz), covering a real bass fretboard's
# usable range while suppressing 2*f0 harmonic mirrors for any note below
# G2 (98 Hz). Real bass fundamentals above G3 are rare in the corpora we
# care about; the strict-F1 win dominates the recall loss.
_TORCHCREPE_INSTRUMENT_FMAX: dict[str, float] = {
    "bass": 200.0,
}


# --- Onset-aware note splitting -------------------------------------------
# The frame decoder alone merges every contiguous run of same-pitch voiced
# frames into ONE note. Real bass/guitar lines re-articulate the same pitch
# (eighth-note pedal tones, re-picked notes) without the periodicity dipping,
# so those repeats collapse into a single sustained note. We detect audio
# onsets and split an already-voiced same-pitch run wherever an onset lands
# inside it. This never fabricates notes in silence — splitting only happens
# between two voiced frames of an existing note.
#
# Gated + robust: if librosa/onset detection is unavailable or errors, we fall
# back to the un-split behavior so the production path never regresses to empty.
_SPLIT_ON_ONSETS_DEFAULT = True

# librosa.onset.onset_detect tuning. `wait`/`delta` follow the pattern already
# used in piano_chord_supplement.py; they favor genuine attacks over decay-tail
# ripple. Overridable via env for benchmarking.
_ONSET_HOP_LENGTH_DEFAULT = 512
_ONSET_DELTA_DEFAULT = 0.06
_ONSET_WAIT_DEFAULT = 3

# When a split lands too close to a note boundary the leading/trailing fragment
# would be shorter than the min-note floor. We require both sides of a split to
# clear `min_note_sec` before accepting the onset as a boundary.


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _midi_from_freq(freq: float) -> int:
    return max(0, min(127, int(round(69.0 + 12.0 * math.log2(freq / 440.0)))))


def _detect_onset_times(samples: list[float], sr: int) -> list[float] | None:
    """Return sorted audio-onset times (seconds), or ``None`` if unavailable.

    Uses ``librosa.onset.onset_detect`` with backtracking so each returned time
    sits on the attack transient. Returns ``None`` (not ``[]``) when the
    detector can't run, so the caller can distinguish "no onsets found" (an
    empty list -> keep runs whole) from "detector unavailable" (fall back).
    """
    if not samples or sr <= 0:
        return None
    try:
        import numpy as np
        import librosa
    except Exception:
        return None

    hop_length = max(1, _env_int("AURAL_TORCHCREPE_ONSET_HOP", _ONSET_HOP_LENGTH_DEFAULT))
    delta = _env_float("AURAL_TORCHCREPE_ONSET_DELTA", _ONSET_DELTA_DEFAULT)
    wait = max(0, _env_int("AURAL_TORCHCREPE_ONSET_WAIT", _ONSET_WAIT_DEFAULT))
    try:
        y = np.asarray(samples, dtype=np.float32)
        onset_times = librosa.onset.onset_detect(
            y=y,
            sr=sr,
            hop_length=hop_length,
            units="time",
            backtrack=True,
            wait=wait,
            delta=delta,
        )
    except Exception:
        return None
    try:
        times = sorted(float(t) for t in onset_times if float(t) >= 0.0)
    except Exception:
        return None
    return times


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


def _velocity_from_conf(mean_conf: float) -> int:
    return max(24, min(127, int(round(36.0 + (mean_conf * 88.0)))))


def _emit_run_notes(
    *,
    pitch: int,
    frames: list[tuple[float, float]],
    t_end: float,
    hop_sec: float,
    min_note_sec: float,
    instrument: str,
    onset_times: list[float] | None,
    out: list[MelodicNote],
) -> None:
    """Emit one or more notes for a single same-pitch voiced run.

    ``frames`` is the list of ``(t, conf)`` samples that make up the run;
    ``t_end`` is the time the run ends (first non-matching / unvoiced frame, or
    the end of the signal). When onset splitting is enabled and one or more
    onset times land strictly inside the run, the run is cut at those onsets
    into consecutive segments — but only where both the segment being closed and
    the remainder still clear ``min_note_sec`` (never fabricates a sub-floor
    sliver, and never splits in silence since the whole run is already voiced).
    Velocity is re-derived per emitted segment from that segment's mean
    confidence.
    """
    if not frames:
        return

    run_start = frames[0][0]

    def _emit(seg_start: float, seg_end: float, seg_frames: list[tuple[float, float]]) -> None:
        if not seg_frames or seg_end - seg_start < min_note_sec:
            return
        mean_conf = sum(c for _, c in seg_frames) / float(len(seg_frames))
        out.append(
            MelodicNote(
                t_on=round(max(0.0, seg_start), 6),
                t_off=round(max(seg_start, seg_end), 6),
                pitch=int(pitch),
                velocity=_velocity_from_conf(mean_conf),
                instrument=instrument,
            )
        )

    # Split points: onset times strictly inside (run_start, t_end). We keep a
    # split only if it leaves >= min_note_sec on both the segment it closes and
    # the remainder, so short re-articulations below the floor stay merged
    # rather than producing invalid slivers.
    splits: list[float] = []
    if onset_times:
        seg_start = run_start
        for onset_t in onset_times:
            if onset_t <= seg_start + min_note_sec:
                continue
            if onset_t >= t_end - min_note_sec:
                break
            splits.append(onset_t)
            seg_start = onset_t

    if not splits:
        _emit(run_start, t_end, frames)
        return

    boundaries = [run_start, *splits, t_end]
    for b_idx in range(len(boundaries) - 1):
        seg_start = boundaries[b_idx]
        seg_end = boundaries[b_idx + 1]
        seg_frames = [fr for fr in frames if seg_start <= fr[0] < seg_end]
        if not seg_frames:
            # Guard: keep at least one frame so velocity is well-defined. This
            # can only happen at a coarse hop; fall back to the enclosing frame.
            seg_frames = [fr for fr in frames if fr[0] < seg_end] or frames[:1]
        _emit(seg_start, seg_end, seg_frames)


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
    onset_times: list[float] | None = None,
) -> list[MelodicNote]:
    freq_list = [float(freq) for freq in freqs]
    confidence_list = [float(conf) for conf in confidences]
    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_frames: list[tuple[float, float]] = []

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_frames
        if cur_pitch is not None and cur_frames:
            _emit_run_notes(
                pitch=cur_pitch,
                frames=cur_frames,
                t_end=t_end,
                hop_sec=hop_sec,
                min_note_sec=min_note_sec,
                instrument=instrument,
                onset_times=onset_times,
                out=out,
            )
        cur_pitch = None
        cur_frames = []

    for idx, (freq, conf) in enumerate(zip(freq_list, confidence_list)):
        t = float(idx) * hop_sec
        voiced = conf >= confidence_threshold and freq_lo <= freq <= freq_hi
        if not voiced:
            flush(t)
            continue

        pitch = _midi_from_freq(freq)
        if cur_pitch is None:
            cur_pitch = pitch
            cur_frames = [(t, conf)]
            continue

        if abs(pitch - cur_pitch) <= 1:
            cur_frames.append((t, conf))
            continue

        flush(t)
        cur_pitch = pitch
        cur_frames = [(t, conf)]

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
    # torchcrepe's pitch model resolves down to ~C1 (32.70 Hz). An fmin below
    # that doesn't error — it silently returns all-zero periodicity, so every
    # frame fails the confidence gate and the result is 0 notes. The bass range
    # starts at 30 Hz (below a 5-string low B), so clamp up to the model floor.
    freq_lo = max(float(freq_lo), _TORCHCREPE_MIN_FMIN)
    # Per-instrument fmax clamp inside the adapter (see comment on
    # _TORCHCREPE_INSTRUMENT_FMAX). Suppresses second-harmonic octave doubling
    # on the low-string bass path without regressing other bass algorithms.
    instrument_fmax = _TORCHCREPE_INSTRUMENT_FMAX.get(instrument)
    if instrument_fmax is not None:
        freq_hi = min(float(freq_hi), float(instrument_fmax))
    hop_length = max(1, int(round(float(hop_sec) * float(sr))))
    device = select_device("AURAL_TORCHCREPE_DEVICE")
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

    # Onset-aware splitting: detect audio onsets once and split same-pitch
    # voiced runs at any onset that lands inside them. Robustly gated — if the
    # detector is disabled or fails, onset_times stays None and segmentation
    # behaves exactly as before (no split), so the path never regresses.
    onset_times: list[float] | None = None
    if _env_flag("AURAL_TORCHCREPE_SPLIT_ON_ONSETS", _SPLIT_ON_ONSETS_DEFAULT):
        onset_times = _detect_onset_times(samples, sr)

    return _iter_notes_from_frames(
        freqs,
        confidences,
        hop_sec=hop_length / float(sr),
        freq_lo=float(freq_lo),
        freq_hi=float(freq_hi),
        min_note_sec=float(min_note_sec),
        confidence_threshold=float(confidence_threshold),
        instrument=instrument,
        onset_times=onset_times,
    )
