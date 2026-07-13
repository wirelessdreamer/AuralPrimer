from __future__ import annotations

import fnmatch
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"
CHECKLIST_REL_PATH = Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"


def _load_module(rel_path: str):
    path = REPO_ROOT / rel_path
    module_name = "_test_" + rel_path.replace("/", "_").replace("\\", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rel_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


WRITERS: tuple[tuple[str, Callable[[Any], Path], str], ...] = (
    (
        "python/ingest/scripts/validate_adtof_runtime.py",
        lambda module: module._gate_evidence_output_path("adtof"),
        "benchmarks/runtime/runs/*_adtof_runtime.json",
    ),
    (
        "python/ingest/scripts/validate_drum_stemsep_runtime.py",
        lambda module: module._gate_evidence_output_path("drum_stemsep"),
        "benchmarks/runtime/runs/*_drum_stemsep_runtime.json",
    ),
    (
        "python/ingest/scripts/validate_qmul_hr_guitar_runtime.py",
        lambda module: module._gate_evidence_output_path("qmul_hr_guitar"),
        "benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json",
    ),
    (
        "python/ingest/scripts/validate_rmvpe_runtime.py",
        lambda module: module._gate_evidence_output_path("rmvpe"),
        "benchmarks/runtime/runs/*_rmvpe_runtime.json",
    ),
    (
        "python/ingest/scripts/validate_roformer_runtime.py",
        lambda module: module._gate_evidence_output_path("roformer"),
        "benchmarks/runtime/runs/*_roformer_runtime.json",
    ),
    (
        "benchmarks/quality/run_musdb_separation_sdr.py",
        lambda module: module._gate_evidence_output_path("demucs", {}),
        "benchmarks/quality/runs/*_musdb_separation_sdr.json",
    ),
    (
        "benchmarks/vocals/run_mir_st500_vocals.py",
        lambda module: module._gate_evidence_output_path(),
        "benchmarks/vocals/gt_runs/*_mir_st500_vocals.json",
    ),
)


MISSING_INPUT_WRITERS: tuple[tuple[str, list[str], str, str, str], ...] = (
    (
        "python/ingest/scripts/validate_adtof_runtime.py",
        ["missing-drums.wav", "--write-gate-evidence", "--require-events"],
        "benchmarks/runtime/runs/*_adtof_runtime.json",
        "engine",
        "adtof_drums",
    ),
    (
        "python/ingest/scripts/validate_drum_stemsep_runtime.py",
        ["missing-drums.wav", "--write-gate-evidence", "--require-events"],
        "benchmarks/runtime/runs/*_drum_stemsep_runtime.json",
        "engine",
        "drum_stemsep",
    ),
    (
        "python/ingest/scripts/validate_qmul_hr_guitar_runtime.py",
        ["missing-guitar.wav", "--write-gate-evidence", "--require-notes"],
        "benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json",
        "engine",
        "qmul_hr_guitar",
    ),
    (
        "python/ingest/scripts/validate_rmvpe_runtime.py",
        ["missing-vocals.wav", "--write-gate-evidence"],
        "benchmarks/runtime/runs/*_rmvpe_runtime.json",
        "engine",
        "melodic_rmvpe",
    ),
    (
        "python/ingest/scripts/validate_roformer_runtime.py",
        [
            "missing-mix.wav",
            "--write-gate-evidence",
            "--require-role",
            "bass",
            "--require-role",
            "drums",
            "--require-role",
            "other",
            "--require-role",
            "vocals",
        ],
        "benchmarks/runtime/runs/*_roformer_runtime.json",
        "provider",
        "roformer",
    ),
)


@pytest.mark.parametrize(("rel_path", "output_path", "strict_glob"), WRITERS)
def test_model_upgrade_evidence_writers_resolve_runtime_check_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rel_path: str,
    output_path: Callable[[Any], Path],
    strict_glob: str,
) -> None:
    module = _load_module(rel_path)
    env_root = tmp_path / "env-root"
    cwd_root = tmp_path / "cwd-root"
    outside_root = tmp_path / "outside"
    cwd_root.joinpath(CHECKLIST_REL_PATH).parent.mkdir(parents=True)
    cwd_root.joinpath(CHECKLIST_REL_PATH).write_text("# gate evidence\n", encoding="utf-8")
    outside_root.mkdir()

    monkeypatch.setenv(EVIDENCE_ROOT_ENV, str(env_root))
    assert module._model_upgrade_evidence_root() == env_root.resolve()
    env_output = output_path(module)
    assert env_output.is_relative_to(env_root.resolve())
    assert fnmatch.fnmatch(_rel_posix(env_output, env_root), strict_glob)

    monkeypatch.delenv(EVIDENCE_ROOT_ENV, raising=False)
    monkeypatch.chdir(cwd_root)
    assert module._model_upgrade_evidence_root() == cwd_root.resolve()
    cwd_output = output_path(module)
    assert cwd_output.is_relative_to(cwd_root.resolve())
    assert fnmatch.fnmatch(_rel_posix(cwd_output, cwd_root), strict_glob)

    monkeypatch.setenv(EVIDENCE_ROOT_ENV, " ")
    monkeypatch.chdir(outside_root)
    assert module._model_upgrade_evidence_root() == REPO_ROOT
    repo_output = output_path(module)
    assert repo_output.is_relative_to(REPO_ROOT)
    assert fnmatch.fnmatch(_rel_posix(repo_output, REPO_ROOT), strict_glob)


@pytest.mark.parametrize(
    ("rel_path", "args", "strict_glob", "identity_field", "identity"),
    MISSING_INPUT_WRITERS,
)
def test_runtime_validation_writers_emit_failed_gate_evidence_for_missing_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rel_path: str,
    args: list[str],
    strict_glob: str,
    identity_field: str,
    identity: str,
) -> None:
    module = _load_module(rel_path)
    env_root = tmp_path / "env-root"
    monkeypatch.setenv(EVIDENCE_ROOT_ENV, str(env_root))
    monkeypatch.chdir(tmp_path)

    rc = module.main(args)

    assert rc == 2
    matches = list(env_root.glob(strict_glob))
    assert len(matches) == 1
    report = json.loads(matches[0].read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report[identity_field] == identity
    assert report["status"] == "input_missing"
    assert "not found" in report["reason"]
