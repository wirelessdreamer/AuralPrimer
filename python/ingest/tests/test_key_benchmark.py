from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from aural_ingest.dataset_adapters.common import GroundTruthCase, GroundTruthKeyEvent
from aural_ingest.transcription import MelodicNote


def _case(notes: list[MelodicNote], key_label: str = "C:major") -> GroundTruthCase:
    key, mode = key_label.split(":", 1)
    return GroundTruthCase(
        case_id="case",
        instrument="guitar",
        audio_path=Path("x.wav"),
        duration_sec=2.0,
        melodic_notes=tuple(notes),
        key_events=(
            GroundTruthKeyEvent(
                t_on=0.0,
                t_off=2.0,
                key=key,
                mode=mode,
                label=key_label,
                confidence=1.0,
            ),
        ),
    )


def test_normalize_key_label_matches_enharmonic_pitch_classes() -> None:
    from aural_ingest.key_benchmark import normalize_key_label

    eb = normalize_key_label("Eb:major")
    ds = normalize_key_label("D#:maj")

    assert eb.pitch_class == ds.pitch_class == 3
    assert eb.mode == ds.mode == "major"


def test_score_key_case_uses_pitch_class_and_mode(monkeypatch) -> None:
    from aural_ingest import key_benchmark

    case = _case([], key_label="Eb:major")

    monkeypatch.setattr(
        key_benchmark,
        "infer_key_from_notes",
        lambda _notes: {
            "key": "D#",
            "mode": "major",
            "confidence": 0.75,
            "score": 0.8,
            "method": "fake",
        },
    )

    score = key_benchmark.score_key_case(case)

    assert score["correct"] is True
    assert score["pitch_class_match"] is True
    assert score["mode_match"] is True
    assert score["spelling_match"] is False


def test_summarize_key_scores_counts_missing_predictions() -> None:
    from aural_ingest.key_benchmark import summarize_key_scores

    summary = summarize_key_scores(
        [
            {
                "status": "ok",
                "correct": True,
                "pitch_class_match": True,
                "mode_match": True,
                "spelling_match": True,
            },
            {
                "status": "ok",
                "correct": False,
                "pitch_class_match": True,
                "mode_match": False,
                "spelling_match": False,
            },
            {"status": "no_prediction"},
            {"status": "no_reference"},
        ]
    )

    assert summary["cases"] == 4
    assert summary["cases_with_reference"] == 3
    assert summary["cases_scored"] == 2
    assert summary["cases_no_prediction"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["pitch_class_accuracy"] == 1.0
    assert summary["mode_accuracy"] == 0.5


def test_guitarset_key_eval_script_loads_case_id_files(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "evaluate_guitarset_key.py"
    spec = importlib.util.spec_from_file_location("evaluate_guitarset_key", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text_cases = tmp_path / "cases.txt"
    text_cases.write_text("# comment\ncase-a\ncase-b\n", encoding="utf-8")
    assert module._load_case_ids(text_cases) == {"case-a", "case-b"}

    json_cases = tmp_path / "cases.json"
    json_cases.write_text('{"cases": [{"case_id": "case-c"}, "case-d"]}', encoding="utf-8")
    assert module._load_case_ids(json_cases) == {"case-c", "case-d"}


def test_guitarset_key_eval_script_fails_empty_case_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "evaluate_guitarset_key.py"
    spec = importlib.util.spec_from_file_location("evaluate_guitarset_key_empty", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "yield_cases", lambda *_args, **_kwargs: iter(()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_guitarset_key",
            "--corpus-root",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "key_report.json"),
        ],
    )

    with pytest.raises(SystemExit, match="no GuitarSet key cases matched"):
        module.main()
    assert not (tmp_path / "key_report.json").exists()
