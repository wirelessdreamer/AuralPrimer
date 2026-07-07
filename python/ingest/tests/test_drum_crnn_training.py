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
    parse_class_thresholds,
)
from aural_ingest.training.drum_crnn.dataset import (
    EGMDDrumDataset,
    _crop_or_pad,
    _pick_window_start_sec,
    _row_duration_sec,
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


def test_decode_events_accepts_per_class_threshold_mapping() -> None:
    # A peak at 0.3 clears a lax cymbals threshold (0.2) but not a strict
    # kick threshold (0.5) -- proves per-class values are actually applied,
    # not just the first one found.
    feat = FeatureConfig()
    n_frames = 60
    probs = np.zeros((n_frames, len(CLASSES)), dtype=np.float32)
    probs[10, CLASSES.index("kick")] = 0.3
    probs[20, CLASSES.index("cymbals")] = 0.3
    thresholds = {name: 0.5 for name in CLASSES}
    thresholds["cymbals"] = 0.2
    events = decode_events(probs, feat, threshold=thresholds, min_gap_sec=0.03)
    notes = {e.note for e in events}
    assert 49 in notes  # cymbals -- cleared its lax threshold
    assert 36 not in notes  # kick -- did not clear its strict threshold


def test_decode_events_mapping_missing_class_raises() -> None:
    feat = FeatureConfig()
    probs = np.zeros((10, len(CLASSES)), dtype=np.float32)
    incomplete = {name: 0.2 for name in CLASSES if name != "cymbals"}
    with pytest.raises(KeyError, match="cymbals"):
        decode_events(probs, feat, threshold=incomplete)


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
# (f2) per-class decode-threshold string parsing (shared by env var + CLI)
# --------------------------------------------------------------------------- #

def test_parse_class_thresholds_parses_all_classes() -> None:
    raw = "kick:0.2,snare:0.25,hi_hat:0.2,toms:0.2,cymbals:0.12"
    result = parse_class_thresholds(raw)
    assert result == {
        "kick": 0.2,
        "snare": 0.25,
        "hi_hat": 0.2,
        "toms": 0.2,
        "cymbals": 0.12,
    }


def test_parse_class_thresholds_tolerates_whitespace_and_partial_input() -> None:
    assert parse_class_thresholds(" kick : 0.3 , cymbals:0.1 ") == {
        "kick": 0.3,
        "cymbals": 0.1,
    }


def test_parse_class_thresholds_skips_malformed_segments() -> None:
    # no colon, empty segment, and an unparseable value are all skipped
    # rather than raising -- this feeds env-var/CLI input.
    raw = "kick:0.2,,justaname,snare:notafloat,toms:0.4"
    assert parse_class_thresholds(raw) == {"kick": 0.2, "toms": 0.4}


def test_parse_class_thresholds_empty_string_returns_empty_dict() -> None:
    assert parse_class_thresholds("") == {}


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


# --------------------------------------------------------------------------- #
# (h) windowed dataset loading (partial-read training-throughput lever)
# --------------------------------------------------------------------------- #

def test_pick_window_start_sec_short_file_returns_zero() -> None:
    rng = np.random.default_rng(0)
    # File shorter than the requested clip -> no room to choose, start at 0.
    assert _pick_window_start_sec([1.0], file_duration_sec=5.0, clip_seconds=8.0, rng=rng) == 0.0


def test_pick_window_start_sec_anchors_on_an_onset() -> None:
    rng = np.random.default_rng(0)
    onsets = [12.0]
    start = _pick_window_start_sec(onsets, file_duration_sec=30.0, clip_seconds=8.0, rng=rng)
    # The chosen window must actually contain the onset it anchored on.
    assert start <= 12.0 <= start + 8.0
    assert 0.0 <= start <= 30.0 - 8.0


def test_pick_window_start_sec_no_onsets_stays_in_bounds() -> None:
    rng = np.random.default_rng(0)
    for _ in range(20):
        start = _pick_window_start_sec([], file_duration_sec=30.0, clip_seconds=8.0, rng=rng)
        assert 0.0 <= start <= 30.0 - 8.0


def test_pick_window_start_sec_ignores_onsets_outside_file() -> None:
    # An onset time beyond the file's own duration (shouldn't happen, but
    # guards against a corrupt/mismatched MIDI) must not be used as an anchor.
    rng = np.random.default_rng(0)
    start = _pick_window_start_sec([999.0], file_duration_sec=30.0, clip_seconds=8.0, rng=rng)
    assert 0.0 <= start <= 30.0 - 8.0


def test_pick_window_start_sec_is_deterministic_per_seed() -> None:
    onsets = [3.0, 10.0, 20.0]
    a = _pick_window_start_sec(onsets, 30.0, 8.0, np.random.default_rng(42))
    b = _pick_window_start_sec(onsets, 30.0, 8.0, np.random.default_rng(42))
    assert a == b


def test_row_duration_sec_prefers_csv_column() -> None:
    # Valid CSV duration -> used directly, no file I/O (path doesn't need to exist).
    row = {"duration": "12.5"}
    assert _row_duration_sec(row, "/nonexistent/path.wav") == 12.5


def test_row_duration_sec_falls_back_to_soundfile_probe(tmp_path: Path) -> None:
    import soundfile as sf

    wav = tmp_path / "probe.wav"
    sf.write(str(wav), np.zeros(22050 * 3, dtype=np.float32), 22050)  # 3.0 s

    for bad_row in ({"duration": "0"}, {"duration": ""}, {"duration": "notanumber"}, {}):
        assert _row_duration_sec(bad_row, str(wav)) == pytest.approx(3.0, abs=1e-2)


def test_crop_or_pad_pads_short_clips() -> None:
    features = np.ones((5, 4), dtype=np.float32)
    targets = np.ones((5, 2), dtype=np.float32)
    rng = np.random.default_rng(0)
    f, t = _crop_or_pad(features, targets, clip_frames=8, rng=rng)
    assert f.shape == (8, 4) and t.shape == (8, 2)
    assert np.array_equal(f[:5], features) and np.array_equal(f[5:], np.zeros((3, 4)))


def test_crop_or_pad_trims_slightly_long_clips() -> None:
    # The off-by-one-frame safety net (librosa's partial-read frame count can
    # be +/-1 vs clip_frames) -- trims from the start, keeps alignment.
    features = np.arange(10 * 4, dtype=np.float32).reshape(10, 4)
    targets = np.arange(10 * 2, dtype=np.float32).reshape(10, 2)
    rng = np.random.default_rng(0)
    f, t = _crop_or_pad(features, targets, clip_frames=8, rng=rng)
    assert f.shape == (8, 4) and t.shape == (8, 2)
    assert np.array_equal(f, features[:8]) and np.array_equal(t, targets[:8])


def test_crop_or_pad_exact_length_is_noop() -> None:
    features = np.ones((8, 4), dtype=np.float32)
    targets = np.ones((8, 2), dtype=np.float32)
    rng = np.random.default_rng(0)
    f, t = _crop_or_pad(features, targets, clip_frames=8, rng=rng)
    assert f is features and t is targets


def _write_minimal_egmd_corpus(
    root: Path, *, audio_seconds: float, notes_at_ticks: list[tuple[int, int]], tpq: int = 480
) -> None:
    """Build a tiny on-disk corpus matching E-GMD's real directory shape
    (``e_gmd_metadata/e-gmd-v1.0.0.csv`` + ``e_gmd_full/e-gmd-v1.0.0/``) so
    ``EGMDDrumDataset`` can be exercised end-to-end (its real ``__init__`` +
    ``__getitem__``, not a hand-built instance)."""
    import csv as csv_mod

    import soundfile as sf

    audio_root = root / "e_gmd_full" / "e-gmd-v1.0.0"
    audio_root.mkdir(parents=True)
    (root / "e_gmd_metadata").mkdir(parents=True)

    sr = 22050
    sf.write(str(audio_root / "x.wav"), np.zeros(int(audio_seconds * sr), dtype=np.float32), sr)
    _write_drum_midi(audio_root / "x.midi", notes_at_ticks, tpq=tpq)

    fields = [
        "drummer", "session", "id", "style", "bpm", "beat_type", "time_signature",
        "duration", "split", "midi_filename", "audio_filename", "kit_name",
    ]
    with (root / "e_gmd_metadata" / "e-gmd-v1.0.0.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(
            {
                "drummer": "drummer1", "session": "s1", "id": "drummer1/s1/1",
                "style": "funk/groove1", "bpm": "120", "beat_type": "beat",
                "time_signature": "4-4", "duration": str(audio_seconds), "split": "train",
                "midi_filename": "x.midi", "audio_filename": "x.wav", "kit_name": "Test Kit",
            }
        )


def test_dataset_getitem_pads_a_file_shorter_than_clip_seconds(tmp_path: Path) -> None:
    """Regression test: a file shorter than ``clip_seconds`` used to crash
    with a shape mismatch in ``_crop_or_pad`` (targets were rasterised at the
    fixed ``clip_frames`` length while features reflected the shorter ACTUAL
    loaded-audio length)."""
    _write_minimal_egmd_corpus(tmp_path, audio_seconds=2.0, notes_at_ticks=[(480, 36)])
    cfg = TrainConfig(corpus_root=str(tmp_path), clip_seconds=8.0)
    ds = EGMDDrumDataset(cfg, "train")
    assert len(ds) == 1

    features, targets = ds[0]
    assert features.shape == (ds.clip_frames, cfg.feature.n_mels)
    assert targets.shape == (ds.clip_frames, len(CLASSES))
    # The planted kick onset (at 0.5s, well inside the 2s file) must survive
    # into the padded target.
    assert targets[:, CLASSES.index("kick")].sum() > 0.0


def test_dataset_getitem_windows_a_file_longer_than_clip_seconds(tmp_path: Path) -> None:
    """A file longer than clip_seconds must be windowed down to exactly
    clip_frames, not the whole (long) file."""
    _write_minimal_egmd_corpus(
        tmp_path, audio_seconds=30.0, notes_at_ticks=[(480, 36), (20 * 480, 38)]
    )
    cfg = TrainConfig(corpus_root=str(tmp_path), clip_seconds=8.0)
    ds = EGMDDrumDataset(cfg, "train")

    features, targets = ds[0]
    assert features.shape == (ds.clip_frames, cfg.feature.n_mels)
    assert targets.shape == (ds.clip_frames, len(CLASSES))
