"""Unit tests for the guitar-tuned Basic Pitch adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from aural_ingest.algorithms import guitar_basic_pitch_playable, melodic_basic_pitch
from aural_ingest.transcription import (
    KNOWN_MELODIC_METHODS,
    MelodicNote,
    build_default_melodic_algorithm_registry,
)


def _make_note(t_on: float, t_off: float, pitch: int) -> MelodicNote:
    return MelodicNote(
        t_on=t_on,
        t_off=t_off,
        pitch=pitch,
        velocity=88,
        instrument="guitar",
    )


def test_guitar_basic_pitch_playable_registered_in_known_methods() -> None:
    """The new algorithm id must appear in the validator's allow-list."""
    assert "guitar_basic_pitch_playable" in KNOWN_MELODIC_METHODS


def test_guitar_basic_pitch_playable_returns_empty_when_model_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Model-absent must degrade to an empty list, not raise."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: False,
    )
    # Reset the once-warned latch so successive tests see a fresh warning if
    # they need to, and so the previous test run doesn't influence this one.
    monkeypatch.setattr(guitar_basic_pitch_playable, "_MODEL_ABSENT_WARNED", False)

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert notes == []


def test_guitar_basic_pitch_playable_returns_empty_on_inference_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inference failures must be caught, not propagated as crashes."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )

    def _boom(*_args, **_kwargs) -> list[MelodicNote]:
        raise RuntimeError("basic_pitch prediction failed")

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", _boom)

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert notes == []


def test_guitar_basic_pitch_playable_clamps_to_default_midi_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Notes below E2 (MIDI 40) or above E6 (MIDI 88) are filtered out."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )

    raw_notes = [
        _make_note(0.10, 0.30, 30),  # below E2 -- drop
        _make_note(0.15, 0.35, 40),  # E2 boundary -- keep
        _make_note(0.20, 0.40, 60),  # middle -- keep
        _make_note(0.25, 0.45, 88),  # E6 boundary -- keep
        _make_note(0.30, 0.50, 95),  # above E6 -- drop
    ]
    monkeypatch.setattr(
        melodic_basic_pitch,
        "transcribe",
        lambda *_args, **_kwargs: list(raw_notes),
    )
    # Clean env: no override should be active.
    monkeypatch.delenv("AURAL_GUITAR_MIN_MIDI", raising=False)
    monkeypatch.delenv("AURAL_GUITAR_MAX_MIDI", raising=False)

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert [n.pitch for n in notes] == [40, 60, 88]


def test_guitar_basic_pitch_playable_env_var_overrides_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AURAL_GUITAR_MIN_MIDI / MAX_MIDI env vars override the defaults."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )

    raw_notes = [
        _make_note(0.10, 0.30, 40),  # below new lo=48 -- drop
        _make_note(0.15, 0.35, 48),  # new lo boundary -- keep
        _make_note(0.20, 0.40, 60),  # inside -- keep
        _make_note(0.25, 0.45, 76),  # new hi=76 boundary -- keep
        _make_note(0.30, 0.50, 80),  # above new hi -- drop
    ]
    monkeypatch.setattr(
        melodic_basic_pitch,
        "transcribe",
        lambda *_args, **_kwargs: list(raw_notes),
    )

    # Acoustic-ish tighter range.
    monkeypatch.setenv("AURAL_GUITAR_MIN_MIDI", "48")
    monkeypatch.setenv("AURAL_GUITAR_MAX_MIDI", "76")

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert [n.pitch for n in notes] == [48, 60, 76]


def test_guitar_basic_pitch_playable_ignores_invalid_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Garbage env values fall back to defaults instead of raising."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )
    raw_notes = [
        _make_note(0.10, 0.30, 40),  # default lo=40 -- keep
        _make_note(0.20, 0.40, 88),  # default hi=88 -- keep
        _make_note(0.30, 0.50, 90),  # above default hi -- drop
    ]
    monkeypatch.setattr(
        melodic_basic_pitch,
        "transcribe",
        lambda *_args, **_kwargs: list(raw_notes),
    )

    monkeypatch.setenv("AURAL_GUITAR_MIN_MIDI", "not-a-number")
    monkeypatch.setenv("AURAL_GUITAR_MAX_MIDI", "999")  # out of MIDI range

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert [n.pitch for n in notes] == [40, 88]


def test_guitar_basic_pitch_playable_swapped_env_vars_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """min>max after resolution must revert to the pinned defaults."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )
    raw_notes = [
        _make_note(0.10, 0.30, 30),
        _make_note(0.20, 0.40, 60),
        _make_note(0.30, 0.50, 100),
    ]
    monkeypatch.setattr(
        melodic_basic_pitch,
        "transcribe",
        lambda *_args, **_kwargs: list(raw_notes),
    )
    monkeypatch.setenv("AURAL_GUITAR_MIN_MIDI", "80")
    monkeypatch.setenv("AURAL_GUITAR_MAX_MIDI", "40")

    notes = guitar_basic_pitch_playable.transcribe(stem)
    # Fallback range 40..88 -- pitch 60 kept, 30 and 100 dropped.
    assert [n.pitch for n in notes] == [60]


def test_guitar_basic_pitch_playable_returns_notes_sorted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Output must be ordered by (t_on, pitch, t_off) regardless of input order."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )
    # Unsorted input on purpose.
    raw_notes = [
        _make_note(0.30, 0.50, 64),
        _make_note(0.10, 0.25, 55),
        _make_note(0.10, 0.20, 60),  # same t_on as prev but higher pitch -> after
        _make_note(0.20, 0.40, 62),
    ]
    monkeypatch.setattr(
        melodic_basic_pitch,
        "transcribe",
        lambda *_args, **_kwargs: list(raw_notes),
    )
    monkeypatch.delenv("AURAL_GUITAR_MIN_MIDI", raising=False)
    monkeypatch.delenv("AURAL_GUITAR_MAX_MIDI", raising=False)

    notes = guitar_basic_pitch_playable.transcribe(stem)
    assert [(n.t_on, n.pitch) for n in notes] == [
        (0.10, 55),
        (0.10, 60),
        (0.20, 62),
        (0.30, 64),
    ]


def test_guitar_basic_pitch_playable_passes_tuned_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default onset/frame/min-note-length params must reach the underlying wrapper."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "_basic_pitch_model_available",
        lambda: True,
    )
    captured: dict[str, object] = {}

    def _capture(_path, **kwargs) -> list[MelodicNote]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", _capture)
    guitar_basic_pitch_playable.transcribe(stem)

    assert captured["instrument"] == "guitar"
    assert captured["allow_fallback"] is False
    # Guitar-tuned thresholds, per module docstring.
    assert captured["onset_threshold"] == pytest.approx(0.4)
    assert captured["frame_threshold"] == pytest.approx(0.25)
    assert captured["minimum_note_length_ms"] == pytest.approx(50.0)


def test_registry_exposes_guitar_basic_pitch_playable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_default_melodic_algorithm_registry must dispatch to our module."""
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"")

    dummy = [_make_note(0.0, 0.2, 60)]
    monkeypatch.setattr(
        guitar_basic_pitch_playable,
        "transcribe",
        lambda _path, *, instrument="guitar": list(dummy),
    )

    registry = build_default_melodic_algorithm_registry(instrument="guitar")
    assert "guitar_basic_pitch_playable" in registry
    assert registry["guitar_basic_pitch_playable"](stem) == dummy
