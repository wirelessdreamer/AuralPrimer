"""Adapter wrapper for running MSST/RoFormer against one mix file.

AuralPrimer's external RoFormer provider passes a single mix path and expects
role-named WAV files in the output directory. MSST's upstream inference CLI is
folder-oriented, so this wrapper stages the one mix into a temporary input
folder, invokes upstream ``inference.py``, then normalizes output names.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROLE_ALIASES = {
    "bass": "bass",
    "drum": "drums",
    "drums": "drums",
    "other": "other",
    "accompaniment": "other",
    "instrumental": "other",
    "no_vocals": "other",
    "vocal": "vocals",
    "vocals": "vocals",
}


def normalize_role(path: Path) -> str | None:
    stem = path.stem.strip().lower().replace(" ", "_").replace("-", "_")
    if stem in ROLE_ALIASES:
        return ROLE_ALIASES[stem]
    for key, role in ROLE_ALIASES.items():
        if stem.endswith(f"_{key}"):
            return role
    return None


def normalize_outputs(out_dir: Path) -> None:
    for candidate in sorted(out_dir.rglob("*.wav")):
        role = normalize_role(candidate)
        if role is None:
            continue
        target = out_dir / f"{role}.wav"
        if candidate.resolve() == target.resolve():
            continue
        shutil.copyfile(candidate, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="MSST checkout root.")
    parser.add_argument("--mix-wav", required=True, type=Path, help="Single mix WAV to separate.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory where role WAVs should be written.")
    parser.add_argument("--config-path", required=True, type=Path, help="MSST model config YAML.")
    parser.add_argument("--model-path", required=True, type=Path, help="MSST checkpoint path.")
    parser.add_argument("--model-type", default="bs_roformer", help="MSST model_type argument.")
    parser.add_argument("--shifts", type=int, default=1, help="AuralPrimer/MSST shift count.")
    parser.add_argument("--force-cpu", action="store_true", help="Pass --force_cpu to MSST inference.")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    mix_wav = args.mix_wav.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    config_path = args.config_path.expanduser().resolve()
    model_path = args.model_path.expanduser().resolve()

    for label, path in (
        ("repo", repo),
        ("mix wav", mix_wav),
        ("config", config_path),
        ("model", model_path),
    ):
        if not path.exists():
            print(f"run_roformer_msst: missing {label}: {path}", file=sys.stderr)
            return 2
    if not repo.is_dir():
        print(f"run_roformer_msst: repo is not a directory: {repo}", file=sys.stderr)
        return 2
    if not mix_wav.is_file() or not config_path.is_file() or not model_path.is_file():
        print("run_roformer_msst: mix, config, and model paths must be files", file=sys.stderr)
        return 2

    inference_py = repo / "inference.py"
    if not inference_py.is_file():
        print(f"run_roformer_msst: missing upstream inference.py: {inference_py}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aural_msst_input_") as temp_dir:
        input_dir = Path(temp_dir)
        shutil.copyfile(mix_wav, input_dir / "mix.wav")
        command = [
            sys.executable,
            str(inference_py),
            "--model_type",
            str(args.model_type),
            "--config_path",
            str(config_path),
            "--start_check_point",
            str(model_path),
            "--input_folder",
            str(input_dir),
            "--store_dir",
            str(out_dir),
            "--filename_template",
            "{instr}",
            "--bigshifts",
            str(max(1, int(args.shifts))),
            "--pcm_type",
            "FLOAT",
        ]
        if args.force_cpu:
            command.append("--force_cpu")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(command, cwd=str(repo), env=env)
        if proc.returncode != 0:
            return int(proc.returncode)

    normalize_outputs(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
