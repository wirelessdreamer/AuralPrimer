"""Launch a real drum-CRNN training run on E-GMD.

Thin CLI wrapper around ``aural_ingest.training.drum_crnn.train.train()``.
Spawn-safe (``num_workers > 0`` on Windows) via the ``__main__`` guard. Writes
checkpoints + ``history.json``/``config.json`` under ``--out`` (which MUST
live outside the repo tree -- weights are never committed).

Examples::

    # Fresh full-corpus run.
    python train_drum_crnn.py --out D:/drum_crnn_run3 \
        --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
        --epochs 30 --batch 16 --workers 16

    # Fine-tune an existing checkpoint with auto class-weighted loss + early
    # stopping (the run-3 recipe in docs/drum-crnn-run3-plan-2026-07-06.md).
    python train_drum_crnn.py --out D:/drum_crnn_run3 \
        --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
        --init-checkpoint D:/drum_crnn_run2_full/checkpoint_best.pt \
        --epochs 15 --early-stop-patience 3 --pos-weight auto \
        --batch 16 --workers 16 --val-limit 800
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

# Make the package importable when run as a plain script (no install needed):
# scripts/ -> ingest/ ; src/ holds the aural_ingest package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _parse_pos_weight(raw: str) -> tuple[float, ...] | str | None:
    lowered = raw.strip().lower()
    if lowered == "auto":
        return "auto"
    if lowered in ("none", ""):
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(float(p) for p in parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="Checkpoint/history output dir (keep OUTSIDE the repo).")
    ap.add_argument("--corpus-root", default="", help="E-GMD root (default: TrainConfig's built-in path).")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--learning-rate", type=float, default=1e-3)
    ap.add_argument("--clip-seconds", type=float, default=8.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--train-limit", type=int, default=0, help="0 = full split.")
    ap.add_argument("--val-limit", type=int, default=800)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="auto")
    ap.add_argument(
        "--pos-weight",
        default="auto",
        help="'auto' (estimate from training rows), 'none' (plain BCE), or "
        "comma-separated per-class weights in kick,snare,hi_hat,toms,cymbals order.",
    )
    ap.add_argument("--pos-weight-cap", type=float, default=25.0)
    ap.add_argument("--pos-weight-sample-files", type=int, default=500)
    ap.add_argument(
        "--init-checkpoint",
        default="",
        help="Fine-tune from this checkpoint's weights instead of random init.",
    )
    ap.add_argument(
        "--early-stop-patience",
        type=int,
        default=3,
        help="Stop after this many epochs with no val-macro-F1 improvement (0 = never).",
    )
    args = ap.parse_args()

    from aural_ingest.training.drum_crnn.config import TrainConfig
    from aural_ingest.training.drum_crnn.train import train

    cfg = TrainConfig()
    extra: dict[str, object] = {}
    if args.corpus_root:
        extra["corpus_root"] = args.corpus_root
    cfg = replace(
        cfg,
        output_dir=args.out,
        epochs=args.epochs,
        batch_size=args.batch,
        learning_rate=args.learning_rate,
        clip_seconds=args.clip_seconds,
        num_workers=args.workers,
        train_limit=(None if args.train_limit == 0 else args.train_limit),
        val_limit=(None if args.val_limit == 0 else args.val_limit),
        seed=args.seed,
        device=args.device,
        pos_weight=_parse_pos_weight(args.pos_weight),
        pos_weight_cap=args.pos_weight_cap,
        pos_weight_sample_files=args.pos_weight_sample_files,
        init_checkpoint=args.init_checkpoint,
        early_stop_patience=args.early_stop_patience,
        **extra,
    )

    t0 = time.time()
    print(
        f"[launch] epochs={cfg.epochs} batch={cfg.batch_size} workers={cfg.num_workers} "
        f"train_limit={cfg.train_limit} val_limit={cfg.val_limit} device={cfg.device} "
        f"pos_weight={cfg.pos_weight} init_checkpoint={cfg.init_checkpoint or '(none)'} "
        f"early_stop_patience={cfg.early_stop_patience}",
        flush=True,
    )
    summary = train(cfg, verbose=True)
    print(
        f"[launch] done in {time.time() - t0:.1f}s "
        f"epochs_run={summary['epochs_run']} stopped_early={summary['stopped_early']} "
        f"best_macro_frame_f1={summary['best_macro_f1']:.4f}",
        flush=True,
    )
    print(f"[launch] best checkpoint: {summary['best_checkpoint']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
