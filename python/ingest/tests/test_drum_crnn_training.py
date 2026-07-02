"""Unit tests for the in-house drum-CRNN training harness.

Covers the four load-bearing contracts of the harness:

  (a) target builder -- a synthetic MIDI with known onsets rasterises to the
      expected per-frame class labels;
  (b) feature shape correctness -- log-mel of a known-length signal has the
      documented ``(n_frames, n_mels)`` shape;
  (c) model forward -- ``(B, T, n_mels)`` -> ``(B, T, num_classes)``;
  (d) ONNX export round-trip -- torch output ~= onnxruntime output.

These require torch / librosa / onnx / onnxruntime; ``conftest.py`` skips the
whole module when those optional deps are absent (lightweight CI).
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from aural_ingest.training.drum_crnn.config import (
    CLASSES,
    FeatureConfig,
    ModelConfig,
    TargetConfig,
)
from aural_ingest.training.drum_crnn.decode import decode_events
from aural_ingest.training.drum_crnn.features import (
    logmel_from_audio,
    n_frames_for_samples,
)
from aural_ingest.training.drum_crnn.model import DrumCRNN, count_parameters
from aural_ingest.training.drum_crnn.targets import (
    build_targets_from_midi,
    gm_note_to_class_index,
    onsets_to_class_frames,
    parse_drum_onsets,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _vlq(value: int) -> bytes:
    """Encode an int as a MIDI variable-length quantity."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _write_drum_midi(path: Path, notes_at_ticks: list[tuple[int, int]], *, tpq: int = 480) -> None:
    """Write a minimal format-0 drum MIDI with NoteOn events on channel 9.

    ``notes_at_ticks`` is a list of ``(absolute_tick, gm_note)``. Tempo is the
    MIDI default (500000 us/qn = 120 BPM) so tick->seconds is
    ``tick / tpq * 0.5``.
    """
    events = sorted(notes_at_ticks)
    track = bytearray()
    prev = 0
    for tick, note in events:
        track += _vlq(tick - prev)
        track += bytes([0x99, note & 0x7F, 100])  # NoteOn ch9, vel 100
        prev = tick
        # Immediate NoteOff (delta 0) keeps the file tidy; parser ignores it.
        track += _vlq(0)
        track += bytes([0x89, note & 0x7F, 0])
    track += _vlq(0) + bytes([0xFF, 0x2F, 0x00])  # End of track

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, tpq)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    path.write_bytes(header + chunk)


# --------------------------------------------------------------------------- #
# (a) target builder
# --------------------------------------------------------------------------- #

def test_gm_note_to_class_index_folds_to_five_classes() -> None:
    assert gm_note_to_class_index(36) == CLASSES.index("kick")
    assert gm_note_to_class_index(38) == CLASSES.index("snare")
    assert gm_note_to_class_index(42) == CLASSES.index("hi_hat")
    assert gm_note_to_class_index(45) == CLASSES.index("toms")  # tom2
    assert gm_note_to_class_index(50) == CLASSES.index("toms")  # tom1
    assert gm_note_to_class_index(49) == CLASSES.index("cymbals")  # crash
    assert gm_note_to_class_index(51) == CLASSES.index("cymbals")  # ride
    # Out-of-taxonomy notes map to None so callers can drop them.
    assert gm_note_to_class_index(99) is None
    assert gm_note_to_class_index(60) is None


def test_onsets_rasterise_to_expected_frames() -> None:
    feat = FeatureConfig()
    tgt = TargetConfig(smoothing_frames=1)
    # kick at 0.5 s, snare at 1.0 s. Expected centre = round(t * sr / hop).
    onsets = [(0.5, 36), (1.0, 38)]
    n_frames = 150
    target = onsets_to_class_frames(onsets, n_frames, feat, tgt)

    assert target.shape == (n_frames, len(CLASSES))
    kick_center = round(0.5 * feat.sample_rate / feat.hop_length)
    snare_center = round(1.0 * feat.sample_rate / feat.hop_length)
    kick_col = CLASSES.index("kick")
    snare_col = CLASSES.index("snare")

    # Peaks land on the expected centres with weight 1.0.
    assert int(np.argmax(target[:, kick_col])) == kick_center
    assert target[kick_center, kick_col] == 1.0
    assert int(np.argmax(target[:, snare_col])) == snare_center
    assert target[snare_center, snare_col] == 1.0

    # +/-1 smoothing frame around each onset is populated, rest is zero.
    assert np.where(target[:, kick_col] > 0)[0].tolist() == [
        kick_center - 1,
        kick_center,
        kick_center + 1,
    ]
    # No spurious activity in classes that had no onsets.
    for other in ("hi_hat", "toms", "cymbals"):
        assert target[:, CLASSES.index(other)].sum() == 0.0


def test_onsets_out_of_range_and_oov_are_dropped() -> None:
    feat = FeatureConfig()
    tgt = TargetConfig(smoothing_frames=0)
    # One onset beyond n_frames, one OOV note -> both dropped, target all zero.
    onsets = [(100.0, 36), (0.2, 99)]
    target = onsets_to_class_frames(onsets, 50, feat, tgt)
    assert target.shape == (50, len(CLASSES))
    assert target.sum() == 0.0


def test_parse_drum_midi_recovers_known_onsets(tmp_path: Path) -> None:
    feat = FeatureConfig()
    tgt = TargetConfig(smoothing_frames=0)
    tpq = 480
    # 120 BPM default -> 1 quarter note = 0.5 s. Put kick at qn 1 (0.5s),
    # snare at qn 2 (1.0s), hi-hat at qn 3 (1.5s).
    midi = tmp_path / "synthetic.midi"
    _write_drum_midi(midi, [(tpq, 36), (2 * tpq, 38), (3 * tpq, 42)], tpq=tpq)

    onsets = parse_drum_onsets(midi)
    times = {round(t, 3): n for t, n in onsets}
    assert times == {0.5: 36, 1.0: 38, 1.5: 42}

    n_frames = n_frames_for_samples(int(2.0 * feat.sample_rate), feat)
    target = build_targets_from_midi(midi, n_frames, feat, tgt)
    # Exactly three positive frames, one per class, at the expected indices.
    assert target[:, CLASSES.index("kick")].sum() == 1.0
    assert target[:, CLASSES.index("snare")].sum() == 1.0
    assert target[:, CLASSES.index("hi_hat")].sum() == 1.0
    assert int(np.argmax(target[:, CLASSES.index("kick")])) == round(
        0.5 * feat.sample_rate / feat.hop_length
    )


# --------------------------------------------------------------------------- #
# (b) feature shape
# --------------------------------------------------------------------------- #

def test_logmel_shape_matches_documented_formula() -> None:
    feat = FeatureConfig()
    n_samples = feat.sample_rate * 2  # 2 seconds
    audio = (np.random.default_rng(0).standard_normal(n_samples) * 0.01).astype(np.float32)
    lm = logmel_from_audio(audio, feat)
    assert lm.dtype == np.float32
    assert lm.shape == (n_frames_for_samples(n_samples, feat), feat.n_mels)
    # Deterministic: same input -> identical output.
    lm2 = logmel_from_audio(audio, feat)
    assert np.array_equal(lm, lm2)


# --------------------------------------------------------------------------- #
# (c) model forward
# --------------------------------------------------------------------------- #

def test_model_forward_output_shape() -> None:
    import torch

    cfg = ModelConfig()
    model = DrumCRNN(cfg)
    model.eval()
    x = torch.randn(3, 128, cfg.n_mels)
    with torch.no_grad():
        y = model(x)
    assert tuple(y.shape) == (3, 128, cfg.num_classes)


def test_model_stays_under_five_million_params() -> None:
    model = DrumCRNN(ModelConfig())
    n = count_parameters(model)
    assert 0 < n < 5_000_000, f"model has {n} params, expected < 5M"


def test_decode_recovers_planted_onsets() -> None:
    feat = FeatureConfig()
    # Build a probability array with sharp isolated peaks per class.
    n_frames = 120
    probs = np.zeros((n_frames, len(CLASSES)), dtype=np.float32)
    probs[30, CLASSES.index("kick")] = 0.9
    probs[60, CLASSES.index("snare")] = 0.8
    events = decode_events(probs, feat, threshold=0.5, min_gap_sec=0.03)
    assert len(events) == 2
    kicks = [e for e in events if e.note == 36]
    snares = [e for e in events if e.note == 38]
    assert len(kicks) == 1 and len(snares) == 1
    assert abs(kicks[0].time - 30 * feat.hop_length / feat.sample_rate) < 1e-6


# --------------------------------------------------------------------------- #
# (d) ONNX export round-trip
# --------------------------------------------------------------------------- #

def test_onnx_export_round_trips(tmp_path: Path) -> None:
    from aural_ingest.training.drum_crnn.export_onnx import export_to_onnx, verify_onnx

    cfg = ModelConfig()
    model = DrumCRNN(cfg)
    model.eval()
    onnx_path = tmp_path / "drum_crnn_test.onnx"
    export_to_onnx(model, onnx_path, n_mels=cfg.n_mels, example_frames=64)
    assert onnx_path.is_file()
    # verify_onnx runs both torch and onnxruntime on a random input and
    # raises if they differ by more than atol; returns the max abs diff.
    max_diff = verify_onnx(model, onnx_path, n_mels=cfg.n_mels, frames=97, atol=1e-4)
    assert max_diff < 1e-4
