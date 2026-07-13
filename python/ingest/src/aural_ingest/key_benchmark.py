"""Key-detection benchmark helpers.

The ingest-side key writer currently uses a deterministic
Krumhansl-Schmuckler pass over MIDI notes. These helpers evaluate that pass
against dataset adapters that expose ``GroundTruthCase.key_events``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from aural_ingest.dataset_adapters.common import GroundTruthCase, GroundTruthKeyEvent
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
class KeyLabel:
    key: str
    mode: str
    pitch_class: int
    label: str


def normalize_key_mode(mode: str) -> str:
    raw = str(mode or "").strip().lower()
    if raw in {"maj", "major", "ionian"}:
        return "major"
    if raw in {"min", "minor", "aeolian"}:
        return "minor"
    return raw


def normalize_key_label(label: str, *, key: str | None = None, mode: str | None = None) -> KeyLabel:
    raw_label = str(label or "").strip()
    raw_key = str(key or "").strip()
    raw_mode = str(mode or "").strip()
    if (not raw_key or not raw_mode) and ":" in raw_label:
        label_key, label_mode = raw_label.split(":", 1)
        raw_key = raw_key or label_key
        raw_mode = raw_mode or label_mode
    if not raw_key:
        raw_key = raw_label
    normalized_mode = normalize_key_mode(raw_mode)
    pitch_class = _PITCH_CLASS_BY_KEY[raw_key]
    label_out = raw_label or f"{raw_key}:{normalized_mode}"
    return KeyLabel(
        key=raw_key,
        mode=normalized_mode,
        pitch_class=pitch_class,
        label=label_out,
    )


def _pretty_midi_from_notes(notes: Sequence[MelodicNote]) -> Any:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=24, name="Guitar")
    for note in notes:
        start = max(0.0, float(note.t_on))
        end = max(start + 0.001, float(note.t_off))
        inst.notes.append(
            pretty_midi.Note(
                velocity=max(1, min(127, int(note.velocity))),
                pitch=max(0, min(127, int(note.pitch))),
                start=start,
                end=end,
            )
        )
    pm.instruments.append(inst)
    return pm


def infer_key_from_notes(notes: Sequence[MelodicNote]) -> dict[str, Any] | None:
    from aural_ingest.feedpak_writer import _infer_key_signature

    if not notes:
        return None
    return _infer_key_signature(_pretty_midi_from_notes(notes))


def _reference_key_for_case(case: GroundTruthCase) -> GroundTruthKeyEvent | None:
    if not case.key_events:
        return None
    return max(case.key_events, key=lambda event: max(0.0, float(event.t_off - event.t_on)))


def score_key_case(case: GroundTruthCase) -> dict[str, Any]:
    ref_event = _reference_key_for_case(case)
    prediction = infer_key_from_notes(case.melodic_notes)
    result: dict[str, Any] = {
        "case_id": case.case_id,
        "note_count": len(case.melodic_notes),
        "status": "ok",
        "metadata": dict(case.metadata),
    }
    if ref_event is None:
        result["status"] = "no_reference"
        return result
    ref = normalize_key_label(ref_event.label, key=ref_event.key, mode=ref_event.mode)
    result.update({
        "reference": {
            "key": ref.key,
            "mode": ref.mode,
            "pitch_class": ref.pitch_class,
            "label": ref.label,
            "confidence": ref_event.confidence,
        }
    })
    if prediction is None:
        result["status"] = "no_prediction"
        return result
    pred = normalize_key_label(
        f"{prediction['key']}:{prediction['mode']}",
        key=str(prediction["key"]),
        mode=str(prediction["mode"]),
    )
    pitch_class_match = pred.pitch_class == ref.pitch_class
    mode_match = pred.mode == ref.mode
    result.update({
        "prediction": {
            "key": pred.key,
            "mode": pred.mode,
            "pitch_class": pred.pitch_class,
            "label": pred.label,
            "confidence": prediction.get("confidence"),
            "score": prediction.get("score"),
            "method": prediction.get("method"),
        },
        "pitch_class_match": pitch_class_match,
        "mode_match": mode_match,
        "correct": pitch_class_match and mode_match,
        "spelling_match": pred.key == ref.key and mode_match,
    })
    return result


def summarize_key_scores(case_scores: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(case_scores)
    scored = [row for row in rows if row.get("status") == "ok"]
    with_reference = [row for row in rows if row.get("status") != "no_reference"]
    no_prediction = [row for row in with_reference if row.get("status") == "no_prediction"]

    def ratio(count: int, denom: int) -> float:
        return round(count / denom, 6) if denom else 0.0

    total = len(scored)
    correct = sum(1 for row in scored if row.get("correct"))
    pitch_class_correct = sum(1 for row in scored if row.get("pitch_class_match"))
    mode_correct = sum(1 for row in scored if row.get("mode_match"))
    spelling_correct = sum(1 for row in scored if row.get("spelling_match"))
    return {
        "cases": len(rows),
        "cases_with_reference": len(with_reference),
        "cases_scored": total,
        "cases_no_prediction": len(no_prediction),
        "correct": correct,
        "pitch_class_correct": pitch_class_correct,
        "mode_correct": mode_correct,
        "spelling_correct": spelling_correct,
        "accuracy": ratio(correct, total),
        "pitch_class_accuracy": ratio(pitch_class_correct, total),
        "mode_accuracy": ratio(mode_correct, total),
        "spelling_accuracy": ratio(spelling_correct, total),
    }


def evaluate_key_cases(cases: Iterable[GroundTruthCase]) -> dict[str, Any]:
    scores = [score_key_case(case) for case in cases]
    return {
        "summary": summarize_key_scores(scores),
        "cases": scores,
    }
