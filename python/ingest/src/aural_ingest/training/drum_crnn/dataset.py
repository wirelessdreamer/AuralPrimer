"""Streaming E-GMD dataset for the drum CRNN.

Reads rows from ``e-gmd-v1.0.0.csv``, filters by the split column, and yields
``(features, targets)`` pairs computed lazily per item -- audio is loaded and
turned into log-mel on demand in ``__getitem__``, never all at once, so memory
stays bounded regardless of the 444 h corpus size.

Each example is a ``clip_seconds``-long window, chosen BEFORE any audio is
decoded: the MIDI is parsed first (cheap, stdlib-only) to find onset times,
then only that ``[start, start + clip_seconds)`` slice of the WAV is loaded
via a partial read (``load_audio_mono_window``). E-GMD clips average ~35 s
but training only needs an 8 s window, so this is the dataset's main
throughput lever -- full-file decode-then-crop would waste ~4x the audio
decode + log-mel compute per item. The window is chosen preferring one that
contains an onset (drums are sparse; a purely random window can be
near-silent), mirroring the previous crop-after-decode behavior but working
in seconds against raw onset times instead of frame indices in a fully
decoded target array.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from torch.utils.data import Dataset

from .config import CLASSES, TrainConfig
from .features import load_audio_mono_window, logmel_from_audio, n_frames_for_samples
from .targets import gm_note_to_class_index, onsets_to_class_frames, parse_drum_onsets


def _resolve_paths(corpus_root: Path) -> tuple[Path, Path]:
    metadata_csv = corpus_root / "e_gmd_metadata" / "e-gmd-v1.0.0.csv"
    audio_root = corpus_root / "e_gmd_full" / "e-gmd-v1.0.0"
    if not metadata_csv.is_file():
        raise FileNotFoundError(f"E-GMD metadata not found: {metadata_csv}")
    if not audio_root.is_dir():
        raise FileNotFoundError(f"E-GMD audio root not found: {audio_root}")
    return metadata_csv, audio_root


def list_split_rows(
    corpus_root: str | Path,
    split: str,
    *,
    limit: int | None = None,
    require_files: bool = True,
) -> list[dict[str, str]]:
    """Return metadata rows for ``split`` whose WAV+MIDI exist on disk.

    Cheap: reads only the CSV and stats the referenced files. No audio is
    decoded here, so callers can enumerate a split without paying feature cost.
    """
    corpus_root = Path(corpus_root)
    metadata_csv, audio_root = _resolve_paths(corpus_root)
    rows: list[dict[str, str]] = []
    with metadata_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["split"] != split:
                continue
            if require_files:
                audio = audio_root / row["audio_filename"]
                midi = audio_root / row["midi_filename"]
                if not audio.is_file() or not midi.is_file():
                    continue
            row = dict(row)
            row["_audio_path"] = str(audio_root / row["audio_filename"])
            row["_midi_path"] = str(audio_root / row["midi_filename"])
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _crop_or_pad(
    features: np.ndarray,
    targets: np.ndarray,
    clip_frames: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Force ``features``/``targets`` to exactly ``clip_frames`` rows.

    A safety net for off-by-one frame counts (librosa's partial-read frame
    count for a requested ``clip_seconds`` window can be +/-1 vs
    ``clip_frames`` due to rounding), NOT the primary window-selection
    mechanism -- that now happens in seconds, before any audio is decoded, via
    ``_pick_window_start_sec``. Longer -> trim from the start (rare: at most a
    frame or two over). Shorter -> zero-pad at the end (short files, or a
    window that ran past end-of-file). Features and targets are
    cropped/padded identically so they stay frame-aligned.
    """
    n = features.shape[0]
    if n == clip_frames:
        return features, targets
    if n < clip_frames:
        fpad = np.zeros((clip_frames, features.shape[1]), dtype=features.dtype)
        tpad = np.zeros((clip_frames, targets.shape[1]), dtype=targets.dtype)
        fpad[:n] = features
        tpad[:n] = targets
        return fpad, tpad
    return features[:clip_frames], targets[:clip_frames]


def _row_duration_sec(row: dict[str, str], audio_path: str) -> float:
    """File duration in seconds: the CSV's ``duration`` column (no I/O) with a
    header-only ``soundfile`` probe as a fallback (still no full decode) for
    rows where it's missing or unparseable."""
    raw = row.get("duration")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    import soundfile as sf

    return float(sf.info(audio_path).duration)


def _pick_window_start_sec(
    onset_times_sec: list[float],
    file_duration_sec: float,
    clip_seconds: float,
    rng: np.random.Generator,
) -> float:
    """Choose a ``[start, start + clip_seconds)`` window in seconds.

    Prefers a window that contains at least one onset (drums are sparse; a
    purely random window can be near-silent) -- operating on raw onset TIMES
    from the cheap MIDI parse, so this decision never requires decoding
    audio. ``onset_times_sec`` should already be filtered to onsets that map
    into the 5-class taxonomy (out-of-vocab notes wouldn't produce a positive
    target frame, so anchoring on one would silently behave like the
    no-onset case anyway).
    """
    max_start = max(0.0, file_duration_sec - clip_seconds)
    if max_start <= 0.0:
        return 0.0
    candidates = [t for t in onset_times_sec if 0.0 <= t <= file_duration_sec]
    if candidates:
        anchor = float(rng.choice(candidates))
        return float(np.clip(anchor - clip_seconds / 2.0, 0.0, max_start))
    return float(rng.uniform(0.0, max_start))


class EGMDDrumDataset(Dataset):
    """Map-style dataset over one E-GMD split, features computed on demand."""

    def __init__(self, cfg: TrainConfig, split: str, *, limit: int | None = None) -> None:
        self.cfg = cfg
        self.split = split
        self.rows = list_split_rows(cfg.corpus_root, split, limit=limit)
        self.clip_frames = n_frames_for_samples(
            int(cfg.clip_seconds * cfg.feature.sample_rate), cfg.feature
        )
        if not self.rows:
            raise ValueError(f"no usable E-GMD rows for split={split!r} under {cfg.corpus_root}")

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> tuple[np.ndarray, np.ndarray]:
        row = self.rows[idx]

        # Parse the MIDI first (cheap, stdlib-only) so the window choice below
        # never needs a decoded waveform.
        onsets = parse_drum_onsets(row["_midi_path"])  # [(t_sec, gm_note), ...]
        valid_onset_times = [
            t for t, note in onsets if gm_note_to_class_index(note) is not None
        ]
        file_duration = _row_duration_sec(row, row["_audio_path"])

        # Deterministic-per-item RNG so a given index always picks the same
        # window (reproducible epochs, stable smoke runs).
        rng = np.random.default_rng(self.cfg.seed * 1_000_003 + idx)
        start_sec = _pick_window_start_sec(
            valid_onset_times, file_duration, self.cfg.clip_seconds, rng
        )

        # Partial read: only this window's audio is decoded (the throughput
        # lever -- E-GMD clips average ~35 s, a window is 8 s). Short files
        # (< clip_seconds) come back shorter than clip_frames; _crop_or_pad
        # below zero-pads those, same as before this refactor.
        audio = load_audio_mono_window(
            row["_audio_path"], self.cfg.feature.sample_rate, start_sec, self.cfg.clip_seconds
        )
        features = logmel_from_audio(audio, self.cfg.feature)  # (T', n_mels), T' ~= clip_frames

        # Shift onsets into the window's local time frame and rasterise at
        # the ACTUAL loaded length (features.shape[0]), not clip_frames --
        # they must match exactly for _crop_or_pad to align them below.
        local_onsets = [
            (t - start_sec, note)
            for t, note in onsets
            if 0.0 <= (t - start_sec) < self.cfg.clip_seconds
        ]
        targets = onsets_to_class_frames(
            local_onsets, features.shape[0], self.cfg.feature, self.cfg.target
        )  # (T', C)

        features, targets = _crop_or_pad(features, targets, self.clip_frames, rng)
        return features.astype(np.float32), targets.astype(np.float32)


def collate(batch: list[tuple[np.ndarray, np.ndarray]]):
    """Stack fixed-length items into ``(B,T,M)`` / ``(B,T,C)`` float tensors."""
    import torch

    feats = np.stack([b[0] for b in batch], axis=0)
    tgts = np.stack([b[1] for b in batch], axis=0)
    return torch.from_numpy(feats), torch.from_numpy(tgts)


__all__ = [
    "CLASSES",
    "EGMDDrumDataset",
    "collate",
    "list_split_rows",
]
