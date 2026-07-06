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
import pytest

from aural_ingest.training.drum_crnn.config import (
    CLASSES,
    FeatureConfig,
    ModelConfig,
    TargetConfig,
    TrainConfig,
)
from aural_ingest.training.drum_crnn.decode import decode_events
from aural_ingest.training.drum_crnn.features import (
    logmel_from_audio,
    n_frames_for_samples,
)
from aural_ingest.training.drum_crnn.model import DrumCRNN, count_parameters
from aural_ingest.training.drum_crnn.targets import (
    build_targets_from_midi,
    estimate_class_frame_counts,
    gm_note_to_class_index,
    onsets_to_class_frames,
    parse_drum_onsets,
    pos_weight_from_counts,
)
from aural_ingest.training.drum_crnn.train import (
    EarlyStopper,
    load_init_checkpoint,
    resolve_pos_weight,
    train,
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


# --------------------------------------------------------------------------- #
# (e) pos_weight estimation + resolution (run-3 class-imbalance fix)
# --------------------------------------------------------------------------- #

def test_estimate_class_frame_counts_from_synthetic_rows(tmp_path: Path) -> None:
    feat = FeatureConfig()
    tgt = TargetConfig(smoothing_frames=0)  # exact single-frame positives, easy to count

    # File A: one kick onset at 0.5s (qn 1 @ 120 BPM).
    midi_a = tmp_path / "a.midi"
    _write_drum_midi(midi_a, [(480, 36)], tpq=480)
    # File B: one cymbal onset at 1.0s (qn 2) -- crash=49.
    midi_b = tmp_path / "b.midi"
    _write_drum_midi(midi_b, [(960, 49)], tpq=480)

    rows = [
        {"_midi_path": str(midi_a), "duration": "2.0"},
        {"_midi_path": str(midi_b), "duration": "2.0"},
    ]
    pos_counts, total_frames = estimate_class_frame_counts(rows, feat, tgt, max_files=10)

    expected_frames_per_file = n_frames_for_samples(int(2.0 * feat.sample_rate), feat)
    assert total_frames == 2 * expected_frames_per_file
    assert pos_counts[CLASSES.index("kick")] == 1
    assert pos_counts[CLASSES.index("cymbals")] == 1
    assert pos_counts[CLASSES.index("snare")] == 0


def test_estimate_class_frame_counts_skips_missing_duration_and_bad_midi(tmp_path: Path) -> None:
    feat = FeatureConfig()
    tgt = TargetConfig()
    bogus_midi = tmp_path / "not_midi.midi"
    bogus_midi.write_bytes(b"not a midi file")
    rows = [
        {"_midi_path": str(bogus_midi), "duration": "2.0"},  # bad midi -> skipped
        {"_midi_path": str(bogus_midi), "duration": "0"},  # zero duration -> skipped
        {"_midi_path": str(bogus_midi), "duration": ""},  # empty duration -> skipped
        {"duration": "2.0"},  # no _midi_path -> skipped
    ]
    pos_counts, total_frames = estimate_class_frame_counts(rows, feat, tgt)
    assert total_frames == 0
    assert pos_counts.sum() == 0


def test_estimate_class_frame_counts_empty_rows_returns_zeros() -> None:
    pos_counts, total_frames = estimate_class_frame_counts([], FeatureConfig(), TargetConfig())
    assert total_frames == 0
    assert pos_counts.shape == (len(CLASSES),)
    assert pos_counts.sum() == 0


def test_pos_weight_from_counts_is_capped_and_floors_at_one() -> None:
    # kick: 500/1000 positive -> neg/pos = 1.0 (floor, not < 1 even though
    # it's the majority class in this synthetic example).
    # cymbals: 10/1000 positive -> neg/pos = 99, capped at 25.
    counts = np.array([500, 10], dtype=np.int64)
    weights = pos_weight_from_counts(counts, total_frames=1000, cap=25.0)
    assert weights.shape == (2,)
    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(25.0)


def test_pos_weight_from_counts_zero_total_returns_ones() -> None:
    weights = pos_weight_from_counts(np.zeros(len(CLASSES), dtype=np.int64), total_frames=0)
    assert np.array_equal(weights, np.ones(len(CLASSES), dtype=np.float32))


def test_resolve_pos_weight_none_disables_weighting() -> None:
    cfg = TrainConfig(pos_weight=None)
    assert resolve_pos_weight(cfg, rows=[]) is None


def test_resolve_pos_weight_explicit_tuple_passthrough() -> None:
    import torch

    weights = (1.0, 2.0, 3.0, 4.0, 5.0)
    cfg = TrainConfig(pos_weight=weights)
    result = resolve_pos_weight(cfg, rows=[])
    assert isinstance(result, torch.Tensor)
    assert result.tolist() == list(weights)


def test_resolve_pos_weight_explicit_tuple_wrong_length_raises() -> None:
    cfg = TrainConfig(pos_weight=(1.0, 2.0))  # only 2 entries, need 5
    with pytest.raises(ValueError, match="5 entries"):
        resolve_pos_weight(cfg, rows=[])


def test_resolve_pos_weight_unknown_string_raises() -> None:
    cfg = TrainConfig(pos_weight="bogus")
    with pytest.raises(ValueError, match="unknown pos_weight mode"):
        resolve_pos_weight(cfg, rows=[])


def test_resolve_pos_weight_auto_produces_capped_weights(tmp_path: Path) -> None:
    import torch

    midi_a = tmp_path / "a.midi"
    _write_drum_midi(midi_a, [(480, 36), (960, 36)], tpq=480)  # 2 kicks, no cymbals
    rows = [{"_midi_path": str(midi_a), "duration": "2.0"}] * 20
    cfg = TrainConfig(pos_weight="auto", pos_weight_sample_files=20)
    result = resolve_pos_weight(cfg, rows)
    assert isinstance(result, torch.Tensor)
    assert result.shape == (len(CLASSES),)
    assert bool((result >= 1.0).all())
    assert bool((result <= cfg.pos_weight_cap).all())
    # cymbals never appear in any row -> should sit at the cap.
    assert result[CLASSES.index("cymbals")].item() == pytest.approx(cfg.pos_weight_cap)


def test_resolve_pos_weight_auto_no_usable_rows_returns_none() -> None:
    # Every row skipped (zero duration) -> total_frames stays 0 -> None.
    rows = [{"_midi_path": "x", "duration": "0"}]
    cfg = TrainConfig(pos_weight="auto")
    assert resolve_pos_weight(cfg, rows) is None


# --------------------------------------------------------------------------- #
# (f) init-checkpoint fine-tune round-trip
# --------------------------------------------------------------------------- #

def test_load_init_checkpoint_round_trips_weights(tmp_path: Path) -> None:
    import torch

    torch.manual_seed(0)
    source = DrumCRNN(ModelConfig())
    ckpt_path = tmp_path / "checkpoint.pt"
    torch.save(
        {
            "epoch": 7,
            "model_state": source.state_dict(),
            "model_config": {},
            "feature_config": {},
            "classes": list(CLASSES),
        },
        ckpt_path,
    )

    torch.manual_seed(1)  # different init -- these are real independent weights
    target = DrumCRNN(ModelConfig())
    assert not torch.equal(source.head.weight, target.head.weight)

    ckpt = load_init_checkpoint(target, ckpt_path, torch.device("cpu"))

    assert ckpt["epoch"] == 7
    assert torch.equal(source.head.weight, target.head.weight)
    assert torch.equal(source.conv[0].conv.weight, target.conv[0].conv.weight)


def test_load_init_checkpoint_missing_file_raises(tmp_path: Path) -> None:
    import torch

    with pytest.raises(FileNotFoundError):
        load_init_checkpoint(DrumCRNN(ModelConfig()), tmp_path / "nope.pt", torch.device("cpu"))


def test_train_raises_on_missing_init_checkpoint(tmp_path: Path) -> None:
    # Must fail fast (before touching any dataset/corpus) so this is testable
    # without a real E-GMD corpus on disk.
    cfg = TrainConfig(
        output_dir=str(tmp_path / "out"),
        init_checkpoint=str(tmp_path / "does_not_exist.pt"),
    )
    with pytest.raises(FileNotFoundError, match="init_checkpoint"):
        train(cfg, verbose=False)


# --------------------------------------------------------------------------- #
# (g) early stopping / LR-halving decision logic
# --------------------------------------------------------------------------- #

def test_early_stopper_improves_resets_counter() -> None:
    stopper = EarlyStopper(patience=3)
    improved, halve, stop = stopper.update(0.10)
    assert improved and not halve and not stop
    improved, halve, stop = stopper.update(0.20)  # improves again
    assert improved and not halve and not stop
    assert stopper.epochs_since_best == 0


def test_early_stopper_halves_lr_once_then_stops_at_patience() -> None:
    stopper = EarlyStopper(patience=3)
    stopper.update(0.50)  # establishes the best
    improved, halve, stop = stopper.update(0.40)  # epochs_since_best = 1
    assert not improved and not halve and not stop
    improved, halve, stop = stopper.update(0.40)  # epochs_since_best = 2 -> halve
    assert not improved and halve and not stop
    improved, halve, stop = stopper.update(0.40)  # epochs_since_best = 3 -> stop
    assert not improved and not halve and stop  # already halved once, no repeat
    assert stopper.lr_halved is True


def test_early_stopper_patience_zero_never_stops_or_halves() -> None:
    stopper = EarlyStopper(patience=0)
    stopper.update(0.5)
    for _ in range(10):
        _improved, halve, stop = stopper.update(0.1)  # never improves again
        assert not halve and not stop


def test_early_stopper_low_patience_stops_before_halving_fires() -> None:
    # patience <= HALVE_AT (2): halving should never fire, only stopping.
    stopper = EarlyStopper(patience=2)
    stopper.update(0.5)
    _improved, halve, stop = stopper.update(0.1)  # epochs_since_best = 1
    assert not halve and not stop
    _improved, halve, stop = stopper.update(0.1)  # epochs_since_best = 2 -> stop
    assert not halve and stop
