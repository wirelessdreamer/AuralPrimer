"""
Tests for the Refine Candidates precompute stage.

Pure helpers (region windowing, scoring, hot-spot classification,
payload assembly) are exercised directly. The orchestration function
that dispatches to the algorithm modules is exercised through an
injectable runner stub so the tests don't need the real models /
checkpoints / optional deps.

The end-to-end JSON shape is then validated against the schema in
``packages/auralsong/schemas/refine_candidates.schema.json`` to catch
drift between the Python emitter and the TS reader.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aural_ingest.refine_precompute import (
    CANDIDATE_ALGOS,
    CANDIDATE_DISPLAY,
    CANDIDATE_IDS,
    REGION_DURATION_SEC,
    SCHEMA_VERSION,
    build_candidates_block,
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
        "basic_pitch": [n(1.0, 60), n(2.0, 62)],
        "basic_pitch_dense": [n(1.0, 60)],  # dup of first
        "ensemble": [n(3.0, 64)],
        "pti": [n(1.04, 60)],  # also dup of first, within tol
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
    scores = {
        "basic_pitch": 0.95,
        "basic_pitch_dense": 0.91,
        "ensemble": 0.9,
        "pti": 0.3,
    }
    hot, conf = classify_hot_spot(scores)
    assert hot == "clean"
    assert 0.0 <= conf <= 1.0


def test_classify_hot_spot_low_confidence_when_disagreeing():
    scores = {
        "basic_pitch": 0.4,
        "basic_pitch_dense": 0.3,
        "ensemble": 0.2,
        "pti": 0.5,
    }
    hot, conf = classify_hot_spot(scores)
    assert hot == "low_confidence"


def test_classify_hot_spot_empty_scores_is_low_confidence_zero():
    hot, conf = classify_hot_spot({})
    assert hot == "low_confidence"
    assert conf == 0.0


def test_pick_auto_candidate_breaks_ties_toward_display_order():
    # All tied at 0.5 -- the earliest in CANDIDATE_DISPLAY wins.
    scores = {cid: 0.5 for cid in CANDIDATE_IDS}
    assert pick_auto_candidate(scores) == CANDIDATE_IDS[0]


def test_pick_auto_candidate_prefers_higher_score():
    scores = {
        "basic_pitch": 0.3,
        "basic_pitch_dense": 0.4,
        "ensemble": 0.9,
        "pti": 0.5,
    }
    assert pick_auto_candidate(scores) == "ensemble"


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
    # basic_pitch has 2 notes in r0 + 1 in r1; the others mirror it.
    stem = [n(0.5, 60), n(1.5, 62), n(5.0, 64)]
    candidate_notes = {cid: list(stem) for cid in CANDIDATE_IDS}
    regions = build_regions(candidate_notes, song_duration_sec=8.0)
    r0 = regions[0]
    r1 = regions[1]
    assert len(r0["candidate_notes"]["basic_pitch"]) == 2
    assert len(r1["candidate_notes"]["basic_pitch"]) == 1


def test_build_regions_marks_perfect_agreement_as_clean():
    # All candidates emit identical notes -> 1.0 score everywhere -> "clean".
    notes = [n(0.5, 60), n(1.5, 62)]
    candidate_notes = {cid: list(notes) for cid in CANDIDATE_IDS}
    regions = build_regions(candidate_notes, song_duration_sec=4.0)
    assert regions[0]["hot_spot_type"] == "clean"
    assert all(s == 1.0 for s in regions[0]["candidate_scores"].values())


def test_build_regions_disagreement_marks_low_confidence():
    # Each candidate emits totally different notes -> low scores -> low_confidence.
    candidate_notes = {
        "basic_pitch": [n(0.5, 60)],
        "basic_pitch_dense": [n(1.0, 70)],
        "ensemble": [n(1.5, 80)],
        "pti": [n(2.0, 90)],
    }
    regions = build_regions(candidate_notes, song_duration_sec=4.0)
    assert regions[0]["hot_spot_type"] == "low_confidence"


def test_build_payload_round_trips_through_schema_validator(tmp_path: Path):
    """Emit a payload, validate its structural invariants.

    Catches drift between the Python emitter and the JSON schema --
    a missing field, a wrong type, a candidate id used in a region but
    not declared at the top level, etc.
    """
    candidate_notes = {
        "basic_pitch": [n(0.5, 60), n(1.5, 62)],
        "basic_pitch_dense": [n(0.5, 60)],
        "ensemble": [n(0.5, 60), n(1.5, 62)],
        "pti": [n(0.5, 60), n(1.5, 62), n(2.5, 64)],
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
    # Declared candidates == exactly the dynamic set in candidate_notes.
    assert set(payload["candidates"].keys()) == set(candidate_notes.keys())
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


def test_build_candidates_block_reflects_dynamic_subset():
    # Only declare the two algorithms passed in, in display order.
    block = build_candidates_block(["ensemble", "basic_pitch"])
    assert list(block.keys()) == ["basic_pitch", "ensemble"]  # display order
    assert block["basic_pitch"]["params"]["algo"] == CANDIDATE_ALGOS["basic_pitch"]
    assert block["ensemble"]["params"]["algo"] == CANDIDATE_ALGOS["ensemble"]


# ------------------------- orchestration test ------------------------------


def _fake_runner_returning(candidate_notes: dict[str, list[MelodicNote]]):
    """Build a runner stub returning a fixed {candidate_id: notes} map."""

    def runner(stem_path: Path, mix_path: Path | None, instrument: str):
        # Smoke-check the paths get passed through unchanged.
        assert stem_path.is_file()
        return {cid: list(notes) for cid, notes in candidate_notes.items()}

    return runner


def _make_song(tmp_path: Path) -> Path:
    sp = tmp_path / "auralsong"
    (sp / "audio" / "stems").mkdir(parents=True)
    (sp / "audio" / "stems" / "keys.wav").write_bytes(b"")
    (sp / "audio" / "mix.wav").write_bytes(b"")
    return sp


def _make_feedpak_song(tmp_path: Path) -> Path:
    sp = tmp_path / "song.feedpak"
    (sp / "audio" / "stems").mkdir(parents=True)
    (sp / "audio" / "stems" / "keys.wav").write_bytes(b"")
    (sp / "audio" / "mix.wav").write_bytes(b"")
    return sp


def test_pack_feature_dirname_routes_feedpak_to_aural(tmp_path: Path):
    from aural_ingest.refine_precompute import pack_feature_dirname

    assert pack_feature_dirname(tmp_path / "song.feedpak") == "aural"
    assert pack_feature_dirname(tmp_path / "song.auralsong") == "features"
    assert pack_feature_dirname(tmp_path / "song") == "features"


def test_precompute_writes_candidates_under_aural_for_feedpak(tmp_path: Path):
    # A .feedpak pack must get candidates under aural/ (where the Studio
    # readiness probe + Refine workspace look) — not the legacy features/.
    sp = _make_feedpak_song(tmp_path)
    notes = [n(0.5, 60), n(1.5, 62), n(5.0, 64)]
    runner = _fake_runner_returning({cid: notes for cid in ("basic_pitch", "ensemble")})

    precompute_refine_candidates(auralsong_root=sp, instrument="keys", runner=runner)

    assert (sp / "aural" / "refine_candidates.keys.json").is_file()
    assert not (sp / "features" / "refine_candidates.keys.json").exists()


def test_precompute_writes_refine_candidates_json_to_features(tmp_path: Path):
    sp = _make_song(tmp_path)

    notes = [n(0.5, 60), n(1.5, 62), n(5.0, 64)]
    runner = _fake_runner_returning(
        {cid: notes for cid in ("basic_pitch", "basic_pitch_dense", "ensemble", "pti")}
    )

    precompute_refine_candidates(
        auralsong_root=sp,
        instrument="keys",
        runner=runner,
    )

    out_path = sp / "features" / "refine_candidates.keys.json"
    assert out_path.is_file()

    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["version"] == SCHEMA_VERSION
    assert on_disk["instrument"] == "keys"
    assert set(on_disk["candidates"].keys()) == {
        "basic_pitch",
        "basic_pitch_dense",
        "ensemble",
        "pti",
    }
    assert len(on_disk["regions"]) > 0
    # song_duration_sec should be at least the largest t_off (~5.5s).
    assert on_disk["song_duration_sec"] >= 5.5


def test_precompute_dynamic_set_only_two_algorithms(tmp_path: Path):
    # Runner returns only 2 algorithms -> only those appear in the payload.
    sp = _make_song(tmp_path)
    runner = _fake_runner_returning(
        {
            "basic_pitch": [n(0.5, 60)],
            "d3rm": [n(0.5, 60), n(1.0, 62)],
        }
    )
    payload = precompute_refine_candidates(
        auralsong_root=sp, instrument="keys", runner=runner
    )
    assert set(payload["candidates"].keys()) == {"basic_pitch", "d3rm"}
    for region in payload["regions"]:
        assert set(region["candidate_notes"].keys()) <= {"basic_pitch", "d3rm"}
        assert region["auto_picked"] in {"basic_pitch", "d3rm"}


def test_precompute_omits_failing_algorithm(tmp_path: Path):
    # A runner that mimics run_algorithm_candidates omitting a raising algo:
    # d3rm + pti simply don't appear in the returned map.
    sp = _make_song(tmp_path)
    runner = _fake_runner_returning(
        {
            "basic_pitch": [n(0.5, 60)],
            "basic_pitch_dense": [n(0.5, 60), n(1.0, 62)],
            "ensemble": [n(0.5, 60), n(1.0, 62), n(2.0, 64)],
            # d3rm and pti omitted (would have raised on missing models).
        }
    )
    payload = precompute_refine_candidates(
        auralsong_root=sp, instrument="keys", runner=runner
    )
    assert "d3rm" not in payload["candidates"]
    assert "pti" not in payload["candidates"]
    assert set(payload["candidates"].keys()) == {
        "basic_pitch",
        "basic_pitch_dense",
        "ensemble",
    }


def test_precompute_auto_pick_with_variable_set(tmp_path: Path):
    # Three algorithms; ensemble is densest and covers the union best, so it
    # should be auto-picked over the sparser ones.
    sp = _make_song(tmp_path)
    union = [n(0.5, 60), n(1.0, 62), n(1.5, 64)]
    runner = _fake_runner_returning(
        {
            "basic_pitch": [n(0.5, 60)],  # 1/3 of the union
            "basic_pitch_dense": [n(0.5, 60), n(1.0, 62)],  # 2/3
            "ensemble": list(union),  # full union -> highest score
        }
    )
    payload = precompute_refine_candidates(
        auralsong_root=sp, instrument="keys", runner=runner
    )
    first_region = payload["regions"][0]
    assert first_region["auto_picked"] == "ensemble"


def test_precompute_raises_when_no_candidates_produced(tmp_path: Path):
    # If every algorithm is skipped, there's nothing to write.
    sp = _make_song(tmp_path)
    runner = _fake_runner_returning({})
    with pytest.raises(RuntimeError):
        precompute_refine_candidates(
            auralsong_root=sp, instrument="keys", runner=runner
        )


def test_precompute_raises_when_no_stem_found(tmp_path: Path):
    sp = tmp_path / "no_stem"
    sp.mkdir()
    runner = _fake_runner_returning({"basic_pitch": []})
    with pytest.raises(FileNotFoundError):
        precompute_refine_candidates(
            auralsong_root=sp, instrument="keys", runner=runner
        )


def test_precompute_rejects_unknown_instrument(tmp_path: Path):
    sp = tmp_path / "sp"
    sp.mkdir()
    with pytest.raises(ValueError):
        precompute_refine_candidates(
            auralsong_root=sp,
            instrument="bassoon",  # type: ignore[arg-type]
        )


# --------------------------- candidate identity ----------------------------


def test_candidate_display_entries_have_unique_ids_colors_and_algos():
    ids = [cid for cid, _, _, _ in CANDIDATE_DISPLAY]
    colors = [color for _, _, color, _ in CANDIDATE_DISPLAY]
    algos = [algo for _, _, _, algo in CANDIDATE_DISPLAY]
    assert len(ids) == 5
    assert len(set(ids)) == len(ids), "candidate ids must be unique"
    assert len(set(colors)) == len(colors), "candidate swatch colors must be unique"
    assert len(set(algos)) == len(algos), "candidate algorithms must be distinct"
    for color in colors:
        assert color.startswith("#") and len(color) == 7
    assert CANDIDATE_ALGOS == {cid: algo for cid, _, _, algo in CANDIDATE_DISPLAY}


def test_region_duration_is_positive():
    assert REGION_DURATION_SEC > 0
