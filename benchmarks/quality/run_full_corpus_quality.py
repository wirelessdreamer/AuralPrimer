r"""Run the unified transcription quality benchmark.

Examples:
    py -3 benchmarks/quality/run_full_corpus_quality.py --scan-root D:\AuralPrimer
    py -3 benchmarks/quality/run_full_corpus_quality.py --manifest benchmarks/quality/full_corpus_manifest.template.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.quality_benchmark import (  # noqa: E402
    build_quality_manifest_from_scan,
    filter_quality_cases,
    load_quality_manifest,
    run_quality_benchmark_suite,
    scan_corpus,
    write_quality_manifest_from_scan,
    write_quality_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_full_corpus_quality")
    parser.add_argument("--manifest")
    parser.add_argument("--scan-root")
    parser.add_argument("--write-manifest")
    parser.add_argument("--manifest-json", action="store_true")
    parser.add_argument("--referenced-only", action="store_true")
    parser.add_argument("--case-filter", action="append")
    parser.add_argument("--role", action="append")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--algorithm", action="append", default=None)
    parser.add_argument("--transcription-profile", default="gameplay_default")
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--label", default="full-corpus-quality")
    parser.add_argument("--out-root", default=str(ROOT / "benchmarks" / "quality" / "runs"))
    args = parser.parse_args()

    if args.scan_root:
        scan_root = Path(args.scan_root)
        if args.write_manifest:
            out = write_quality_manifest_from_scan(
                scan_root,
                Path(args.write_manifest),
                include_unreferenced=not bool(args.referenced_only),
            )
            print(out)
            return
        payload = scan_corpus(scan_root)
        if args.manifest_json:
            payload = build_quality_manifest_from_scan(
                payload,
                include_unreferenced=not bool(args.referenced_only),
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not args.manifest:
        raise SystemExit("--manifest is required unless --scan-root is provided")

    cases = load_quality_manifest(Path(args.manifest))
    cases = filter_quality_cases(
        cases,
        case_filters=args.case_filter,
        roles=args.role,
        max_cases=args.max_cases,
    )
    if not cases:
        raise SystemExit("no quality cases after filtering")
    payload = run_quality_benchmark_suite(
        cases,
        profile=args.transcription_profile,
        algorithms=args.algorithm,
        tolerance_ms=args.tolerance_ms,
    )
    out_dir = write_quality_outputs(payload, output_root=Path(args.out_root), label=args.label)
    print(out_dir)


if __name__ == "__main__":
    main()
