"""Unit tests for the multi-part -> two-hand piano reduction.

The reduction adds no dependency and runs no model, so everything here is
exercised for real -- there is nothing to fake. The contracts under test:

  (a) cross-part unison merge + provenance (the corroboration signal);
  (b) octave-doubling collapse keeps the outer voices;
  (c) hand assignment is non-crossing and inside the hand model;
  (d) the end-to-end pass leaves nothing unplayable;
  (e) pack I/O degrades to a clear message instead of a crash.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aural_ingest.algorithms import piano_reduction as pr
from aural_ingest.algorithms.piano_playability import (
    group_by_onset,
    is_hand_feasible,
    note_key,
)
from aural_ingest.transcription import MelodicNote


def note(t_on: float, pitch: int, *, dur: float = 0.5, velocity: int = 90) -> MelodicNote:
    return MelodicNote(
        t_on=t_on, t_off=t_on + dur, pitch=pitch, velocity=velocity, instrument="src"
    )


# --------------------------------------------------------------------------- #
# fold_into_keyboard
# --------------------------------------------------------------------------- #


def test_fold_into_keyboard_leaves_in_range_pitches_alone() -> None:
    for pitch in (21, 60, 108):
        assert pr.fold_into_keyboard(pitch) == pitch


@pytest.mark.parametrize("pitch", [0, 5, 12, 20, 109, 120, 127])
def test_fold_into_keyboard_preserves_pitch_class(pitch: int) -> None:
    """Folding, not clamping: a clamp would invent a pitch class."""
    folded = pr.fold_into_keyboard(pitch)
    assert pr.KEYBOARD_MIN <= folded <= pr.KEYBOARD_MAX
    assert folded % 12 == pitch % 12


# --------------------------------------------------------------------------- #
# merge_parts
# --------------------------------------------------------------------------- #


def test_merge_parts_collapses_a_cross_part_unison_into_one_note() -> None:
    """Guitar and keys on the same pitch is one key, one finger -- and two votes."""
    parts = {
        "rhythm_guitar": [note(0.0, 60, dur=0.4, velocity=70)],
        "keys": [note(0.01, 60, dur=0.9, velocity=110)],
    }
    merged, provenance = pr.merge_parts(parts)

    assert len(merged) == 1
    only = merged[0]
    assert only.pitch == 60
    assert only.t_on == pytest.approx(0.0)          # union: earliest onset
    assert only.t_off == pytest.approx(0.91)        # union: latest release
    assert only.velocity == 110                     # loudest wins
    assert only.instrument == "keys"                # higher role prior wins the label
    assert provenance[note_key(only)] == ["keys", "rhythm_guitar"]


def test_merge_parts_keeps_a_restruck_key_as_two_notes() -> None:
    """Same pitch, far apart: a re-struck key is a real second note."""
    parts = {"keys": [note(0.0, 60, dur=0.2), note(1.0, 60, dur=0.2)]}
    merged, provenance = pr.merge_parts(parts)

    assert [n.t_on for n in merged] == [0.0, 1.0]
    assert all(len(roles) == 1 for roles in provenance.values())


def test_merge_parts_drops_drums_and_folds_out_of_range_pitches() -> None:
    parts = {
        "drums": [note(0.0, 36)],
        "bass": [note(0.0, 9)],  # below A0 -- an octave error, not a real pitch
    }
    merged, _ = pr.merge_parts(parts)

    assert [n.pitch for n in merged] == [21]  # 9 -> 21, pitch class preserved
    assert all(n.instrument != "drums" for n in merged)


def test_merge_parts_records_every_contributing_role() -> None:
    parts = {
        "bass": [note(0.0, 48)],
        "rhythm_guitar": [note(0.0, 48)],
        "keys": [note(0.0, 48)],
    }
    merged, provenance = pr.merge_parts(parts)

    assert len(merged) == 1
    assert provenance[note_key(merged[0])] == ["bass", "keys", "rhythm_guitar"]


def test_merge_parts_cluster_is_anchored_not_chained() -> None:
    """A run of near-onsets must not walk into one held note."""
    window = pr.DEFAULT_REDUCTION_CONFIG.merge_window_sec
    step = window * 0.9
    parts = {"keys": [note(i * step, 60, dur=0.01) for i in range(6)]}

    merged, _ = pr.merge_parts(parts)

    # Chaining would collapse all six; anchoring bounds each cluster to `window`.
    assert len(merged) > 1
    for merged_note in merged:
        assert merged_note.t_on - merged[0].t_on >= 0.0
    spans = [n.t_off - n.t_on for n in merged]
    assert max(spans) <= window + 0.02


# --------------------------------------------------------------------------- #
# collapse_octave_doublings
# --------------------------------------------------------------------------- #


def test_collapse_octave_doublings_keeps_the_outer_voices() -> None:
    """Bass anchor at the bottom, melody at the top; the middle octave goes."""
    notes = [note(0.0, 36), note(0.0, 48), note(0.0, 60), note(0.0, 72)]
    kept, removed = pr.collapse_octave_doublings(notes)

    assert removed == 2
    assert [n.pitch for n in kept] == [36, 72]


def test_collapse_octave_doublings_leaves_a_within_limit_chord_alone() -> None:
    notes = [note(0.0, 60), note(0.0, 64), note(0.0, 67), note(0.0, 72)]
    kept, removed = pr.collapse_octave_doublings(notes)

    assert removed == 0
    assert [n.pitch for n in kept] == [60, 64, 67, 72]


def test_collapse_octave_doublings_is_per_attack_not_global() -> None:
    """Doubling the same pitch class in a later bar is not a doubling."""
    notes = [note(0.0, 48), note(0.0, 60), note(2.0, 48), note(2.0, 60)]
    kept, removed = pr.collapse_octave_doublings(notes)

    assert removed == 0
    assert len(kept) == 4


def test_collapse_octave_doublings_honours_the_limit() -> None:
    """A looser limit cuts less, and still cuts from the middle outward."""
    config = pr.ReductionConfig(max_octave_doublings=2)
    notes = [note(0.0, 36), note(0.0, 48), note(0.0, 60), note(0.0, 72)]
    kept, removed = pr.collapse_octave_doublings(notes, config=config)

    assert removed == 1
    pitches = [n.pitch for n in kept]
    assert len(pitches) == 3
    assert 36 in pitches and 72 in pitches  # the outer voices always survive


# --------------------------------------------------------------------------- #
# primary_role
# --------------------------------------------------------------------------- #


def test_primary_role_picks_the_melody_bearing_part() -> None:
    parts = {
        "bass": [note(0.0, 40)],
        "rhythm_guitar": [note(0.0, 52)],
        "vocals": [note(0.0, 72)],
    }
    assert pr.primary_role(parts) == "vocals"


def test_primary_role_ignores_empty_and_dropped_parts() -> None:
    parts = {"vocals": [], "drums": [note(0.0, 36)], "keys": [note(0.0, 60)]}
    assert pr.primary_role(parts) == "keys"


def test_primary_role_survives_an_empty_score() -> None:
    assert pr.primary_role({}) == "melodic"


# --------------------------------------------------------------------------- #
# assign_hands
# --------------------------------------------------------------------------- #


def test_assign_hands_splits_bass_from_melody_without_crossing() -> None:
    notes = [note(0.0, 40), note(0.0, 43), note(0.0, 72), note(0.0, 76)]
    left, right = pr.assign_hands(notes)

    assert [n.pitch for n in left] == [40, 43]
    assert [n.pitch for n in right] == [72, 76]
    assert max(n.pitch for n in left) < min(n.pitch for n in right)


def test_assign_hands_labels_each_note_with_its_hand() -> None:
    left, right = pr.assign_hands([note(0.0, 40), note(0.0, 76)])

    assert all(n.instrument == pr.LEFT_HAND_ROLE for n in left)
    assert all(n.instrument == pr.RIGHT_HAND_ROLE for n in right)


def test_assign_hands_gives_a_lone_low_note_to_the_left_hand() -> None:
    """`hand_split` parks an unsplittable run on the right; register decides."""
    left, right = pr.assign_hands([note(0.0, 40)])

    assert [n.pitch for n in left] == [40]
    assert right == []


def test_assign_hands_gives_a_lone_high_note_to_the_right_hand() -> None:
    left, right = pr.assign_hands([note(0.0, 76)])

    assert left == []
    assert [n.pitch for n in right] == [76]


def test_assign_hands_never_exceeds_the_hand_model() -> None:
    config = pr.DEFAULT_REDUCTION_CONFIG
    notes = [note(0.0, p) for p in (40, 44, 47, 64, 67, 71, 76)]
    assert is_hand_feasible([n.pitch for n in notes], config=config.playability)

    left, right = pr.assign_hands(notes, config=config)
    for hand in (left, right):
        for group in group_by_onset(
            hand, window_sec=config.playability.onset_window_sec
        ):
            pitches = [n.pitch for n in group]
            assert len(pitches) <= config.playability.max_notes_per_hand
            assert max(pitches) - min(pitches) <= config.playability.max_hand_span


def test_assign_hands_keeps_every_note() -> None:
    notes = [note(0.0, p) for p in (40, 52, 60, 67, 76)]
    left, right = pr.assign_hands(notes)

    assert len(left) + len(right) == len(notes)
    assert sorted(n.pitch for n in [*left, *right]) == sorted(n.pitch for n in notes)


# --------------------------------------------------------------------------- #
# reduce_score -- end to end
# --------------------------------------------------------------------------- #


def _dense_mix() -> dict[str, list[MelodicNote]]:
    """A Jireh-shaped mix: guitar and keys stating the same harmony, plus a tune."""
    return {
        "bass": [note(0.0, 40), note(1.0, 45)],
        "rhythm_guitar": [
            note(0.0, 52), note(0.0, 56), note(0.0, 59),
            note(1.0, 57), note(1.0, 60), note(1.0, 64),
        ],
        "keys": [
            note(0.0, 52), note(0.0, 56), note(0.0, 64),
            note(1.0, 57), note(1.0, 60), note(1.0, 69),
        ],
        "vocals": [note(0.0, 71), note(1.0, 72)],
        "drums": [note(0.0, 36)],
    }


def test_reduce_score_output_is_playable() -> None:
    reduction = pr.reduce_score(_dense_mix())

    assert reduction.report["playability"]["after"]["unplayable_groups"] == 0
    for group in group_by_onset(reduction.notes):
        assert is_hand_feasible([n.pitch for n in group])


def test_reduce_score_counts_the_cross_part_agreement() -> None:
    reduction = pr.reduce_score(_dense_mix())

    # 52/56 at t=0 and 57/60 at t=1 are played by both guitar and keys.
    assert reduction.report["unisons_merged"] == 4
    assert reduction.report["corroborated_notes"] == 4


def test_reduce_score_feeds_cross_part_agreement_to_the_salience_model(monkeypatch) -> None:
    """The corroboration signal has to reach the ranking, not just the report.

    Asserted at the hand-off rather than on a specific survivor: which note wins
    depends on `piano_playability`'s salience weights, which that module
    documents as a sweep starting point rather than a fixed claim. Pinning a
    winner here would make this test fail whenever those weights are retuned,
    which is not what it is for.
    """
    seen: dict[str, object] = {}
    real = pr.make_playable

    def spy(notes, **kwargs):
        seen.update(kwargs)
        return real(notes, **kwargs)

    monkeypatch.setattr(pr, "make_playable", spy)
    pr.reduce_score(_dense_mix())

    provenance = seen["provenance"]
    assert seen["primary_source"] == "vocals"
    assert any(len(roles) > 1 for roles in provenance.values())
    assert {"keys", "rhythm_guitar"} in [set(r) for r in provenance.values()]


def test_reduce_score_skips_drums_and_names_the_primary() -> None:
    reduction = pr.reduce_score(_dense_mix())

    assert reduction.report["skipped_parts"] == {"drums": 1}
    assert reduction.report["primary_source"] == "vocals"
    assert reduction.report["notes_in"] == 16


def test_reduce_score_hands_are_non_crossing_and_within_the_model() -> None:
    reduction = pr.reduce_score(_dense_mix())
    hands = reduction.report["hands"]

    assert hands["left_max_span"] <= hands["max_hand_span"]
    assert hands["right_max_span"] <= hands["max_hand_span"]
    assert hands["left_max_fingers"] <= hands["max_notes_per_hand"]
    assert hands["right_max_fingers"] <= hands["max_notes_per_hand"]
    assert hands["left_notes"] + hands["right_notes"] == reduction.report["notes_out"]


def test_reduce_score_reduces_a_dense_mix() -> None:
    reduction = pr.reduce_score(_dense_mix())

    assert reduction.report["notes_out"] < reduction.report["notes_in"]
    assert reduction.report["merged_notes"] < reduction.report["notes_in"]


def test_reduce_score_on_an_empty_score_is_empty_not_an_error() -> None:
    reduction = pr.reduce_score({"keys": []})

    assert reduction.left_hand == []
    assert reduction.right_hand == []
    assert reduction.report["notes_out"] == 0


def test_reduce_score_is_deterministic() -> None:
    first = pr.reduce_score(_dense_mix())
    second = pr.reduce_score(_dense_mix())

    assert [(n.t_on, n.pitch) for n in first.notes] == [
        (n.t_on, n.pitch) for n in second.notes
    ]


def test_reduce_score_without_context_still_runs() -> None:
    """No beat grid, no harmony, no audio evidence -- less informed, not broken."""
    reduction = pr.reduce_score(_dense_mix(), harmony=None, beats=None, evidence=None)
    assert reduction.report["notes_out"] > 0


def test_notes_property_is_time_ordered() -> None:
    reduction = pr.reduce_score(_dense_mix())
    times = [n.t_on for n in reduction.notes]
    assert times == sorted(times)


# --------------------------------------------------------------------------- #
# MIDI + pack I/O
# --------------------------------------------------------------------------- #


def test_role_for_track_name_maps_the_pipeline_track_names() -> None:
    assert pr.role_for_track_name("Rhythm Guitar") == "rhythm_guitar"
    assert pr.role_for_track_name("bass") == "bass"
    assert pr.role_for_track_name("Vocals") == "vocals"


def test_role_for_track_name_keeps_an_unknown_track() -> None:
    """An unrecognised track still played notes; ranking it low beats dropping it."""
    assert pr.role_for_track_name("Pedal Steel") == "pedal_steel"
    assert pr.role_for_track_name("") == "melodic"


def _write_score_midi(path: Path, parts: dict[str, list[MelodicNote]]) -> None:
    pretty_midi = pytest.importorskip("pretty_midi")
    from aural_ingest.arrangement_prep import ROLE_TRACK_NAME

    midi = pretty_midi.PrettyMIDI()
    for role, notes in parts.items():
        instrument = pretty_midi.Instrument(
            program=0, is_drum=(role == "drums"), name=ROLE_TRACK_NAME.get(role, role)
        )
        for n in notes:
            instrument.notes.append(
                pretty_midi.Note(
                    velocity=n.velocity, pitch=n.pitch, start=n.t_on, end=n.t_off
                )
            )
        midi.instruments.append(instrument)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.write(str(path))


def test_parts_from_midi_round_trips_roles_and_skips_drums(tmp_path: Path) -> None:
    pytest.importorskip("pretty_midi")
    source = tmp_path / "notes.mid"
    _write_score_midi(source, _dense_mix())

    parts = pr.parts_from_midi(source)

    assert "drums" not in parts
    assert set(parts) == {"bass", "rhythm_guitar", "keys", "vocals"}
    assert len(parts["rhythm_guitar"]) == 6
    assert {n.pitch for n in parts["bass"]} == {40, 45}


def test_write_reduction_midi_writes_two_named_hands(tmp_path: Path) -> None:
    pretty_midi = pytest.importorskip("pretty_midi")
    reduction = pr.reduce_score(_dense_mix())

    out = pr.write_reduction_midi(reduction, tmp_path / "piano_reduction.mid")

    assert out.is_file()
    written = pretty_midi.PrettyMIDI(str(out))
    assert [i.name for i in written.instruments] == [
        pr.RIGHT_HAND_TRACK,
        pr.LEFT_HAND_TRACK,
    ]
    assert sum(len(i.notes) for i in written.instruments) == reduction.report["notes_out"]
    assert not any(i.is_drum for i in written.instruments)


def test_reduce_pack_writes_midi_report_and_manifest_key(tmp_path: Path) -> None:
    import json

    import yaml

    pytest.importorskip("pretty_midi")
    pack = tmp_path / "song.feedpak"
    _write_score_midi(pack / "aural" / "notes.mid", _dense_mix())
    (pack / "manifest.yaml").write_text("title: Test\n", encoding="utf-8")

    status = pr.reduce_pack(pack)

    assert status["ok"] is True
    assert status["skipped"] is False
    assert (pack / "aural" / "piano_reduction.mid").is_file()

    report = json.loads((pack / "aural" / "piano_reduction.json").read_text("utf-8"))
    assert report["engine"] == pr.ENGINE_ID

    manifest = yaml.safe_load((pack / "manifest.yaml").read_text("utf-8"))
    assert manifest["piano_reduction"] == "aural/piano_reduction.mid"
    assert manifest["title"] == "Test"  # existing keys survive


def test_reduce_pack_never_rewrites_the_source_score(tmp_path: Path) -> None:
    """Additive analysis: the chart the game reads must be untouched."""
    pytest.importorskip("pretty_midi")
    pack = tmp_path / "song.feedpak"
    notes_mid = pack / "aural" / "notes.mid"
    _write_score_midi(notes_mid, _dense_mix())
    before = notes_mid.read_bytes()

    pr.reduce_pack(pack)

    assert notes_mid.read_bytes() == before


def test_reduce_pack_skips_existing_output_without_force(tmp_path: Path) -> None:
    pytest.importorskip("pretty_midi")
    pack = tmp_path / "song.feedpak"
    _write_score_midi(pack / "aural" / "notes.mid", _dense_mix())
    pr.reduce_pack(pack)
    stamp = (pack / "aural" / "piano_reduction.mid").read_bytes()

    again = pr.reduce_pack(pack)
    assert again["ok"] is True
    assert again["skipped"] is True

    forced = pr.reduce_pack(pack, force=True)
    assert forced["skipped"] is False
    assert (pack / "aural" / "piano_reduction.mid").read_bytes() == stamp


def test_reduce_pack_reports_a_missing_score_instead_of_crashing(tmp_path: Path) -> None:
    pack = tmp_path / "empty.feedpak"
    pack.mkdir()

    status = pr.reduce_pack(pack)

    assert status["ok"] is False
    assert "no melodic score" in status["error"]


def test_reduce_pack_reports_a_missing_pack(tmp_path: Path) -> None:
    status = pr.reduce_pack(tmp_path / "nope.feedpak")

    assert status["ok"] is False
    assert "not a directory" in status["error"]


def test_reduce_pack_reports_a_drums_only_pack(tmp_path: Path) -> None:
    pytest.importorskip("pretty_midi")
    pack = tmp_path / "drums.feedpak"
    _write_score_midi(pack / "aural" / "notes.mid", {"drums": [note(0.0, 36)]})

    status = pr.reduce_pack(pack)

    assert status["ok"] is False
    assert "no melodic notes" in status["error"]


def test_harmony_and_beats_for_pack_degrades_to_none(tmp_path: Path) -> None:
    pack = tmp_path / "bare.feedpak"
    pack.mkdir()

    harmony, beats = pr.harmony_and_beats_for_pack(pack)

    assert harmony is None
    assert beats is None


def test_harmony_and_beats_for_pack_survives_malformed_json(tmp_path: Path) -> None:
    pack = tmp_path / "broken.feedpak"
    (pack / "aural").mkdir(parents=True)
    (pack / "song_timeline.json").write_text("{not json", encoding="utf-8")
    (pack / "aural" / "harmony.json").write_text("[[[", encoding="utf-8")

    harmony, beats = pr.harmony_and_beats_for_pack(pack)

    assert harmony is None
    assert beats is None
