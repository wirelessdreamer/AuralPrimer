"""End-to-end SMOKE RUN for the drum-CRNN harness.

Proves the whole pipeline runs on a TINY sample: feature extraction -> target
building -> a few training steps -> a validation frame-F1 -> checkpoint -> ONNX
export -> onnxruntime forward pass. This is a PLUMBING PROOF, not a quality
result -- the metrics printed here mean nothing about model accuracy.

Run (from ``python/ingest``)::

    .venv/Scripts/python.exe -m aural_ingest.training.drum_crnn.smoke \
        --output-dir <scratch>/drum_crnn_smoke --files 4 --epochs 2

Nothing it writes belongs in the repo; point ``--output-dir`` at a scratch
directory.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch

from .config import FeatureConfig, ModelConfig, TrainConfig
from .dataset import EGMDDrumDataset
from .decode import decode_events
from .export_onnx import export_to_onnx, verify_onnx
from .model import DrumCRNN
from .train import train


def build_smoke_config(output_dir: str, n_files: int, epochs: int) -> TrainConfig:
    """A deliberately tiny config: few files, few epochs, small clips."""
    return TrainConfig(
        output_dir=output_dir,
        epochs=epochs,
        batch_size=2,
        learning_rate=1e-3,
        clip_seconds=2.0,
        train_limit=n_files,
        val_limit=max(2, n_files // 2),
        num_workers=0,
        seed=0,
        feature=FeatureConfig(),
        # Shrink the recurrent width so the smoke run is fast on CPU.
        model=ModelConfig(gru_hidden=32, conv_channels=(16, 16, 32)),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Drum-CRNN smoke run (plumbing proof only).")
    ap.add_argument("--output-dir", required=True, help="scratch dir for smoke artifacts (NOT in repo)")
    ap.add_argument("--corpus-root", default=None, help="override E-GMD corpus root")
    ap.add_argument("--files", type=int, default=4, help="number of train files (tiny)")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    args = ap.parse_args()

    cfg = build_smoke_config(args.output_dir, args.files, args.epochs)
    cfg = replace(cfg, device=args.device)
    if args.corpus_root:
        cfg = replace(cfg, corpus_root=args.corpus_root)

    print("=" * 72)
    print("DRUM-CRNN SMOKE RUN  (PLUMBING PROOF ONLY -- metrics are NOT quality)")
    print("=" * 72)

    # --- 1. Inspect one (features, targets) example to confirm shapes. -------
    probe_ds = EGMDDrumDataset(cfg, "train", limit=cfg.train_limit)
    feats0, tgts0 = probe_ds[0]
    n_onset_frames = int((tgts0.max(axis=1) > 0).sum())
    print(
        f"[smoke] example features shape={feats0.shape} dtype={feats0.dtype} | "
        f"targets shape={tgts0.shape} onset_frames={n_onset_frames} "
        f"active_classes={sorted(np.where(tgts0.max(axis=0) > 0)[0].tolist())}"
    )
    print(
        f"[smoke] feature cfg: sr={cfg.feature.sample_rate} n_fft={cfg.feature.n_fft} "
        f"hop={cfg.feature.hop_length} n_mels={cfg.feature.n_mels} "
        f"fps={cfg.feature.frames_per_second:.2f}"
    )

    # --- 2. Train (tiny). ----------------------------------------------------
    summary = train(cfg, verbose=True)
    print(f"[smoke] trained params={summary['params']:,} best_macro_f1={summary['best_macro_f1']:.3f}")
    print("[smoke] NOTE: F1 above is smoke-only (few files/epochs) -- NOT a quality signal.")

    # --- 3. Rebuild best model, time a CPU forward, decode events. -----------
    model = DrumCRNN(cfg.model)
    ckpt = torch.load(summary["best_checkpoint"], map_location="cpu")
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    x1 = torch.from_numpy(feats0[None, ...])  # (1, T, n_mels)
    with torch.no_grad():
        for _ in range(2):
            model(x1)  # warmup
        t0 = time.perf_counter()
        reps = 5
        for _ in range(reps):
            logits = model(x1)
        cpu_ms = (time.perf_counter() - t0) / reps * 1000.0
    probs = torch.sigmoid(logits)[0].numpy()
    events = decode_events(probs, cfg.feature, threshold=0.3)
    print(
        f"[smoke] CPU forward on 1x{feats0.shape[0]}x{feats0.shape[1]} clip: "
        f"{cpu_ms:.1f} ms | decoded {len(events)} onset events "
        f"(threshold=0.3, smoke-untrained)"
    )

    # --- 4. ONNX export + onnxruntime verification. --------------------------
    onnx_path = Path(cfg.output_dir) / "drum_crnn_smoke.onnx"
    export_to_onnx(model, onnx_path, n_mels=cfg.feature.n_mels)
    max_diff = verify_onnx(model, onnx_path, n_mels=cfg.feature.n_mels)
    print(
        f"[smoke] ONNX exported -> {onnx_path.name} "
        f"({onnx_path.stat().st_size} bytes); onnxruntime forward OK, "
        f"max|torch-onnx|={max_diff:.2e}"
    )

    print("=" * 72)
    print("SMOKE RUN COMPLETE: features -> targets -> train -> val F1 -> checkpoint")
    print("                    -> decode -> ONNX export -> onnxruntime forward. OK.")
    print("=" * 72)


if __name__ == "__main__":
    main()
