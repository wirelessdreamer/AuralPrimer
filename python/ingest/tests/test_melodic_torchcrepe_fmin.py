"""Regression tests for the torchcrepe adapter's fmin clamp.

torchcrepe's CREPE model resolves down to ~C1 (32.70 Hz). Passing a lower
fmin does NOT error — it silently returns all-zero periodicity, so every
frame fails the confidence gate and the result is 0 notes. The bass range
starts at 30 Hz, which tripped exactly this silent failure. These tests pin
the clamp by capturing the fmin handed to torchcrepe.predict (mocked, so the
real model never runs).
"""
from __future__ import annotations

import sys
import types

import pytest

# Heavy imports at module level so conftest skip-collects this file in the
# lightweight CI runtime (no torch) and runs it in the full sidecar env.
import torch  # noqa: F401

from aural_ingest.algorithms import melodic_torchcrepe as mtc


def _install_fake_torchcrepe(monkeypatch, captured: dict) -> None:
    fake = types.ModuleType("torchcrepe")

    def predict(audio, sr, hop, fmin, fmax, **kwargs):  # noqa: ANN001, ANN202
        captured["fmin"] = fmin
        captured["fmax"] = fmax
        n = 4
        return torch.zeros((1, n)), torch.zeros((1, n))

    fake.predict = predict  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torchcrepe", fake)
    # Skip the real wav read; the samples themselves don't matter here.
    monkeypatch.setattr(mtc, "read_wav_mono_normalized", lambda _p: ([0.0] * 16000, 16000))


def test_bass_fmin_clamped_to_model_floor(monkeypatch):
    captured: dict = {}
    _install_fake_torchcrepe(monkeypatch, captured)
    # bass range is (30.0, 400.0) — 30 Hz is below torchcrepe's floor.
    mtc.transcribe("dummy.wav", instrument="bass")
    assert captured["fmin"] == pytest.approx(mtc._TORCHCREPE_MIN_FMIN)
    assert captured["fmin"] >= 32.70319566 - 1e-9


def test_keys_fmin_clamped_too(monkeypatch):
    captured: dict = {}
    _install_fake_torchcrepe(monkeypatch, captured)
    # keys range starts at 27 Hz (A0) — also below the floor.
    mtc.transcribe("dummy.wav", instrument="keys")
    assert captured["fmin"] == pytest.approx(mtc._TORCHCREPE_MIN_FMIN)


def test_inrange_fmin_is_left_untouched(monkeypatch):
    captured: dict = {}
    _install_fake_torchcrepe(monkeypatch, captured)
    # rhythm_guitar starts at 75 Hz, comfortably above the floor — no clamp.
    mtc.transcribe("dummy.wav", instrument="rhythm_guitar")
    assert captured["fmin"] == pytest.approx(75.0)
