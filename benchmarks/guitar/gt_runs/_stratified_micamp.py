"""Stratified micamp bench: cap cases per category so all five categories
are represented within the time budget.

Uses the same ``run_sweep`` / ``write_report`` plumbing as the CLI, so the
output JSON has the same shape and can flow through ``build_report.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aural_ingest.dataset_adapters.guitar_techs import yield_cases
from aural_ingest.ground_truth_benchmark import run_sweep, write_report


CORPUS = Path(r"E:\AudioSourceOfTruthData\extracted\guitar_techs")
OUT = Path(r"D:\AuralPrimer\benchmarks\guitar\gt_runs\gt_micamp_stratified.json")
ALGOS = ("melodic_combined", "melodic_combined_guitar")

# Per-category caps. Kept small to fit the 60-minute wall-clock budget:
# ``techniques`` (avg 349 s audio) and ``singlenotes`` (avg 550 s audio) are
# capped to a single case each, since even one takes minutes of CPU per algo.
# Music is short and gets full coverage.
CAPS = {
    "chords": 8,           # 8 of 56
    "scales": 6,           # 6 of 24
    "singlenotes": 1,      # 1 of 2
    "techniques": 2,       # 2 of 10
    "music": 8,            # 8 of 12
}


def main() -> int:
    all_cases = list(yield_cases(CORPUS, signal="micamp"))
    by_cat: dict[str, list] = {}
    for c in all_cases:
        by_cat.setdefault(c.metadata["category"], []).append(c)

    stratified = []
    for cat, cap in CAPS.items():
        if cat not in by_cat:
            print(f"WARNING: category {cat} not present, skipping", file=sys.stderr)
            continue
        stratified.extend(by_cat[cat][:cap])
    print(f"stratified sample: {len(stratified)} cases", file=sys.stderr)
    for cat, cap in CAPS.items():
        actual = sum(1 for c in stratified if c.metadata["category"] == cat)
        print(f"  {cat:12s}  {actual}/{cap}", file=sys.stderr)

    def on_case(idx: int, s) -> None:
        print(f"[{idx:4d}/{len(stratified)}] {s.algorithm_id}  {s.case_id}"
              f"  f1={s.f1:.3f} tp={s.tp} fp={s.fp} fn={s.fn}", file=sys.stderr, flush=True)

    scores = run_sweep(
        stratified,
        algorithms=list(ALGOS),
        family="melodic",
        tolerance_sec=0.05,
        pitch_tolerance_semitones=0,
        on_case=on_case,
    )
    write_report(
        scores,
        out_path=OUT,
        dataset="guitar_techs",
        family="melodic",
        extra={
            "split": "test",
            "variant": "micamp",
            "limit": None,
            "tolerance_ms": 50.0,
            "pitch_tolerance_semitones": 0,
            "stratified_caps": CAPS,
        },
    )
    print(f"wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
