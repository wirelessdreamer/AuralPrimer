from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pretty_midi
import pytest

from aural_ingest.dataset_adapters.common import GroundTruthCase, GroundTruthChordEvent
from aural_ingest.transcription import MelodicNote


def test_normalize_chord_label_handles_guitarset_extensions() -> None:
    from aural_ingest.chord_benchmark import normalize_chord_label

    assert normalize_chord_label("D#:maj").pitch_class == normalize_chord_label("Eb:maj").pitch_class
    assert normalize_chord_label("G#:maj6(*5)/1").quality == "maj"
    assert normalize_chord_label("D#:sus2(7)/1").quality == "sus2"
    assert normalize_chord_label("N").root is None


def test_score_chord_case_counts_enharmonic_root_match(monkeypatch) -> None:
    from aural_ingest import chord_benchmark

    case = GroundTruthCase(
        case_id="case",
        instrument="guitar",
        audio_path=Path("x.wav"),
        duration_sec=2.0,
        melodic_notes=(
            MelodicNote(t_on=0.0, t_off=1.0, pitch=63, velocity=90, instrument="guitar"),
            MelodicNote(t_on=0.0, t_off=1.0, pitch=67, velocity=90, instrument="guitar"),
            MelodicNote(t_on=0.0, t_off=1.0, pitch=70, velocity=90, instrument="guitar"),
        ),
        chord_events=(
            GroundTruthChordEvent(t_on=0.0, t_off=1.0, label="Eb:maj", source="mireval"),
        ),
    )

    monkeypatch.setattr(
        chord_benchmark,
        "_infer_chord_from_notes",
        lambda *_args, **_kwargs: {
            "root": "D#",
            "quality": "maj",
            "confidence": 0.9,
            "score": 0.8,
        },
    )

    score = chord_benchmark.score_chord_case(case)

    event = score["events"][0]
    assert event["root_match"] is True
    assert event["quality_match"] is True
    assert event["correct"] is True


def test_summarize_chord_scores_counts_no_predictions() -> None:
    from aural_ingest.chord_benchmark import summarize_chord_scores

    summary = summarize_chord_scores([
        {
            "status": "ok",
            "events": [
                {
                    "status": "ok",
                    "reference": {"pitch_class": 0},
                    "correct": True,
                    "root_match": True,
                    "quality_match": True,
                },
                {
                    "status": "ok",
                    "reference": {"pitch_class": 7},
                    "correct": False,
                    "root_match": True,
                    "quality_match": False,
                },
                {"status": "no_prediction", "reference": {"pitch_class": 5}},
            ],
        },
        {"status": "no_reference", "events": []},
    ])

    assert summary["cases"] == 2
    assert summary["events"] == 3
    assert summary["events_scored"] == 2
    assert summary["events_no_prediction"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["root_accuracy"] == 1.0
    assert summary["quality_accuracy"] == 0.5


def test_feedpak_chord_events_detect_simple_block_chords() -> None:
    from aural_ingest import feedpak_writer

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=0, name="Keys")
    for pitches, start, end in (
        ((60, 64, 67), 0.0, 2.0),
        ((65, 69, 72), 2.0, 4.0),
        ((67, 71, 74, 77), 4.0, 6.0),
    ):
        for pitch in pitches:
            inst.notes.append(pretty_midi.Note(velocity=100, pitch=pitch, start=start, end=end))
    pm.instruments.append(inst)
    measures = feedpak_writer._derive_measures(
        {"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 6.0, "time_signature": "4/4"}]},
        None,
        6.0,
    )
    key = feedpak_writer._infer_key_signature(pm)

    events = feedpak_writer._infer_chord_events(pm, measures, 6.0, key)

    assert [(event["t"], event["root"], event["quality"], event["rn"]) for event in events] == [
        (0.0, "C", "maj", "I"),
        (2.0, "F", "maj", "IV"),
        (4.0, "G", "7", "V7"),
    ]


def test_guitarset_chord_eval_script_fails_empty_case_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "evaluate_guitarset_chords.py"
    spec = importlib.util.spec_from_file_location("evaluate_guitarset_chords_empty", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "yield_cases", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_guitarset_chords",
            "--corpus-root",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "chord_report.json"),
        ],
    )

    with pytest.raises(SystemExit, match="no GuitarSet chord cases matched"):
        module.main()
    assert not (tmp_path / "chord_report.json").exists()
