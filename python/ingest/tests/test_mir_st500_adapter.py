from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_mir_st500_root(root: Path) -> None:
    ann = root / "MIR-ST500_20210206"
    ann.mkdir(parents=True)
    payload = {
        "1": [[0.20, 0.40, 60.0], [0.50, 0.80, 62.2]],
        "2": [[0.10, 0.30, 65.0]],
        "401": [[1.00, 1.25, 70.0], [1.50, 1.75, 69.0]],
    }
    (ann / "MIR-ST500_corrected.json").write_text(json.dumps(payload), encoding="utf-8")
    (ann / "metadata.csv").write_text(
        "\n".join(
            [
                "song_id,youtube_link,orig_id,labeler,verifier",
                "1,https://example.test/1,488,9,5",
                "2,https://example.test/2,87,6,1",
                "401,https://example.test/401,12,3,2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for split, song_id in (("train", "1"), ("train", "2"), ("test", "401")):
        song_dir = root / split / song_id
        song_dir.mkdir(parents=True)
        (song_dir / "Vocal.wav").write_bytes(b"fake wav")


def test_mir_st500_yields_vocal_cases_from_official_layout(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    _write_mir_st500_root(tmp_path)

    cases = list(mir_st500.yield_cases(tmp_path, split="test"))

    assert [case.case_id for case in cases] == ["mir_st500:401"]
    case = cases[0]
    assert case.instrument == "vocals"
    assert case.audio_path == tmp_path / "test" / "401" / "Vocal.wav"
    assert [(note.t_on, note.t_off, note.pitch, note.instrument) for note in case.melodic_notes] == [
        (1.0, 1.25, 70, "vocals"),
        (1.5, 1.75, 69, "vocals"),
    ]
    assert case.duration_sec == 1.75
    assert case.metadata["split"] == "test"
    assert case.metadata["song_id"] == "401"
    assert case.metadata["signal"] == "vocal"
    assert case.metadata["labeler"] == "3"
    assert case.metadata["verifier"] == "2"


def test_mir_st500_case_ids_filter_before_limit(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    _write_mir_st500_root(tmp_path)

    cases = list(
        mir_st500.yield_cases(
            tmp_path,
            split="train",
            case_ids={"mir_st500:002"},
            limit=1,
        )
    )

    assert [case.case_id for case in cases] == ["mir_st500:002"]


def test_mir_st500_accepts_annotation_directory_as_root(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    _write_mir_st500_root(tmp_path)

    cases = list(mir_st500.yield_cases(tmp_path / "MIR-ST500_20210206", split="test"))

    assert [case.case_id for case in cases] == ["mir_st500:401"]


def test_mir_st500_diagnose_corpus_reports_missing_annotation(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    report = mir_st500.diagnose_corpus(tmp_path, split="test")

    assert report["emitted_count"] == 0
    assert "missing MIR-ST500_corrected.json" in str(report["reason"])
    assert str(tmp_path / "MIR-ST500_corrected.json") in report["annotation_candidates"]


def test_mir_st500_diagnose_corpus_reports_missing_vocal_audio(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    ann = tmp_path / "MIR-ST500_20210206"
    ann.mkdir(parents=True)
    (ann / "MIR-ST500_corrected.json").write_text(
        json.dumps({"401": [[0.10, 0.30, 60.0]]}),
        encoding="utf-8",
    )

    report = mir_st500.diagnose_corpus(tmp_path, split="test", variant="vocal")

    assert report["annotation_count"] == 1
    assert report["split_annotation_count"] == 1
    assert report["audio_found_count"] == 0
    assert "no vocal audio files found" in str(report["reason"])
    assert "mir_st500:401" in str(report["missing_audio_examples"])


def test_mir_st500_invalid_variant_is_explicit(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import mir_st500

    _write_mir_st500_root(tmp_path)

    with pytest.raises(ValueError, match="unknown MIR-ST500 variant"):
        list(mir_st500.yield_cases(tmp_path, variant="invalid"))

    report = mir_st500.diagnose_corpus(tmp_path, variant="invalid")
    assert "unknown MIR-ST500 variant" in str(report["reason"])
