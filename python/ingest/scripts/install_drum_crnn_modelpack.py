"""Package the trained in-house drum-CRNN as an installed modelpack.

Loads a trained ``DrumCRNN`` checkpoint (``checkpoint_best.pt`` from a training
run), reconstructs the model from the checkpoint's saved ``model_config``,
exports it to ONNX, verifies the ONNX matches the torch model, and lays the
result out as a modelpack the sidecar resolver discovers:

    <models_root>/drum_crnn/<version>/
        modelpack.json
        files/drum_crnn.onnx

This mirrors ``install_mt3_modelpacks.py`` (same ``<id>/<version>/modelpack.json``
+ ``files/`` layout that ``resolve_mt3_modelpack`` /
``_iter_installed_modelpack_dirs`` / ``_candidate_model_roots`` scan for), so the
``drum_crnn`` adapter can resolve the ONNX exactly the way the MT3 engines
resolve their checkpoints.

ONNX is the packaging route because ``onnxruntime`` is already a shipped sidecar
dependency (basic_pitch uses it); the full torch stack is NOT in the frozen
binary. The weights (``.pt`` / ``.onnx``) are NEVER committed -- only this script
is. E-GMD is CC BY 4.0; a model trained on it can be shipped with attribution.

Run (from the repo root, with the ingest venv):

    python/ingest/.venv/Scripts/python.exe \
        python/ingest/scripts/install_drum_crnn_modelpack.py \
        --checkpoint D:/drum_crnn_run1/checkpoint_best.pt \
        --models-root assets/models
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable when run as a plain script (no install needed):
# scripts/ -> ingest/ ; src/ holds the aural_ingest package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

MODELPACK_ID = "drum_crnn"
DEFAULT_VERSION = "0.1.0"
ONNX_RELPATH = Path("files") / "drum_crnn.onnx"
DESCRIPTION = "In-house drum CRNN (5-class: kick/snare/hi_hat/toms/cymbals), ONNX"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--checkpoint",
        default=r"D:\drum_crnn_run1\checkpoint_best.pt",
        help="Trained DrumCRNN checkpoint (default: D:/drum_crnn_run1/checkpoint_best.pt).",
    )
    ap.add_argument(
        "--models-root",
        default="assets/models",
        help="Search root the sidecar scans (default: assets/models under the repo root).",
    )
    ap.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Modelpack version dir (default: {DEFAULT_VERSION}).",
    )
    args = ap.parse_args()

    import torch

    from aural_ingest.training.drum_crnn.config import ModelConfig
    from aural_ingest.training.drum_crnn.export_onnx import export_to_onnx, verify_onnx
    from aural_ingest.training.drum_crnn.model import DrumCRNN

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.is_file():
        raise SystemExit(f"checkpoint not found: {ckpt_path}")

    print(f"== drum_crnn modelpack ({ckpt_path}) ==")
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model_cfg_dict = dict(ckpt["model_config"])
    # conv_channels round-trips through JSON as a list; ModelConfig wants a tuple.
    model_cfg_dict["conv_channels"] = tuple(model_cfg_dict["conv_channels"])
    model_cfg = ModelConfig(**model_cfg_dict)

    model = DrumCRNN(model_cfg)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    print(
        f"  reconstructed DrumCRNN: n_mels={model_cfg.n_mels} "
        f"conv={model_cfg.conv_channels} gru_hidden={model_cfg.gru_hidden} "
        f"classes={model_cfg.num_classes} (epoch {ckpt.get('epoch')})"
    )

    ver_dir = Path(args.models_root) / MODELPACK_ID / args.version
    onnx_path = ver_dir / ONNX_RELPATH
    export_to_onnx(model, onnx_path, n_mels=model_cfg.n_mels)
    print(f"  exported ONNX -> {onnx_path} ({onnx_path.stat().st_size / 1e6:.2f} MB)")

    max_diff = verify_onnx(model, onnx_path, n_mels=model_cfg.n_mels)
    print(f"  verified ONNX vs torch: max abs diff = {max_diff:.2e}")

    manifest = {
        "id": MODELPACK_ID,
        "version": args.version,
        "description": DESCRIPTION,
        "backend": "crnn",
        "classes": list(ckpt.get("classes", [])),
        "feature_config": dict(ckpt.get("feature_config", {})),
        "model_config": dict(ckpt["model_config"]),
        "checkpoints": [
            {"model": MODELPACK_ID, "path": str(ONNX_RELPATH).replace("\\", "/")}
        ],
    }
    (ver_dir / "modelpack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"  wrote {ver_dir / 'modelpack.json'}")

    print("done. The drum_crnn adapter will now resolve this modelpack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
