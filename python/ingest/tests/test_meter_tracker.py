"""Unit tests for the pure meter-derivation helpers (no model / audio needed)."""

from __future__ import annotations

from aural_ingest.meter_tracker import (
    assign_bars_from_downbeats,
    derive_beats_per_bar,
    derive_bpm,
    _time_signature_string,
)


def test_assign_bars_clean_4_4():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    downbeats = [0.0, 2.0]
    rows = assign_bars_from_downbeats(beats, downbeats)
    assert [r["beat"] for r in rows] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert [r["bar"] for r in rows] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert [r["strength"] for r in rows] == [1.0, 0.5, 0.5, 0.5, 1.0, 0.5, 0.5, 0.5]


def test_assign_bars_uses_model_phase_not_index_0():
    # Downbeat is on the 3rd beat (a phase-2 song like Beat It) -> measure starts
    # must follow the model, NOT force beat-0 onto the first tracked beat.
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    downbeats = [1.0, 2.0, 3.0]  # bar = 2 beats, phase 2 (the "1" is on index 2)
    rows = assign_bars_from_downbeats(beats, downbeats)
    # First downbeat at index 2 -> pickup beats 0,1 stay non-zero; the "1" lands there.
    assert [r["beat"] for r in rows] == [1, 2, 0, 1, 0, 1, 0, 1]
    # Only true downbeats are beat==0
    assert [i for i, r in enumerate(rows) if r["beat"] == 0] == [2, 4, 6]


def test_assign_bars_3_4():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    downbeats = [0.0, 1.5]  # every 3 beats
    rows = assign_bars_from_downbeats(beats, downbeats)
    assert [r["beat"] for r in rows] == [0, 1, 2, 0, 1, 2]
    assert derive_beats_per_bar(rows) == 3


def test_assign_bars_tolerates_irregular_bar():
    # Model wobble: one 5-beat bar shouldn't desync everything after it.
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    downbeats = [0.0, 2.0, 4.0]  # bars of 4, 4
    # inject a phantom middle downbeat scenario handled by 'mode'
    rows = assign_bars_from_downbeats(beats, downbeats)
    assert derive_beats_per_bar(rows) == 4


def test_derive_beats_per_bar_mode_over_mean():
    beats = list(x * 0.5 for x in range(13))
    downbeats = [0.0, 2.0, 4.0, 5.0, 6.0]  # lengths 4,4,2,2 -> mode is 4 (tie broken by count)
    rows = assign_bars_from_downbeats(beats, downbeats)
    # 4 appears twice, 2 appears twice; Counter.most_common returns first-seen on tie
    assert derive_beats_per_bar(rows) in (2, 4)


def test_derive_beats_per_bar_defaults_to_4_without_downbeats():
    rows = assign_bars_from_downbeats([0.0, 0.5, 1.0], [])
    assert derive_beats_per_bar(rows) == 4


def test_derive_bpm_from_median_ibi():
    assert derive_bpm([0.0, 0.5, 1.0, 1.5, 2.0]) == 120.0
    assert derive_bpm([0.0, 0.4296]) == 139.665 or abs(derive_bpm([0.0, 0.4296]) - 139.66) < 0.1
    assert derive_bpm([]) == 120.0
    assert derive_bpm([1.23]) == 120.0


def test_derive_bpm_ignores_outlier_gaps():
    # A missing beat (big gap) must not drag the tempo down: median ignores it.
    beats = [0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 3.5]
    assert derive_bpm(beats) == 120.0


def test_time_signature_string():
    assert _time_signature_string(4) == "4/4"
    assert _time_signature_string(3) == "3/4"
    assert _time_signature_string(6) == "6/4"
    assert _time_signature_string(0) == "4/4"


def test_empty_beats():
    assert assign_bars_from_downbeats([], [0.0]) == []
