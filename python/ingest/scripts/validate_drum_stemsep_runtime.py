"""Validate the opt-in DrumSep Route A runtime contract.

This script runs in the normal ingest environment. The heavy separator runtime
still lives outside the sidecar and is invoked by ``drum_stemsep`` through the
configured external Python/runner/checkpoint.
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
    parser.add_argument("wav_path", type=Path, help="Drum stem WAV to pass through the DrumSep runner.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument(
        "--write-gate-evidence",
        action="store_true",
        help="Write the JSON report to the runtime-check model-upgrade gate evidence directory.",
    )
    parser.add_argument(
        "--require-events",
        action="store_true",
        help="Fail if the runner contract succeeds but returns zero drum events.",
    )
    args = parser.parse_args(argv)
    output = args.output
    if args.write_gate_evidence:
        if output is not None:
            parser.error("--write-gate-evidence cannot be combined with --output")
        if not args.require_events:
            parser.error("--write-gate-evidence requires --require-events")
        output = _gate_evidence_output_path("drum_stemsep")

    wav_path = args.wav_path.expanduser()
    if not wav_path.is_file():
        reason = f"input WAV not found: {wav_path}"
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "engine": "drum_stemsep",
                        "status": "input_missing",
                        "reason": reason,
                        "wav_path": str(wav_path),
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

    from aural_ingest.algorithms import drum_stemsep

    report = drum_stemsep.validate_runtime(wav_path, require_events=bool(args.require_events))
    payload = json.dumps(report, indent=2, sort_keys=True)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        print(output)
    else:
        print(payload)

    if report.get("ok") is True:
        return 0
    return 1 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
