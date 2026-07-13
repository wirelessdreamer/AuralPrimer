from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aural_ingest.ground_truth_benchmark import get_drum_algorithm


_ENV_VARS = (
    "AURAL_DRUM_STEMSEP_PYTHON",
    "AURAL_DRUM_STEMSEP_REPO",
    "AURAL_DRUM_STEMSEP_RUNNER",
    "AURAL_DRUM_STEMSEP_CHECKPOINT",
)


def _load_validate_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_drum_stemsep_runtime.py"
    spec = importlib.util.spec_from_file_location("_test_validate_drum_stemsep_runtime", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_msst_runner_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_drum_stemsep_msst.py"
    spec = importlib.util.spec_from_file_location("_test_run_drum_stemsep_msst", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drum_stemsep_imports_without_external_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")

    assert module.ENGINE_ID == "drum_stemsep"
    assert module.transcribe(tmp_path / "drums.wav") == []


def test_drum_stemsep_runtime_status_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    status = module.runtime_status(tmp_path / "drums.wav")
    report = module.validate_runtime(tmp_path / "drums.wav")

    assert status["configured"] is False
    assert status["runner"] is None
    assert status["checkpoint"] is None
    assert any("AURAL_DRUM_STEMSEP_PYTHON" in item for item in status["missing"])
    assert report["ok"] is False
    assert report["status"] == "not_configured"
    assert report["event_count"] == 0


def test_drum_stemsep_missing_runtime_does_not_spawn_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("drum_stemsep should be inert without runtime config")

    monkeypatch.setattr("aural_ingest.algorithms.drum_stemsep.subprocess.run", fail_run)

    assert module.transcribe(tmp_path / "drums.wav") == []


def test_drum_stemsep_rejects_checkpoint_directory_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_runner = tmp_path / "run_drum_stemsep.py"
    fake_runner.write_text("", encoding="utf-8")
    checkpoint_dir = tmp_path / "checkpoint-dir"
    checkpoint_dir.mkdir()

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_RUNNER", str(fake_runner))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CHECKPOINT", str(checkpoint_dir))

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("drum_stemsep should not launch subprocess with a checkpoint directory")

    monkeypatch.setattr("aural_ingest.algorithms.drum_stemsep.subprocess.run", fail_run)

    status = module.runtime_status(tmp_path / "drums.wav")
    assert status["configured"] is False
    assert any("AURAL_DRUM_STEMSEP_CHECKPOINT" in item and "file" in item for item in status["missing"])
    assert module.transcribe(tmp_path / "drums.wav") == []


@pytest.mark.parametrize(
    ("repo_factory", "expected_reason"),
    [
        (lambda tmp_path: tmp_path / "missing-repo", "does not exist"),
        (
            lambda tmp_path: (tmp_path / "repo.txt"),
            "is not a directory",
        ),
    ],
)
def test_drum_stemsep_rejects_invalid_repo_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_factory,
    expected_reason: str,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_runner = tmp_path / "run_drum_stemsep.py"
    fake_runner.write_text("", encoding="utf-8")
    fake_checkpoint = tmp_path / "drumsep.ckpt"
    fake_checkpoint.write_text("", encoding="utf-8")
    fake_repo = repo_factory(tmp_path)
    if fake_repo.suffix:
        fake_repo.write_text("not a repo", encoding="utf-8")

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_REPO", str(fake_repo))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_RUNNER", str(fake_runner))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CHECKPOINT", str(fake_checkpoint))

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("drum_stemsep should not launch subprocess with an invalid repo path")

    monkeypatch.setattr("aural_ingest.algorithms.drum_stemsep.subprocess.run", fail_run)

    status = module.runtime_status(tmp_path / "drums.wav")
    assert status["configured"] is False
    assert status["repo"] == str(fake_repo)
    assert status["repo_is_dir"] is False
    assert any("AURAL_DRUM_STEMSEP_REPO" in item and expected_reason in item for item in status["missing"])
    assert module.transcribe(tmp_path / "drums.wav") == []


def test_drum_stemsep_maps_subprocess_json_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "DrumSepRuntime"
    fake_repo.mkdir()
    fake_runner = fake_repo / "run_drum_stemsep.py"
    fake_runner.write_text("", encoding="utf-8")
    fake_checkpoint = tmp_path / "drumsep.ckpt"
    fake_checkpoint.write_text("", encoding="utf-8")
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_REPO", str(fake_repo))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_RUNNER", str(fake_runner))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CHECKPOINT", str(fake_checkpoint))

    captured: dict[str, object] = {}

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env_pythonpath"] = env.get("PYTHONPATH", "")
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        contract = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        captured["contract"] = contract
        Path(contract["out_json"]).write_text(
            json.dumps(
                {
                    "events": [
                        [0.10, "kick", 0.75, 0.03],
                        {"time": 0.20, "stem": "ride", "velocity": 64},
                        {"onset": 0.30, "label": "tom_high", "velocity": 0.5},
                        {"start": 0.40, "class": 2},
                        {"time": 0.50, "note": 41, "velocity": 140},
                        {"time": "bad", "stem": "snare"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.drum_stemsep.subprocess.run", fake_run)

    events = module.transcribe(wav)

    assert [(event.time, event.note, event.velocity, event.duration) for event in events] == [
        (0.10, 36, 95, 0.03),
        (0.20, 51, 64, 0.05),
        (0.30, 48, 64, 0.05),
        (0.40, 47, 100, 0.05),
        (0.50, 41, 127, 0.05),
    ]
    assert captured["cmd"] == [str(fake_python), str(fake_runner), captured["cmd"][2]]
    assert captured["cwd"] == str(fake_repo)
    assert str(fake_repo) in captured["env_pythonpath"]
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60 * 30
    assert captured["contract"]["engine"] == "drum_stemsep"
    assert captured["contract"]["wav_path"] == str(wav)
    assert captured["contract"]["checkpoint_path"] == str(fake_checkpoint)
    assert captured["contract"]["stems"] == ["kick", "snare", "toms", "hi_hat", "crash", "ride"]


def test_drum_stemsep_validate_runtime_reports_runner_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_runner = tmp_path / "run_drum_stemsep.py"
    fake_runner.write_text("", encoding="utf-8")
    fake_checkpoint = tmp_path / "drumsep.ckpt"
    fake_checkpoint.write_text("", encoding="utf-8")
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_RUNNER", str(fake_runner))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CHECKPOINT", str(fake_checkpoint))

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        contract = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        Path(contract["out_json"]).write_text(
            json.dumps({"events": [{"time": 0.25, "stem": "snare", "velocity": 0.9}]}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="runner ok", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.drum_stemsep.subprocess.run", fake_run)

    report = module.validate_runtime(wav, require_events=True)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["event_count"] == 1
    assert report["returncode"] == 0
    assert report["stdout_tail"] == "runner ok"
    assert report["events"] == [{"time": 0.25, "note": 38, "velocity": 114, "duration": 0.05}]
    assert report["runtime"]["configured"] is True
    assert report["runtime"]["cwd"] == str(tmp_path)


def test_drum_stemsep_subprocess_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.drum_stemsep")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_runner = tmp_path / "run_drum_stemsep.py"
    fake_runner.write_text("", encoding="utf-8")
    fake_checkpoint = tmp_path / "drumsep.ckpt"
    fake_checkpoint.write_text("", encoding="utf-8")

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_RUNNER", str(fake_runner))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CHECKPOINT", str(fake_checkpoint))
    monkeypatch.setattr(
        "aural_ingest.algorithms.drum_stemsep.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="failed"),
    )

    assert module.transcribe(tmp_path / "drums.wav") == []


def test_validate_drum_stemsep_runtime_script_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import drum_stemsep

    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"wav")
    output = tmp_path / "drum_stemsep_report.json"
    monkeypatch.setattr(
        drum_stemsep,
        "validate_runtime",
        lambda wav_path, require_events=False: {
            "ok": bool(require_events),
            "engine": "drum_stemsep",
            "wav_path": str(wav_path),
            "status": "ok",
            "event_count": 1,
        },
    )

    script = _load_validate_script()
    rc = script.main([str(wav), "--output", str(output), "--require-events"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["wav_path"] == str(wav)


def test_validate_drum_stemsep_runtime_script_writes_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import drum_stemsep

    evidence_root = tmp_path / "evidence"
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"wav")
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(
        drum_stemsep,
        "validate_runtime",
        lambda wav_path, require_events=False: {
            "ok": bool(require_events),
            "engine": "drum_stemsep",
            "wav_path": str(wav_path),
            "status": "ok",
            "require_events": require_events,
            "event_count": 1,
        },
    )

    script = _load_validate_script()
    rc = script.main([str(wav), "--write-gate-evidence", "--require-events"])

    assert rc == 0
    output = Path(capsys.readouterr().out.strip())
    assert output.parent == evidence_root / "benchmarks" / "runtime" / "runs"
    assert output.name.endswith("_drum_stemsep_runtime.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["require_events"] is True


def test_validate_drum_stemsep_runtime_script_requires_events_for_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_script()
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))

    with pytest.raises(SystemExit) as exc_info:
        script.main([str(tmp_path / "drums.wav"), "--write-gate-evidence"])

    assert exc_info.value.code == 2
    assert "--write-gate-evidence requires --require-events" in capsys.readouterr().err
    assert not list(evidence_root.glob("benchmarks/runtime/runs/*_drum_stemsep_runtime.json"))


def test_validate_drum_stemsep_runtime_script_rejects_missing_wav(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_script()
    missing = tmp_path / "missing.wav"

    rc = script.main([str(missing)])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"validate_drum_stemsep_runtime: input WAV not found: {missing}"


def test_run_drum_stemsep_msst_writes_adapter_event_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_msst_runner_script()
    repo = tmp_path / "msst"
    repo.mkdir()
    (repo / "inference.py").write_text("# fake\n", encoding="utf-8")
    checkpoint = tmp_path / "drumsep.ckpt"
    checkpoint.write_text("checkpoint", encoding="utf-8")
    config = tmp_path / "config_drumsep_mdx23c.yaml"
    config.write_text("training:\n  instruments: [kick, hh]\n", encoding="utf-8")
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"fake wav; _run_msst is monkeypatched")
    out_json = tmp_path / "events.json"
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "wav_path": str(wav),
                "out_json": str(out_json),
                "checkpoint_path": str(checkpoint),
                "stems": ["kick", "hi_hat"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AURAL_DRUM_STEMSEP_REPO", str(repo))
    monkeypatch.setenv("AURAL_DRUM_STEMSEP_CONFIG", str(config))
    captured: dict[str, object] = {}

    def fake_run_msst(*, repo, wav_path, checkpoint_path, config_path, output_dir):
        captured["repo"] = repo
        captured["wav_path"] = wav_path
        captured["checkpoint_path"] = checkpoint_path
        captured["config_path"] = config_path
        import numpy as np
        import soundfile as sf

        sample_rate = 8000
        audio = np.zeros(sample_rate, dtype=np.float32)
        audio[800:840] = 0.8
        sf.write(output_dir / "kick.wav", audio, sample_rate)
        audio = np.zeros(sample_rate, dtype=np.float32)
        audio[1600:1640] = 0.5
        sf.write(output_dir / "hh.wav", audio, sample_rate)
        return 0

    monkeypatch.setattr(runner, "_run_msst", fake_run_msst)
    monkeypatch.setattr("sys.argv", ["run_drum_stemsep_msst.py", str(request)])

    assert runner.main() == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    stems = {event["stem"] for event in payload["events"]}
    assert {"kick", "hi_hat"}.issubset(stems)
    assert all(isinstance(event["time"], float) for event in payload["events"])
    assert all(1 <= int(event["velocity"]) <= 127 for event in payload["events"])
    assert captured["repo"] == repo.resolve()
    assert captured["wav_path"] == wav.resolve()
    assert captured["checkpoint_path"] == checkpoint.resolve()
    assert captured["config_path"] == config.resolve()


def test_drum_stemsep_is_registered() -> None:
    from aural_ingest.transcription import KNOWN_NEURAL_DRUM_ENGINES, build_default_drum_algorithm_registry

    assert "drum_stemsep" in KNOWN_NEURAL_DRUM_ENGINES
    registry = build_default_drum_algorithm_registry()
    assert "drum_stemsep" in registry
    assert callable(registry["drum_stemsep"])


def test_ground_truth_benchmark_resolves_drum_stemsep_adapter() -> None:
    algorithm = get_drum_algorithm("drum_stemsep")

    assert callable(algorithm)
