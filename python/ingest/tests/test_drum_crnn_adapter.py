"""Tests for the opt-in in-house drum-CRNN adapter.

Three load-bearing contracts:

  (1) import-safety -- ``aural_ingest.algorithms.drum_crnn`` imports with NO
      model and NO onnxruntime present (the registry imports every algorithm at
      sidecar startup, so a missing model must NOT break import);
  (2) inference -- with the ONNX model resolvable, ``transcribe`` on a drum WAV
      returns a non-empty ``list[DrumEvent]`` using the 5 canonical MIDI notes;
  (3) fall-through contract -- with ``AURAL_DRUM_CRNN_ONNX`` pointing at a
      nonexistent path, ``transcribe`` raises ``RuntimeError`` (so the DSP
      fallback chain takes over instead of the sidecar crashing).

Only (2) needs the runtime (onnxruntime + a resolvable model); it is skipped
when either is absent so the pure-logic contracts still run in lightweight CI.
"""
from __future__ import annotations

import importlib
import wave
from pathlib import Path

import numpy as np
import pytest

from aural_ingest.transcription import DrumEvent

# Import-safety contract (1): this bare import must never raise, even with no
# onnxruntime and no model installed. If this line fails, the module is doing a
# heavy import or model resolution at import time -- the exact thing the registry
# can't tolerate.
from aural_ingest.algorithms import drum_crnn

CANONICAL_NOTES = {36, 38, 42, 47, 49}  # kick, snare, hi_hat, toms, cymbals


def _write_drum_wav(path: Path, *, sr: int = 22050, seconds: float = 2.0) -> None:
    """Synthesize a simple four-on-the-floor-ish drum WAV (16-bit PCM mono).

    Plants a low-frequency 'kick' thump and a noisy 'snare'/'hat' burst on a
    grid so the CRNN has real broadband transients to fire on. Written with the
    stdlib ``wave`` module so the test needs no soundfile.
    """
    rng = np.random.default_rng(0)
    n = int(sr * seconds)
    y = np.zeros(n, dtype=np.float32)

    def _place(t: float, samples: np.ndarray) -> None:
        start = int(t * sr)
        end = min(n, start + len(samples))
        if end > start:
            y[start:end] += samples[: end - start]

    step = 0.25  # 240 BPM sixteenths-ish grid
    t = 0.0
    while t < seconds - 0.1:
        # Kick: decaying low sine.
        dur = int(0.12 * sr)
        env = np.exp(-np.linspace(0, 8, dur))
        kick = (np.sin(2 * np.pi * 60 * np.arange(dur) / sr) * env).astype(np.float32)
        _place(t, kick * 0.9)
        # Snare/hat: short noise burst a beat later.
        dur2 = int(0.06 * sr)
        env2 = np.exp(-np.linspace(0, 12, dur2))
        noise = (rng.standard_normal(dur2) * env2).astype(np.float32)
        _place(t + step, noise * 0.6)
        t += 2 * step

    peak = float(np.max(np.abs(y))) or 1.0
    pcm = np.clip(y / peak * 0.9, -1.0, 1.0)
    ints = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(ints.tobytes())


def _model_resolvable() -> bool:
    try:
        drum_crnn._resolve_onnx_path(None)
        return True
    except Exception:
        return False


def test_module_imports_without_model_present() -> None:
    """Contract (1): importable + has the module-level transcribe entry point."""
    mod = importlib.import_module("aural_ingest.algorithms.drum_crnn")
    assert hasattr(mod, "transcribe")
    assert callable(mod.transcribe)


def test_transcribe_raises_when_model_missing(tmp_path: Path, monkeypatch) -> None:
    """Contract (3): an explicit-but-missing ONNX path -> RuntimeError.

    This proves the fall-through contract: the DSP orchestrator catches this and
    moves on to the heuristic engines.
    """
    monkeypatch.setenv("AURAL_DRUM_CRNN_ONNX", str(tmp_path / "does_not_exist.onnx"))
    wav = tmp_path / "clip.wav"
    _write_drum_wav(wav)
    with pytest.raises(RuntimeError):
        drum_crnn.transcribe(wav)


@pytest.mark.skipif(
    importlib.util.find_spec("onnxruntime") is None,
    reason="onnxruntime not installed",
)
def test_transcribe_returns_events_with_model(tmp_path: Path) -> None:
    """Contract (2): with the model resolvable, get a non-empty DrumEvent list."""
    if not _model_resolvable():
        pytest.skip(
            "drum_crnn ONNX modelpack not installed "
            "(run scripts/install_drum_crnn_modelpack.py)"
        )
    wav = tmp_path / "drums.wav"
    _write_drum_wav(wav, seconds=2.0)

    events = drum_crnn.transcribe(wav)
    assert isinstance(events, list)
    assert events, "expected a non-empty transcription for a drum clip"
    for e in events:
        assert isinstance(e, DrumEvent)
        assert e.note in CANONICAL_NOTES, f"unexpected note {e.note}"
        assert 1 <= e.velocity <= 127
        assert e.time >= 0.0
    # Events are time-sorted by the decoder.
    times = [e.time for e in events]
    assert times == sorted(times)
