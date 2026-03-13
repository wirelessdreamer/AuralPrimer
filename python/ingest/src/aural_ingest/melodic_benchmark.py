"""Melodic transcription benchmark evaluation module.

Mirrors the drum_benchmark.py pattern for note-level evaluation
with metrics: F1, precision, recall, pitch accuracy, timing MAE,
and octave error rate.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from aural_ingest.transcription import MelodicNote, INSTRUMENT_FREQ_RANGES

# Re-use MIDI parsing from drum_benchmark
from aural_ingest.drum_benchmark import (
    _parse_midi_note_ons,
    _tick_to_seconds,
    _compress_tempo_changes,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MelodicBenchmarkEvent:
    time: float
    pitch: int  # MIDI pitch 0-127
    duration: float = 0.0  # optional, for offset evaluation


# ---------------------------------------------------------------------------
# MIDI reference parsing
# ---------------------------------------------------------------------------

def parse_melodic_midi_reference(
    midi_path: Path,
    offset_sec: float = 0.0,
) -> list[MelodicBenchmarkEvent]:
    """Extract note-on events from a MIDI file as benchmark reference."""
    note_ons, tempo_changes_raw, tpq = _parse_midi_note_ons(midi_path)
    tempo_changes = _compress_tempo_changes(tempo_changes_raw)
    events = []
    for n in note_ons:
        t = _tick_to_seconds(n.tick, tempo_changes, tpq) + offset_sec
        if t < 0:
            continue
        events.append(MelodicBenchmarkEvent(time=round(t, 6), pitch=n.note))
    return sorted(events, key=lambda e: e.time)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class MelodicEvalResult:
    """Evaluation result for one algorithm on one song/instrument."""
    tp: int = 0
    fp: int = 0
    fn: int = 0
    pitch_correct: int = 0  # within ±1 semitone
    pitch_octave_error: int = 0  # within ±1 semitone modulo 12
    timing_errors_ms: list[float] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / max(1, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return self.tp / max(1, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / max(1e-9, p + r)

    @property
    def pitch_accuracy(self) -> float:
        """Fraction of TPs with correct pitch."""
        return self.pitch_correct / max(1, self.tp)

    @property
    def octave_error_rate(self) -> float:
        return self.pitch_octave_error / max(1, self.tp)

    @property
    def timing_mae_ms(self) -> float | None:
        if not self.timing_errors_ms:
            return None
        return sum(abs(e) for e in self.timing_errors_ms) / len(self.timing_errors_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "f1": round(self.f1, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "pitch_correct": self.pitch_correct,
            "pitch_accuracy": round(self.pitch_accuracy, 4),
            "octave_errors": self.pitch_octave_error,
            "octave_error_rate": round(self.octave_error_rate, 4),
            "timing_mae_ms": round(self.timing_mae_ms, 2) if self.timing_mae_ms is not None else None,
        }


def evaluate_melodic(
    predicted: list[MelodicNote],
    reference: list[MelodicBenchmarkEvent],
    *,
    tolerance_sec: float = 0.06,
) -> MelodicEvalResult:
    """Compare predicted notes against reference using onset matching.

    A predicted note is a true positive if its onset is within
    ``tolerance_sec`` of a reference note onset.  If matched, we also
    check whether the pitch is correct (within ±1 semitone) and whether
    it's an octave error (correct chroma, wrong octave).
    """
    result = MelodicEvalResult()

    # Sort both lists by time
    pred_sorted = sorted(predicted, key=lambda n: n.t_on)
    ref_sorted = list(reference)  # already sorted

    ref_matched = [False] * len(ref_sorted)

    for pred_note in pred_sorted:
        best_idx = -1
        best_dist = tolerance_sec + 1.0

        for j, ref_evt in enumerate(ref_sorted):
            if ref_matched[j]:
                continue
            dist = abs(pred_note.t_on - ref_evt.time)
            if dist <= tolerance_sec and dist < best_dist:
                best_dist = dist
                best_idx = j

        if best_idx >= 0:
            ref_matched[best_idx] = True
            result.tp += 1
            result.timing_errors_ms.append(best_dist * 1000.0)

            ref_pitch = ref_sorted[best_idx].pitch
            pred_pitch = pred_note.pitch

            # Pitch accuracy check (within ±1 semitone)
            if abs(pred_pitch - ref_pitch) <= 1:
                result.pitch_correct += 1
            # Octave error check (correct chroma class, wrong octave)
            elif (pred_pitch % 12) == (ref_pitch % 12) and abs(pred_pitch - ref_pitch) > 1:
                result.pitch_octave_error += 1
        else:
            result.fp += 1

    # Unmatched reference events are false negatives
    result.fn = sum(1 for m in ref_matched if not m)

    return result


# ---------------------------------------------------------------------------
# Algorithm registry and runner
# ---------------------------------------------------------------------------

MELODIC_ALGORITHMS = [
    "melodic_basic_pitch",
    "melodic_pyin",
    "melodic_yin",
    "melodic_onset_yin",
    "melodic_hpss_yin",
    "melodic_fft_hps",
    "melodic_librosa_pyin",
    # --- experiments (round 2) ---
    "melodic_yin_t020",
    "melodic_yin_bass80",
    "melodic_combined",
    "melodic_hpss_onset",
    "melodic_pyin_long",
    # --- experiments (round 3) ---
    "melodic_octave_fix",
    "melodic_adaptive",
    "melodic_hpss_combined",
    # --- experiments (round 4: template multi-pass) ---
    "melodic_template_multipass",
    "melodic_yin_octave_hps",
    "melodic_yin_octave_hps_fix",
]


def _load_algorithm(name: str):
    """Dynamically import a melodic algorithm module."""
    import importlib
    mod = importlib.import_module(f"aural_ingest.algorithms.{name}")
    return mod


def benchmark_melodic_algorithms(
    wav_path: Path,
    reference: list[MelodicBenchmarkEvent],
    algorithms: list[str],
    *,
    instrument: str = "melodic",
    tolerance_sec: float = 0.06,
) -> list[dict[str, Any]]:
    """Run multiple melodic algorithms on a single WAV and evaluate each."""
    results = []

    for alg_name in algorithms:
        try:
            mod = _load_algorithm(alg_name)
            t0 = time.time()
            predicted = mod.transcribe(wav_path, instrument=instrument)
            elapsed = time.time() - t0

            eval_result = evaluate_melodic(
                predicted, reference, tolerance_sec=tolerance_sec,
            )

            results.append({
                "algorithm": alg_name,
                "note_count": len(predicted),
                "elapsed_sec": round(elapsed, 2),
                "overall": eval_result.to_dict(),
            })
        except Exception as exc:
            results.append({
                "algorithm": alg_name,
                "error": str(exc),
                "note_count": 0,
                "overall": MelodicEvalResult().to_dict(),
            })

    return results


def format_melodic_summary(payload: Mapping[str, Any]) -> str:
    """Format a single song's results as a readable table."""
    lines = []
    lines.append(f"  {'Algorithm':<30} {'F1':>6} {'Prec':>6} {'Rec':>6} {'PitchAcc':>8} {'OctErr':>6} {'MAE':>7} {'Notes':>6}")
    lines.append("  " + "-" * 90)

    for r in payload.get("results", []):
        o = r.get("overall", {})
        err = r.get("error")
        if err:
            lines.append(f"  {r['algorithm']:<30} ERROR: {err}")
            continue
        mae = o.get("timing_mae_ms")
        mae_str = f"{mae:>6.1f}ms" if mae is not None else "    n/a"
        lines.append(
            f"  {r['algorithm']:<30} "
            f"{o.get('f1', 0):>6.3f} "
            f"{o.get('precision', 0):>6.3f} "
            f"{o.get('recall', 0):>6.3f} "
            f"{o.get('pitch_accuracy', 0):>8.1%} "
            f"{o.get('octave_error_rate', 0):>6.1%} "
            f"{mae_str} "
            f"{r.get('note_count', 0):>6}"
        )

    return "\n".join(lines)
