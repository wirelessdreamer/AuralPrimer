from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

MUSDB_ROLES = ("vocals", "drums", "bass", "other")


def _load_runner_module():
    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "benchmarks" / "quality" / "run_musdb_separation_sdr.py"
    spec = importlib.util.spec_from_file_location("_test_run_musdb_separation_sdr", runner_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_synthetic_track(track_dir: Path) -> None:
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")

    track_dir.mkdir(parents=True)
    sample_rate = 8_000
    frames = 128
    t = np.arange(frames, dtype=np.float32) / float(sample_rate)
    stems = []
    for idx, role in enumerate(MUSDB_ROLES):
        audio = (
            0.04 * np.sin(2.0 * np.pi * (220.0 + (idx * 70.0)) * t)
            + 0.01 * np.cos(2.0 * np.pi * (110.0 + (idx * 35.0)) * t)
        ).astype(np.float32)
        stems.append(audio)
        sf.write(str(track_dir / f"{role}.wav"), audio, sample_rate, subtype="PCM_16")

    mixture = np.sum(np.stack(stems, axis=0), axis=0).astype(np.float32)
    sf.write(str(track_dir / "mixture.wav"), mixture, sample_rate, subtype="PCM_16")


def test_musdb_sdr_runner_smoke_uses_synthetic_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("museval")
    pytest.importorskip("soundfile")

    musdb_root = tmp_path / "musdb"
    track_dir = musdb_root / "test" / "Synthetic Track"
    _write_synthetic_track(track_dir)

    provider_module = tmp_path / "synthetic_musdb_provider.py"
    provider_module.write_text(
        textwrap.dedent(
            """
            from pathlib import Path

            import soundfile as sf


            def separate(mix_wav, stems_dir, *, mix_sha256, shifts, config):
                stems_dir = Path(stems_dir)
                refs_dir = Path(config["reference_stems_dir"])
                stem_paths = {}
                for role in ("vocals", "drums", "bass", "other"):
                    audio, sample_rate = sf.read(str(refs_dir / f"{role}.wav"), always_2d=True)
                    dst = stems_dir / f"{role}.wav"
                    sf.write(str(dst), audio * 0.99, sample_rate, subtype="PCM_16")
                    stem_paths[role] = dst.name
                return {
                    "ok": True,
                    "status": "ok",
                    "mix_sha256_seen": mix_sha256,
                    "shifts_seen": shifts,
                    "stem_paths": stem_paths,
                }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("synthetic_musdb_provider", None)

    config_path = tmp_path / "provider_config.json"
    config_path.write_text(
        json.dumps({"reference_stems_dir": str(track_dir)}),
        encoding="utf-8",
    )
    output_path = tmp_path / "musdb_sdr_summary.json"

    runner = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_musdb_separation_sdr",
            "--musdb-root",
            str(musdb_root),
            "--split",
            "test",
            "--limit",
            "1",
            "--provider",
            "synthetic",
            "--provider-path",
            "synthetic_musdb_provider:separate",
            "--config-json",
            str(config_path),
            "--output",
            str(output_path),
        ],
    )

    runner.main()

    captured = capsys.readouterr()
    assert str(output_path) in captured.out
    assert "[  1/1] test/Synthetic Track ok" in captured.err
    assert output_path.is_file()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["provider"] == "synthetic"
    assert payload["config"]["stem_separation_provider"] == "synthetic"
    assert payload["config"]["reference_stems_dir"] == str(track_dir)
    assert payload["summary"]["tracks_ok"] == 1
    assert payload["summary"]["tracks_failed"] == 0
    assert payload["summary"]["median_sdr_mean"] is not None

    track = payload["tracks"][0]
    assert track["track_id"] == "test/Synthetic Track"
    assert track["separation"]["provider_path"] == "synthetic_musdb_provider:separate"
    assert track["separation"]["stem_paths"] == {role: f"{role}.wav" for role in MUSDB_ROLES}

    evaluation = track["evaluation"]
    assert evaluation["available"] is True
    assert evaluation["status"] == "ok"
    assert set(evaluation["roles"]) == set(MUSDB_ROLES)
    role_metrics = {metric["role"]: metric for metric in evaluation["role_metrics"]}
    assert set(role_metrics) == set(MUSDB_ROLES)
    assert all(metric["frame_count"] >= 1 for metric in role_metrics.values())
    assert all(metric["sdr_median"] is not None for metric in role_metrics.values())


def test_musdb_sdr_runner_fails_when_all_tracks_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("soundfile")

    musdb_root = tmp_path / "musdb"
    track_dir = musdb_root / "test" / "Synthetic Track"
    _write_synthetic_track(track_dir)

    provider_module = tmp_path / "empty_musdb_provider.py"
    provider_module.write_text(
        textwrap.dedent(
            """
            def separate(mix_wav, stems_dir, *, mix_sha256, shifts, config):
                return {
                    "ok": True,
                    "status": "ok",
                    "stem_paths": {},
                }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("empty_musdb_provider", None)

    output_path = tmp_path / "musdb_sdr_summary.json"
    runner = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_musdb_separation_sdr",
            "--musdb-root",
            str(musdb_root),
            "--split",
            "test",
            "--limit",
            "1",
            "--provider",
            "empty",
            "--provider-path",
            "empty_musdb_provider:separate",
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit, match="failed/skipped/no successful"):
        runner.main()

    captured = capsys.readouterr()
    assert str(output_path) in captured.out
    assert "[  1/1] test/Synthetic Track skipped" in captured.err
    assert output_path.is_file()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["summary"]["tracks_ok"] == 0
    assert payload["summary"]["tracks_failed"] == 0
    assert payload["summary"]["tracks_skipped"] == 1
    assert payload["summary"]["median_sdr_mean"] is None
    assert payload["tracks"][0]["evaluation"]["reason"] == "separation provider returned no stem_paths"


def test_musdb_sdr_runner_writes_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    musdb_root = tmp_path / "musdb"
    mix = musdb_root / "test" / "Synthetic Track" / "mixture.wav"
    mix.parent.mkdir(parents=True)
    mix.write_bytes(b"synthetic mix")
    evidence_root = tmp_path / "evidence"
    config_path = tmp_path / "provider_config.json"
    config_path.write_text(
        json.dumps({"stem_separation_modelpack_id": "demucs_ft_drums"}),
        encoding="utf-8",
    )
    runner = _load_runner_module()

    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(
        runner,
        "discover_musdb18_tracks",
        lambda root, *, split, limit: [
            SimpleNamespace(
                track_id=f"test/Synthetic Track {idx:02d}",
                name=f"Synthetic Track {idx:02d}",
                split="test",
                mixture_path=mix,
                reference_stems={},
            )
            for idx in range(10)
        ],
    )
    monkeypatch.setattr(runner, "_sha256_file", lambda _path: "abc123")
    monkeypatch.setattr(
        runner,
        "_run_stem_separation",
        lambda *args, **kwargs: {
            "ok": True,
            "status": "ok",
            "stem_paths": {"drums": "drums.wav"},
        },
    )
    monkeypatch.setattr(
        runner,
        "prepare_musdb_estimate_stems",
        lambda estimated, eval_dir, *, stems_dir: {"drums": eval_dir / "drums.wav"},
    )
    monkeypatch.setattr(
        runner,
        "evaluate_museval_separation",
        lambda reference_stems, musdb_estimated: {"available": True, "backend": "museval", "status": "ok"},
    )
    monkeypatch.setattr(
        runner,
        "summarize_museval_separation_runs",
        lambda results: {
            "tracks": len(results),
            "tracks_ok": len(results),
            "tracks_failed": 0,
            "tracks_skipped": 0,
            "median_sdr_mean": 5.25,
            "role_summary": {
                role: {"track_count": len(results), "median_sdr_mean": 5.25}
                for role in MUSDB_ROLES
            },
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_musdb_separation_sdr",
            "--musdb-root",
            str(musdb_root),
            "--split",
            "test",
            "--limit",
            "10",
            "--provider",
            "demucs",
            "--config-json",
            str(config_path),
            "--write-gate-evidence",
        ],
    )

    runner.main()

    output = Path(capsys.readouterr().out.strip())
    assert output.parent == evidence_root / "benchmarks" / "quality" / "runs"
    assert output.name.endswith("_demucs_demucs_ft_drums_musdb_separation_sdr.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["promotion_usable"] is True
    assert payload["promotion_min_tracks"] == 10
    assert payload["provider"] == "demucs"
    assert payload["config"]["stem_separation_modelpack_id"] == "demucs_ft_drums"


def test_musdb_sdr_runner_rejects_gate_evidence_without_test_split(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner_module()
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_musdb_separation_sdr",
            "--musdb-root",
            str(tmp_path / "musdb"),
            "--write-gate-evidence",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        runner.main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "--write-gate-evidence requires --split test" in captured.err
    assert not list(evidence_root.glob("benchmarks/quality/runs/*_musdb_separation_sdr.json"))
