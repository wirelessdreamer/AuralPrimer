"""Training loop for the drum CRNN.

Binary-cross-entropy per class (``BCEWithLogitsLoss``, multi-label, sigmoid
fused into the loss), Adam, per-epoch checkpointing, and a validation pass
that reports per-class frame F1. Config-driven via :class:`TrainConfig`.

Frame F1 is computed against the *onset frames* of the smoothed target: a
target frame counts as positive when its label weight is > 0 (i.e. it is on or
adjacent to a real onset), and a prediction counts as positive when its
sigmoid probability clears ``frame_threshold``. This is a plumbing-level metric
for the harness, not the event-level F1 the benchmark ultimately reports.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from .config import CLASSES, TrainConfig
from .dataset import EGMDDrumDataset, collate
from .model import DrumCRNN, count_parameters


class FrameF1:
    """Accumulate TP/FP/FN per class across a validation pass."""

    def __init__(self, num_classes: int) -> None:
        self.tp = np.zeros(num_classes, dtype=np.int64)
        self.fp = np.zeros(num_classes, dtype=np.int64)
        self.fn = np.zeros(num_classes, dtype=np.int64)

    def update(self, logits: torch.Tensor, targets: torch.Tensor, threshold: float) -> None:
        pred = (torch.sigmoid(logits) >= threshold).cpu().numpy()  # (B,T,C) bool
        gold = (targets.cpu().numpy() > 0.0)  # (B,T,C) bool
        # Sum over batch and time, per class.
        self.tp += np.sum(pred & gold, axis=(0, 1)).astype(np.int64)
        self.fp += np.sum(pred & ~gold, axis=(0, 1)).astype(np.int64)
        self.fn += np.sum(~pred & gold, axis=(0, 1)).astype(np.int64)

    def per_class_f1(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for i, name in enumerate(CLASSES):
            denom = 2 * self.tp[i] + self.fp[i] + self.fn[i]
            out[name] = float(2 * self.tp[i] / denom) if denom > 0 else 0.0
        return out

    def macro_f1(self) -> float:
        vals = list(self.per_class_f1().values())
        return float(sum(vals) / len(vals)) if vals else 0.0


def _make_loader(ds: EGMDDrumDataset, cfg: TrainConfig, *, shuffle: bool) -> DataLoader:
    return DataLoader(
        ds,
        batch_size=cfg.batch_size,
        shuffle=shuffle,
        num_workers=cfg.num_workers,
        collate_fn=collate,
        drop_last=False,
    )


@torch.no_grad()
def evaluate(
    model: DrumCRNN,
    loader: DataLoader,
    criterion: nn.Module,
    cfg: TrainConfig,
    device: torch.device,
) -> tuple[float, dict[str, float]]:
    """Return (mean val loss, per-class frame F1) over ``loader``."""
    model.eval()
    metric = FrameF1(cfg.model.num_classes)
    total_loss = 0.0
    n_batches = 0
    for feats, tgts in loader:
        feats = feats.to(device)
        tgts = tgts.to(device)
        logits = model(feats)
        total_loss += float(criterion(logits, tgts).item())
        n_batches += 1
        metric.update(logits, tgts, cfg.frame_threshold)
    mean_loss = total_loss / max(1, n_batches)
    return mean_loss, metric.per_class_f1()


def train(cfg: TrainConfig, *, verbose: bool = True) -> dict:
    """Run training end to end. Returns a summary dict of the final state.

    Writes ``checkpoint_epochXX.pt`` and ``checkpoint_best.pt`` plus a
    ``config.json`` under ``cfg.output_dir`` (which MUST live outside the repo
    tree -- it holds weights we never commit).
    """
    if not cfg.output_dir:
        raise ValueError("TrainConfig.output_dir is required (keep it OUTSIDE the repo)")
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    if cfg.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(cfg.device)

    train_ds = EGMDDrumDataset(cfg, "train", limit=cfg.train_limit)
    val_split = "validation"
    try:
        val_ds = EGMDDrumDataset(cfg, val_split, limit=cfg.val_limit)
    except ValueError:
        # Tiny smoke corpora may have no validation rows on disk; fall back to
        # reusing the train set so the val-pass plumbing still exercises.
        val_ds = EGMDDrumDataset(cfg, "train", limit=cfg.val_limit or cfg.train_limit)
        val_split = "train(fallback)"

    train_loader = _make_loader(train_ds, cfg, shuffle=True)
    val_loader = _make_loader(val_ds, cfg, shuffle=False)

    model = DrumCRNN(cfg.model).to(device)
    n_params = count_parameters(model)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)

    (out_dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")

    if verbose:
        print(
            f"[drum_crnn] device={device.type} params={n_params:,} "
            f"train={len(train_ds)} val={len(val_ds)} ({val_split}) "
            f"clip_frames={train_ds.clip_frames}"
        )

    history: list[dict] = []
    best_macro = -1.0
    best_path = out_dir / "checkpoint_best.pt"

    for epoch in range(1, cfg.epochs + 1):
        model.train()
        epoch_loss = 0.0
        n_batches = 0
        for feats, tgts in train_loader:
            feats = feats.to(device)
            tgts = tgts.to(device)
            optimizer.zero_grad()
            logits = model(feats)
            loss = criterion(logits, tgts)
            loss.backward()
            optimizer.step()
            epoch_loss += float(loss.item())
            n_batches += 1
        train_loss = epoch_loss / max(1, n_batches)

        val_loss, per_class_f1 = evaluate(model, val_loader, criterion, cfg, device)
        macro = float(sum(per_class_f1.values()) / len(per_class_f1))
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_macro_f1": macro,
                "val_per_class_f1": per_class_f1,
            }
        )
        if verbose:
            f1s = " ".join(f"{k}={v:.3f}" for k, v in per_class_f1.items())
            print(
                f"[drum_crnn] epoch {epoch:>3}/{cfg.epochs} "
                f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
                f"macroF1={macro:.3f} | {f1s}"
            )

        ckpt = {
            "epoch": epoch,
            "model_state": model.state_dict(),
            "model_config": asdict(cfg.model),
            "feature_config": asdict(cfg.feature),
            "classes": list(CLASSES),
        }
        torch.save(ckpt, out_dir / f"checkpoint_epoch{epoch:02d}.pt")
        if macro >= best_macro:
            best_macro = macro
            torch.save(ckpt, best_path)

    summary = {
        "params": n_params,
        "device": device.type,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "val_split": val_split,
        "clip_frames": train_ds.clip_frames,
        "best_macro_f1": best_macro,
        "best_checkpoint": str(best_path),
        "history": history,
    }
    (out_dir / "history.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
