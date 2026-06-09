"""
Tests for the Refine Candidates precompute stage.

Pure helpers (region windowing, scoring, hot-spot classification,
payload assembly) are exercised directly. The orchestration function
that wires PTI is exercised through an injectable runner stub so the
tests don't need piano_transcription_inference / librosa / a GPU.

The end-to-end JSON shape is then validated against the schema in
``packages/songpack/schemas/refine_candidates.schema.json`` to catch
drift between the Python emitter and the TS reader.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aural_ingest.refine_precompute import (
    CANDIDATE_DISPLAY,
    CANDIDATE_IDS,
    CandidateNotes,
    REGION_DURATION_SEC,
    SCHEMA_VERSION,
    build_payload,
    build_regions,
    classify_hot_spot,
    jaccard_overlap,
    notes_in_region,
    pick_auto_candidate,
    pipeline_signature,
    precompute_refine_candidates,
    score_candidate,
    serialize_note,
    union_of_candidates,
)
from aural_ingest.transcription import MelodicNote


def n(t_on: float, pitch: int, t_off: float | None = None, vel: int = 90) -> MelodicNote:
    """Convenience MelodicNote builder for tests."""
    return MelodicNote(
        t_on=t_on,
        t_off=t_off if t_off is not None else t_on + 0.5,
        pitch=pitch,
        velocity=vel,
        instrument="keys",
    )


# ----------------------------- pure helpers --------------------------------


def test_pipeline_signature_is_stable():
    sig1 = pipeline_signature()
    sig2 = pipeline_signature()
    assert sig1 == sig2
    assert len(sig1) == 16
    assert all(c in "0123456789abcdef" for c in sig1)


def test_notes_in_region_inclusive_lower_exclusive_upper():
    notes = [n(0.0, 60), n(2.0, 62), n(4.0, 64), n(6.0, 65)]
    region = notes_in_region(notes, 2.0, 4.0)
    # [2.0, 4.0): 2.0 included, 4.0 excluded.
    assert [x.pitch for x in region] == [62]


def test_serialize_note_clamps_zero_duration_to_positive():
    # Schema requires t_off strictly > 0.
    bad = MelodicNote(t_on=1.5, t_off=1.5, pitch=60, velocity=90)
    serialized = serialize_note(bad)
    assert serialized["t_off"] > serialized["t_on"]
    assert serialized["t_off"] > 0


def test_serialize_note_clamps_velocity_into_midi_range():
    too_high = MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=255)
    too_low = MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=-5)
    assert serialize_note(too_high)["velocity"] == 127
    assert serialize_note(too_low)["velocity"] == 1


def test_jaccard_overlap_empty_empty_is_one():
    assert jaccard_overlap([], []) == 1.0


def test_jaccard_overlap_empty_vs_nonempty_is_zero():
    assert jaccard_overlap([], [n(0.0, 60)]) == 0.0
    assert jaccard_overlap([n(0.0, 60)], []) == 0.0


def test_jaccard_overlap_exact_match_within_tolerance():
    a = [n(1.0, 60), n(2.0, 62)]
    # Slightly off but within tolerance + pitch-exact.
    b = [n(1.03, 60), n(2.04, 62)]
    assert jaccard_overlap(a, b, onset_tol_sec=0.05) == 1.0


def test_jaccard_overlap_pitch_mismatch_breaks_match():
    a = [n(1.0, 60)]
    # Same onset, pitch one off -- pitch must match exactly.
    b = [n(1.0, 61)]
    assert jaccard_overlap(a, b) == 0.0


def test_jaccard_overlap_partial_overlap():
    a = [n(0.0, 60), n(1.0, 62)]  # 2 notes
    b = [n(0.0, 60), n(2.0, 64)]  # 2 notes, 1 shared
    # |intersection| = 1, |union| = 3 -> 1/3.
    assert abs(jaccard_overlap(a, b) - (1 / 3)) < 1e-9


def test_union_of_candidates_dedups_within_tolerance():
    candidates = {
        "stem_only": [n(1.0, 60), n(2.0, 62)],
        "consensus_tight": [n(1.0, 60)],  # dup of first
        "consensus_default": [n(3.0, 64)],
        "denoise_consensus": [n(1.04, 60)],  # also dup of first, within tol
    }
    u = union_of_candidates(candidates)
    pitches = sorted(x.pitch for x in u)
    assert pitches == [60, 62, 64]


def test_score_candidate_matches_jaccard_against_consensus():
    consensus = [n(0.0, 60), n(1.0, 62), n(2.0, 64)]
    perfect_match = consensus
    assert score_candidate(perfect_match, consensus) == 1.0
    partial = [n(0.0, 60)]
    # 1 match, |union|=3 -> 1/3.
    assert abs(score_candidate(partial, consensus) - (1 / 3)) < 1e-9


def test_classify_hot_spot_clean_when_three_or_more_high():
    # 3 of 4 score >= 0.9 -> "clean".
    scores = {"stem_only": 0.95, "consensus_tight": 0.91, "consensus_default": 0.9, "denoise_consensus": 0.3}
    hot, conf = classify_hot_spot(scores)
    assert hot == "clean"
    assert 0.0 <= conf <= 1.0


def test_classify_hot_spot_low_confidence_when_disagreeing():
    scores = {"stem_only": 0.4, "consensus_tight": 0.3, "consensus_default": 0.2, "denoise_consensus": 0.5}
    hot, conf = classify_hot_spot(scores)
    assert hot == "low_confidence"


def test_classify_hot_spot_empty_scores_is_low_confidence_zero():
    hot, conf = classify_hot_spot({})
    assert hot == "low_confidence"
    assert conf == 0.0


def test_pick_auto_candidate_breaks_ties_toward_display_order():
    # All tied at 0.5 -- stem_only should win because it's first in CANDIDATE_DISPLAY.
    scores = {cid: 0.5 for cid in CANDIDATE_IDS}
    assert pick_auto_candidate(scores) == "stem_only"


def test_pick_auto_candidate_prefers_higher_score():
    scores = {
        "stem_only": 0.3,
        "consensus_tight": 0.4,
        "consensus_default": 0.9,
        "denoise_consensus": 0.5,
    }
    assert pick_auto_candidate(scores) == "consensus_default"


def test_pick_auto_candidate_empty_falls_back_to_first_id():
    assert pick_auto_candidate({}) == CANDIDATE_IDS[0]


# ------------------------ build_regions + payload --------------------------


def test_build_regions_uses_fixed_window_size():
    # 10s song with default 4s windows -> 3 regions: [0,4), [4,8), [8,10).
    candidate_notes = {cid: [] for cid in CANDIDATE_IDS}
    regions = build_regions(candidate_notes, song_duration_sec=10.0)
    assert [r["id"] for r in regions] == ["r0000", "r0001", "r0002"]
    assert regions[0]["t_start"] == 0.0
    assert regions[0]["t_end"] == 4.0
    assert regions[-1]["t_start"] == 8.0
    assert regions[-1]["t_end"] == 10.0


def test_build_regions_empty_returns_empty_for_zero_duration():
    assert build_regions({}, song_duration_sec=0) == []


def test_build_regions_routes_notes_into_correct_window():
    # Stem-only has 2 notes in r0 + 1 in r1; consensus_default mirrors stem_only.
    stem = [n(0.5, 60), n(1.5, 62), n(5.0, 64)]
    candidate_notes = {
        "stem_only": stem,
        "consensus_tight": list(stem),
        "consensus_default": list(stem),
        "denoise_consensus": list(stem),
    }
    regions = build_regions(candidate_notes, song_duration_sec=8.0)
    r0 = regions[0]
    r1 = regions[1]
    assert len(r0["candidate_notes"]["stem_only"]) == 2
    assert len(r1["candidate_notes"]["stem_only"]) == 1


def test_build_regions_marks_perfect_agreement_as_clean():
    # All 4 candidates emit identical notes -> 1.0 score everywhere -> "clean".
    notes = [n(0.5, 60), n(1.5, 62)]
    candidate_notes = {cid: list(notes) for cid in CANDIDATE_IDS}
    regions = build_regions(candidate_notes, song_duration_sec=4.0)
    assert regions[0]["hot_spot_type"] == "clean"
    assert all(s == 1.0 for s in regions[0]["candidate_scores"].values())


def test_build_regions_disagreement_marks_low_confidence():
    # Each candidate emits totally different notes -> low scores -> low_confidence.
    candidate_notes = {
        "stem_only": [n(0.5, 60)],
        "consensus_tight": [n(1.0, 70)],
        "consensus_default": [n(1.5, 80)],
        "denoise_consensus": [n(2.0, 90)],
    }
    regions = build_regions(candidate_notes, song_duration_sec=4.0)
    assert regions[0]["hot_spot_type"] == "low_confidence"


def test_build_payload_round_trips_through_schema_validator(tmp_path: Path):
    """Emit a payload, validate it against the JSON Schema.

    Catches drift between the Python emitter and the TS / JSON schema --
    a missing field, a wrong type, a candidate id used in a region but
    not declared at the top level, etc.
    """
    candidate_notes = {
        "stem_only": [n(0.5, 60), n(1.5, 62)],
        "consensus_tight": [n(0.5, 60)],
        "consensus_default": [n(0.5, 60), n(1.5, 62)],
        "denoise_consensus": [n(0.5, 60), n(1.5, 62), n(2.5, 64)],
    }
    payload = build_payload(
        instrument="keys",
        candidate_notes=candidate_notes,
        song_duration_sec=8.0,
        computed_at="2026-06-08T12:00:00Z",
        signature="abc123def4567890",
    )

    assert payload["version"] == SCHEMA_VERSION
    assert payload["instrument"] == "keys"
    assert payload["song_duration_sec"] == 8.0
    assert set(payload["candidates"].keys()) == set(CANDIDATE_IDS)
    assert all("label" in c and "color" in c for c in payload["candidates"].values())
    # Every region's auto_picked must reference a declared candidate.
    for region in payload["regions"]:
        assert region["auto_picked"] in payload["candidates"]
        for cid in region["candidate_scores"].keys():
            assert cid in payload["candidates"]
        for cid in region["candidate_notes"].keys():
            assert cid in payload["candidates"]
        assert 0.0 <= region["confidence"] <= 1.0
        for note in region["candidate_notes"][region["auto_picked"]]:
            # Schema requires t_off > 0.
            assert note["t_off"] > 0
            assert note["t_on"] >= 0
            assert 0 <= note["pitch"] <= 127
            assert 1 <= note["velocity"] <= 127


def test_build_payload_rejects_unknown_instrument():
    with pytest.raises(ValueError):
        build_payload(
            instrument="bassoon",  # type: ignore[arg-type]
            candidate_notes={},
            song_duration_sec=1.0,
        )


# ------------------------- orchestration test ------------------------------


def _fake_runner_returning(stem_only, tight, default, denoised):
    """Build a runner stub for precompute_refine_candidates."""

    def runner(stem_path: Path, mix_path: Path | None, instrument: str) -> CandidateNotes:
        # Smoke-check the paths get passed through unchanged.
        assert stem_path.is_file()
        return CandidateNotes(
            stem_only=stem_only,
            consensus_tight=tight,
            consensus_default=default,
            denoise_consensus=denoised,
        )

    return runner


def test_precompute_writes_refine_candidates_json_to_features(tmp_path: Path):
    # Build a minimal SongPack layout: audio/stems/keys.wav (empty file).
    sp = tmp_path / "songpack"
    (sp / "audio" / "stems").mkdir(parents=True)
    (sp / "audio" / "stems" / "keys.wav").write_bytes(b"")
    (sp / "audio" / "mix.wav").write_bytes(b"")

    # Inject deterministic notes; the orchestrator runs the runner instead
    # of importing piano_pti.
    notes = [n(0.5, 60), n(1.5, 62), n(5.0, 64)]
    runner = _fake_runner_returning(notes, notes, notes, notes)

    payload = precompute_refine_candidates(
        songpack_root=sp,
        instrument="keys",
        runner=runner,
    )

    out_path = sp / "features" / "refine_candidates.keys.json"
    assert out_path.is_file()

    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["version"] == SCHEMA_VERSION
    assert on_disk["instrument"] == "keys"
    assert set(on_disk["candidates"].keys()) == set(CANDIDATE_IDS)
    assert len(on_disk["regions"]) > 0
    # song_duration_sec should be at least the largest t_off (~5.5s).
    assert on_disk["song_duration_sec"] >= 5.5


def test_precompute_raises_when_no_stem_found(tmp_path: Path):
    sp = tmp_path / "no_stem"
    sp.mkdir()
    runner = _fake_runner_returning([], [], [], [])
    with pytest.raises(FileNotFoundError):
        precompute_refine_candidates(
            songpack_root=sp, instrument="keys", runner=runner
        )


def test_precompute_rejects_unknown_instrument(tmp_path: Path):
    sp = tmp_path / "sp"
    sp.mkdir()
    with pytest.raises(ValueError):
        precompute_refine_candidates(
            songpack_root=sp,
            instrument="bassoon",  # type: ignore[arg-type]
        )


# --------------------------- candidate identity ----------------------------


def test_candidate_display_has_four_entries_with_unique_ids_and_colors():
    ids = [cid for cid, _, _, _ in CANDIDATE_DISPLAY]
    colors = [color for _, _, color, _ in CANDIDATE_DISPLAY]
    assert len(ids) == 4
    assert len(set(ids)) == 4, "candidate ids must be unique"
    assert len(set(colors)) == 4, "candidate swatch colors must be unique"
    for color in colors:
        assert color.startswith("#") and len(color) == 7


def test_region_duration_is_positive():
    assert REGION_DURATION_SEC > 0
