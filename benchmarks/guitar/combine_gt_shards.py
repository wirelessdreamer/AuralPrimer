"""Combine non-overlapping gt-benchmark JSON shards into one report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.ground_truth_benchmark import combine_report_files  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="combine_gt_shards")
    parser.add_argument("reports", nargs="+", help="Input gt-benchmark JSON shard reports.")
    parser.add_argument("--output", required=True, help="Path to write the combined JSON report.")
    parser.add_argument("--label", default=None, help="Optional label stored in extra.shard_label.")
    args = parser.parse_args()

    extra = {"shard_label": args.label} if args.label else None
    combined = combine_report_files(args.reports, out_path=args.output, extra=extra)
    per_algorithm = {
        algorithm: {
            "cases": metrics["cases"],
            "f1": metrics["f1"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "onset_mae_sec": metrics["onset_mae_sec"],
        }
        for algorithm, metrics in combined["summary"]["per_algorithm"].items()
    }
    print(
        json.dumps(
            {
                "ok": True,
                "dataset": combined["dataset"],
                "case_count": combined["case_count"],
                "per_algorithm": per_algorithm,
                "report_path": str(Path(args.output)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
