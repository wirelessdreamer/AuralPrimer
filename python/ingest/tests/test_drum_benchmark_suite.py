import json
from pathlib import Path


def _fake_case_result(
    algorithm: str,
    *,
    overall_f1: float,
    kick_f1: float,
    snare_f1: float,
    hi_hat_f1: float,
    timing_mae_ms: float,
    snare_confusions: list[dict[str, object]] | None = None,
    hi_hat_confusions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "algorithm": algorithm,
        "raw_predicted_count": 4,
        "ignored_predicted_events": 0,
        "reference_count": 4,
        "predicted_count": 4,
        "tolerance_ms": 60.0,
        "overall": {
            "reference_count": 4,
            "predicted_count": 4,
            "tp": 3,
            "fp": 1,
            "fn": 1,
            "precision": overall_f1,
            "recall": overall_f1,
            "f1": overall_f1,
            "timing_mae_ms": timing_mae_ms,
        },
        "per_class": {
            "kick": {
                "reference_count": 1,
                "predicted_count": 1,
                "tp": 1 if kick_f1 > 0 else 0,
                "fp": 0 if kick_f1 > 0 else 1,
                "fn": 0 if kick_f1 > 0 else 1,
                "precision": kick_f1,
                "recall": kick_f1,
                "f1": kick_f1,
                "timing_mae_ms": timing_mae_ms,
            },
            "snare": {
                "reference_count": 1,
                "predicted_count": 1,
                "tp": 1 if snare_f1 > 0 else 0,
                "fp": 0 if snare_f1 > 0 else 1,
                "fn": 0 if snare_f1 > 0 else 1,
                "precision": snare_f1,
                "recall": snare_f1,
                "f1": snare_f1,
                "timing_mae_ms": timing_mae_ms,
            },
            "hi_hat": {
                "reference_count": 1,
                "predicted_count": 1,
                "tp": 1 if hi_hat_f1 > 0 else 0,
                "fp": 0 if hi_hat_f1 > 0 else 1,
                "fn": 0 if hi_hat_f1 > 0 else 1,
                "precision": hi_hat_f1,
                "recall": hi_hat_f1,
                "f1": hi_hat_f1,
                "timing_mae_ms": timing_mae_ms,
            }
        },
        "confusions": [*(snare_confusions or []), *(hi_hat_confusions or [])],
    }


def test_load_suite_cases_uses_rendered_fixture_set() -> None:
    from aural_ingest.drum_benchmark_suite import load_suite_cases

    root = Path(__file__).resolve().parents[3]
    fixture_dir = root / "assets" / "test_fixtures" / "drum_benchmark_midis"
    cases, manifest, warnings = load_suite_cases(fixture_dir)

    assert manifest["format"] == "auralprimer_drum_benchmark_manifest_v1"
    assert len(cases) == 10
    assert not warnings
    assert all(case.wav_path.is_file() for case in cases)
    assert all(case.reference_path.is_file() for case in cases)


def test_write_suite_outputs_emits_required_static_artifacts(tmp_path: Path) -> None:
    from aural_ingest.drum_benchmark_suite import REQUIRED_VISUALIZATION_FILES, write_suite_outputs

    payload = {
        "suite_version": "1.0.0",
        "generated_at_utc": "2026-03-10T10:00:00Z",
        "fixtures_dir": "fixtures",
        "algorithms": ["combined_filter", "adaptive_beat_grid"],
        "tolerance_ms": 60.0,
        "class_order": [],
        "manifest_format": "auralprimer_drum_benchmark_manifest_v1",
        "warnings": [],
        "cases": [
            {
                "case_id": "01_case",
                "title": "Case One",
                "bpm": 120.0,
                "tags": ["rock"],
                "focus": ["snare"],
                "summary": "First case",
                "wav_path": "01_case.wav",
                "reference_path": "01_case.mid",
                "reference_count": 4,
                "reference_meta": {"format": "midi"},
                "results": [
                    _fake_case_result(
                        "combined_filter",
                        overall_f1=0.82,
                        kick_f1=0.85,
                        snare_f1=0.74,
                        hi_hat_f1=0.41,
                        timing_mae_ms=18.0,
                    ),
                    _fake_case_result(
                        "adaptive_beat_grid",
                        overall_f1=0.61,
                        kick_f1=0.66,
                        snare_f1=0.0,
                        hi_hat_f1=0.12,
                        timing_mae_ms=29.0,
                        snare_confusions=[
                            {
                                "reference_class": "snare",
                                "predicted_class": "tom1",
                                "count": 2,
                            }
                        ],
                        hi_hat_confusions=[
                            {
                                "reference_class": "hi_hat",
                                "predicted_class": "snare",
                                "count": 3,
                            }
                        ],
                    ),
                ],
            },
            {
                "case_id": "02_case",
                "title": "Case Two",
                "bpm": 150.0,
                "tags": ["metal"],
                "focus": ["double bass"],
                "summary": "Second case",
                "wav_path": "02_case.wav",
                "reference_path": "02_case.mid",
                "reference_count": 4,
                "reference_meta": {"format": "midi"},
                "results": [
                    _fake_case_result(
                        "combined_filter",
                        overall_f1=0.88,
                        kick_f1=0.91,
                        snare_f1=0.79,
                        hi_hat_f1=0.37,
                        timing_mae_ms=16.0,
                    ),
                    _fake_case_result(
                        "adaptive_beat_grid",
                        overall_f1=0.58,
                        kick_f1=0.64,
                        snare_f1=0.22,
                        hi_hat_f1=0.09,
                        timing_mae_ms=33.0,
                    ),
                ],
            },
        ],
    }

    out_dir = write_suite_outputs(payload, output_root=tmp_path / "bench" / "runs", label="smoke")

    for name in REQUIRED_VISUALIZATION_FILES:
        assert (out_dir / name).is_file()

    summary = json.loads((out_dir / "summary.json").read_text("utf-8"))
    assert summary["summary"]["algorithm_summaries"][0]["algorithm"] == "combined_filter"
    assert summary["summary"]["algorithm_summaries"][0]["mean_kick_f1"] == 0.88
    assert "snare_confusion_heatmap.svg" in (out_dir / "report.md").read_text("utf-8")
    assert "hi_hat_confusion_heatmap.svg" in (out_dir / "report.md").read_text("utf-8")
