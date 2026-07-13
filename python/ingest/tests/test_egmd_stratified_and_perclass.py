"""Tests for the additive drum-baseline tooling:

* the E-GMD adapter's ``case_ids`` allow-list filter (used to consume a
  stratified sample instead of the first-N-in-CSV-order rows), and
* the per-5-class P/R/F breakdown added to drum scoring + aggregation.

Both are pure/offline: the E-GMD test builds a tiny synthetic corpus in
``tmp_path``; the scoring tests use synthetic ``DrumEvent`` lists.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from aural_ingest.dataset_adapters.common import GroundTruthCase
from aural_ingest.dataset_adapters.egmd import yield_cases
from aural_ingest.ground_truth_benchmark import (
    _midi_note_to_5class,
    _summarise_per_class,
    aggregate,
    combine_report_payloads,
    score_drum_case,
)
from aural_ingest.transcription import DrumEvent


# ---------------------------------------------------------------------------
# Minimal MIDI / WAV / CSV builders for a synthetic E-GMD corpus.
# ---------------------------------------------------------------------------


def _vlq(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(parts))


def _drum_midi(notes: list[tuple[int, int]], *, division: int = 480) -> bytes:
    """Single format-0 track, channel-9 NoteOn at tick 0 for each (note, vel)."""
    body = b""
    for note, vel in notes:
        body += _vlq(0) + bytes([0x99, note, vel])  # 0x90|ch9 NoteOn
    body += _vlq(0) + bytes([0xFF, 0x2F, 0x00])  # end-of-track
    track = b"MTrk" + len(body).to_bytes(4, "big") + body
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + division.to_bytes(2, "big")
    )
    return header + track


def _tiny_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(struct.pack("<10h", *([0] * 10)))


def _build_egmd_corpus(root: Path, rows: list[dict[str, str]]) -> None:
    """Lay out ``e_gmd_metadata/`` + ``e_gmd_full/e-gmd-v1.0.0/`` from rows."""
    meta_dir = root / "e_gmd_metadata"
    audio_root = root / "e_gmd_full" / "e-gmd-v1.0.0"
    meta_dir.mkdir(parents=True)
    audio_root.mkdir(parents=True)

    header = [
        "drummer", "session", "id", "style", "bpm", "beat_type",
        "time_signature", "duration", "split", "midi_filename",
        "audio_filename", "kit_name",
    ]
    lines = [",".join(header)]
    for r in rows:
        (audio_root / r["midi_filename"]).parent.mkdir(parents=True, exist_ok=True)
        (audio_root / r["midi_filename"]).write_bytes(
            _drum_midi([(36, 100), (38, 90), (42, 80)])
        )
        _tiny_wav(audio_root / r["audio_filename"])
        lines.append(",".join(r[k] for k in header))
    (meta_dir / "e-gmd-v1.0.0.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(rid: str, style: str, bpm: str, stem: str) -> dict[str, str]:
    return {
        "drummer": "drummer1",
        "session": "drummer1/s",
        "id": rid,
        "style": style,
        "bpm": bpm,
        "beat_type": "beat",
        "time_signature": "4-4",
        "duration": "1.0",
        "split": "test",
        "midi_filename": f"drummer1/s/{stem}.midi",
        "audio_filename": f"drummer1/s/{stem}.wav",
        "kit_name": "Acoustic Kit",
    }


# ---------------------------------------------------------------------------
# case_ids filter
# ---------------------------------------------------------------------------


def test_egmd_case_ids_filter_selects_only_listed(tmp_path: Path) -> None:
    rows = [
        _row("drummer1/s/1", "funk/groove1", "138", "a"),
        _row("drummer1/s/2", "rock/groove2", "120", "b"),
        _row("drummer1/s/3", "jazz/groove3", "90", "c"),
    ]
    _build_egmd_corpus(tmp_path, rows)

    want = {"egmd:drummer1/s/2::b", "egmd:drummer1/s/3::c"}
    cases = list(yield_cases(tmp_path, split="test", case_ids=want))

    assert {c.case_id for c in cases} == want


def test_egmd_case_ids_none_yields_all(tmp_path: Path) -> None:
    rows = [
        _row("drummer1/s/1", "funk/groove1", "138", "a"),
        _row("drummer1/s/2", "rock/groove2", "120", "b"),
    ]
    _build_egmd_corpus(tmp_path, rows)

    cases = list(yield_cases(tmp_path, split="test", case_ids=None))

    assert {c.case_id for c in cases} == {
        "egmd:drummer1/s/1::a",
        "egmd:drummer1/s/2::b",
    }


def test_egmd_case_ids_filter_wins_over_limit_ordering(tmp_path: Path) -> None:
    # The stratified allow-list must win regardless of CSV order: even with
    # limit=1, we must get the *listed* case, not the first CSV row.
    rows = [
        _row("drummer1/s/1", "funk/groove1", "138", "first"),
        _row("drummer1/s/2", "rock/groove2", "120", "second"),
    ]
    _build_egmd_corpus(tmp_path, rows)

    cases = list(
        yield_cases(
            tmp_path,
            split="test",
            case_ids={"egmd:drummer1/s/2::second"},
            limit=1,
        )
    )

    assert [c.case_id for c in cases] == ["egmd:drummer1/s/2::second"]


# ---------------------------------------------------------------------------
# per-5-class scoring
# ---------------------------------------------------------------------------


def test_midi_note_to_5class_mapping() -> None:
    assert _midi_note_to_5class(36) == "kick"
    assert _midi_note_to_5class(38) == "snare"
    assert _midi_note_to_5class(42) == "hi_hat"
    assert _midi_note_to_5class(46) == "hi_hat"
    assert _midi_note_to_5class(41) == "toms"
    assert _midi_note_to_5class(50) == "toms"
    assert _midi_note_to_5class(49) == "cymbals"
    assert _midi_note_to_5class(51) == "cymbals"
    assert _midi_note_to_5class(99) is None  # out of vocabulary


def _case(events: list[DrumEvent]) -> GroundTruthCase:
    return GroundTruthCase(
        case_id="t",
        instrument="drums",
        audio_path=Path("."),
        duration_sec=2.0,
        drum_events=tuple(events),
    )


def test_score_drum_case_per_class_breakdown() -> None:
    ref = [
        DrumEvent(0.0, 36, 100),   # kick
        DrumEvent(0.5, 38, 100),   # snare
        DrumEvent(1.0, 42, 100),   # hi_hat
        DrumEvent(1.5, 42, 100),   # hi_hat (this one gets missed)
        DrumEvent(2.0, 49, 100),   # cymbal (crash)
    ]
    pred = [
        DrumEvent(0.01, 36, 100),  # kick hit
        DrumEvent(0.51, 38, 100),  # snare hit
        DrumEvent(1.02, 42, 100),  # one hi_hat hit
        DrumEvent(2.30, 49, 100),  # cymbal too far -> miss + false pos
    ]
    cs = score_drum_case(
        _case(ref), pred, algorithm_id="x", runtime_sec=0.1, tolerance_sec=0.05
    )
    assert cs.per_class["kick"] == {"tp": 1, "fp": 0, "fn": 0}
    assert cs.per_class["snare"] == {"tp": 1, "fp": 0, "fn": 0}
    assert cs.per_class["hi_hat"] == {"tp": 1, "fp": 0, "fn": 1}
    assert cs.per_class["cymbals"] == {"tp": 0, "fp": 1, "fn": 1}
    assert "toms" not in cs.per_class  # absent class omitted


def test_score_drum_case_fine_class_breakdown_splits_cymbals_and_toms() -> None:
    ref = [
        DrumEvent(0.0, 49, 100),  # crash
        DrumEvent(0.5, 51, 100),  # ride
        DrumEvent(1.0, 48, 100),  # tom1
        DrumEvent(1.5, 41, 100),  # tom3
    ]
    pred = [
        DrumEvent(0.01, 49, 100),  # crash hit
        DrumEvent(0.51, 49, 100),  # predicted crash where ride ref lives
        DrumEvent(1.02, 48, 100),  # tom1 hit
        DrumEvent(1.52, 47, 100),  # predicted tom2 where tom3 ref lives
    ]
    cs = score_drum_case(
        _case(ref), pred, algorithm_id="x", runtime_sec=0.1, tolerance_sec=0.05
    )

    assert cs.per_class["cymbals"] == {"tp": 2, "fp": 0, "fn": 0}
    assert cs.per_class["toms"] == {"tp": 2, "fp": 0, "fn": 0}
    assert cs.per_fine_class["crash"] == {"tp": 1, "fp": 1, "fn": 0}
    assert cs.per_fine_class["ride"] == {"tp": 0, "fp": 0, "fn": 1}
    assert cs.per_fine_class["tom1"] == {"tp": 1, "fp": 0, "fn": 0}
    assert cs.per_fine_class["tom2"] == {"tp": 0, "fp": 1, "fn": 0}
    assert cs.per_fine_class["tom3"] == {"tp": 0, "fp": 0, "fn": 1}


def test_score_drum_case_onset_only_ignores_class() -> None:
    # A kick predicted where the reference has a snare should MISS under
    # class-aware scoring but MATCH under onset-only.
    ref = [DrumEvent(0.0, 38, 100)]           # snare
    pred = [DrumEvent(0.01, 36, 100)]         # kick at same time
    aware = score_drum_case(
        _case(ref), pred, algorithm_id="x", runtime_sec=0.1, tolerance_sec=0.05
    )
    onset = score_drum_case(
        _case(ref), pred, algorithm_id="x", runtime_sec=0.1,
        tolerance_sec=0.05, pitch_aware=False,
    )
    assert (aware.tp, aware.fp, aware.fn) == (0, 1, 1)
    assert (onset.tp, onset.fp, onset.fn) == (1, 0, 0)


def test_summarise_per_class_micro_averages() -> None:
    c1 = score_drum_case(
        _case([DrumEvent(0.0, 42, 100), DrumEvent(0.5, 42, 100)]),
        [DrumEvent(0.01, 42, 100)],  # 1 of 2 hi_hats
        algorithm_id="x", runtime_sec=0.1, tolerance_sec=0.05,
    )
    c2 = score_drum_case(
        _case([DrumEvent(0.0, 42, 100)]),
        [],  # miss the only hi_hat
        algorithm_id="x", runtime_sec=0.1, tolerance_sec=0.05,
    )
    agg = _summarise_per_class([c1, c2])
    # hi_hat: tp=1, fn=2 across both cases -> recall 1/3, support 3.
    assert agg["hi_hat"]["tp"] == 1
    assert agg["hi_hat"]["fn"] == 2
    assert agg["hi_hat"]["support"] == 3
    # recall is reported rounded to 6 dp.
    assert abs(agg["hi_hat"]["recall"] - (1 / 3)) < 1e-6


def test_aggregate_includes_per_class_block() -> None:
    cs = score_drum_case(
        _case([DrumEvent(0.0, 36, 100), DrumEvent(0.5, 38, 100)]),
        [DrumEvent(0.01, 36, 100), DrumEvent(0.51, 38, 100)],
        algorithm_id="alg1", runtime_sec=0.1, tolerance_sec=0.05,
    )
    report = aggregate([cs])
    per_class = report["per_algorithm"]["alg1"]["per_class"]
    assert per_class["kick"]["f1"] == 1.0
    assert per_class["snare"]["f1"] == 1.0
    assert report["per_algorithm"]["alg1"]["per_fine_class"]["kick"]["f1"] == 1.0


def test_combine_report_payloads_reaggregates_non_overlapping_shards() -> None:
    report_a = {
        "dataset": "guitar_techs",
        "family": "melodic",
        "cases": [
            {
                "case_id": "case-a",
                "algorithm_id": "alg1",
                "runtime_sec": 1.0,
                "tp": 3,
                "fp": 1,
                "fn": 2,
                "onset_mae_sec": 0.01,
                "metadata": {"category": "music", "signal": "directinput"},
            },
            {
                "case_id": "case-a",
                "algorithm_id": "alg2",
                "runtime_sec": 2.0,
                "tp": 1,
                "fp": 3,
                "fn": 4,
                "onset_mae_sec": 0.03,
                "metadata": {"category": "music", "signal": "directinput"},
            },
        ],
    }
    report_b = {
        "dataset": "guitar_techs",
        "family": "melodic",
        "cases": [
            {
                "case_id": "case-b",
                "algorithm_id": "alg1",
                "runtime_sec": 3.0,
                "tp": 2,
                "fp": 2,
                "fn": 1,
                "onset_mae_sec": 0.02,
                "metadata": {"category": "techniques", "signal": "directinput"},
            }
        ],
    }

    combined = combine_report_payloads(
        [report_a, report_b],
        source_reports=["a.json", "b.json"],
        extra={"shard": "synthetic"},
    )

    assert combined["case_count"] == 2
    assert combined["extra"]["source_reports"] == ["a.json", "b.json"]
    assert combined["extra"]["shard"] == "synthetic"
    alg1 = combined["summary"]["per_algorithm"]["alg1"]
    assert alg1["tp"] == 5
    assert alg1["fp"] == 3
    assert alg1["fn"] == 3
    assert alg1["f1"] == 0.625
    assert alg1["buckets"]["category"]["music"]["tp"] == 3
    assert combined["summary"]["per_algorithm"]["alg2"]["cases"] == 1


def test_combine_report_payloads_rejects_duplicate_case_algorithm() -> None:
    report = {
        "dataset": "guitar_techs",
        "family": "melodic",
        "cases": [
            {"case_id": "case-a", "algorithm_id": "alg1", "tp": 1, "fp": 0, "fn": 0},
        ],
    }

    with pytest.raises(ValueError, match="duplicate case/algorithm"):
        combine_report_payloads([report, report])
