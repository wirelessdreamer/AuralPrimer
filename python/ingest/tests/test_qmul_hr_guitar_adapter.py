from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aural_ingest.ground_truth_benchmark import get_melodic_algorithm
from aural_ingest.transcription import MelodicNote


def _load_validate_qmul_runtime_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_qmul_hr_guitar_runtime.py"
    spec = importlib.util.spec_from_file_location("_test_validate_qmul_hr_guitar_runtime", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qmul_hr_guitar_imports_without_external_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AURAL_QMUL_GUITAR_PYTHON", raising=False)
    monkeypatch.delenv("AURAL_QMUL_GUITAR_REPO", raising=False)
    monkeypatch.delenv("AURAL_QMUL_GUITAR_COMMAND", raising=False)

    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")

    assert module.ENGINE_ID == "qmul_hr_guitar"
    status = module.runtime_status()
    assert status["configured"] is False
    assert "AURAL_QMUL_GUITAR_PYTHON is unset" in status["missing"]
    report = module.validate_runtime(tmp_path / "guitar.wav")
    assert report["ok"] is False
    assert report["status"] == "not_configured"
    assert report["note_count"] == 0
    assert module.transcribe(tmp_path / "guitar.wav", instrument="lead_guitar") == []


def test_qmul_hr_guitar_command_json_output_to_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "qmul"
    fake_repo.mkdir()
    wav = tmp_path / "lead.wav"
    wav.write_bytes(b"not real audio; subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_QMUL_GUITAR_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_REPO", str(fake_repo))
    monkeypatch.setenv(
        "AURAL_QMUL_GUITAR_COMMAND",
        "{python_q} -m qmul.predict --audio {wav_path_q} --json {out_json_q}",
    )
    monkeypatch.setenv("AURAL_QMUL_GUITAR_TIMEOUT_SEC", "12")

    captured: dict[str, object] = {}

    def fake_run(command, cwd, env, capture_output, text, shell, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env_pythonpath"] = env.get("PYTHONPATH", "")
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["shell"] = shell
        captured["timeout"] = timeout
        out_json = Path(command.split("--json ", 1)[1].strip().strip('"'))
        out_json.write_text(
            json.dumps(
                {
                    "notes": [
                        {
                            "onset": 0.50,
                            "offset": 0.75,
                            "midi_note": 64,
                            "velocity": 0.5,
                            "string": 1,
                            "fret": 5,
                        },
                        {"t_on": 0.10, "duration": 0.25, "pitch": 60, "v": 90, "s": 2, "f": 8},
                        {"onset": 0.80, "offset": 0.95, "pitch": 65, "string": 99, "fret": 7},
                        {"onset": 1.00, "offset": 0.90, "pitch": 67},
                        {"onset": 1.10, "offset": 1.20, "pitch": 200},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.qmul_hr_guitar.subprocess.run", fake_run)

    notes = module.transcribe(wav, instrument="rhythm_guitar")

    got = [
        (
            note.t_on,
            note.t_off,
            note.pitch,
            note.velocity,
            note.instrument,
            note.string,
            note.fret,
        )
        for note in notes
    ]
    assert got == [
        (0.10, 0.35, 60, 90, "rhythm_guitar", 2, 8),
        (0.50, 0.75, 64, 64, "rhythm_guitar", 1, 5),
        (0.80, 0.95, 65, 100, "rhythm_guitar", None, None),
    ]
    assert str(fake_python) in str(captured["command"])
    assert str(wav) in str(captured["command"])
    assert captured["cwd"] == str(fake_repo)
    assert str(fake_repo) in str(captured["env_pythonpath"])
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["shell"] is True
    assert captured["timeout"] == 12.0


def test_qmul_hr_guitar_validate_runtime_reports_runner_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "qmul"
    fake_repo.mkdir()
    wav = tmp_path / "lead.wav"
    wav.write_bytes(b"not real audio; subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_QMUL_GUITAR_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_REPO", str(fake_repo))
    monkeypatch.setenv(
        "AURAL_QMUL_GUITAR_COMMAND",
        "{python_q} -m qmul.predict --audio {wav_path_q} --json {out_json_q}",
    )

    def fake_run(command, *_args, **_kwargs):
        out_json = Path(command.split("--json ", 1)[1].strip().strip('"'))
        out_json.write_text(
            json.dumps({"notes": [{"onset": 0.5, "offset": 0.75, "pitch": 64, "string": 1, "fret": 5}]}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.qmul_hr_guitar.subprocess.run", fake_run)

    report = module.validate_runtime(wav, instrument="rhythm_guitar", require_notes=True)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["returncode"] == 0
    assert report["stdout_tail"] == "ok"
    assert report["runtime"]["configured"] is True
    assert report["note_count"] == 1
    assert report["notes"] == [
        {
            "t_on": 0.5,
            "t_off": 0.75,
            "pitch": 64,
            "velocity": 100,
            "instrument": "rhythm_guitar",
            "string": 1,
            "fret": 5,
        }
    ]


def test_qmul_hr_guitar_validate_runtime_can_require_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "qmul"
    fake_repo.mkdir()
    wav = tmp_path / "lead.wav"
    wav.write_bytes(b"not real audio; subprocess is monkeypatched")

    monkeypatch.setenv("AURAL_QMUL_GUITAR_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_REPO", str(fake_repo))
    monkeypatch.setenv(
        "AURAL_QMUL_GUITAR_COMMAND",
        "{python_q} -m qmul.predict --audio {wav_path_q} --json {out_json_q}",
    )

    def fake_run(command, *_args, **_kwargs):
        out_json = Path(command.split("--json ", 1)[1].strip().strip('"'))
        out_json.write_text(json.dumps({"notes": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("aural_ingest.algorithms.qmul_hr_guitar.subprocess.run", fake_run)

    report = module.validate_runtime(wav, require_notes=True)

    assert report["ok"] is False
    assert report["status"] == "ok"
    assert report["reason"] == "runner returned valid output but no guitar notes"
    assert report["note_count"] == 0


def test_qmul_hr_guitar_subprocess_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "qmul"
    fake_repo.mkdir()
    monkeypatch.setenv("AURAL_QMUL_GUITAR_PYTHON", str(fake_python))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_REPO", str(fake_repo))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_COMMAND", "{python_q} -m qmul.predict")
    monkeypatch.setattr(
        "aural_ingest.algorithms.qmul_hr_guitar.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="failed"),
    )

    assert module.transcribe(tmp_path / "lead.wav") == []


def test_qmul_hr_guitar_rejects_wrong_path_types_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    fake_python_dir = tmp_path / "python-dir"
    fake_python_dir.mkdir()
    fake_repo_file = tmp_path / "repo.txt"
    fake_repo_file.write_text("not a repo", encoding="utf-8")

    monkeypatch.setenv("AURAL_QMUL_GUITAR_PYTHON", str(fake_python_dir))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_REPO", str(fake_repo_file))
    monkeypatch.setenv("AURAL_QMUL_GUITAR_COMMAND", "{python_q} -m qmul.predict")

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("qmul_hr_guitar should not launch subprocess for wrong path types")

    monkeypatch.setattr("aural_ingest.algorithms.qmul_hr_guitar.subprocess.run", fail_run)

    assert module.transcribe(tmp_path / "lead.wav") == []


def test_validate_qmul_hr_guitar_runtime_script_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    script = _load_validate_qmul_runtime_script()
    wav = tmp_path / "lead.wav"
    wav.write_bytes(b"not real audio; script call is monkeypatched")
    output = tmp_path / "qmul-report.json"
    captured: dict[str, object] = {}

    def fake_validate_runtime(wav_path, *, instrument, require_notes):
        captured["wav_path"] = wav_path
        captured["instrument"] = instrument
        captured["require_notes"] = require_notes
        return {
            "ok": True,
            "engine": "qmul_hr_guitar",
            "wav_path": str(wav_path),
            "instrument": instrument,
            "status": "ok",
            "reason": None,
            "require_notes": require_notes,
            "note_count": 1,
            "runtime": {"configured": True},
            "notes": [],
        }

    monkeypatch.setattr(module, "validate_runtime", fake_validate_runtime)

    rc = script.main([str(wav), "--instrument", "rhythm_guitar", "--require-notes", "--output", str(output)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == str(output)
    assert captured == {"wav_path": wav, "instrument": "rhythm_guitar", "require_notes": True}
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["engine"] == "qmul_hr_guitar"


def test_validate_qmul_hr_guitar_runtime_script_writes_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    script = _load_validate_qmul_runtime_script()
    evidence_root = tmp_path / "evidence"
    wav = tmp_path / "lead.wav"
    wav.write_bytes(b"not real audio; script call is monkeypatched")

    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))

    def fake_validate_runtime(wav_path, *, instrument, require_notes):
        return {
            "ok": True,
            "engine": "qmul_hr_guitar",
            "wav_path": str(wav_path),
            "instrument": instrument,
            "status": "ok",
            "require_notes": require_notes,
            "note_count": 1,
            "runtime": {"configured": True},
        }

    monkeypatch.setattr(module, "validate_runtime", fake_validate_runtime)

    rc = script.main([str(wav), "--require-notes", "--write-gate-evidence"])

    assert rc == 0
    output = Path(capsys.readouterr().out.strip())
    assert output.parent == evidence_root / "benchmarks" / "runtime" / "runs"
    assert output.name.endswith("_qmul_hr_guitar_runtime.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["require_notes"] is True


def test_validate_qmul_hr_guitar_runtime_script_requires_notes_for_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_qmul_runtime_script()
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))

    with pytest.raises(SystemExit) as exc_info:
        script.main([str(tmp_path / "lead.wav"), "--write-gate-evidence"])

    assert exc_info.value.code == 2
    assert "--write-gate-evidence requires --require-notes" in capsys.readouterr().err
    assert not list(evidence_root.glob("benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json"))


def test_validate_qmul_hr_guitar_runtime_script_rejects_missing_wav(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_qmul_runtime_script()
    missing = tmp_path / "missing.wav"

    rc = script.main([str(missing)])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"validate_qmul_hr_guitar_runtime: input WAV not found: {missing}"


def test_ground_truth_benchmark_resolves_qmul_hr_guitar_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    stem = tmp_path / "lead.wav"
    stem.write_bytes(b"not real audio; adapter is monkeypatched")
    expected = [
        MelodicNote(
            t_on=0.25,
            t_off=0.5,
            pitch=69,
            velocity=100,
            instrument="lead_guitar",
        )
    ]
    captured: dict[str, object] = {}

    def fake_transcribe(stem_path: Path, *, instrument: str) -> list[MelodicNote]:
        captured["stem_path"] = stem_path
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(module, "transcribe", fake_transcribe)

    algorithm = get_melodic_algorithm("qmul_hr_guitar")
    assert algorithm(stem, "lead_guitar") == expected
    assert captured == {"stem_path": stem, "instrument": "lead_guitar"}


def test_qmul_hr_guitar_is_opt_in_melodic_method(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest.transcription import (
        KNOWN_MELODIC_METHODS,
        build_default_melodic_algorithm_registry,
        validate_melodic_method,
    )

    module = importlib.import_module("aural_ingest.algorithms.qmul_hr_guitar")
    stem = tmp_path / "lead.wav"
    stem.write_bytes(b"not real audio; adapter is monkeypatched")
    expected = [
        MelodicNote(
            t_on=0.0,
            t_off=0.25,
            pitch=64,
            velocity=90,
            instrument="lead_guitar",
        )
    ]
    captured: dict[str, object] = {}

    def fake_transcribe(stem_path: Path, *, instrument: str) -> list[MelodicNote]:
        captured["stem_path"] = stem_path
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(module, "transcribe", fake_transcribe)

    assert "qmul_hr_guitar" in KNOWN_MELODIC_METHODS
    assert validate_melodic_method("qmul_hr_guitar") == "qmul_hr_guitar"

    registry = build_default_melodic_algorithm_registry(instrument="lead_guitar")
    assert registry["qmul_hr_guitar"](stem) == expected
    assert captured == {"stem_path": stem, "instrument": "lead_guitar"}
