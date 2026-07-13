"""Evaluate ingest key detection against GuitarSet key_mode annotations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.dataset_adapters.guitarset import yield_cases  # noqa: E402
from aural_ingest.key_benchmark import evaluate_key_cases  # noqa: E402


def _load_case_ids(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        obj = json.loads(text)
        rows: Any
        if isinstance(obj, dict):
            rows = obj.get("cases", [])
        else:
            rows = obj
        out: set[str] = set()
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("case_id"):
                    out.add(str(row["case_id"]))
                elif isinstance(row, str) and row.strip():
                    out.add(row.strip())
        return out
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="evaluate_guitarset_key")
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--variant", default="mic")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case-id-file", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    case_ids = _load_case_ids(Path(args.case_id_file)) if args.case_id_file else None
    adapter_limit = None if case_ids is not None else args.limit
    cases = []
    for case in yield_cases(Path(args.corpus_root), variant=args.variant, limit=adapter_limit):
        if case_ids is not None and case.case_id not in case_ids:
            continue
        cases.append(case)
    if case_ids is not None and args.limit is not None:
        cases = cases[: max(0, int(args.limit))]
    if not cases:
        raise SystemExit("no GuitarSet key cases matched corpus/filter settings")

    report = evaluate_key_cases(cases)
    report.update({
        "dataset": "guitarset",
        "task": "key_detection",
        "variant": args.variant,
        "case_count": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })

    if args.output:
        output = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = ROOT / "benchmarks" / "guitar" / "gt_runs" / f"guitarset_key_eval_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "summary": report["summary"]}))


if __name__ == "__main__":
    main()
