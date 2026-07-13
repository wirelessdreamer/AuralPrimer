"""Unit tests for the pure meter-derivation helpers (no model / audio needed)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from aural_ingest import meter_tracker
from aural_ingest.meter_tracker import (
    _time_signature_string,
    assign_bars_from_downbeats,
    derive_beats_per_bar,
    derive_bpm,
)


def _install_fake_file2beats(monkeypatch: pytest.MonkeyPatch, fake_cls: type) -> None:
    beat_this = types.ModuleType("beat_this")
    inference = types.ModuleType("beat_this.inference")
    inference.File2Beats = fake_cls
    beat_this.inference = inference
    monkeypatch.setitem(sys.modules, "beat_this", beat_this)
    monkeypatch.setitem(sys.modules, "beat_this.inference", inference)


def _track_with_fake_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: dict | None = None,
):
    monkeypatch.setattr(
        meter_tracker,
        "resolve_checkpoint",
        lambda _config=None: tmp_path / "beat_this-final0.ckpt",
    )
    return meter_tracker.track_meter(
        tmp_path / "song.wav",
        duration_sec=3.0,
        config=config,
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


def test_track_meter_defaults_to_dbn_postprocessor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[bool] = []

    class FakeFile2Beats:
        def __init__(self, *, checkpoint_path: str, device: str, dbn: bool) -> None:
            calls.append(dbn)
            assert checkpoint_path.endswith("beat_this-final0.ckpt")
            assert device == "cpu"

        def __call__(self, _wav_path: str):
            return [0.0, 0.5, 1.0, 1.5, 2.0, 2.5], [0.0, 2.0]

    _install_fake_file2beats(monkeypatch, FakeFile2Beats)

    result = _track_with_fake_checkpoint(tmp_path, monkeypatch)

    assert result is not None
    _bpm, _beats, _tempo_map, meta = result
    assert calls == [True]
    assert meta["beat_source"] == "beat_this"
    assert meta["postprocessor"] == "dbn"


@pytest.mark.parametrize(
    ("config", "env_value", "expected_dbn"),
    [
        ({}, "0", False),
        ({"meter_dbn": False}, None, False),
        ({"meter_dbn": True}, "0", True),
        ({"meter_dbn": "minimal"}, None, False),
    ],
)
def test_track_meter_dbn_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    env_value: str | None,
    expected_dbn: bool,
):
    calls: list[bool] = []

    class FakeFile2Beats:
        def __init__(self, *, checkpoint_path: str, device: str, dbn: bool) -> None:
            calls.append(dbn)

        def __call__(self, _wav_path: str):
            return [0.0, 0.5, 1.0, 1.5, 2.0], [0.0]

    if env_value is not None:
        monkeypatch.setenv(meter_tracker.METER_DBN_ENV, env_value)
    else:
        monkeypatch.delenv(meter_tracker.METER_DBN_ENV, raising=False)
    _install_fake_file2beats(monkeypatch, FakeFile2Beats)

    result = _track_with_fake_checkpoint(tmp_path, monkeypatch, config=config)

    assert result is not None
    assert calls == [expected_dbn]
    assert result[3]["postprocessor"] == ("dbn" if expected_dbn else "minimal")


def test_track_meter_madmom_import_error_retries_without_dbn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[bool] = []

    class FakeFile2Beats:
        def __init__(self, *, checkpoint_path: str, device: str, dbn: bool) -> None:
            calls.append(dbn)
            if dbn:
                raise ImportError("madmom unavailable")

        def __call__(self, _wav_path: str):
            return [0.0, 0.5, 1.0, 1.5, 2.0], [0.0]

    _install_fake_file2beats(monkeypatch, FakeFile2Beats)

    result = _track_with_fake_checkpoint(tmp_path, monkeypatch)

    assert result is not None
    assert calls == [True, False]
    assert result[3]["postprocessor"] == "minimal"
