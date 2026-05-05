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
