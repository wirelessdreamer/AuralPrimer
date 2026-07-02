"""Streaming E-GMD dataset for the drum CRNN.

Reads rows from ``e-gmd-v1.0.0.csv``, filters by the split column, and yields
``(features, targets)`` pairs computed lazily per item -- audio is loaded and
turned into log-mel on demand in ``__getitem__``, never all at once, so memory
stays bounded regardless of the 444 h corpus size.

Each example is cropped or zero-padded to a fixed number of frames
(``clip_seconds``) so a batch stacks into a dense tensor. Cropping picks a
window that contains at least one onset when possible (drums are sparse; a
purely random window can be near-silent).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from torch.utils.data import Dataset

from .config import CLASSES, TrainConfig
from .features import logmel_from_audio, load_audio_mono, n_frames_for_samples
from .targets import onsets_to_class_frames, parse_drum_onsets


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

    Longer -> random window preferring one that contains an onset. Shorter ->
    zero-pad at the end. Features and targets are cropped/padded identically so
    they stay frame-aligned.
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

    # n > clip_frames: choose a start. Prefer a window with an onset.
    onset_frames = np.where(targets.max(axis=1) > 0.0)[0]
    max_start = n - clip_frames
    if onset_frames.size > 0:
        anchor = int(rng.choice(onset_frames))
        start = int(np.clip(anchor - clip_frames // 2, 0, max_start))
    else:
        start = int(rng.integers(0, max_start + 1))
    end = start + clip_frames
    return features[start:end], targets[start:end]


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
        audio = load_audio_mono(row["_audio_path"], self.cfg.feature.sample_rate)
        features = logmel_from_audio(audio, self.cfg.feature)  # (T, n_mels)
        onsets = parse_drum_onsets(row["_midi_path"])
        targets = onsets_to_class_frames(
            onsets, features.shape[0], self.cfg.feature, self.cfg.target
        )  # (T, C)
        # Deterministic-per-item RNG so a given index always crops the same way
        # (reproducible epochs, stable smoke runs).
        rng = np.random.default_rng(self.cfg.seed * 1_000_003 + idx)
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
