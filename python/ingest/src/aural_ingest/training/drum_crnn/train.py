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
from .targets import estimate_class_frame_counts, pos_weight_from_counts


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


def resolve_pos_weight(
    cfg: TrainConfig, rows: list[dict[str, str]]
) -> torch.Tensor | None:
    """Resolve ``cfg.pos_weight`` into a per-class tensor, or ``None``.

    ``"auto"`` estimates neg/pos frame ratios from ``rows`` (see
    ``estimate_class_frame_counts`` in ``.targets``); an explicit tuple is
    validated and used as-is; ``None`` disables weighting.
    """
    if cfg.pos_weight is None:
        return None
    if isinstance(cfg.pos_weight, str):
        if cfg.pos_weight != "auto":
            raise ValueError(
                f"unknown pos_weight mode: {cfg.pos_weight!r} "
                "(expected 'auto', a tuple, or None)"
            )
        pos_counts, total_frames = estimate_class_frame_counts(
            rows, cfg.feature, cfg.target, max_files=cfg.pos_weight_sample_files
        )
        if total_frames <= 0:
            return None
        weights = pos_weight_from_counts(pos_counts, total_frames, cap=cfg.pos_weight_cap)
        return torch.tensor(weights, dtype=torch.float32)
    weights = np.asarray(cfg.pos_weight, dtype=np.float32)
    if weights.shape != (len(CLASSES),):
        got = weights.shape[0] if weights.ndim else "scalar"
        raise ValueError(
            f"pos_weight tuple must have {len(CLASSES)} entries (one per "
            f"class {CLASSES}), got {got}"
        )
    return torch.tensor(weights, dtype=torch.float32)


def load_init_checkpoint(
    model: DrumCRNN, checkpoint_path: str | Path, device: torch.device
) -> dict:
    """Load ``model_state`` from ``checkpoint_path`` into ``model`` in place.

    Returns the raw checkpoint dict (callers may want ``epoch`` for logging).
    Only the weights are restored -- the optimizer and epoch counter always
    start fresh, so this is a fine-tune / warm-start, not a resume. Raises
    ``FileNotFoundError`` if ``checkpoint_path`` doesn't exist.
    """
    path = Path(checkpoint_path)
    if not path.is_file():
        raise FileNotFoundError(f"init_checkpoint not found: {path}")
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state"])
    return ckpt


class EarlyStopper:
    """Tracks best val macro-F1; decides when to halve LR and when to stop.

    Semantics: after ``patience`` consecutive epochs with no improvement,
    stop. The LR is halved exactly once, at the 2nd consecutive non-improving
    epoch (a fixed heuristic -- giving the model one shot at a lower LR before
    the run gives up). ``patience <= 0`` disables both early stopping and LR
    halving; ``patience <= HALVE_AT`` (2) disables halving specifically (the
    run would stop before a 2nd non-improving epoch could trigger it).
    """

    HALVE_AT = 2

    def __init__(self, patience: int) -> None:
        self.patience = patience
        self.best = float("-inf")
        self.epochs_since_best = 0
        self.lr_halved = False

    def update(self, macro_f1: float) -> tuple[bool, bool, bool]:
        """Record one epoch's macro-F1. Returns ``(improved, halve_lr, stop)``."""
        if macro_f1 >= self.best:
            self.best = macro_f1
            self.epochs_since_best = 0
            return True, False, False
        self.epochs_since_best += 1
        should_halve = (
            self.patience > self.HALVE_AT
            and not self.lr_halved
            and self.epochs_since_best == self.HALVE_AT
        )
        if should_halve:
            self.lr_halved = True
        should_stop = self.patience > 0 and self.epochs_since_best >= self.patience
        return False, should_halve, should_stop


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
    if cfg.init_checkpoint and not Path(cfg.init_checkpoint).is_file():
        # Fail before touching the (possibly huge, possibly absent) corpus.
        raise FileNotFoundError(f"init_checkpoint not found: {cfg.init_checkpoint}")
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
    if cfg.init_checkpoint:
        init_ckpt = load_init_checkpoint(model, cfg.init_checkpoint, device)
        if verbose:
            print(
                f"[drum_crnn] initialized from {cfg.init_checkpoint} "
                f"(epoch {init_ckpt.get('epoch')})"
            )
    n_params = count_parameters(model)

    pos_weight = resolve_pos_weight(cfg, train_ds.rows)
    criterion = nn.BCEWithLogitsLoss(
        pos_weight=pos_weight.to(device) if pos_weight is not None else None
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)

    (out_dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")

    if verbose:
        print(
            f"[drum_crnn] device={device.type} params={n_params:,} "
            f"train={len(train_ds)} val={len(val_ds)} ({val_split}) "
            f"clip_frames={train_ds.clip_frames} "
            f"pos_weight={pos_weight.tolist() if pos_weight is not None else None}"
        )

    history: list[dict] = []
    best_macro = -1.0
    best_path = out_dir / "checkpoint_best.pt"
    stopper = EarlyStopper(cfg.early_stop_patience)
    stopped_early = False
    final_epoch = 0

    for epoch in range(1, cfg.epochs + 1):
        final_epoch = epoch
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
        current_lr = float(optimizer.param_groups[0]["lr"])
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_macro_f1": macro,
                "val_per_class_f1": per_class_f1,
                "lr": current_lr,
            }
        )
        if verbose:
            f1s = " ".join(f"{k}={v:.3f}" for k, v in per_class_f1.items())
            print(
                f"[drum_crnn] epoch {epoch:>3}/{cfg.epochs} "
                f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
                f"macroF1={macro:.3f} lr={current_lr:.2e} | {f1s}"
            )

        ckpt = {
            "epoch": epoch,
            "model_state": model.state_dict(),
            "model_config": asdict(cfg.model),
            "feature_config": asdict(cfg.feature),
            "classes": list(CLASSES),
        }
        torch.save(ckpt, out_dir / f"checkpoint_epoch{epoch:02d}.pt")

        improved, should_halve, should_stop = stopper.update(macro)
        if improved:
            best_macro = macro
            torch.save(ckpt, best_path)
        if should_halve:
            for g in optimizer.param_groups:
                g["lr"] = g["lr"] * 0.5
            if verbose:
                print(
                    f"[drum_crnn] no improvement for {stopper.epochs_since_best} "
                    f"epochs -> halved LR to {optimizer.param_groups[0]['lr']:.2e}"
                )
        if should_stop:
            stopped_early = True
            if verbose:
                print(
                    f"[drum_crnn] early stopping at epoch {epoch} "
                    f"(no improvement for {stopper.epochs_since_best} epochs)"
                )
            break

    summary = {
        "params": n_params,
        "device": device.type,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "val_split": val_split,
        "clip_frames": train_ds.clip_frames,
        "pos_weight": pos_weight.tolist() if pos_weight is not None else None,
        "best_macro_f1": best_macro,
        "best_checkpoint": str(best_path),
        "epochs_run": final_epoch,
        "stopped_early": stopped_early,
        "history": history,
    }
    (out_dir / "history.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
