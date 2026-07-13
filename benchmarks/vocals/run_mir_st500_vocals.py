"""Run a MIR-ST500 vocal transcription ground-truth benchmark.

Example:
    $env:AURAL_MIR_ST500_ROOT = "E:\\AudioSourceOfTruthData\\extracted\\mir_st500"
    $env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\\AuralPrimer"
    D:\\AuralPrimer\\python\\ingest\\.venv\\Scripts\\python.exe `
        benchmarks\\vocals\\run_mir_st500_vocals.py --algorithm melodic_rmvpe --write-gate-evidence
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.dataset_adapters.mir_st500 import diagnose_corpus, yield_cases  # noqa: E402
from aural_ingest.ground_truth_benchmark import run_sweep, write_report  # noqa: E402

MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"
MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"
VOCALS_EVIDENCE_DIR = Path("benchmarks") / "vocals" / "gt_runs"
VOCALS_EXPLORATORY_DIR = Path("benchmarks") / "vocals" / "exploratory_gt_runs"


def _default_root() -> Path | None:
    raw = os.environ.get("AURAL_MIR_ST500_ROOT", "").strip()
    return Path(raw).expanduser() if raw else None


def _load_case_ids(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        obj = json.loads(text)
        rows: Any = obj.get("cases", []) if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            return set()
        return {
            str(row["case_id"]) if isinstance(row, dict) and "case_id" in row else str(row)
            for row in rows
        }
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _model_upgrade_evidence_root() -> Path:
    raw = os.environ.get(MODEL_UPGRADE_EVIDENCE_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).is_file():
        return cwd
    return ROOT


def _gate_evidence_output_path() -> Path:
    root = _model_upgrade_evidence_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return root / VOCALS_EVIDENCE_DIR / f"{stamp}_mir_st500_vocals.json"


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_mir_st500_vocals")
    parser.add_argument("--corpus-root", default=None)
    parser.add_argument("--split", choices=["train", "test", "all"], default="test")
    parser.add_argument("--variant", choices=["vocal", "mixture"], default="vocal")
    parser.add_argument("--algorithm", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case-id-file", default=None)
    parser.add_argument("--tolerance-ms", type=float, default=50.0)
    parser.add_argument("--pitch-tolerance-semitones", type=int, default=0)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--write-gate-evidence",
        action="store_true",
        help="Write the report to the runtime-check model-upgrade gate evidence directory.",
    )
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    if args.write_gate_evidence and args.output:
        parser.error("--write-gate-evidence cannot be combined with --output")

    corpus_root = Path(args.corpus_root).expanduser() if args.corpus_root else _default_root()
    if corpus_root is None:
        raise SystemExit("set --corpus-root or AURAL_MIR_ST500_ROOT")

    case_ids = _load_case_ids(Path(args.case_id_file)) if args.case_id_file else None
    cases = list(
        yield_cases(
            corpus_root,
            split=None if args.split == "all" else args.split,
            variant=args.variant,
            case_ids=case_ids,
            limit=args.limit,
        )
    )
    if not cases:
        diagnostics = diagnose_corpus(
            corpus_root,
            split=None if args.split == "all" else args.split,
            variant=args.variant,
            case_ids=case_ids,
            limit=args.limit,
        )
        reason = diagnostics.get("reason") or "no cases emitted"
        raise SystemExit(
            "no MIR-ST500 cases found under "
            f"{corpus_root}: {reason}\n"
            f"diagnostics={json.dumps(diagnostics, sort_keys=True)}"
        )

    algorithms = args.algorithm or ["melodic_rmvpe"]

    def on_case(idx: int, score) -> None:
        if args.progress:
            print(
                f"[{idx:>4}/{len(cases)}] {score.algorithm_id} "
                f"{score.case_id} f1={score.f1:.3f} tp={score.tp} fp={score.fp} fn={score.fn}",
                file=sys.stderr,
                flush=True,
            )

    scores = run_sweep(
        cases,
        algorithms=algorithms,
        family="melodic",
        tolerance_sec=args.tolerance_ms / 1000.0,
        pitch_tolerance_semitones=args.pitch_tolerance_semitones,
        on_case=on_case,
    )

    if args.write_gate_evidence:
        output = _gate_evidence_output_path()
    elif args.output:
        output = Path(args.output)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        output = ROOT / VOCALS_EXPLORATORY_DIR / f"{timestamp}_mir_st500_vocals.json"
    report = write_report(
        scores,
        out_path=output,
        dataset="mir_st500",
        family="melodic",
        extra={
            "split": args.split,
            "variant": args.variant,
            "limit": args.limit,
            "tolerance_ms": args.tolerance_ms,
            "pitch_tolerance_semitones": args.pitch_tolerance_semitones,
            "algorithms": algorithms,
        },
    )
    promotion_usable = (
        report.get("ok") is True
        and args.write_gate_evidence
        and args.split == "test"
        and args.variant == "vocal"
        and args.limit is None
        and "melodic_rmvpe" in {str(item) for item in algorithms}
    )
    print(
        json.dumps(
            {
                "ok": report.get("ok") is True,
                "promotion_usable": promotion_usable,
                "case_count": report["case_count"],
                "output": str(output),
            }
        )
    )
    if report.get("ok") is not True:
        raise SystemExit(f"MIR-ST500 benchmark had case errors; wrote {output}")
    if args.write_gate_evidence and not promotion_usable:
        raise SystemExit(
            "MIR-ST500 gate evidence requires an unbounded test/vocal melodic_rmvpe run; "
            f"wrote {output}"
        )


if __name__ == "__main__":
    main()
