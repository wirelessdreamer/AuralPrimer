from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "python" / "ingest" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aural_ingest.drum_benchmark_suite import (  # noqa: E402
    DEFAULT_FIXTURES_DIR,
    DEFAULT_OUTPUT_ROOT,
    run_benchmark_suite,
    write_suite_outputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_drum_benchmark_suite")
    parser.add_argument("--fixtures-dir", default=str(ROOT / DEFAULT_FIXTURES_DIR))
    parser.add_argument("--out-root", default=str(ROOT / DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--algorithm", action="append")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--label")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_benchmark_suite(
        fixtures_dir=Path(args.fixtures_dir),
        algorithms=args.algorithm,
        tolerance_ms=float(args.tolerance_ms),
        selected_case_ids=args.cases,
    )
    out_dir = write_suite_outputs(payload, output_root=Path(args.out_root), label=args.label)
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
