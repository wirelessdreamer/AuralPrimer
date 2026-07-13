from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_fake_rmvpe_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "RMVPE"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "inference.py").write_text("", encoding="utf-8")
    return repo


def _load_validate_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_rmvpe_runtime.py"
    spec = importlib.util.spec_from_file_location("_test_validate_rmvpe_runtime", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_melodic_rmvpe_imports_without_checkpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AURAL_RMVPE_CHECKPOINT", raising=False)

    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")

    assert module.ENGINE_ID == "melodic_rmvpe"
    assert module.transcribe(tmp_path / "vocals.wav", instrument="vocals") == []


def test_melodic_rmvpe_frames_to_notes_segments_and_filters_range() -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")

    notes = module._frames_to_notes(
        [
            (0.00, 220.0, 0.9),
            (0.02, 220.0, 0.8),
            (0.04, 220.0, 0.7),
            (0.06, 220.0, 0.6),
            (0.08, 220.0, 0.5),
            (0.10, 0.0, 0.0),
            (0.20, 440.0, 0.4),
            (0.22, 440.0, 0.4),
            (0.24, 440.0, 0.4),
            (0.26, 440.0, 0.4),
            (0.28, 440.0, 0.4),
            (0.40, 2000.0, 1.0),
        ],
        instrument="vocals",
    )

    assert [(note.t_on, note.t_off, note.pitch, note.instrument) for note in notes] == [
        (0.0, 0.1, 57, "vocals"),
        (0.2, 0.3, 69, "vocals"),
    ]
    assert 80 <= notes[0].velocity <= 127


def test_melodic_rmvpe_frames_to_contour_doc_keeps_confidence() -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    from aural_ingest import feedpak_validate

    doc = module._frames_to_contour_doc(
        [
            (0.0, 440.1234567, 0.87654321),
            (0.02, 0.0, 0.0),
            (0.04, -1.0, 0.9),
        ]
    )

    assert doc == {
        "version": 1,
        "samples": [
            {"t": 0.0, "hz": 440.123457, "confidence": 0.876543},
        ],
    }
    assert not feedpak_validate.iter_errors(doc, "vocal-pitch-contour.schema.json")


def test_rmvpe_resolver_uses_env_or_model_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from aural_ingest.transcription import resolve_rmvpe_checkpoint_path

    explicit = tmp_path / "explicit.pt"
    explicit.write_bytes(b"x")
    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT", str(explicit))
    assert resolve_rmvpe_checkpoint_path([tmp_path]) == explicit

    monkeypatch.delenv("AURAL_RMVPE_CHECKPOINT", raising=False)
    model_root = tmp_path / "assets" / "models" / "rmvpe"
    model_root.mkdir(parents=True)
    checkpoint = model_root / "rmvpe.pt"
    checkpoint.write_bytes(b"x")
    assert resolve_rmvpe_checkpoint_path([tmp_path]) == checkpoint


def test_melodic_rmvpe_reports_invalid_repo_before_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    data = b"fake checkpoint"
    checkpoint = tmp_path / "rmvpe.pt"
    checkpoint.write_bytes(data)
    missing_repo = tmp_path / "missing-rmvpe"

    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT_SHA256", _sha256(data))
    monkeypatch.setenv("AURAL_RMVPE_REPO", str(missing_repo))

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("RMVPE inference should not run when repo preflight fails")

    monkeypatch.setattr(module, "_run_rmvpe_f0", fail_run)

    assert module.transcribe(tmp_path / "vocals.wav", instrument="vocals") == []
    last_run = module.transcribe.last_run
    assert "does not exist" in last_run["warnings"][0]
    assert last_run["meta"]["checkpoint_found"] is True
    assert last_run["meta"]["runtime"]["ready"] is False
    assert last_run["meta"]["runtime"]["repo"]["path"] == str(missing_repo)


def test_melodic_rmvpe_requires_reviewed_checkpoint_hash_before_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    checkpoint = tmp_path / "rmvpe.pt"
    checkpoint.write_bytes(b"unreviewed checkpoint")
    repo = _make_fake_rmvpe_repo(tmp_path)

    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("AURAL_RMVPE_REPO", str(repo))
    monkeypatch.delenv("AURAL_RMVPE_CHECKPOINT_SHA256", raising=False)
    monkeypatch.setattr(module, "_find_spec", lambda name: name in {"src", "torch"})

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("RMVPE inference should not run without a reviewed checkpoint hash")

    monkeypatch.setattr(module, "_run_rmvpe_f0", fail_run)

    assert module.transcribe(tmp_path / "vocals.wav", instrument="vocals") == []
    last_run = module.transcribe.last_run
    assert "AURAL_RMVPE_CHECKPOINT_SHA256" in last_run["warnings"][0]
    assert last_run["meta"]["runtime"]["checkpoint_review"]["ready"] is False


def test_rmvpe_validate_runtime_reports_missing_checkpoint_without_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription

    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    for env_var in (
        "AURAL_RMVPE_CHECKPOINT",
        "AURAL_RMVPE_CHECKPOINT_SHA256",
        "AURAL_RMVPE_REPO",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(transcription, "_default_basic_pitch_model_roots", lambda: [])
    monkeypatch.setattr(transcription, "resolve_rmvpe_checkpoint_path", lambda _roots: None)

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("RMVPE inference should not run when runtime evidence is absent")

    monkeypatch.setattr(module, "_run_rmvpe_f0", fail_run)

    report = module.validate_runtime(tmp_path / "vocals.wav", require_notes=True)

    assert report["ok"] is False
    assert report["status"] == "not_ready"
    assert report["reason"] == "RMVPE checkpoint not found"
    assert report["note_count"] == 0
    assert report["runtime"]["ready"] is False
    assert "RMVPE checkpoint not found" in report["runtime"]["missing"]


def test_melodic_rmvpe_requires_explicit_repo_path_even_when_src_is_importable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    data = b"reviewed checkpoint"
    checkpoint = tmp_path / "rmvpe.pt"
    checkpoint.write_bytes(data)

    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT_SHA256", _sha256(data))
    monkeypatch.delenv("AURAL_RMVPE_REPO", raising=False)
    monkeypatch.setattr(module, "_find_spec", lambda name: name in {"src", "torch"})

    def fail_run(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("RMVPE inference should not run without an explicit repo path")

    monkeypatch.setattr(module, "_run_rmvpe_f0", fail_run)

    assert module.transcribe(tmp_path / "vocals.wav", instrument="vocals") == []
    last_run = module.transcribe.last_run
    assert "AURAL_RMVPE_REPO" in last_run["warnings"][0]
    assert last_run["meta"]["runtime"]["repo"]["ready"] is False


def test_melodic_rmvpe_accepts_installer_manifest_hash_before_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.melodic_rmvpe")
    data = b"reviewed checkpoint"
    checkpoint = tmp_path / "rmvpe.pt"
    checkpoint.write_bytes(data)
    (tmp_path / "rmvpe.checkpoint.json").write_text(
        json.dumps(
            {
                "version": 1,
                "filename": "rmvpe.pt",
                "sha256": _sha256(data),
                "license_confirmed": True,
            }
        ),
        encoding="utf-8",
    )
    repo = _make_fake_rmvpe_repo(tmp_path)

    monkeypatch.setenv("AURAL_RMVPE_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("AURAL_RMVPE_REPO", str(repo))
    monkeypatch.delenv("AURAL_RMVPE_CHECKPOINT_SHA256", raising=False)
    monkeypatch.setattr(module, "_find_spec", lambda name: name in {"src", "torch"})
    monkeypatch.setattr(module, "select_device", lambda _env_name: "cpu")
    monkeypatch.setattr(
        module,
        "_run_rmvpe_f0",
        lambda stem_path, checkpoint_path, device: [(idx * 0.02, 440.0, 0.9) for idx in range(5)],
    )

    notes = module.transcribe(tmp_path / "vocals.wav", instrument="vocals")

    assert [(note.t_on, note.t_off, note.pitch) for note in notes] == [(0.0, 0.1, 69)]
    last_run = module.transcribe.last_run
    assert last_run["meta"]["runtime"]["checkpoint_review"]["ready"] is True
    assert last_run["meta"]["runtime"]["checkpoint_review"]["source"] == "manifest"


def test_validate_rmvpe_runtime_script_writes_status_without_wav(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import melodic_rmvpe

    script = _load_validate_script()
    output = tmp_path / "rmvpe-runtime.json"
    monkeypatch.setattr(
        melodic_rmvpe,
        "resolved_runtime_status",
        lambda: {
            "engine": "melodic_rmvpe",
            "ready": False,
            "reason": "RMVPE checkpoint not found",
            "missing": ["RMVPE checkpoint not found"],
        },
    )

    rc = script.main(["--output", str(output)])

    assert rc == 2
    assert capsys.readouterr().out.strip() == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["status"] == "not_ready"
    assert payload["runtime"]["missing"] == ["RMVPE checkpoint not found"]


def test_validate_rmvpe_runtime_script_writes_gate_evidence_without_wav(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.algorithms import melodic_rmvpe

    script = _load_validate_script()
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(
        melodic_rmvpe,
        "resolved_runtime_status",
        lambda: {
            "engine": "melodic_rmvpe",
            "ready": True,
            "reason": None,
            "missing": [],
        },
    )

    rc = script.main(["--write-gate-evidence"])

    assert rc == 0
    output = Path(capsys.readouterr().out.strip())
    assert output.parent == evidence_root / "benchmarks" / "runtime" / "runs"
    assert output.name.endswith("_rmvpe_runtime.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["engine"] == "melodic_rmvpe"


def test_validate_rmvpe_runtime_script_rejects_missing_wav(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_validate_script()
    missing = tmp_path / "missing.wav"

    rc = script.main([str(missing)])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"validate_rmvpe_runtime: input WAV not found: {missing}"


def test_vocals_chain_and_registry_include_rmvpe() -> None:
    from aural_ingest.transcription import (
        INSTRUMENT_FREQ_RANGES,
        INSTRUMENT_ROLES,
        KNOWN_MELODIC_METHODS,
        build_default_melodic_algorithm_registry,
        melodic_fallback_chain,
        melodic_methods_for_profile,
    )

    assert "vocals" in INSTRUMENT_ROLES
    assert "vocals" in INSTRUMENT_FREQ_RANGES
    assert "melodic_rmvpe" in KNOWN_MELODIC_METHODS
    assert melodic_fallback_chain("auto", instrument="vocals")[:3] == [
        "melodic_rmvpe",
        "torchcrepe",
        "pyin",
    ]
    assert melodic_methods_for_profile("gameplay_default", "vocals")[:3] == [
        "melodic_rmvpe",
        "torchcrepe",
        "pyin",
    ]
    registry = build_default_melodic_algorithm_registry(instrument="vocals")
    assert callable(registry["melodic_rmvpe"])


def test_rmvpe_registry_propagates_contour_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest.algorithms import melodic_rmvpe
    from aural_ingest import transcription
    from aural_ingest.transcription import MelodicNote

    def fake_transcribe(stem_path: Path, *, instrument: str = "vocals") -> list[MelodicNote]:
        fake_transcribe.last_run = {
            "warnings": [],
            "scores": {},
            "meta": {
                "vocal_pitch_contour": {
                    "version": 1,
                    "samples": [{"t": 0.0, "hz": 440.0, "confidence": 0.9}],
                }
            },
        }
        return [MelodicNote(t_on=0.0, t_off=0.2, pitch=69, velocity=100, instrument=instrument)]

    fake_transcribe.last_run = {"warnings": [], "scores": {}, "meta": {}}
    monkeypatch.setattr(melodic_rmvpe, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "score_transcription", lambda notes, stem_path: 1.0)

    registry = transcription.build_default_melodic_algorithm_registry(instrument="vocals")
    result = transcription.transcribe_melodic(
        tmp_path / "vocals.wav",
        requested_method="melodic_rmvpe",
        algorithm_registry=registry,
        instrument="vocals",
    )

    assert result.used_method == "melodic_rmvpe"
    assert result.meta["vocal_pitch_contour"]["samples"][0]["hz"] == 440.0


def test_rmvpe_contour_metadata_survives_empty_note_segmentation(tmp_path: Path) -> None:
    from aural_ingest import transcription

    def fake_rmvpe(stem_path: Path) -> list[object]:
        fake_rmvpe.last_run = {
            "warnings": [],
            "scores": {},
            "meta": {
                "vocal_pitch_contour": {
                    "version": 1,
                    "samples": [{"t": 0.0, "hz": 220.0, "confidence": 0.7}],
                }
            },
        }
        return []

    fake_rmvpe.last_run = {"warnings": [], "scores": {}, "meta": {}}
    result = transcription.transcribe_melodic(
        tmp_path / "vocals.wav",
        requested_method="melodic_rmvpe",
        algorithm_registry={"melodic_rmvpe": fake_rmvpe},
        instrument="vocals",
    )

    assert result.notes == []
    assert result.used_method is None
    assert result.meta["vocal_pitch_contour"]["samples"][0]["hz"] == 220.0
