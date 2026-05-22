"""Manifest-driven regression suite for piano transcription.

Usage:
    py -3 benchmarks/piano/run_piano_regression.py
    py -3 benchmarks/piano/run_piano_regression.py --manifest benchmarks/piano/piano_suite_manifest.json
    py -3 benchmarks/piano/run_piano_regression.py --algorithm piano_auto --algorithm piano_pti_clean
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.piano_benchmark import PIANO_ALGORITHMS
from aural_ingest.piano_benchmark_suite import (
    run_piano_benchmark_suite,
    write_piano_suite_outputs,
)

DEFAULT_MANIFEST = ROOT / "benchmarks" / "piano" / "piano_suite_manifest.template.json"


def _load_song_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    songs = payload.get("songs")
    if not isinstance(songs, list) or not songs:
        raise ValueError(f"manifest '{path}' must contain a non-empty 'songs' array")
    return [dict(song) for song in songs]


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_piano_regression")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--algorithm", dest="algorithms", action="append", default=None)
    parser.add_argument("--label", default="piano-regression")
    parser.add_argument("--instrument", default=None, help="Filter to one instrument (usually keys)")
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--offset-tolerance-ms", type=float, default=120.0)
    parser.add_argument("--velocity-tolerance", type=int, default=20)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"manifest not found: {manifest_path}\n"
            f"Start from {DEFAULT_MANIFEST} and replace the placeholder song paths with your piano cases."
        )

    songs = _load_song_manifest(manifest_path)
    if args.instrument:
        songs = [song for song in songs if song.get("instrument") == args.instrument]
    if not songs:
        raise ValueError("no songs left after applying the requested filters")

    algorithms = args.algorithms or list(PIANO_ALGORITHMS)
    payload = run_piano_benchmark_suite(
        songs,
        algorithms=algorithms,
        tolerance_ms=args.tolerance_ms,
        offset_tolerance_ms=args.offset_tolerance_ms,
        velocity_tolerance=args.velocity_tolerance,
    )
    out_dir = write_piano_suite_outputs(
        payload,
        output_root=ROOT / "benchmarks" / "piano" / "runs",
        label=args.label,
    )
    print(f"\nOutputs saved to: {out_dir}")


if __name__ == "__main__":
    main()
