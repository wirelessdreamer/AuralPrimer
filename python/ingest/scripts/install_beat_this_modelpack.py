"""Install the Beat This! beat/downbeat/meter modelpack.

Lays out the MIT-licensed Beat This! (CPJKU, JKU Linz) checkpoint as an installed
modelpack that the sidecar `meter_tracker` resolver discovers:

    <models_root>/beat_this/<version>/
        modelpack.json
        files/checkpoints/beat_this/final0.ckpt

The checkpoint auto-downloads via `beat_this` from cloud.cp.jku.at into
``~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt`` on first inference; this
script copies that local cache (or a ``--src`` path) into the modelpack layout so
the shipped sidecar can run offline. Both code and weights are MIT-licensed.

    python/ingest/.venv/Scripts/python.exe \
        python/ingest/scripts/install_beat_this_modelpack.py \
        --models-root AuralPrimerPortable/data/assets/models
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

MODELPACK_ID = "beat_this"
VERSION = "1.0.0"
MODEL_ID = "beat_this"
DEST_CKPT = "final0.ckpt"
DEFAULT_SRC = Path(os.path.expanduser("~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt"))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the Beat This! meter modelpack.")
    ap.add_argument("--models-root", required=True, help="e.g. AuralPrimerPortable/data/assets/models")
    ap.add_argument(
        "--src",
        default=str(DEFAULT_SRC),
        help="beat_this .ckpt to install (default: the torch-hub cache populated on first run)",
    )
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.is_file():
        print(
            f"ERROR: checkpoint not found at {src}. Run Beat This! once to populate the "
            f"torch-hub cache, or pass --src <path to final0.ckpt>.",
            file=sys.stderr,
        )
        return 1

    version_dir = Path(args.models_root) / MODELPACK_ID / VERSION
    ckpt_dir = version_dir / "files" / "checkpoints" / MODEL_ID
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    dest = ckpt_dir / DEST_CKPT
    shutil.copyfile(src, dest)

    manifest = {
        "modelpack_id": MODELPACK_ID,
        "version": VERSION,
        "source": "https://github.com/CPJKU/beat_this",
        "license": "MIT",
        "checkpoints": [
            {
                "model": MODEL_ID,
                "path": f"files/checkpoints/{MODEL_ID}/{DEST_CKPT}",
                "sha256": _sha256(dest),
            }
        ],
    }
    (version_dir / "modelpack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "installed": str(dest), "sha256": manifest["checkpoints"][0]["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
