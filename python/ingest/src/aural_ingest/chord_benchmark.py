"""Chord benchmark helpers for GuitarSet-style chord annotations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from aural_ingest.dataset_adapters.common import GroundTruthCase, GroundTruthChordEvent
from aural_ingest.key_benchmark import _pretty_midi_from_notes
from aural_ingest.transcription import MelodicNote


_PITCH_CLASS_BY_KEY: dict[str, int] = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "Fb": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "Cb": 11,
}


@dataclass(frozen=True)
class ChordLabel:
    root: str | None
    quality: str
    pitch_class: int | None
    label: str


def normalize_chord_quality(raw_quality: str) -> str:
    raw = str(raw_quality or "").strip().lower()
    if not raw:
        return ""
    # Remove JAMS/mireval additions such as ``maj6(*5)`` or ``sus2(7)``.
    base = raw.split("(", 1)[0]
    if base in {"maj", "major", ""}:
        return "maj"
    if base in {"min", "minor", "m"}:
        return "min"
    if base in {"7", "dom7"}:
        return "7"
    if base in {"maj7", "major7"}:
        return "maj7"
    if base in {"min7", "minor7", "m7"}:
        return "min7"
    if base in {"dim", "dim7", "hdim7", "aug", "sus2", "sus4"}:
        return base
    if base.startswith("maj"):
        return "maj"
    if base.startswith("min"):
        return "min"
    if base.startswith("sus2"):
        return "sus2"
    if base.startswith("sus4") or base.startswith("sus"):
        return "sus4"
    if base.startswith("dim"):
        return "dim"
    if base.startswith("aug"):
        return "aug"
    return base


def normalize_chord_label(label: str) -> ChordLabel:
    raw = str(label or "").strip()
    if not raw or raw.upper() in {"N", "NC", "N.C.", "X"}:
        return ChordLabel(root=None, quality="none", pitch_class=None, label=raw)
    root_part, sep, quality_part = raw.partition(":")
    root = root_part.strip()
    quality_raw = quality_part if sep else ""
    quality_raw = quality_raw.split("/", 1)[0]
    quality = normalize_chord_quality(quality_raw)
    return ChordLabel(
        root=root,
        quality=quality,
        pitch_class=_PITCH_CLASS_BY_KEY[root],
        label=raw,
    )


def _infer_chord_from_notes(
    notes: tuple[MelodicNote, ...],
    start: float,
    end: float,
    key_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from aural_ingest.feedpak_writer import _infer_chord_for_span, _pitch_class_weights_for_span

    pm = _pretty_midi_from_notes(notes)
    weights, lowest_pitch = _pitch_class_weights_for_span(pm, start, end)
    return _infer_chord_for_span(weights, lowest_pitch, key_analysis=key_analysis)


def _reference_chords_for_case(
    case: GroundTruthCase,
    *,
    source: str,
) -> list[GroundTruthChordEvent]:
    refs = [event for event in case.chord_events if event.source == source]
    return refs if refs else list(case.chord_events)


def score_chord_case(case: GroundTruthCase, *, source: str = "mireval") -> dict[str, Any]:
    from aural_ingest.key_benchmark import infer_key_from_notes

    refs = _reference_chords_for_case(case, source=source)
    result: dict[str, Any] = {
        "case_id": case.case_id,
        "source": source,
        "reference_events": len(refs),
        "metadata": dict(case.metadata),
        "events": [],
    }
    if not refs:
        result["status"] = "no_reference"
        return result

    key_analysis = infer_key_from_notes(case.melodic_notes)
    for ref_event in refs:
        ref = normalize_chord_label(ref_event.label)
        event: dict[str, Any] = {
            "t": round(float(ref_event.t_on), 6),
            "duration": round(float(ref_event.t_off - ref_event.t_on), 6),
            "reference": {
                "root": ref.root,
                "quality": ref.quality,
                "pitch_class": ref.pitch_class,
                "label": ref.label,
            },
        }
        prediction = _infer_chord_from_notes(
            case.melodic_notes,
            float(ref_event.t_on),
            float(ref_event.t_off),
            key_analysis,
        )
        if prediction is None:
            event["status"] = "no_prediction"
        else:
            pred_label = normalize_chord_label(f"{prediction['root']}:{prediction['quality']}")
            root_match = pred_label.pitch_class == ref.pitch_class
            quality_match = pred_label.quality == ref.quality
            event.update({
                "status": "ok",
                "prediction": {
                    "root": pred_label.root,
                    "quality": pred_label.quality,
                    "pitch_class": pred_label.pitch_class,
                    "label": pred_label.label,
                    "confidence": prediction.get("confidence"),
                    "score": prediction.get("score"),
                },
                "root_match": root_match,
                "quality_match": quality_match,
                "correct": root_match and quality_match,
            })
        result["events"].append(event)
    result["status"] = "ok"
    return result


def summarize_chord_scores(case_scores: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(case_scores)
    events = [
        event
        for row in rows
        if row.get("status") == "ok"
        for event in row.get("events", [])
        if event.get("reference", {}).get("pitch_class") is not None
    ]
    scored = [event for event in events if event.get("status") == "ok"]
    no_prediction = [event for event in events if event.get("status") == "no_prediction"]

    def ratio(count: int, denom: int) -> float:
        return round(count / denom, 6) if denom else 0.0

    correct = sum(1 for event in scored if event.get("correct"))
    root_correct = sum(1 for event in scored if event.get("root_match"))
    quality_correct = sum(1 for event in scored if event.get("quality_match"))
    return {
        "cases": len(rows),
        "events": len(events),
        "events_scored": len(scored),
        "events_no_prediction": len(no_prediction),
        "correct": correct,
        "root_correct": root_correct,
        "quality_correct": quality_correct,
        "accuracy": ratio(correct, len(scored)),
        "root_accuracy": ratio(root_correct, len(scored)),
        "quality_accuracy": ratio(quality_correct, len(scored)),
    }


def evaluate_chord_cases(
    cases: Iterable[GroundTruthCase],
    *,
    source: str = "mireval",
) -> dict[str, Any]:
    scores = [score_chord_case(case, source=source) for case in cases]
    return {
        "summary": summarize_chord_scores(scores),
        "cases": scores,
    }
