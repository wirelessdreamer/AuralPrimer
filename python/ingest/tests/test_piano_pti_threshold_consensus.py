"""Unit tests for piano_pti threshold env vars + stem-vs-mix consensus filter.

These tests exercise the pure-Python plumbing -- env-var parsing, threshold
application, and the merge_consensus algorithm -- without invoking the
underlying piano_transcription_inference model. They guard against
regressions in the wiring layer that ships with every release: if a future
refactor drops the threshold override, breaks the env-var parse, or changes
the consensus matching semantics, these tests catch it without needing a
GPU / checkpoint.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# Ensure the in-tree ingest source is importable even when the test is run
# without an editable install.
ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aural_ingest.algorithms import piano_pti  # noqa: E402
from aural_ingest.transcription import MelodicNote  # noqa: E402


# ---------------------------------------------------------------------------
# _parse_threshold
# ---------------------------------------------------------------------------


def test_parse_threshold_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", raising=False)
    assert piano_pti._parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD") is None


def test_parse_threshold_returns_none_when_empty(monkeypatch):
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", "   ")
    assert piano_pti._parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD") is None


def test_parse_threshold_returns_float_when_valid(monkeypatch):
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", "0.45")
    assert piano_pti._parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD") == pytest.approx(0.45)


def test_parse_threshold_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", "definitely_not_a_number")
    assert piano_pti._parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD") is None


@pytest.mark.parametrize("bad", ["-0.1", "1.5", "2"])
def test_parse_threshold_rejects_out_of_range(monkeypatch, bad):
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", bad)
    assert piano_pti._parse_threshold("AURAL_PIANO_PTI_ONSET_THRESHOLD") is None


# ---------------------------------------------------------------------------
# _apply_thresholds
# ---------------------------------------------------------------------------


class _FakeTranscriptor:
    """Mimics the PTI PianoTranscription instance just enough for the test."""
    def __init__(self):
        self.onset_threshold = 0.3
        # Note the upstream package's misspelled attribute name.
        self.offset_threshod = 0.3
        self.frame_threshold = 0.1


def test_apply_thresholds_overrides_all_three(monkeypatch):
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", "0.5")
    monkeypatch.setenv("AURAL_PIANO_PTI_OFFSET_THRESHOLD", "0.4")
    monkeypatch.setenv("AURAL_PIANO_PTI_FRAME_THRESHOLD", "0.2")

    t = _FakeTranscriptor()
    piano_pti._apply_thresholds(t)

    assert t.onset_threshold == pytest.approx(0.5)
    assert t.offset_threshod == pytest.approx(0.4)  # sic
    assert t.frame_threshold == pytest.approx(0.2)


def test_apply_thresholds_leaves_defaults_when_env_unset(monkeypatch):
    for var in (
        "AURAL_PIANO_PTI_ONSET_THRESHOLD",
        "AURAL_PIANO_PTI_OFFSET_THRESHOLD",
        "AURAL_PIANO_PTI_FRAME_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)

    t = _FakeTranscriptor()
    piano_pti._apply_thresholds(t)

    assert t.onset_threshold == pytest.approx(0.3)
    assert t.offset_threshod == pytest.approx(0.3)
    assert t.frame_threshold == pytest.approx(0.1)


def test_apply_thresholds_only_touches_set_vars(monkeypatch):
    """Setting one env var must not clobber the others."""
    monkeypatch.delenv("AURAL_PIANO_PTI_OFFSET_THRESHOLD", raising=False)
    monkeypatch.delenv("AURAL_PIANO_PTI_FRAME_THRESHOLD", raising=False)
    monkeypatch.setenv("AURAL_PIANO_PTI_ONSET_THRESHOLD", "0.6")

    t = _FakeTranscriptor()
    piano_pti._apply_thresholds(t)

    assert t.onset_threshold == pytest.approx(0.6)
    assert t.offset_threshod == pytest.approx(0.3)  # unchanged
    assert t.frame_threshold == pytest.approx(0.1)  # unchanged


# ---------------------------------------------------------------------------
# merge_consensus
# ---------------------------------------------------------------------------


def _note(t_on: float, pitch: int, *, t_off: float = None, velocity: int = 80) -> MelodicNote:
    if t_off is None:
        t_off = t_on + 0.25
    return MelodicNote(t_on=t_on, t_off=t_off, pitch=pitch, velocity=velocity, instrument="keys")


def test_consensus_keeps_matching_notes():
    stem = [_note(1.0, 60), _note(2.0, 64), _note(3.0, 67)]
    mix = [_note(1.0, 60), _note(2.0, 64), _note(3.0, 67)]

    out = piano_pti.merge_consensus(stem, mix)

    assert len(out) == 3
    assert [n.pitch for n in out] == [60, 64, 67]


def test_consensus_drops_stem_only_hallucinations():
    # The first stem note has no match in the mix -- the hallucination case.
    stem = [_note(0.5, 72), _note(2.0, 64)]
    mix = [_note(2.0, 64)]

    out = piano_pti.merge_consensus(stem, mix)

    assert len(out) == 1
    assert out[0].pitch == 64
    assert out[0].t_on == pytest.approx(2.0)


def test_consensus_respects_onset_tolerance():
    # 60ms gap is outside the default 50ms window -> no match.
    stem = [_note(1.000, 60)]
    mix = [_note(1.060, 60)]

    out_strict = piano_pti.merge_consensus(stem, mix, onset_tolerance_sec=0.05)
    assert out_strict == []

    out_loose = piano_pti.merge_consensus(stem, mix, onset_tolerance_sec=0.1)
    assert len(out_loose) == 1


def test_consensus_allows_one_semitone_pitch_slack_by_default():
    # The mix transcription often picks the same onset at +/- 1 semitone
    # due to inharmonicity / pitch-bend; that should still count as a match.
    stem = [_note(1.0, 60)]
    mix = [_note(1.0, 61)]

    out = piano_pti.merge_consensus(stem, mix)
    assert len(out) == 1
    assert out[0].pitch == 60  # stem wins


def test_consensus_rejects_octave_mismatches():
    # Octave drift is a *real* PTI failure mode (the model picks the wrong
    # register). The mix and stem should agree on register, so a 12-semitone
    # gap must NOT count as a match.
    stem = [_note(1.0, 60)]
    mix = [_note(1.0, 72)]  # +octave

    out = piano_pti.merge_consensus(stem, mix)
    assert out == []


def test_consensus_returns_empty_when_mix_is_empty():
    stem = [_note(1.0, 60)]
    out = piano_pti.merge_consensus(stem, [])
    assert out == []


def test_consensus_returns_stem_when_stem_is_empty():
    out = piano_pti.merge_consensus([], [_note(1.0, 60)])
    assert out == []


def test_consensus_handles_unsorted_mix_input():
    # Implementation sorts internally; pathological order must still match.
    stem = [_note(1.0, 60), _note(2.0, 64)]
    mix = [_note(2.0, 64), _note(1.0, 60)]

    out = piano_pti.merge_consensus(stem, mix)
    assert len(out) == 2


def test_consensus_velocity_comes_from_stem():
    # Stem velocities are the authoritative signal (cleaner spectral profile).
    stem = [_note(1.0, 60, velocity=95)]
    mix = [_note(1.0, 60, velocity=40)]

    out = piano_pti.merge_consensus(stem, mix)
    assert len(out) == 1
    assert out[0].velocity == 95


# ---------------------------------------------------------------------------
# _find_mix_audio
# ---------------------------------------------------------------------------


def test_find_mix_audio_locates_mix_wav_next_to_stems(tmp_path):
    audio_dir = tmp_path / "audio"
    stems_dir = audio_dir / "stems"
    stems_dir.mkdir(parents=True)
    stem = stems_dir / "keys.wav"
    stem.write_bytes(b"")
    mix = audio_dir / "mix.wav"
    mix.write_bytes(b"")

    found = piano_pti._find_mix_audio(stem)
    assert found == mix


def test_find_mix_audio_prefers_wav_over_alternatives(tmp_path):
    audio_dir = tmp_path / "audio"
    stems_dir = audio_dir / "stems"
    stems_dir.mkdir(parents=True)
    stem = stems_dir / "keys.wav"
    stem.write_bytes(b"")
    (audio_dir / "mix.mp3").write_bytes(b"")
    (audio_dir / "mix.wav").write_bytes(b"")

    found = piano_pti._find_mix_audio(stem)
    assert found is not None and found.suffix == ".wav"


def test_find_mix_audio_falls_back_to_mp3(tmp_path):
    audio_dir = tmp_path / "audio"
    stems_dir = audio_dir / "stems"
    stems_dir.mkdir(parents=True)
    stem = stems_dir / "keys.wav"
    stem.write_bytes(b"")
    (audio_dir / "mix.mp3").write_bytes(b"")

    found = piano_pti._find_mix_audio(stem)
    assert found is not None and found.suffix == ".mp3"


def test_find_mix_audio_returns_none_when_no_mix(tmp_path):
    audio_dir = tmp_path / "audio"
    stems_dir = audio_dir / "stems"
    stems_dir.mkdir(parents=True)
    stem = stems_dir / "keys.wav"
    stem.write_bytes(b"")

    assert piano_pti._find_mix_audio(stem) is None


def test_find_mix_audio_returns_none_when_layout_unrecognized(tmp_path):
    # If the parent isn't `audio/`, we can't safely guess where the mix is.
    odd = tmp_path / "weird" / "keys.wav"
    odd.parent.mkdir(parents=True)
    odd.write_bytes(b"")

    assert piano_pti._find_mix_audio(odd) is None
