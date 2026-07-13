from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_runner_module():
    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "benchmarks" / "vocals" / "run_mir_st500_vocals.py"
    spec = importlib.util.spec_from_file_location("_test_run_mir_st500_vocals", runner_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_mir_st500_root(root: Path) -> None:
    ann = root / "MIR-ST500_20210206"
    ann.mkdir(parents=True)
    (ann / "MIR-ST500_corrected.json").write_text(
        json.dumps(
            {
                "401": [[0.10, 0.30, 60.0], [0.50, 0.75, 62.0]],
                "402": [[0.20, 0.40, 64.0]],
            }
        ),
        encoding="utf-8",
    )
    (ann / "metadata.csv").write_text(
        "\n".join(
            [
                "song_id,youtube_link,orig_id,labeler,verifier",
                "401,https://example.test/401,12,3,2",
                "402,https://example.test/402,13,4,5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for song_id in ("401", "402"):
        song_dir = root / "test" / song_id
        song_dir.mkdir(parents=True)
        (song_dir / "Vocal.wav").write_bytes(b"fake wav")


def test_mir_st500_vocals_runner_writes_report_with_fake_sweep(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.ground_truth_benchmark import CaseScore

    corpus_root = tmp_path / "mir_st500"
    _write_mir_st500_root(corpus_root)
    output_path = tmp_path / "mir_st500_vocals_report.json"
    runner = _load_runner_module()

    observed: dict[str, object] = {}

    def fake_run_sweep(
        cases,
        *,
        algorithms,
        family,
        tolerance_sec,
        pitch_tolerance_semitones,
        on_case=None,
    ):
        case_list = list(cases)
        observed["case_ids"] = [case.case_id for case in case_list]
        observed["audio_paths"] = [case.audio_path for case in case_list]
        observed["instruments"] = [case.instrument for case in case_list]
        observed["algorithms"] = list(algorithms)
        observed["family"] = family
        observed["tolerance_sec"] = tolerance_sec
        observed["pitch_tolerance_semitones"] = pitch_tolerance_semitones

        score = CaseScore(
            case_id=case_list[0].case_id,
            algorithm_id=algorithms[0],
            runtime_sec=0.1234,
            tp=2,
            fp=1,
            fn=0,
            onset_mae_sec=0.01,
            metadata=dict(case_list[0].metadata),
        )
        if on_case is not None:
            on_case(1, score)
        return [score]

    monkeypatch.setattr(runner, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_mir_st500_vocals",
            "--corpus-root",
            str(corpus_root),
            "--split",
            "test",
            "--variant",
            "vocal",
            "--algorithm",
            "fake_vocal",
            "--limit",
            "1",
            "--tolerance-ms",
            "25",
            "--pitch-tolerance-semitones",
            "1",
            "--output",
            str(output_path),
            "--progress",
        ],
    )

    runner.main()

    captured = capsys.readouterr()
    stdout_payload = json.loads(captured.out)
    assert stdout_payload == {
        "ok": True,
        "promotion_usable": False,
        "case_count": 1,
        "output": str(output_path),
    }
    assert "[   1/1] fake_vocal mir_st500:401 f1=0.800 tp=2 fp=1 fn=0" in captured.err

    assert observed == {
        "case_ids": ["mir_st500:401"],
        "audio_paths": [corpus_root / "test" / "401" / "Vocal.wav"],
        "instruments": ["vocals"],
        "algorithms": ["fake_vocal"],
        "family": "melodic",
        "tolerance_sec": 0.025,
        "pitch_tolerance_semitones": 1,
    }

    assert output_path.is_file()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["dataset"] == "mir_st500"
    assert report["family"] == "melodic"
    assert report["case_count"] == 1
    assert report["extra"] == {
        "split": "test",
        "variant": "vocal",
        "limit": 1,
        "tolerance_ms": 25.0,
        "pitch_tolerance_semitones": 1,
        "algorithms": ["fake_vocal"],
    }
    assert report["summary"]["overall"]["f1"] == 0.8
    assert report["cases"][0]["case_id"] == "mir_st500:401"
    assert report["cases"][0]["algorithm_id"] == "fake_vocal"
    assert report["cases"][0]["metadata"]["signal"] == "vocal"


def test_mir_st500_vocals_runner_writes_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.ground_truth_benchmark import CaseScore

    corpus_root = tmp_path / "mir_st500"
    evidence_root = tmp_path / "evidence"
    _write_mir_st500_root(corpus_root)
    runner = _load_runner_module()

    def fake_run_sweep(
        cases,
        *,
        algorithms,
        family,
        tolerance_sec,
        pitch_tolerance_semitones,
        on_case=None,
    ):
        case = list(cases)[0]
        return [
            CaseScore(
                case_id=case.case_id,
                algorithm_id=algorithms[0],
                runtime_sec=0.1234,
                tp=2,
                fp=1,
                fn=0,
                onset_mae_sec=0.01,
                metadata=dict(case.metadata),
            )
        ]

    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))
    monkeypatch.setattr(runner, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_mir_st500_vocals",
            "--corpus-root",
            str(corpus_root),
            "--split",
            "test",
            "--variant",
            "vocal",
            "--algorithm",
            "melodic_rmvpe",
            "--write-gate-evidence",
        ],
    )

    runner.main()

    stdout_payload = json.loads(capsys.readouterr().out)
    output = Path(stdout_payload["output"])
    assert output.parent == evidence_root / "benchmarks" / "vocals" / "gt_runs"
    assert output.name.endswith("_mir_st500_vocals.json")
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["dataset"] == "mir_st500"
    assert report["extra"]["limit"] is None
    assert report["summary"]["per_algorithm"]["melodic_rmvpe"]["cases_ok"] == 1
    assert stdout_payload["promotion_usable"] is True


def test_mir_st500_vocals_runner_exits_nonzero_when_report_has_case_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aural_ingest.ground_truth_benchmark import CaseScore

    corpus_root = tmp_path / "mir_st500"
    _write_mir_st500_root(corpus_root)
    output_path = tmp_path / "mir_st500_vocals_report.json"
    runner = _load_runner_module()

    def fake_run_sweep(*args, **kwargs):
        return [
            CaseScore(
                case_id="mir_st500:401",
                algorithm_id="melodic_rmvpe",
                runtime_sec=0.1234,
                tp=0,
                fp=0,
                fn=2,
                onset_mae_sec=None,
                metadata={"signal": "vocal"},
                error="synthetic failure",
            )
        ]

    monkeypatch.setattr(runner, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_mir_st500_vocals",
            "--corpus-root",
            str(corpus_root),
            "--split",
            "test",
            "--variant",
            "vocal",
            "--algorithm",
            "melodic_rmvpe",
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit, match="case errors"):
        runner.main()

    stdout_payload = json.loads(capsys.readouterr().out)
    assert stdout_payload["ok"] is False
    assert stdout_payload["promotion_usable"] is False
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["summary"]["per_algorithm"]["melodic_rmvpe"]["cases_err"] == 1


def test_mir_st500_vocals_runner_reports_empty_corpus_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "mir_st500"
    ann = corpus_root / "MIR-ST500_20210206"
    ann.mkdir(parents=True)
    (ann / "MIR-ST500_corrected.json").write_text(
        json.dumps({"401": [[0.10, 0.30, 60.0]]}),
        encoding="utf-8",
    )
    runner = _load_runner_module()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_mir_st500_vocals",
            "--corpus-root",
            str(corpus_root),
            "--split",
            "test",
            "--variant",
            "vocal",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        runner.main()

    message = str(exc_info.value)
    assert "no MIR-ST500 cases found" in message
    assert "no vocal audio files found" in message
    assert '"audio_found_count": 0' in message
    assert "mir_st500:401" in message
