"""Install the optional MT3 drum-benchmark modelpacks (MR-MT3 + YourMT3).

These are NOT bundled or hosted by AuralPrimer (preferredModelPacks.ts leaves
their urls blank), and they are OPTIONAL -- the default drum pipeline uses the
heuristic engines, not MT3. They power the `mr_mt3_drums` / `yourmt3_drums`
benchmark engines only.

Downloads the upstream research checkpoints from Hugging Face and lays them out
as installed modelpacks the sidecar resolver discovers:

    <models_root>/<id>/<version>/
        modelpack.json
        files/checkpoints/<model_id>/.../<checkpoint>

Run (needs a working network -- this machine could not reach huggingface.co
when the script was written):

    python/ingest/.venv/Scripts/python.exe \
        python/ingest/scripts/install_mt3_modelpacks.py \
        --models-root AuralPrimerPortable/data/assets/models

LICENSES: MR-MT3 (github.com/gudgud96/MR-MT3) and YourMT3
(huggingface.co/mimbres/YourMT3) are research releases -- verify their licenses
allow your use before relying on them. Downloading here is for local drum
benchmark evaluation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (repo_id, version, model_id, checkpoint repo-relative path or None to auto-find,
#  destination checkpoint relpath under files/checkpoints/<model_id>/)
PACKS = [
    {
        "id": "mr_mt3",
        "version": "0.0.1",
        "repo": "gudgud1014/MR-MT3",
        "model_id": "mr_mt3",
        "src_file": None,  # auto-find the .pth in the repo
        "src_match": (".pth",),
        "dest_rel": Path("files") / "checkpoints" / "mr_mt3" / "mt3.pth",
        "description": "MR-MT3 drum transcription baseline",
    },
    {
        "id": "yourmt3",
        "version": "0.0.1",
        "repo": "mimbres/YourMT3",
        "model_id": "yourmt3",
        "src_file": "logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt",
        "src_match": (".ckpt",),
        "dest_rel": Path("files")
        / "checkpoints"
        / "yourmt3"
        / "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
        / "last.ckpt",
        "description": "YourMT3 drum transcription research candidate",
    },
]


def _resolve_src_file(api, repo: str, declared: str | None, match: tuple[str, ...]) -> str:
    if declared:
        return declared
    files = api.list_repo_files(repo)
    hits = [f for f in files if f.lower().endswith(match)]
    if not hits:
        raise SystemExit(f"no {match} checkpoint found in {repo}; files: {files[:20]}")
    # Prefer a top-level / shortest path (the main checkpoint).
    hits.sort(key=lambda f: (f.count("/"), len(f)))
    print(f"  auto-selected checkpoint in {repo}: {hits[0]}")
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--models-root",
        default="AuralPrimerPortable/data/assets/models",
        help="Search root the sidecar scans (default: the portable's data/assets/models).",
    )
    ap.add_argument("--only", choices=[p["id"] for p in PACKS], help="Install just one pack.")
    args = ap.parse_args()

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    root = Path(args.models_root)
    for pack in PACKS:
        if args.only and pack["id"] != args.only:
            continue
        print(f"== {pack['id']} ({pack['repo']}) ==")
        ver_dir = root / pack["id"] / pack["version"]
        dest = ver_dir / pack["dest_rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.is_file():
            print(f"  already present: {dest}")
        else:
            src = _resolve_src_file(api, pack["repo"], pack["src_file"], pack["src_match"])
            print(f"  downloading {pack['repo']}::{src} (resumable)...")
            local = hf_hub_download(repo_id=pack["repo"], filename=src)
            dest.write_bytes(Path(local).read_bytes())
            print(f"  -> {dest}  ({dest.stat().st_size / 1e6:.1f} MB)")

        manifest = {
            "id": pack["id"],
            "version": pack["version"],
            "description": pack["description"],
            "checkpoints": [
                {"model": pack["model_id"], "path": str(pack["dest_rel"]).replace("\\", "/")}
            ],
        }
        (ver_dir / "modelpack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  wrote {ver_dir / 'modelpack.json'}")

    print("done. Re-run the Studio model check / `aural_ingest runtime-check` to confirm.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
