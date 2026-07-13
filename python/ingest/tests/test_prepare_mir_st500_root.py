from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_prepare_module():
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "python" / "ingest" / "scripts" / "prepare_mir_st500_root.py"
    spec = importlib.util.spec_from_file_location("_test_prepare_mir_st500_root", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_source_repo(root: Path) -> None:
    dataset = root / "MIR-ST500_20210206"
    dataset.mkdir(parents=True)
    (dataset / "MIR-ST500_corrected.json").write_text(
        json.dumps(
            {
                "1": [[0.10, 0.20, 60.0]],
                "401": [[0.20, 0.40, 62.0]],
                "402": [[0.50, 0.80, 64.0]],
            }
        ),
        encoding="utf-8",
    )
    (dataset / "MIR-ST500_link.json").write_text(
        json.dumps({"1": "https://example.test/1", "401": "https://example.test/401"}),
        encoding="utf-8",
    )
    (dataset / "metadata.csv").write_text(
        "song_id,youtube_link,orig_id,labeler,verifier\n"
        "1,https://example.test/1,1,1,1\n"
        "401,https://example.test/401,401,2,2\n"
        "402,https://example.test/402,402,3,3\n",
        encoding="utf-8",
    )
    (dataset / "README").write_text("MIR-ST500 test fixture\n", encoding="utf-8")


def test_prepare_mir_st500_root_copies_metadata_and_reports_missing_audio(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_prepare_module()
    source_repo = tmp_path / "source"
    target_root = tmp_path / "target"
    output = tmp_path / "status.json"
    _write_source_repo(source_repo)
    song_dir = target_root / "test" / "401"
    song_dir.mkdir(parents=True)
    (song_dir / "Vocal.wav").write_bytes(b"fake wav")
    (song_dir / "Mixture.mp3").write_bytes(b"fake mp3")

    rc = module.main(
        [
            "--source-repo",
            str(source_repo),
            "--target-root",
            str(target_root),
            "--output",
            str(output),
            "--copy-metadata",
        ]
    )

    assert rc == 1
    assert capsys.readouterr().out.strip() == str(output.resolve())
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["annotation_count"] == 3
    assert report["train_annotation_count"] == 1
    assert report["test_annotation_count"] == 2
    assert report["test_vocal_diagnostics"]["emitted_count"] == 1
    assert report["missing_test_vocal_case_ids"] == ["mir_st500:402"]
    assert report["missing_test_mixture_case_ids"] == ["mir_st500:402"]
    assert report["gate_ready"] is False
    assert set(report["dependencies"]) == {"yt_dlp", "youtube_dl", "spleeter", "tensorflow"}
    assert report["dependencies_ready"] is False
    assert report["external_dependencies_ready"] is False
    assert report["reconstruction_dependencies_ready"] is False
    assert report["audio_source_review_required"] is True
    assert report["audio_reconstruction_required"] is True
    assert report["audio_reconstruction_status"] == "missing_test_audio"
    assert (target_root / "MIR-ST500_20210206" / "MIR-ST500_corrected.json").is_file()


def test_prepare_mir_st500_root_reports_external_dependency_python(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_prepare_module()
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[0] == str(fake_python)
        assert capture_output is True
        assert text is True
        assert timeout == 60

        class Result:
            returncode = 0
            stdout = json.dumps(
                {
                    "yt_dlp": True,
                    "youtube_dl": True,
                    "spleeter": False,
                    "tensorflow": False,
                }
            )
            stderr = ""

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    status = module._external_dependency_status(fake_python)

    assert status["python_exists"] is True
    assert status["python_is_file"] is True
    assert status["returncode"] == 0
    assert status["dependencies"] == {
        "yt_dlp": True,
        "youtube_dl": True,
        "spleeter": False,
        "tensorflow": False,
    }


def test_prepare_mir_st500_root_marks_external_dependencies_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_prepare_module()
    source_repo = tmp_path / "source"
    target_root = tmp_path / "target"
    fake_python = tmp_path / "python.exe"
    _write_source_repo(source_repo)
    fake_python.write_text("", encoding="utf-8")

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[0] == str(fake_python)

        class Result:
            returncode = 0
            stdout = json.dumps({name: True for name in module.DEPENDENCIES})
            stderr = ""

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    copied_metadata = module._copy_metadata(source_repo, target_root)
    report = module.build_report(
        source_repo,
        target_root,
        copied_metadata=copied_metadata,
        dependency_python=fake_python,
    )

    assert report["external_dependencies_ready"] is True
    assert report["reconstruction_dependencies_ready"] is True
    assert report["audio_reconstruction_required"] is True
    assert report["audio_reconstruction_status"] == "missing_test_audio"
