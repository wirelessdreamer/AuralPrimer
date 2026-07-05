"""
Stratified E-GMD test-split sampler.

The default `--limit N` on `aural_ingest gt-benchmark` takes the first N
rows of the E-GMD metadata CSV *in file order*. E-GMD lists every
performance against ~40 kits back-to-back, so `--limit 20` yields 20 kits
of the SAME groove / drummer / tempo (funk/groove1, drummer1, 138 bpm).
That is not representative of the corpus.

This module selects a representative sample spread across DISTINCT
(style-family, drummer, bpm-bucket) strata, round-robin so no single
groove dominates. Selection is fully deterministic -- rows are sorted by a
stable key and strata are visited in a fixed order -- so the emitted sample
JSON is reproducible and reviewable. No randomness is used.

Emit a manifest with:
    aural-ingest-stratify --corpus-root <root> --size 40 --output sample.json

The manifest's ``cases[].case_id`` values feed straight into
``gt-benchmark --case-id-file sample.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any


# BPM buckets: coarse tempo bands so "same tempo class" strata are stable
# across the corpus's 60..190 bpm range.
_BPM_EDGES: tuple[tuple[str, int, int], ...] = (
    ("bpm_lt90", 0, 90),
    ("bpm_90_119", 90, 120),
    ("bpm_120_149", 120, 150),
    ("bpm_ge150", 150, 10_000),
)


def _bpm_bucket(bpm: int) -> str:
    for label, lo, hi in _BPM_EDGES:
        if lo <= bpm < hi:
            return label
    return "bpm_unknown"


def _style_family(style: str) -> str:
    """E-GMD styles are ``family/groupN`` (e.g. ``funk/groove1``)."""
    return style.split("/", 1)[0]


def _case_id(row: dict[str, str]) -> str:
    """Reproduce the egmd adapter's case-id derivation exactly."""
    return f"egmd:{row['id']}::{Path(row['audio_filename']).stem}"


def select_sample(
    metadata_csv: Path,
    *,
    size: int = 40,
    split: str = "test",
    max_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``size`` stratified rows from the CSV, deterministically.

    Strategy
    --------
    1. Filter to the requested split (and drop clips longer than
       ``max_duration_sec`` if given -- a few E-GMD performances run 4+
       minutes and would dominate benchmark wall-clock without adding
       representational value).
    2. Group rows by ``(style_family, drummer, bpm_bucket)`` stratum.
    3. Within each stratum, sort rows by a stable key and keep the first
       (one representative kit per stratum on the first pass).
    4. Round-robin across strata -- ordered by descending stratum
       population, then stratum name -- taking one row per visit, until
       ``size`` rows are collected or all strata are exhausted. Extra
       passes pull the next-unused row from each stratum, so a large
       ``size`` still spreads across strata before doubling up.
    """
    with metadata_csv.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == split]
    if max_duration_sec is not None:
        def _short_enough(r: dict[str, str]) -> bool:
            try:
                return float(r.get("duration", "0") or 0.0) <= max_duration_sec
            except ValueError:
                return True
        rows = [r for r in rows if _short_enough(r)]

    # A stable, deterministic ordering of every kit in the split, so we can
    # rotate the chosen kit across strata (otherwise every stratum picks the
    # alphabetically-first kit and the whole sample lands on one kit).
    kit_order = sorted({r["kit_name"] for r in rows})
    kit_rank = {k: i for i, k in enumerate(kit_order)}
    n_kits = max(1, len(kit_order))

    # Group into strata.
    strata: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        try:
            bpm = int(r["bpm"])
        except (KeyError, ValueError):
            bpm = -1
        key = (_style_family(r["style"]), r["drummer"], _bpm_bucket(bpm))
        strata[key].append(r)

    # Stable within-stratum ordering: full style, then case id. Kit is chosen
    # per-visit via a rotating preference (below), not by this sort.
    for key in strata:
        strata[key].sort(key=lambda r: (r["style"], _case_id(r)))

    # Deterministic stratum visit order: most-populated first (so common
    # styles are represented), ties broken by the stratum tuple.
    ordered_keys = sorted(
        strata.keys(),
        key=lambda k: (-len(strata[k]), k),
    )

    def _pick(rows_in_stratum: list[dict[str, str]], used: set[str], visit: int) -> dict[str, str] | None:
        """Pick an unused row, preferring one whose kit matches the rotating
        target rank (visit index modulo the kit count). Falls back to the
        first unused row so we never skip a stratum just because its kits are
        taken. Deterministic given ``visit``."""
        target = visit % n_kits
        best: dict[str, str] | None = None
        best_dist: int | None = None
        for r in rows_in_stratum:
            if _case_id(r) in used:
                continue
            dist = (kit_rank.get(r["kit_name"], 0) - target) % n_kits
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = r
        return best

    selected: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    visit = 0
    made_progress = True
    while len(selected) < size and made_progress:
        made_progress = False
        for key in ordered_keys:
            if len(selected) >= size:
                break
            row = _pick(strata[key], set(selected.keys()), visit)
            if row is None:
                continue
            visit += 1
            made_progress = True
            cid = _case_id(row)
            fam, drummer, bpmb = key
            selected[cid] = {
                "case_id": cid,
                "style": row["style"],
                "style_family": fam,
                "drummer": drummer,
                "bpm": row["bpm"],
                "bpm_bucket": bpmb,
                "kit_name": row["kit_name"],
                "time_signature": row["time_signature"],
                "duration": row.get("duration"),
                "audio_filename": row["audio_filename"],
                "midi_filename": row["midi_filename"],
            }

    return list(selected.values())


def build_manifest(
    metadata_csv: Path,
    *,
    size: int,
    split: str,
    max_duration_sec: float | None = None,
) -> dict[str, Any]:
    cases = select_sample(
        metadata_csv, size=size, split=split, max_duration_sec=max_duration_sec
    )
    fam_counts: dict[str, int] = defaultdict(int)
    drummer_counts: dict[str, int] = defaultdict(int)
    bpm_counts: dict[str, int] = defaultdict(int)
    combos: set[tuple[str, str, str]] = set()
    for c in cases:
        fam_counts[c["style_family"]] += 1
        drummer_counts[c["drummer"]] += 1
        bpm_counts[c["bpm_bucket"]] += 1
        combos.add((c["style_family"], c["drummer"], c["bpm_bucket"]))
    return {
        "dataset": "egmd",
        "split": split,
        "requested_size": size,
        "max_duration_sec": max_duration_sec,
        "selected": len(cases),
        "distinct_style_drummer_bpm_strata": len(combos),
        "distribution": {
            "style_family": dict(sorted(fam_counts.items())),
            "drummer": dict(sorted(drummer_counts.items())),
            "bpm_bucket": dict(sorted(bpm_counts.items())),
        },
        "selection": (
            "deterministic round-robin over (style_family, drummer, bpm_bucket) "
            "strata; no randomness"
        ),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--corpus-root",
        required=True,
        help="E-GMD root containing e_gmd_metadata/e-gmd-v1.0.0.csv",
    )
    ap.add_argument("--size", type=int, default=40, help="Target sample size.")
    ap.add_argument("--split", default="test", help="Dataset split to sample.")
    ap.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Drop clips longer than this many seconds (bounds benchmark runtime).",
    )
    ap.add_argument("--output", required=True, help="Manifest JSON path.")
    args = ap.parse_args(argv)

    metadata_csv = Path(args.corpus_root) / "e_gmd_metadata" / "e-gmd-v1.0.0.csv"
    if not metadata_csv.is_file():
        # Allow passing the CSV path directly too.
        alt = Path(args.corpus_root)
        if alt.is_file():
            metadata_csv = alt
        else:
            raise SystemExit(f"metadata CSV not found: {metadata_csv}")

    manifest = build_manifest(
        metadata_csv,
        size=args.size,
        split=args.split,
        max_duration_sec=args.max_duration,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "selected": manifest["selected"],
                "strata": manifest["distinct_style_drummer_bpm_strata"],
                "output": str(out),
                "distribution": manifest["distribution"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
