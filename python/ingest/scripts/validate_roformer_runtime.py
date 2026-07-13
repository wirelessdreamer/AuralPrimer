"""Validate the opt-in RoFormer/MSST stem-separation provider contract.

This script runs in the normal ingest environment. The MSST/RoFormer runtime
remains outside the sidecar and is invoked through the configured external
Python/repo/command provider.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

INGEST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = INGEST_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_VALIDATOR_NAME = Path(__file__).stem
MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"
MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"
RUNTIME_EVIDENCE_DIR = Path("benchmarks") / "runtime" / "runs"
PROMOTION_REQUIRED_ROLES = ("bass", "drums", "other", "vocals")


def _model_upgrade_evidence_root() -> Path:
    raw = os.environ.get(MODEL_UPGRADE_EVIDENCE_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).is_file():
        return cwd
    return REPO_ROOT


def _gate_evidence_output_path(stem: str) -> Path:
    root = _model_upgrade_evidence_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return root / RUNTIME_EVIDENCE_DIR / f"{stamp}_{stem}_runtime.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mix_wav", type=Path, help="Mix WAV to pass through the RoFormer provider.")
    parser.add_argument("--stems-dir", type=Path, default=None, help="Optional directory for copied stem outputs.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument(
        "--write-gate-evidence",
        action="store_true",
        help="Write the JSON report to the runtime-check model-upgrade gate evidence directory.",
    )
    parser.add_argument("--shifts", type=int, default=1, help="Provider shift count.")
    parser.add_argument(
        "--require-role",
        action="append",
        default=[],
        help="Role that must be present in copied outputs; can be passed more than once.",
    )
    args = parser.parse_args(argv)
    output = args.output
    if args.write_gate_evidence:
        if output is not None:
            parser.error("--write-gate-evidence cannot be combined with --output")
        missing_roles = sorted(set(PROMOTION_REQUIRED_ROLES).difference(str(role) for role in args.require_role))
        if missing_roles:
            required = " ".join(f"--require-role {role}" for role in PROMOTION_REQUIRED_ROLES)
            parser.error(f"--write-gate-evidence requires {required}")
        output = _gate_evidence_output_path("roformer")

    mix_wav = args.mix_wav.expanduser()
    if not mix_wav.is_file():
        reason = f"input WAV not found: {mix_wav}"
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "provider": "roformer",
                        "status": "input_missing",
                        "reason": reason,
                        "mix_wav": str(mix_wav),
                        "require_roles": args.require_role,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            print(output)
        else:
            print(f"{_VALIDATOR_NAME}: {reason}", file=sys.stderr)
        return 2

    from aural_ingest import cli

    report = cli.validate_roformer_runtime(
        mix_wav,
        stems_dir=args.stems_dir.expanduser() if args.stems_dir is not None else None,
        shifts=max(1, int(args.shifts)),
        require_roles=args.require_role,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        print(output)
    else:
        print(payload)

    if report.get("ok") is True:
        return 0
    return 1 if report.get("status") == "fresh" else 2


if __name__ == "__main__":
    raise SystemExit(main())
