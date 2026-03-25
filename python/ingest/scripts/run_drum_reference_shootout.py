from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "python" / "ingest" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aural_ingest.drum_reference_shootout import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    run_reference_shootout,
    write_reference_shootout_outputs,
)
from aural_ingest.drum_benchmark_suite import DEFAULT_FIXTURES_DIR  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_drum_reference_shootout")
    parser.add_argument("--trusted-fixtures-dir", default=str(ROOT / DEFAULT_FIXTURES_DIR))
    parser.add_argument("--trusted-summary-json")
    parser.add_argument("--suspect-manifest", required=True)
    parser.add_argument("--out-root", default=str(ROOT / DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--algorithm", action="append")
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--label")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_reference_shootout(
        suspect_manifest_path=Path(args.suspect_manifest),
        trusted_fixtures_dir=Path(args.trusted_fixtures_dir),
        trusted_summary_json=Path(args.trusted_summary_json) if args.trusted_summary_json else None,
        algorithms=args.algorithm,
        tolerance_ms=float(args.tolerance_ms),
    )
    out_dir = write_reference_shootout_outputs(
        payload,
        output_root=Path(args.out_root),
        label=args.label,
    )
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
