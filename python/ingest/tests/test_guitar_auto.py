"""Tests for the ``guitar_auto`` split-aware orchestrator.

These tests intentionally mock out both the split and the underlying
per-channel algorithms so they exercise only the router: fallback ladder,
optional post-processors, merge/sort behavior, and that split artifacts
are cleaned up.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from aural_ingest.algorithms import guitar_auto
from aural_ingest.transcription import MelodicNote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lead_note(t_on: float, pitch: int) -> MelodicNote:
    return MelodicNote(
        t_on=round(t_on, 6),
        t_off=round(t_on + 0.1, 6),
        pitch=pitch,
        velocity=90,
        instrument="lead_guitar",
    )


def _rhythm_note(t_on: float, pitch: int) -> MelodicNote:
    return MelodicNote(
        t_on=round(t_on, 6),
        t_off=round(t_on + 0.25, 6),
        pitch=pitch,
        velocity=88,
        instrument="rhythm_guitar",
    )


def _fake_split(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: bool = False,
) -> dict:
    """Patch ``split_lead_rhythm_guitar_stem`` on the module the
    orchestrator imports it from. Records the (lead_path, rhythm_path)
    it was called with so the test can assert routing later.
    """
    from aural_ingest import guitar_split as gsplit

    captured: dict = {"lead": None, "rhythm": None, "called": 0}

    def fake_split(source, lead_out, rhythm_out):
        captured["called"] += 1
        captured["lead"] = Path(lead_out)
        captured["rhythm"] = Path(rhythm_out)
        if fail:
            raise RuntimeError("split blew up")
        # Materialize both files so downstream (mocked) transcribers
        # can be handed the paths without surprise.
        Path(lead_out).parent.mkdir(parents=True, exist_ok=True)
        Path(rhythm_out).parent.mkdir(parents=True, exist_ok=True)
        Path(lead_out).write_bytes(b"\x00" * 16)
        Path(rhythm_out).write_bytes(b"\x00" * 16)
        return {"method": "fake", "frames": 1, "mean_lead_ratio": 0.5}

    monkeypatch.setattr(gsplit, "split_lead_rhythm_guitar_stem", fake_split)
    return captured


def _install_lead_stub(
    monkeypatch: pytest.MonkeyPatch,
    notes: list[MelodicNote],
    *,
    error: bool = False,
) -> dict:
    calls: dict = {"count": 0, "last_path": None}

    def _stub(stem_path, *, instrument="melodic", **_):
        calls["count"] += 1
        calls["last_path"] = Path(stem_path)
        if error:
            raise RuntimeError("torchcrepe unavailable")
        return list(notes)

    from aural_ingest.algorithms import melodic_torchcrepe

    monkeypatch.setattr(melodic_torchcrepe, "transcribe", _stub)
    return calls


def _install_rhythm_stub(
    monkeypatch: pytest.MonkeyPatch,
    notes: list[MelodicNote],
) -> dict:
    calls: dict = {"count": 0, "last_path": None}

    def _stub(stem_path, *, instrument="melodic", **_):
        calls["count"] += 1
        calls["last_path"] = Path(stem_path)
        return list(notes)

    from aural_ingest.algorithms import melodic_basic_pitch

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", _stub)
    return calls


def _install_combined_stub(
    monkeypatch: pytest.MonkeyPatch,
    notes: list[MelodicNote],
) -> dict:
    calls: dict = {"count": 0, "last_path": None}

    def _stub(stem_path, *, instrument="melodic", **_):
        calls["count"] += 1
        calls["last_path"] = Path(stem_path)
        return list(notes)

    from aural_ingest.algorithms import melodic_combined

    monkeypatch.setattr(melodic_combined, "transcribe", _stub)
    return calls


# ---------------------------------------------------------------------------
# Happy-path merge
# ---------------------------------------------------------------------------


def test_lead_and_rhythm_merged_and_time_sorted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    split_calls = _fake_split(monkeypatch)
    lead_notes = [_lead_note(0.20, 62), _lead_note(0.60, 64)]
    rhythm_notes = [_rhythm_note(0.00, 55), _rhythm_note(0.40, 59)]
    _install_lead_stub(monkeypatch, lead_notes)
    _install_rhythm_stub(monkeypatch, rhythm_notes)

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    # Merged: both streams present.
    assert len(out) == 4
    # Time-sorted.
    starts = [n.t_on for n in out]
    assert starts == sorted(starts)
    # Both channels contributed.
    assert {n.pitch for n in out} == {55, 59, 62, 64}
    # Split was called exactly once with the source stem.
    assert split_calls["called"] == 1


def test_split_paths_are_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    split_calls = _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [_lead_note(0.1, 60)])
    _install_rhythm_stub(monkeypatch, [_rhythm_note(0.2, 55)])

    guitar_auto.transcribe(src, instrument="lead_guitar")

    assert split_calls["lead"] is not None
    # Temp dir removed after the call.
    assert not split_calls["lead"].parent.exists()


def test_lead_and_rhythm_get_different_stem_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    split_calls = _fake_split(monkeypatch)
    lead_calls = _install_lead_stub(monkeypatch, [])
    rhythm_calls = _install_rhythm_stub(monkeypatch, [])

    guitar_auto.transcribe(src, instrument="lead_guitar")

    assert lead_calls["last_path"] is not None
    assert rhythm_calls["last_path"] is not None
    assert lead_calls["last_path"].name == "lead.wav"
    assert rhythm_calls["last_path"].name == "rhythm.wav"
    # And they match what split was told to produce.
    assert lead_calls["last_path"] == split_calls["lead"]
    assert rhythm_calls["last_path"] == split_calls["rhythm"]


# ---------------------------------------------------------------------------
# Fallback ladder
# ---------------------------------------------------------------------------


def test_lead_falls_back_to_combined_when_torchcrepe_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    _fake_split(monkeypatch)
    lead_stub = _install_lead_stub(monkeypatch, [], error=True)
    combined_stub = _install_combined_stub(monkeypatch, [_lead_note(0.5, 63)])
    _install_rhythm_stub(monkeypatch, [])

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    # torchcrepe was attempted, errored, and combined absorbed the lead call.
    assert lead_stub["count"] == 1
    assert combined_stub["count"] >= 1
    assert any(n.pitch == 63 for n in out)


def test_split_failure_returns_whole_stem_combined_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    _fake_split(monkeypatch, fail=True)
    combined_stub = _install_combined_stub(
        monkeypatch,
        [_lead_note(0.1, 60), _lead_note(0.2, 62)],
    )
    # Should never fire on the split-failure path.
    lead_stub = _install_lead_stub(monkeypatch, [_lead_note(0.3, 61)])
    rhythm_stub = _install_rhythm_stub(monkeypatch, [_rhythm_note(0.3, 55)])

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    assert combined_stub["count"] == 1
    assert combined_stub["last_path"] == src
    assert lead_stub["count"] == 0
    assert rhythm_stub["count"] == 0
    assert {n.pitch for n in out} == {60, 62}


def test_rhythm_falls_back_when_basic_pitch_playable_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 2's ``guitar_basic_pitch_playable`` may not be merged.

    Simulate that: force the lazy import to raise ImportError. The
    router should slide down to ``melodic_basic_pitch``.
    """
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    # Ensure any previously imported stub is out of the way, then arm
    # the sys.modules trap that raises ImportError on the lazy import.
    monkeypatch.setitem(sys.modules, "aural_ingest.algorithms.guitar_basic_pitch_playable", None)

    _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [])
    rhythm_stub = _install_rhythm_stub(
        monkeypatch,
        [_rhythm_note(0.4, 57)],
    )

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    assert rhythm_stub["count"] == 1
    assert any(n.pitch == 57 for n in out)


def test_source_stem_missing_returns_whole_stem_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "does_not_exist.wav"

    combined_stub = _install_combined_stub(monkeypatch, [_lead_note(0.0, 60)])
    # Split must NOT even be attempted.
    from aural_ingest import guitar_split as gsplit

    def _boom(*_a, **_kw):
        raise AssertionError("split_lead_rhythm_guitar_stem should not be called")

    monkeypatch.setattr(gsplit, "split_lead_rhythm_guitar_stem", _boom)

    out = guitar_auto.transcribe(src, instrument="lead_guitar")
    assert combined_stub["count"] == 1
    assert combined_stub["last_path"] == src
    assert [n.pitch for n in out] == [60]


# ---------------------------------------------------------------------------
# Optional post-processors
# ---------------------------------------------------------------------------


def test_optional_cleanup_absent_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_cleanup", None)
    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_chord_supplement", None)
    monkeypatch.setitem(
        sys.modules, "aural_ingest.algorithms.guitar_basic_pitch_playable", None
    )

    _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [_lead_note(0.1, 60)])
    _install_rhythm_stub(monkeypatch, [_rhythm_note(0.2, 55)])

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    assert {n.pitch for n in out} == {55, 60}


def test_optional_cleanup_applied_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    calls: list[dict] = []

    fake_cleanup_mod = types.ModuleType("aural_ingest.guitar_cleanup")

    def _apply_guitar_cleanup(notes, *, stem_path=None, instrument=None, role=None):
        calls.append({"role": role, "count": len(notes)})
        # Drop one note from each stream so we can verify the pass fired.
        return list(notes[:-1]) if notes else list(notes)

    fake_cleanup_mod.apply_guitar_cleanup = _apply_guitar_cleanup
    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_cleanup", fake_cleanup_mod)
    # Explicitly clear the chord supplement so it doesn't perturb counts.
    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_chord_supplement", None)
    monkeypatch.setitem(
        sys.modules, "aural_ingest.algorithms.guitar_basic_pitch_playable", None
    )

    _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [_lead_note(0.1, 60), _lead_note(0.3, 62)])
    _install_rhythm_stub(
        monkeypatch, [_rhythm_note(0.2, 55), _rhythm_note(0.4, 57)]
    )

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    # Both roles were seen by the cleanup pass.
    roles = {c["role"] for c in calls}
    assert roles == {"lead", "rhythm"}
    # Each stream lost one note per the fake cleanup.
    assert len(out) == 2


def test_optional_chord_supplement_applied_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    fake_supp_mod = types.ModuleType("aural_ingest.guitar_chord_supplement")

    def _supplement_rhythm_guitar(notes, *, stem_path=None, instrument=None):
        # Tag each incoming note by adding a companion +7 semitone note.
        out = list(notes)
        for n in notes:
            out.append(
                MelodicNote(
                    t_on=n.t_on,
                    t_off=n.t_off,
                    pitch=n.pitch + 7,
                    velocity=n.velocity,
                    instrument=n.instrument,
                )
            )
        return out

    fake_supp_mod.supplement_rhythm_guitar = _supplement_rhythm_guitar
    monkeypatch.setitem(
        sys.modules, "aural_ingest.guitar_chord_supplement", fake_supp_mod
    )
    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_cleanup", None)
    monkeypatch.setitem(
        sys.modules, "aural_ingest.algorithms.guitar_basic_pitch_playable", None
    )

    _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [_lead_note(0.1, 60)])
    _install_rhythm_stub(monkeypatch, [_rhythm_note(0.2, 55)])

    out = guitar_auto.transcribe(src, instrument="lead_guitar")

    pitches = [n.pitch for n in out]
    # Lead unchanged: 60. Rhythm now has 55 + supplemental 62.
    assert 60 in pitches
    assert 55 in pitches
    assert 62 in pitches  # 55 + 7 chord tone


def test_optional_supplement_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "guitar.wav"
    src.write_bytes(b"x")

    fake_supp_mod = types.ModuleType("aural_ingest.guitar_chord_supplement")

    def _blows_up(*_a, **_kw):
        raise RuntimeError("supplement crashed")

    fake_supp_mod.supplement_rhythm_guitar = _blows_up
    monkeypatch.setitem(
        sys.modules, "aural_ingest.guitar_chord_supplement", fake_supp_mod
    )
    monkeypatch.setitem(sys.modules, "aural_ingest.guitar_cleanup", None)
    monkeypatch.setitem(
        sys.modules, "aural_ingest.algorithms.guitar_basic_pitch_playable", None
    )

    _fake_split(monkeypatch)
    _install_lead_stub(monkeypatch, [_lead_note(0.1, 60)])
    _install_rhythm_stub(monkeypatch, [_rhythm_note(0.2, 55)])

    # The router must not propagate the supplement crash — output is
    # simply the unsupplemented merge.
    out = guitar_auto.transcribe(src, instrument="lead_guitar")
    assert {n.pitch for n in out} == {55, 60}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_guitar_auto_is_registered_in_default_registry() -> None:
    from aural_ingest.transcription import (
        KNOWN_MELODIC_METHODS,
        build_default_melodic_algorithm_registry,
    )

    assert "guitar_auto" in KNOWN_MELODIC_METHODS
    registry = build_default_melodic_algorithm_registry(instrument="lead_guitar")
    assert "guitar_auto" in registry
    assert callable(registry["guitar_auto"])
