"""Onset-driven drum timing: a mistimed hit snaps onto its real transient, a hit
with no transient is left alone, and lane/velocity are preserved."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
librosa = pytest.importorskip("librosa")
sf = pytest.importorskip("soundfile")

from aural_ingest.drum_onset_align import align_drum_tab_to_onsets  # noqa: E402

SR = 22050


def _kick(y: "np.ndarray", t0: float, freq: float = 60.0, tau: float = 0.02) -> None:
    i0 = int(round(t0 * SR))
    n = int(0.05 * SR)
    tt = np.arange(n) / SR
    y[i0 : i0 + n] += (0.9 * np.exp(-tt / tau) * np.sin(2 * np.pi * freq * tt)).astype(np.float32)


def test_snaps_mistimed_kick_onto_the_transient(tmp_path: Path) -> None:
    y = np.zeros(int(2.5 * SR), dtype=np.float32)
    _kick(y, 1.0)  # sharp transient at exactly 1.0s
    wav = tmp_path / "drums.wav"
    sf.write(str(wav), y, SR, subtype="PCM_16")

    # A kick the transcriber placed 60ms late.
    tab = {
        "version": 1, "name": "drums", "kit": [{"id": "kick"}],
        "hits": [{"t": 1.06, "p": "kick", "v": 120}],
    }
    out, stats = align_drum_tab_to_onsets(tab, wav)

    assert stats == {"moved": 1, "kept": 0, "total": 1}
    h = out["hits"][0]
    assert abs(h["t"] - 1.0) < 0.03  # pulled back onto the transient
    assert h["p"] == "kick" and h["v"] == 120  # lane + velocity preserved


def test_leaves_a_hit_with_no_nearby_transient(tmp_path: Path) -> None:
    y = np.zeros(int(2.0 * SR), dtype=np.float32)  # silence
    wav = tmp_path / "drums.wav"
    sf.write(str(wav), y, SR, subtype="PCM_16")

    tab = {"hits": [{"t": 1.0, "p": "kick", "v": 100}]}
    out, stats = align_drum_tab_to_onsets(tab, wav)

    assert stats["moved"] == 0
    assert out["hits"][0]["t"] == 1.0  # unchanged


def test_two_hits_never_collapse_onto_one_transient(tmp_path: Path) -> None:
    y = np.zeros(int(3.0 * SR), dtype=np.float32)
    _kick(y, 1.0)
    _kick(y, 1.5)  # two distinct transients
    wav = tmp_path / "drums.wav"
    sf.write(str(wav), y, SR, subtype="PCM_16")

    # Two kicks, both nudged toward the 1.0 transient; only the nearest may take it.
    tab = {"hits": [{"t": 1.04, "p": "kick", "v": 100}, {"t": 1.46, "p": "kick", "v": 100}]}
    out, stats = align_drum_tab_to_onsets(tab, wav)

    times = sorted(h["t"] for h in out["hits"])
    assert len(times) == 2
    assert times[1] - times[0] > 0.3  # stayed two separate hits, not merged
