"""Benchmark-overlay artifact for the Studio side-by-side comparison view.

Runs a roster of melodic transcribers on a single instrument stem and writes,
under ``features/benchmark/<role>/``::

    manifest.json          engine roster + per-engine status + scores
    <engine_id>.json       that engine's full-song note set

Each engine is scored against a hand-corrected ground-truth MIDI
(``features/ground_truth.<role>.mid``) when one is present -- the Studio's
spectrogram editor curates that file. Engines whose optional runtime
dependency isn't installed (e.g. ``piano_pti`` needs
``piano_transcription_inference``) are skipped gracefully and marked
``ok=false`` in the manifest, so the flow works end-to-end with whatever
transcribers are actually available and lights the rest up as deps land.

On-demand (like ``refine-candidates``), not part of every import.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from aural_ingest.ground_truth_benchmark import (
    _greedy_match_onsets,
    get_melodic_algorithm,
)
from aural_ingest.transcription import MelodicNote

# Friendly display labels. Unknown engine ids fall back to the raw id.
ENGINE_LABELS: dict[str, str] = {
    "piano_basic_pitch_clean": "basic-pitch",
    "melodic_basic_pitch": "basic-pitch",
    "piano_pti": "piano-pti",
    "piano_pti_clean": "piano-pti (clean)",
    "piano_hft": "hFT-Transformer",
    "piano_transkun": "Transkun",
    "piano_d3rm": "D3RM",
    "piano_polyphonic": "poly-CNN",
    "melodic_torchcrepe": "torchcrepe",
    "melodic_crepe": "CREPE",
    "melodic_hpss_combined": "HPSS+onset",
    "melodic_yin_octave_hps_fix": "YIN+HPS",
}

# Curated, diverse default roster per instrument (NOT tuned production variants
# -- distinct algorithm families so the comparison is meaningful). Override via
# the CLI ``--engines`` flag. Unavailable engines are skipped.
DEFAULT_ROSTER: dict[str, list[str]] = {
    "keys": ["piano_basic_pitch_clean", "piano_pti", "piano_hft", "piano_transkun"],
    "bass": ["melodic_basic_pitch", "melodic_torchcrepe", "melodic_yin_octave_hps_fix"],
    "lead_guitar": ["melodic_basic_pitch", "melodic_hpss_combined", "melodic_crepe"],
    "rhythm_guitar": ["melodic_basic_pitch", "melodic_hpss_combined", "melodic_crepe"],
    "melodic": ["melodic_basic_pitch", "melodic_torchcrepe"],
}


def _label(engine_id: str) -> str:
    return ENGINE_LABELS.get(engine_id, engine_id)


def _note_dict(n: MelodicNote) -> dict[str, Any]:
    return {
        "t_on": round(float(n.t_on), 4),
        "t_off": round(max(float(n.t_off), float(n.t_on) + 0.01), 4),
        "pitch": int(n.pitch),
        "velocity": int(max(1, min(127, n.velocity))),
    }


def _find_stem(auralsong_dir: Path, instrument: str) -> Path | None:
    cand = auralsong_dir / "audio" / "stems" / f"{instrument}.wav"
    return cand if cand.is_file() else None


def _load_ground_truth(auralsong_dir: Path, instrument: str) -> list[MelodicNote] | None:
    """Read the hand-corrected reference MIDI, if the Studio has saved one."""
    p = auralsong_dir / "features" / f"ground_truth.{instrument}.mid"
    if not p.is_file():
        return None
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(p))
    notes: list[MelodicNote] = []
    for inst in pm.instruments:
        for n in inst.notes:
            notes.append(
                MelodicNote(
                    t_on=float(n.start),
                    t_off=float(n.end),
                    pitch=int(n.pitch),
                    velocity=int(n.velocity),
                    instrument=instrument,
                )
            )
    notes.sort(key=lambda x: x.t_on)
    return notes


def _score(
    reference: Sequence[MelodicNote],
    predicted: Sequence[MelodicNote],
    *,
    tolerance_sec: float = 0.05,
) -> dict[str, Any]:
    """Pitch-exact, onset-tolerant precision/recall/F1 (mirrors score_melodic_case)."""
    ref_by: dict[int, list[float]] = defaultdict(list)
    pred_by: dict[int, list[float]] = defaultdict(list)
    for n in reference:
        ref_by[n.pitch].append(n.t_on)
    for n in predicted:
        pred_by[n.pitch].append(n.t_on)

    tp = fp = fn = 0
    for pitch in set(ref_by) | set(pred_by):
        matches, ref_un, pred_un = _greedy_match_onsets(
            ref_by[pitch], pred_by[pitch], tolerance_sec
        )
        tp += len(matches)
        fp += len(pred_un)
        fn += len(ref_un)

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def run_benchmark_overlay(
    auralsong_dir: str | Path,
    instrument: str,
    *,
    engine_ids: list[str] | None = None,
    onset_tolerance_sec: float = 0.05,
) -> dict[str, Any]:
    """Run the roster, write per-engine note sets + manifest. Returns the manifest."""
    auralsong_dir = Path(auralsong_dir)
    stem = _find_stem(auralsong_dir, instrument)
    if stem is None:
        return {"ok": False, "error": f"no stem at audio/stems/{instrument}.wav"}

    engines = engine_ids or DEFAULT_ROSTER.get(instrument, DEFAULT_ROSTER["melodic"])
    out_dir = auralsong_dir / "features" / "benchmark" / instrument
    out_dir.mkdir(parents=True, exist_ok=True)
    gt = _load_ground_truth(auralsong_dir, instrument)

    engines_meta: list[dict[str, Any]] = []
    for eid in engines:
        entry: dict[str, Any] = {"id": eid, "label": _label(eid)}
        try:
            fn = get_melodic_algorithm(eid)
            t0 = time.perf_counter()
            notes = sorted(fn(stem, instrument), key=lambda n: n.t_on)
            elapsed = time.perf_counter() - t0
            (out_dir / f"{eid}.json").write_text(
                json.dumps(
                    {
                        "engine": eid,
                        "label": _label(eid),
                        "instrument": instrument,
                        "notes": [_note_dict(n) for n in notes],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            entry.update(
                {
                    "ok": True,
                    "notes_file": f"{eid}.json",
                    "note_count": len(notes),
                    "elapsed_sec": round(elapsed, 2),
                    "score": _score(gt, notes, tolerance_sec=onset_tolerance_sec)
                    if gt is not None
                    else None,
                }
            )
        except Exception as e:  # noqa: BLE001 -- any engine failure is non-fatal
            entry.update(
                {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "notes_file": None,
                    "note_count": 0,
                    "score": None,
                }
            )
        engines_meta.append(entry)

    manifest: dict[str, Any] = {
        "version": 1,
        "instrument": instrument,
        "match": {"onset_tolerance_sec": onset_tolerance_sec, "pitch_tolerance_semitones": 0},
        "ground_truth": {
            "present": gt is not None,
            "rel_path": f"features/ground_truth.{instrument}.mid",
            "note_count": len(gt) if gt is not None else 0,
        },
        "engines": engines_meta,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"ok": True, **manifest}
