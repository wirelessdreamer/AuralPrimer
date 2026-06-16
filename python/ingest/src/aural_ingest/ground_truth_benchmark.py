"""
Ground-truth benchmark runner.

Sweeps one or more transcription algorithms across a prepared annotated
dataset, scores predictions against the canonical ground truth, and
aggregates per-case metrics into a corpus-wide report with bucketed
breakdowns (per kit / style / signal / etc.).

Datasets are accessed via the ``dataset_adapters`` package; algorithms
are looked up by id via the existing transcription registry so adding a
new algorithm to the ingest registry automatically makes it available
here.

Output is a single JSON file plus a Markdown summary -- the same shape
as the existing ``benchmark_drums`` / ``benchmark_quality`` flows so
the visual-report tooling can consume it later.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from aural_ingest.transcription import DrumEvent, MelodicNote

from .dataset_adapters.common import GroundTruthCase


# ---------------------------------------------------------------------------
# Algorithm registry adapters -- thin wrappers around the existing methods.
# ---------------------------------------------------------------------------


DrumTranscribeFn = Callable[[Path], list[DrumEvent]]
MelodicTranscribeFn = Callable[[Path], list[MelodicNote]]


def get_drum_algorithm(algorithm_id: str) -> DrumTranscribeFn:
    """Resolve a drum algorithm id to a callable.

    The transcription registry uses module-level ``transcribe(stem_path,
    *, ...)`` functions; we wrap them so the runner doesn't care about
    per-algorithm keyword arguments. New algorithms register simply by
    being importable as ``aural_ingest.algorithms.<id>.transcribe``.
    """
    import importlib

    try:
        module = importlib.import_module(f"aural_ingest.algorithms.{algorithm_id}")
    except ImportError as e:
        raise ValueError(f"unknown drum algorithm {algorithm_id!r}: {e}") from e
    fn = getattr(module, "transcribe", None)
    if fn is None:
        raise ValueError(f"algorithm {algorithm_id!r} has no transcribe() entry point")

    def adapter(audio_path: Path) -> list[DrumEvent]:
        return list(fn(audio_path))

    return adapter


def get_melodic_algorithm(algorithm_id: str) -> MelodicTranscribeFn:
    """Resolve a melodic algorithm id to a callable.

    Melodic methods in the registry take ``(stem_path, instrument='...')``;
    we wire instrument from the case at call time.
    """
    import importlib

    try:
        module = importlib.import_module(f"aural_ingest.algorithms.{algorithm_id}")
    except ImportError as e:
        raise ValueError(f"unknown melodic algorithm {algorithm_id!r}: {e}") from e
    fn = getattr(module, "transcribe", None)
    if fn is None:
        raise ValueError(f"algorithm {algorithm_id!r} has no transcribe() entry point")

    def adapter(audio_path: Path) -> list[MelodicNote]:
        # The registry's contract is ``transcribe(path, instrument=...)``;
        # default to "melodic" when an algorithm doesn't expose an
        # instrument knob (older modules).
        try:
            return list(fn(audio_path, instrument="melodic"))
        except TypeError:
            return list(fn(audio_path))

    return adapter


# ---------------------------------------------------------------------------
# Scoring -- one routine per event family.
# ---------------------------------------------------------------------------


def _greedy_match_onsets(
    reference: Sequence[float],
    predicted: Sequence[float],
    tolerance_sec: float,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Greedy nearest-onset pairing within ``tolerance_sec``.

    Returns ``(matches, unmatched_ref_idx, unmatched_pred_idx)`` where
    ``matches`` is ``(ref_idx, pred_idx, abs_time_error_sec)``.
    """
    if not reference or not predicted:
        return [], list(range(len(reference))), list(range(len(predicted)))
    # Sort both by time, remembering original indices.
    ref_order = sorted(range(len(reference)), key=lambda i: reference[i])
    pred_order = sorted(range(len(predicted)), key=lambda i: predicted[i])

    used_pred: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    start = 0
    for ri in ref_order:
        t_ref = reference[ri]
        while start < len(pred_order) and predicted[pred_order[start]] < t_ref - tolerance_sec:
            start += 1
        best_pi: int | None = None
        best_err: float | None = None
        j = start
        while j < len(pred_order):
            pi = pred_order[j]
            t_pred = predicted[pi]
            if t_pred > t_ref + tolerance_sec:
                break
            if pi not in used_pred:
                err = abs(t_pred - t_ref)
                if best_err is None or err < best_err:
                    best_err = err
                    best_pi = pi
            j += 1
        if best_pi is not None and best_err is not None:
            matches.append((ri, best_pi, best_err))
            used_pred.add(best_pi)

    unmatched_ref = [i for i in range(len(reference)) if not any(m[0] == i for m in matches)]
    unmatched_pred = [i for i in range(len(predicted)) if i not in used_pred]
    return matches, unmatched_ref, unmatched_pred


@dataclass
class CaseScore:
    """Per-case scoring outcome."""

    case_id: str
    algorithm_id: str
    runtime_sec: float
    tp: int
    fp: int
    fn: int
    onset_mae_sec: float | None
    metadata: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        d = {
            "case_id": self.case_id,
            "algorithm_id": self.algorithm_id,
            "runtime_sec": round(self.runtime_sec, 4),
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
            "onset_mae_sec": (
                round(self.onset_mae_sec, 6) if self.onset_mae_sec is not None else None
            ),
            "metadata": dict(self.metadata),
        }
        if self.error:
            d["error"] = self.error
        return d


def score_drum_case(
    case: GroundTruthCase,
    predicted: Sequence[DrumEvent],
    *,
    algorithm_id: str,
    runtime_sec: float,
    tolerance_sec: float = 0.05,
    pitch_aware: bool = True,
) -> CaseScore:
    """Greedy-match drum predictions against the case's reference events.

    ``pitch_aware`` matches only within the same drum-class (treating
    out-of-vocab MIDI notes as their own class). Set ``False`` to score
    onset-only.
    """
    from .transcription import _midi_note_to_benchmark_class

    if not pitch_aware:
        ref_times = [ev.time for ev in case.drum_events]
        pred_times = [ev.time for ev in predicted]
        matches, _ref_un, _pred_un = _greedy_match_onsets(
            ref_times, pred_times, tolerance_sec
        )
        tp = len(matches)
        fp = len(predicted) - tp
        fn = len(case.drum_events) - tp
        errs = [e for _r, _p, e in matches]
    else:
        # Bucket by class. Notes that don't map to a benchmark class go
        # under an "out_of_vocab" sentinel so they neither gain nor
        # leak credit across the standard classes.
        ref_by_cls: dict[str, list[float]] = defaultdict(list)
        pred_by_cls: dict[str, list[float]] = defaultdict(list)
        for ev in case.drum_events:
            cls = _midi_note_to_benchmark_class(ev.note) or f"oov_{ev.note}"
            ref_by_cls[cls].append(ev.time)
        for ev in predicted:
            cls = _midi_note_to_benchmark_class(ev.note) or f"oov_{ev.note}"
            pred_by_cls[cls].append(ev.time)
        tp = 0
        fp = 0
        fn = 0
        errs: list[float] = []
        all_classes = set(ref_by_cls) | set(pred_by_cls)
        for cls in all_classes:
            matches, ref_un, pred_un = _greedy_match_onsets(
                ref_by_cls[cls], pred_by_cls[cls], tolerance_sec
            )
            tp += len(matches)
            fp += len(pred_un)
            fn += len(ref_un)
            errs.extend(e for _r, _p, e in matches)

    onset_mae = statistics.mean(errs) if errs else None
    return CaseScore(
        case_id=case.case_id,
        algorithm_id=algorithm_id,
        runtime_sec=runtime_sec,
        tp=tp,
        fp=fp,
        fn=fn,
        onset_mae_sec=onset_mae,
        metadata=dict(case.metadata),
    )


def score_melodic_case(
    case: GroundTruthCase,
    predicted: Sequence[MelodicNote],
    *,
    algorithm_id: str,
    runtime_sec: float,
    tolerance_sec: float = 0.05,
    pitch_tolerance_semitones: int = 0,
) -> CaseScore:
    """Greedy-match melodic predictions against the case's reference notes.

    Default ``pitch_tolerance_semitones=0`` requires pitch-exact match
    (the strict guitar-transcription convention). Increase to allow
    octave-error forgiveness during diagnostics.
    """
    ref_by_pitch: dict[int, list[float]] = defaultdict(list)
    for n in case.melodic_notes:
        ref_by_pitch[n.pitch].append(n.t_on)
    pred_by_pitch: dict[int, list[float]] = defaultdict(list)
    for n in predicted:
        pred_by_pitch[n.pitch].append(n.t_on)

    tp = 0
    fp = 0
    fn = 0
    errs: list[float] = []

    if pitch_tolerance_semitones == 0:
        all_pitches = set(ref_by_pitch) | set(pred_by_pitch)
        for pitch in all_pitches:
            matches, ref_un, pred_un = _greedy_match_onsets(
                ref_by_pitch[pitch], pred_by_pitch[pitch], tolerance_sec
            )
            tp += len(matches)
            fp += len(pred_un)
            fn += len(ref_un)
            errs.extend(e for _r, _p, e in matches)
    else:
        # Pitch-tolerant match: pair each ref note with the closest pred
        # note in [pitch-tol .. pitch+tol] within onset tolerance.
        used_pred: set[int] = set()
        ref_sorted = sorted(range(len(case.melodic_notes)), key=lambda i: case.melodic_notes[i].t_on)
        pred_sorted = sorted(range(len(predicted)), key=lambda i: predicted[i].t_on)
        for ri in ref_sorted:
            r = case.melodic_notes[ri]
            best_pi: int | None = None
            best_err: float | None = None
            for pi in pred_sorted:
                if pi in used_pred:
                    continue
                p = predicted[pi]
                if p.t_on < r.t_on - tolerance_sec:
                    continue
                if p.t_on > r.t_on + tolerance_sec:
                    break
                if abs(p.pitch - r.pitch) > pitch_tolerance_semitones:
                    continue
                err = abs(p.t_on - r.t_on)
                if best_err is None or err < best_err:
                    best_err = err
                    best_pi = pi
            if best_pi is not None and best_err is not None:
                tp += 1
                used_pred.add(best_pi)
                errs.append(best_err)
            else:
                fn += 1
        fp = len(predicted) - len(used_pred)

    onset_mae = statistics.mean(errs) if errs else None
    return CaseScore(
        case_id=case.case_id,
        algorithm_id=algorithm_id,
        runtime_sec=runtime_sec,
        tp=tp,
        fp=fp,
        fn=fn,
        onset_mae_sec=onset_mae,
        metadata=dict(case.metadata),
    )


# ---------------------------------------------------------------------------
# Sweep orchestration.
# ---------------------------------------------------------------------------


def _safe_mean(values: Iterable[float | None]) -> float | None:
    vs = [v for v in values if v is not None and not math.isnan(v)]
    return statistics.mean(vs) if vs else None


def aggregate(
    scores: Sequence[CaseScore],
    *,
    bucket_keys: Sequence[str] = ("split", "style", "kit_name", "category", "signal"),
) -> dict[str, Any]:
    """Aggregate per-case scores into corpus + per-bucket summaries.

    The returned dict has the shape:

        {
            "overall": {"cases": N, "f1": …, "precision": …, ...},
            "per_algorithm": {"<alg>": {…overall, "buckets": {bucket_key: {bucket_value: {…}}}}},
        }
    """

    def _summarise(group: Sequence[CaseScore]) -> dict[str, Any]:
        ok = [s for s in group if s.error is None]
        tp = sum(s.tp for s in ok)
        fp = sum(s.fp for s in ok)
        fn = sum(s.fn for s in ok)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        return {
            "cases": len(group),
            "cases_ok": len(ok),
            "cases_err": len(group) - len(ok),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(p, 6),
            "recall": round(r, 6),
            "f1": round(f1, 6),
            "onset_mae_sec": round(_safe_mean(s.onset_mae_sec for s in ok), 6)
                if any(s.onset_mae_sec is not None for s in ok)
                else None,
            "mean_runtime_sec": round(_safe_mean(s.runtime_sec for s in ok), 4)
                if ok else None,
        }

    by_alg: dict[str, list[CaseScore]] = defaultdict(list)
    for s in scores:
        by_alg[s.algorithm_id].append(s)

    per_alg: dict[str, Any] = {}
    for alg, group in by_alg.items():
        overall = _summarise(group)
        buckets: dict[str, dict[str, dict[str, Any]]] = {}
        for key in bucket_keys:
            vals = {s.metadata.get(key) for s in group}
            if vals == {None}:
                continue
            buckets[key] = {}
            for val in sorted(v for v in vals if v is not None):
                sub = [s for s in group if s.metadata.get(key) == val]
                if sub:
                    buckets[key][val] = _summarise(sub)
        overall["buckets"] = buckets
        per_alg[alg] = overall

    return {
        "overall": _summarise(scores),
        "per_algorithm": per_alg,
    }


def run_sweep(
    cases: Iterable[GroundTruthCase],
    *,
    algorithms: Sequence[str],
    family: str,
    tolerance_sec: float = 0.05,
    pitch_tolerance_semitones: int = 0,
    on_case: Callable[[int, CaseScore], None] | None = None,
) -> list[CaseScore]:
    """Run each algorithm against every case, return all per-case scores.

    ``family`` is ``"drums"`` or ``"melodic"``; selects the scoring
    routine and the algorithm-resolution helper.
    """
    if family == "drums":
        resolve = get_drum_algorithm
        score = lambda case, pred, alg, rt: score_drum_case(  # noqa: E731
            case, pred, algorithm_id=alg, runtime_sec=rt, tolerance_sec=tolerance_sec
        )
    elif family == "melodic":
        resolve = get_melodic_algorithm
        score = lambda case, pred, alg, rt: score_melodic_case(  # noqa: E731
            case,
            pred,
            algorithm_id=alg,
            runtime_sec=rt,
            tolerance_sec=tolerance_sec,
            pitch_tolerance_semitones=pitch_tolerance_semitones,
        )
    else:
        raise ValueError(f"unknown family: {family!r}")

    algo_fns = {alg: resolve(alg) for alg in algorithms}
    all_scores: list[CaseScore] = []
    case_idx = 0
    for case in cases:
        case_idx += 1
        for alg, fn in algo_fns.items():
            start = time.perf_counter()
            try:
                pred = fn(case.audio_path)
                rt = time.perf_counter() - start
                cs = score(case, pred, alg, rt)
            except Exception as exc:
                rt = time.perf_counter() - start
                cs = CaseScore(
                    case_id=case.case_id,
                    algorithm_id=alg,
                    runtime_sec=rt,
                    tp=0,
                    fp=0,
                    fn=(
                        len(case.drum_events)
                        if family == "drums"
                        else len(case.melodic_notes)
                    ),
                    onset_mae_sec=None,
                    metadata=dict(case.metadata),
                    error=str(exc),
                )
            all_scores.append(cs)
            if on_case is not None:
                on_case(case_idx, cs)
    return all_scores


def write_report(
    scores: Sequence[CaseScore],
    *,
    out_path: Path,
    dataset: str,
    family: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the sweep result + aggregate to a JSON report on disk.

    Returns the report dict.
    """
    summary = aggregate(scores)
    report = {
        "dataset": dataset,
        "family": family,
        "extra": dict(extra) if extra else {},
        "case_count": len({s.case_id for s in scores}),
        "summary": summary,
        "cases": [s.as_dict() for s in scores],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
