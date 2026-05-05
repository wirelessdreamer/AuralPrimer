from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable


def _load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency-free CI path
            raise SystemExit(
                f"{path} is not JSON-compatible YAML and PyYAML is unavailable: {exc}"
            ) from exc
        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}


def _expand_paths(patterns: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            out.extend(Path(match) for match in matches)
        else:
            out.append(Path(pattern))
    return [path for path in out if path.is_file()]


def _warning(message: str) -> None:
    print(f"::warning::{message}")


def _benchmarks_from_vitest_json(path: Path) -> Iterable[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for file_payload in data.get("files", []):
        for group in file_payload.get("groups", []):
            for benchmark in group.get("benchmarks", []):
                yield benchmark


def _benchmarks_from_pytest_json(path: Path) -> Iterable[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for benchmark in data.get("benchmarks", []):
        stats = benchmark.get("stats", {})
        name = benchmark.get("name") or benchmark.get("fullname")
        mean_sec = stats.get("mean")
        if name and mean_sec is not None:
            yield {"name": name, "mean": float(mean_sec) * 1000.0}


def _check_runtime_json(
    *,
    kind: str,
    paths: Iterable[Path],
    thresholds: dict[str, Any],
    pytest_schema: bool = False,
) -> list[str]:
    violations: list[str] = []
    max_mean = thresholds.get("max_mean_ms_by_benchmark", {}) or {}
    for path in paths:
        benchmarks = (
            _benchmarks_from_pytest_json(path) if pytest_schema else _benchmarks_from_vitest_json(path)
        )
        for benchmark in benchmarks:
            name = str(benchmark.get("name", ""))
            if name not in max_mean:
                continue
            mean_ms = float(benchmark.get("mean", benchmark.get("period", 0.0)) or 0.0)
            limit_ms = float(max_mean[name])
            if mean_ms > limit_ms:
                violations.append(
                    f"{kind} benchmark '{name}' mean {mean_ms:.3f}ms exceeds {limit_ms:.3f}ms in {path}"
                )
    return violations


def _quality_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    rows = summary.get("rows", [])
    return rows if isinstance(rows, list) else []


def _quality_candidates(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    candidates = summary.get("promotion_candidates", [])
    return candidates if isinstance(candidates, list) else []


def _check_quality(paths: Iterable[Path], thresholds: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    min_f1_by_role = thresholds.get("min_mean_f1_by_role", {}) or {}
    max_errors = thresholds.get("max_error_count")
    max_sync = thresholds.get("max_sync_quarantine_count")
    max_flagged_rate = thresholds.get("max_flagged_case_rate")

    for path in paths:
        rows = _quality_rows(path)
        candidates = _quality_candidates(path)
        if not rows and not candidates:
            _warning(f"quality summary has no rows or promotion candidates: {path}")
            continue

        error_count = 0
        sync_count = 0
        flagged_count = 0
        for row in rows:
            flags = row.get("gameplay_flags", {}) if isinstance(row, dict) else {}
            if bool(flags.get("error")):
                error_count += 1
            if bool(flags.get("sync_quarantine")):
                sync_count += 1
            if any(bool(value) for value in flags.values()):
                flagged_count += 1
        flagged_rate = flagged_count / max(1, len(rows))

        if max_errors is not None and error_count > int(max_errors):
            violations.append(f"{path}: {error_count} errored quality rows exceeds {max_errors}")
        if max_sync is not None and sync_count > int(max_sync):
            violations.append(f"{path}: {sync_count} sync-quarantined rows exceeds {max_sync}")
        if max_flagged_rate is not None and flagged_rate > float(max_flagged_rate):
            violations.append(
                f"{path}: flagged row rate {flagged_rate:.3f} exceeds {float(max_flagged_rate):.3f}"
            )

        for candidate in candidates:
            role = str(candidate.get("role", ""))
            if role not in min_f1_by_role:
                continue
            mean_f1 = candidate.get("mean_f1")
            if mean_f1 is not None and float(mean_f1) < float(min_f1_by_role[role]):
                violations.append(
                    f"{path}: role {role} winner mean F1 {float(mean_f1):.3f} below {float(min_f1_by_role[role]):.3f}"
                )
    return violations


def _check_hardware(paths: Iterable[Path], thresholds: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    target_profile_id = str(thresholds.get("target_profile", "minimum_modern"))
    profiles = thresholds.get("profiles", {}) or {}
    target_profile = profiles.get(target_profile_id, {}) or {}
    min_logical_cpus = int(target_profile.get("min_logical_cpus", 0) or 0)
    min_memory_gb = float(target_profile.get("min_memory_gb", 0.0) or 0.0)
    allowed_arch = {str(item).lower() for item in target_profile.get("allowed_arch", []) or []}

    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        cpu = data.get("cpu", {}) if isinstance(data, dict) else {}
        memory = data.get("memory", {}) if isinstance(data, dict) else {}
        platform_payload = data.get("platform", {}) if isinstance(data, dict) else {}

        logical_count = int(cpu.get("logical_count", 0) or 0)
        memory_gb = float(memory.get("total_gb", 0.0) or 0.0)
        arch = str(platform_payload.get("arch", "")).lower()

        if min_logical_cpus and logical_count < min_logical_cpus:
            violations.append(
                f"{path}: hardware profile has {logical_count} logical CPUs; "
                f"{target_profile_id} requires >= {min_logical_cpus}"
            )
        if min_memory_gb and memory_gb < min_memory_gb:
            violations.append(
                f"{path}: hardware profile has {memory_gb:.3f} GB RAM; "
                f"{target_profile_id} requires >= {min_memory_gb:.3f} GB"
            )
        if allowed_arch and arch not in allowed_arch:
            violations.append(
                f"{path}: hardware profile arch {arch!r} is outside {sorted(allowed_arch)}"
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(prog="check-benchmark-thresholds")
    parser.add_argument("--thresholds", default="benchmarks/thresholds.yml")
    parser.add_argument("--quality-summary", action="append", default=[])
    parser.add_argument("--frontend-json", action="append", default=[])
    parser.add_argument("--python-json", action="append", default=[])
    parser.add_argument("--hardware-json", action="append", default=[])
    parser.add_argument("--mode", choices=["warn", "strict"])
    args = parser.parse_args()

    config = _load_config(Path(args.thresholds))
    mode = args.mode or os.environ.get("AURAL_BENCHMARK_GATE_MODE") or str(config.get("mode", "warn"))

    violations: list[str] = []
    quality_paths = _expand_paths(args.quality_summary)
    frontend_paths = _expand_paths(args.frontend_json)
    python_paths = _expand_paths(args.python_json)
    hardware_paths = _expand_paths(args.hardware_json)

    if args.quality_summary and not quality_paths:
        _warning("no quality summary files matched")
    if args.frontend_json and not frontend_paths:
        _warning("no frontend benchmark JSON files matched")
    if args.python_json and not python_paths:
        _warning("no Python benchmark JSON files matched")
    if args.hardware_json and not hardware_paths:
        _warning("no hardware profile JSON files matched")

    violations.extend(_check_quality(quality_paths, config.get("quality", {}) or {}))
    violations.extend(_check_hardware(hardware_paths, config.get("hardware", {}) or {}))
    violations.extend(
        _check_runtime_json(
            kind="frontend",
            paths=frontend_paths,
            thresholds=config.get("frontend", {}) or {},
        )
    )
    violations.extend(
        _check_runtime_json(
            kind="python",
            paths=python_paths,
            thresholds=config.get("python", {}) or {},
            pytest_schema=True,
        )
    )

    for violation in violations:
        if mode == "strict":
            print(f"::error::{violation}")
        else:
            _warning(violation)

    if violations and mode == "strict":
        return 1
    print(
        json.dumps(
            {
                "mode": mode,
                "violation_count": len(violations),
                "quality_files": [str(path) for path in quality_paths],
                "frontend_files": [str(path) for path in frontend_paths],
                "python_files": [str(path) for path in python_paths],
                "hardware_files": [str(path) for path in hardware_paths],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
