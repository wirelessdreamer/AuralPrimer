from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_threshold_checker() -> ModuleType:
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "check-benchmark-thresholds.py"
    spec = importlib.util.spec_from_file_location("check_benchmark_thresholds", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hardware_threshold_checker_accepts_minimum_modern(tmp_path: Path) -> None:
    checker = _load_threshold_checker()
    profile = tmp_path / "hardware.json"
    profile.write_text(
        json.dumps(
            {
                "platform": {"arch": "x64"},
                "cpu": {"logical_count": 8},
                "memory": {"total_gb": 16.0},
            }
        ),
        encoding="utf-8",
    )

    violations = checker._check_hardware(
        [profile],
        {
            "target_profile": "minimum_modern",
            "profiles": {
                "minimum_modern": {
                    "min_logical_cpus": 8,
                    "min_memory_gb": 16,
                    "allowed_arch": ["x64", "arm64"],
                }
            },
        },
    )

    assert violations == []


def test_hardware_threshold_checker_reports_below_baseline(tmp_path: Path) -> None:
    checker = _load_threshold_checker()
    profile = tmp_path / "hardware.json"
    profile.write_text(
        json.dumps(
            {
                "platform": {"arch": "x86"},
                "cpu": {"logical_count": 4},
                "memory": {"total_gb": 8.0},
            }
        ),
        encoding="utf-8",
    )

    violations = checker._check_hardware(
        [profile],
        {
            "target_profile": "minimum_modern",
            "profiles": {
                "minimum_modern": {
                    "min_logical_cpus": 8,
                    "min_memory_gb": 16,
                    "allowed_arch": ["x64", "arm64"],
                }
            },
        },
    )

    assert len(violations) == 3
    assert "logical CPUs" in violations[0]
    assert "GB RAM" in violations[1]
    assert "outside" in violations[2]


def test_model_upgrade_decision_checker_accepts_matching_report(tmp_path: Path) -> None:
    checker = _load_threshold_checker()
    report = tmp_path / "model-report.json"
    report.write_text(
        json.dumps(
            {
                "dataset": "example",
                "summary": {
                    "per_algorithm": {
                        "candidate": {
                            "cases_ok": 10,
                            "cases_err": 0,
                            "f1": 0.72,
                            "per_class": {
                                "kick": {"f1": 0.8},
                                "snare": {"f1": 0.6},
                            },
                        },
                        "baseline": {"f1": 0.41},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    violations = checker._check_model_upgrade_decisions(
        {
            "decisions": [
                {
                    "id": "example_decision",
                    "report": str(report),
                    "checks": [
                        {"id": "dataset", "path": "dataset", "equals": "example"},
                        {"id": "cases_ok", "path": "summary.per_algorithm.candidate.cases_ok", "min": 10},
                        {"id": "cases_err", "path": "summary.per_algorithm.candidate.cases_err", "equals": 0},
                        {
                            "id": "beats_baseline",
                            "path": "summary.per_algorithm.candidate.f1",
                            "min_delta_over_path": 0.25,
                            "over_path": "summary.per_algorithm.baseline.f1",
                        },
                        {
                            "id": "macro_cap",
                            "average_of": [
                                "summary.per_algorithm.candidate.per_class.kick.f1",
                                "summary.per_algorithm.candidate.per_class.snare.f1",
                            ],
                            "max": 0.71,
                        },
                    ],
                }
            ]
        }
    )

    assert violations == []


def test_model_upgrade_decision_checker_reports_contradictions(tmp_path: Path) -> None:
    checker = _load_threshold_checker()
    report = tmp_path / "model-report.json"
    report.write_text(
        json.dumps(
            {
                "dataset": "wrong",
                "summary": {
                    "per_algorithm": {
                        "candidate": {"cases_ok": 9, "cases_err": 1, "f1": 0.50},
                        "baseline": {"f1": 0.49},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    violations = checker._check_model_upgrade_decisions(
        {
            "decisions": [
                {
                    "id": "example_decision",
                    "report": str(report),
                    "checks": [
                        {"id": "dataset", "path": "dataset", "equals": "example"},
                        {"id": "cases_ok", "path": "summary.per_algorithm.candidate.cases_ok", "min": 10},
                        {"id": "cases_err", "path": "summary.per_algorithm.candidate.cases_err", "equals": 0},
                        {
                            "id": "beats_baseline",
                            "path": "summary.per_algorithm.candidate.f1",
                            "min_delta_over_path": 0.25,
                            "over_path": "summary.per_algorithm.baseline.f1",
                        },
                    ],
                }
            ]
        }
    )

    assert len(violations) == 4
    assert all("example_decision:" in violation for violation in violations)
    assert "expected 'example'" in violations[0]
    assert "expected min" in violations[1]
    assert "expected 0" in violations[2]
    assert "expected delta" in violations[3]
