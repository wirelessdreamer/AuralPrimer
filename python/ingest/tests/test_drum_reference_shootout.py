import json
from pathlib import Path
import pytest


def _fake_case_result(
    algorithm: str,
    *,
    overall_f1: float,
    kick_f1: float,
    snare_f1: float,
    hi_hat_f1: float,
    timing_mae_ms: float,
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
                "tp": 1,
                "fp": 0,
                "fn": 0,
                "precision": kick_f1,
                "recall": kick_f1,
                "f1": kick_f1,
                "timing_mae_ms": timing_mae_ms,
            },
            "snare": {
                "reference_count": 1,
                "predicted_count": 1,
                "tp": 1,
                "fp": 0,
                "fn": 0,
                "precision": snare_f1,
                "recall": snare_f1,
                "f1": snare_f1,
                "timing_mae_ms": timing_mae_ms,
            },
            "hi_hat": {
                "reference_count": 1,
                "predicted_count": 1,
                "tp": 1,
                "fp": 0,
                "fn": 0,
                "precision": hi_hat_f1,
                "recall": hi_hat_f1,
                "f1": hi_hat_f1,
                "timing_mae_ms": timing_mae_ms,
            },
        },
        "confusions": [],
    }


def _fake_corpus_payload(corpus_id: str, title: str, trust: str, algo_a: dict[str, float], algo_b: dict[str, float]) -> dict[str, object]:
    return {
        "suite_version": "1.0.0",
        "generated_at_utc": "2026-03-24T10:00:00Z",
        "fixtures_dir": "fixtures",
        "algorithms": ["algo_a", "algo_b"],
        "tolerance_ms": 60.0,
        "class_order": [],
        "manifest_format": "auralprimer_manual_corpus_v1",
        "warnings": [],
        "corpus": {
            "corpus_id": corpus_id,
            "title": title,
            "reference_trust": trust,
            "description": "",
        },
        "cases": [
            {
                "case_id": f"{corpus_id}_case",
                "title": title,
                "bpm": 120.0,
                "tags": [corpus_id],
                "focus": [],
                "summary": "",
                "wav_path": f"{corpus_id}.wav",
                "reference_path": f"{corpus_id}.mid",
                "reference_count": 4,
                "reference_meta": {"format": "midi"},
                "results": [
                    _fake_case_result("algo_a", **algo_a),
                    _fake_case_result("algo_b", **algo_b),
                ],
            }
        ],
    }


def test_build_reference_shootout_payload_computes_ranks_and_deltas() -> None:
    from aural_ingest.drum_reference_shootout import build_reference_shootout_payload

    trusted = _fake_corpus_payload(
        "synthetic_trusted",
        "Trusted",
        "trusted",
        {"overall_f1": 0.9, "kick_f1": 0.9, "snare_f1": 0.8, "hi_hat_f1": 0.7, "timing_mae_ms": 12.0},
        {"overall_f1": 0.7, "kick_f1": 0.8, "snare_f1": 0.6, "hi_hat_f1": 0.5, "timing_mae_ms": 15.0},
    )
    suspect = _fake_corpus_payload(
        "suno_suspect",
        "Suspect",
        "suspect",
        {"overall_f1": 0.4, "kick_f1": 0.5, "snare_f1": 0.3, "hi_hat_f1": 0.2, "timing_mae_ms": 25.0},
        {"overall_f1": 0.6, "kick_f1": 0.6, "snare_f1": 0.5, "hi_hat_f1": 0.4, "timing_mae_ms": 20.0},
    )

    payload = build_reference_shootout_payload(trusted, suspect)

    rows = {row["algorithm"]: row for row in payload["comparison"]["rows"]}
    assert rows["algo_a"]["trusted_rank"] == 1
    assert rows["algo_a"]["suspect_rank"] == 2
    assert rows["algo_a"]["rank_shift"] == 1
    assert rows["algo_a"]["delta_suspect_minus_trusted"]["mean_overall_f1"] == -0.5
    assert rows["algo_a"]["delta_suspect_minus_trusted"]["mean_timing_mae_ms"] == 13.0

    assert rows["algo_b"]["trusted_rank"] == 2
    assert rows["algo_b"]["suspect_rank"] == 1
    assert rows["algo_b"]["rank_shift"] == -1
    assert rows["algo_b"]["delta_suspect_minus_trusted"]["mean_snare_f1"] == pytest.approx(-0.1)


def test_write_reference_shootout_outputs_emits_required_files(tmp_path: Path) -> None:
    from aural_ingest.drum_reference_shootout import (
        REQUIRED_OUTPUT_FILES,
        build_reference_shootout_payload,
        write_reference_shootout_outputs,
    )

    trusted = _fake_corpus_payload(
        "synthetic_trusted",
        "Trusted",
        "trusted",
        {"overall_f1": 0.9, "kick_f1": 0.9, "snare_f1": 0.8, "hi_hat_f1": 0.7, "timing_mae_ms": 12.0},
        {"overall_f1": 0.7, "kick_f1": 0.8, "snare_f1": 0.6, "hi_hat_f1": 0.5, "timing_mae_ms": 15.0},
    )
    suspect = _fake_corpus_payload(
        "suno_suspect",
        "Suspect",
        "suspect",
        {"overall_f1": 0.4, "kick_f1": 0.5, "snare_f1": 0.3, "hi_hat_f1": 0.2, "timing_mae_ms": 25.0},
        {"overall_f1": 0.6, "kick_f1": 0.6, "snare_f1": 0.5, "hi_hat_f1": 0.4, "timing_mae_ms": 20.0},
    )

    payload = build_reference_shootout_payload(trusted, suspect)
    out_dir = write_reference_shootout_outputs(payload, output_root=tmp_path, label="smoke")

    for name in REQUIRED_OUTPUT_FILES:
        assert (out_dir / name).is_file()

    summary = json.loads((out_dir / "summary.json").read_text("utf-8"))
    assert summary["comparison"]["rows"][0]["algorithm"] == "algo_a"
    assert "Delta (Suspect - Trusted)" in (out_dir / "report.md").read_text("utf-8")
