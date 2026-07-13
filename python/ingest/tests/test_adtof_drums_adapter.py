from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest

from aural_ingest.ground_truth_benchmark import get_drum_algorithm


_ENV_VARS = ("AURAL_ADTOF_PYTHON", "AURAL_ADTOF_REPO")


def _load_validate_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_adtof_runtime.py"
    spec = importlib.util.spec_from_file_location("_test_validate_adtof_runtime", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_fake_adtof_runtime(tmp_path: Path) -> tuple[Path, Path]:
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "ADTOF"
    (fake_repo / "adtof" / "model").mkdir(parents=True)
    (fake_repo / "adtof" / "model" / "model.py").write_text("", encoding="utf-8")
    (fake_repo / "adtof" / "models").mkdir()
    (fake_repo / "adtof" / "models" / "Frame_RNN_adtofAll_0.index").write_text("", encoding="utf-8")
    (fake_repo / "adtof" / "models" / "Frame_RNN_adtofAll_0.data-00000-of-00001").write_text(
        "",
        encoding="utf-8",
    )
    return fake_python, fake_repo


def test_adtof_setup_script_pins_external_git_sources() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "setup_adtof_env.ps1"
    script = script_path.read_text(encoding="utf-8")

    assert '[string]$AdtofCommit = "b3968fb332f69b65ee07c089fc62f436503755db"' in script
    assert '[string]$MadmomCommit = "27f032e8947204902c675e5e341a3faf5dc86dae"' in script
    assert '[string]$TapcorrectCommit = "4f2d21e73fb0137119a4136513c42936b322fc0b"' in script
    assert "git -C $adtofRepo checkout $AdtofCommit" in script
    assert (
        "tapcorrect @ git+https://github.com/MZehren/tapcorrect@$TapcorrectCommit"
        "#subdirectory=python&egg=tapcorrect"
    ) in script
    assert "madmom @ git+https://github.com/CPJKU/madmom@$MadmomCommit" in script
    assert "git+https://github.com/MZehren/tapcorrect#subdirectory=python&egg=tapcorrect" not in script


def test_adtof_drum_adapter_imports_without_external_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")

    assert module.ENGINE_ID == "adtof_drums"
    assert module.transcribe(tmp_path / "drums.wav") == []


def test_adtof_runtime_status_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    status = module.runtime_status()
    report = module.validate_runtime(tmp_path / "drums.wav")

    assert status["configured"] is False
    assert status["runner"] is not None
    assert any("AURAL_ADTOF_PYTHON" in item for item in status["missing"])
    assert any("AURAL_ADTOF_REPO" in item for item in status["missing"])
    assert report["ok"] is False
    assert report["status"] == "not_configured"
    assert report["event_count"] == 0


def test_adtof_missing_runtime_does_not_spawn_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("adtof_drums should be inert without runtime config")

    monkeypatch.setattr("aural_ingest.algorithms.adtof_drums.subprocess.run", fail_run)

    assert module.transcribe(tmp_path / "drums.wav") == []


def test_adtof_drum_adapter_maps_subprocess_json_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")
    fake_python, fake_repo = _make_fake_adtof_runtime(tmp_path)
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"not real audio; subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_ADTOF_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_ADTOF_REPO", str(fake_repo))

    captured: dict[str, object] = {}

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env_pythonpath"] = env.get("PYTHONPATH", "")
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        contract = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        Path(contract["out_json"]).write_text(
            json.dumps(
                {
                    "events": [
                        [0.25, 35, 110],
                        {"time": 0.50, "label": "SD", "velocity": 0.5, "duration": 0.075},
                        {"onset": 0.75, "class": "CY+RD", "velocity": 140},
                        {"start": 1.00, "pitch": 47},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.adtof_drums.subprocess.run", fake_run)

    events = module.transcribe(wav)

    assert [(event.time, event.note, event.velocity, event.duration) for event in events] == [
        (0.25, 36, 110, 0.05),
        (0.50, 38, 64, 0.075),
        (0.75, 49, 127, 0.05),
        (1.00, 47, 100, 0.05),
    ]
    assert captured["cmd"][0] == str(fake_python)
    assert captured["cwd"] == str(fake_repo)
    assert str(fake_repo) in captured["env_pythonpath"]
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60 * 20


def test_adtof_validate_runtime_reports_runner_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")
    fake_python, fake_repo = _make_fake_adtof_runtime(tmp_path)
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"not real audio; subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_ADTOF_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_ADTOF_REPO", str(fake_repo))

    def fake_run(cmd, cwd, env, capture_output, text, timeout):
        contract = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        Path(contract["out_json"]).write_text(
            json.dumps({"events": [{"time": 0.25, "label": "SD", "velocity": 0.9}]}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="runner ok", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.adtof_drums.subprocess.run", fake_run)

    report = module.validate_runtime(wav, require_events=True)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["event_count"] == 1
    assert report["returncode"] == 0
    assert report["stdout_tail"] == "runner ok"
    assert report["events"] == [{"time": 0.25, "note": 38, "velocity": 114, "duration": 0.05}]
    assert report["runtime"]["configured"] is True
    assert report["runtime"]["weight_index_exists"] is True


def test_adtof_drum_adapter_subprocess_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.adtof_drums")
    fake_python, fake_repo = _make_fake_adtof_runtime(tmp_path)

    monkeypatch.setenv("AURAL_ADTOF_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_ADTOF_REPO", str(fake_repo))
    monkeypatch.setattr(
        "aural_ingest.algorithms.adtof_drums.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="failed"),
    )

    assert module.transcribe(tmp_path / "drums.wav") == []


def test_validate_adtof_runtime_script_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import adtof_drums

    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"wav")
    output = tmp_path / "adtof_report.json"
    monkeypatch.setattr(
        adtof_drums,
        "validate_runtime",
        lambda wav_path, require_events=False: {
            "ok": bool(require_events),
            "engine": "adtof_drums",
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


def test_validate_adtof_runtime_script_writes_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import adtof_drums

    evidence_root = tmp_path / "evidence"
    wav = tmp_path / "drums.wav"
    wav.write_bytes(b"wav")
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(
        adtof_drums,
        "validate_runtime",
        lambda wav_path, require_events=False: {
            "ok": bool(require_events),
            "engine": "adtof_drums",
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
    assert output.name.endswith("_adtof_runtime.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["require_events"] is True


def test_validate_adtof_runtime_script_requires_events_for_gate_evidence(
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
    assert not list(evidence_root.glob("benchmarks/runtime/runs/*_adtof_runtime.json"))


def test_validate_adtof_runtime_script_rejects_missing_wav(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_script()
    missing = tmp_path / "missing.wav"

    rc = script.main([str(missing)])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"validate_adtof_runtime: input WAV not found: {missing}"


def test_adtof_runner_contract_invokes_model_and_writes_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_adtof_adapter.py"
    spec = importlib.util.spec_from_file_location("run_adtof_adapter_test", script_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    repo = tmp_path / "ADTOF"
    wav = tmp_path / "drums.wav"
    out_json = tmp_path / "out" / "events.json"
    repo.mkdir()
    wav.write_bytes(b"fake wav")
    captured: dict[str, object] = {}

    class FakeOfficialModel:
        weightLoadedFlag = True

        def predictFolder(self, wav_path, out_dir, writeMidi, **hparams):
            captured["wav_path"] = wav_path
            captured["write_midi"] = writeMidi
            captured["hparams"] = hparams
            Path(out_dir, "prediction.mid").write_bytes(b"MThd")

    class FakeModelFactory:
        @staticmethod
        def modelFactory(**kwargs):
            captured["factory_kwargs"] = kwargs
            return FakeOfficialModel(), {"peakThreshold": 0.5, "extra": "kept"}

    model_mod = types.ModuleType("adtof.model.model")
    model_mod.Model = FakeModelFactory
    monkeypatch.setitem(sys.modules, "adtof", types.ModuleType("adtof"))
    monkeypatch.setitem(sys.modules, "adtof.model", types.ModuleType("adtof.model"))
    monkeypatch.setitem(sys.modules, "adtof.model.model", model_mod)

    pretty_midi = types.ModuleType("pretty_midi")

    class FakePrettyMIDI:
        def __init__(self, midi_path: str) -> None:
            captured["midi_path"] = midi_path
            self.instruments = [
                SimpleNamespace(
                    notes=[
                        SimpleNamespace(start=0.25, end=0.33, pitch=36, velocity=100),
                        SimpleNamespace(start=0.10, end=0.16, pitch=38, velocity=0),
                    ]
                )
            ]

    pretty_midi.PrettyMIDI = FakePrettyMIDI
    monkeypatch.setitem(sys.modules, "pretty_midi", pretty_midi)

    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "repo_path": str(repo),
                "wav_path": str(wav),
                "out_json": str(out_json),
            }
        ),
        encoding="utf-8",
    )

    assert runner.main([str(contract)]) == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert [event[:3] for event in payload["events"]] == [[0.1, 38, 100], [0.25, 36, 100]]
    assert payload["events"][0][3] == pytest.approx(0.06)
    assert payload["events"][1][3] == pytest.approx(0.08)
    assert captured["factory_kwargs"] == {
        "modelName": "Frame_RNN",
        "scenario": "adtofAll",
        "fold": 0,
    }
    assert captured["wav_path"] == str(wav.resolve())
    assert captured["write_midi"] is True
    assert captured["hparams"] == {"peakThreshold": 0.5, "extra": "kept"}


def test_adtof_drum_adapter_is_registered() -> None:
    from aural_ingest.transcription import KNOWN_NEURAL_DRUM_ENGINES, build_default_drum_algorithm_registry

    assert "adtof_drums" in KNOWN_NEURAL_DRUM_ENGINES
    registry = build_default_drum_algorithm_registry()
    assert "adtof_drums" in registry
    assert callable(registry["adtof_drums"])


def test_ground_truth_benchmark_resolves_adtof_drum_adapter() -> None:
    algorithm = get_drum_algorithm("adtof_drums")

    assert callable(algorithm)
