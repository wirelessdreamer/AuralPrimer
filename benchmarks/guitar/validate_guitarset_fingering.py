"""Validate GuitarSet string/fret metadata extracted by the adapter."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.dataset_adapters.common import GroundTruthCase  # noqa: E402
from aural_ingest.dataset_adapters.guitarset import (  # noqa: E402
    GUITARSET_OPEN_STRING_MIDI,
    yield_cases,
)


GUITARSET_VARIANTS = ("mic", "pickup_mix", "hex_original", "hex_debleeded")


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    try:
        if float(value) != float(number):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    return number


def _issue(case: GroundTruthCase, note_index: int, reason: str) -> dict[str, Any]:
    return {"case_id": case.case_id, "note_index": note_index, "reason": reason}


def validate_cases(
    cases: Iterable[GroundTruthCase],
    *,
    max_fret: int = 24,
    max_examples: int = 20,
) -> dict[str, Any]:
    case_count = 0
    note_count = 0
    metadata_count = 0
    invalid_count = 0
    examples: list[dict[str, Any]] = []
    string_counts: Counter[int] = Counter()
    style_counts: Counter[str] = Counter()
    fret_min: int | None = None
    fret_max: int | None = None

    def add_issue(case: GroundTruthCase, note_index: int, reason: str) -> None:
        nonlocal invalid_count
        invalid_count += 1
        if len(examples) < max_examples:
            examples.append(_issue(case, note_index, reason))

    for case in cases:
        case_count += 1
        note_count += len(case.melodic_notes)
        metadata_count += len(case.melodic_note_metadata)
        style = case.metadata.get("style")
        if style:
            style_counts[str(style)] += 1

        if len(case.melodic_note_metadata) != len(case.melodic_notes):
            add_issue(
                case,
                -1,
                (
                    "metadata length "
                    f"{len(case.melodic_note_metadata)} != notes length {len(case.melodic_notes)}"
                ),
            )

        for index, (note, metadata) in enumerate(
            zip(case.melodic_notes, case.melodic_note_metadata, strict=False)
        ):
            string_idx = _int_or_none(metadata.get("string"))
            fret = _int_or_none(metadata.get("fret"))
            open_midi = _int_or_none(metadata.get("open_midi"))

            if string_idx is None or not 0 <= string_idx < len(GUITARSET_OPEN_STRING_MIDI):
                add_issue(case, index, f"invalid string {metadata.get('string')!r}")
                continue
            if fret is None or not 0 <= fret <= max_fret:
                add_issue(case, index, f"invalid fret {metadata.get('fret')!r}")
                continue

            expected_open_midi = GUITARSET_OPEN_STRING_MIDI[string_idx]
            if open_midi != expected_open_midi:
                add_issue(
                    case,
                    index,
                    f"open_midi {open_midi!r} != expected {expected_open_midi}",
                )
                continue
            if int(note.pitch) - expected_open_midi != fret:
                add_issue(
                    case,
                    index,
                    f"pitch/open string imply fret {int(note.pitch) - expected_open_midi}, got {fret}",
                )
                continue

            string_counts[string_idx] += 1
            fret_min = fret if fret_min is None else min(fret_min, fret)
            fret_max = fret if fret_max is None else max(fret_max, fret)

    return {
        "ok": case_count > 0 and invalid_count == 0,
        "case_count": case_count,
        "note_count": note_count,
        "metadata_count": metadata_count,
        "invalid_note_count": invalid_count,
        "fret_min": fret_min,
        "fret_max": fret_max,
        "string_counts": {str(k): string_counts[k] for k in sorted(string_counts)},
        "style_counts": {k: style_counts[k] for k in sorted(style_counts)},
        "invalid_examples": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="validate_guitarset_fingering")
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--variant", choices=(*GUITARSET_VARIANTS, "all"), default="mic")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-fret", type=int, default=24)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    variants = GUITARSET_VARIANTS if args.variant == "all" else (args.variant,)
    per_variant: dict[str, dict[str, Any]] = {}
    for variant in variants:
        per_variant[variant] = validate_cases(
            yield_cases(Path(args.corpus_root), variant=variant, limit=args.limit),
            max_fret=args.max_fret,
        )

    total_cases = sum(item["case_count"] for item in per_variant.values())
    if total_cases == 0:
        raise SystemExit("no GuitarSet fingering cases matched corpus/filter settings")

    report = {
        "dataset": "guitarset",
        "task": "fingering_metadata_validation",
        "corpus_root": str(Path(args.corpus_root)),
        "variant": args.variant,
        "max_fret": args.max_fret,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ok": all(item["ok"] for item in per_variant.values()),
            "variant_count": len(per_variant),
            "case_count": total_cases,
            "note_count": sum(item["note_count"] for item in per_variant.values()),
            "metadata_count": sum(item["metadata_count"] for item in per_variant.values()),
            "invalid_note_count": sum(item["invalid_note_count"] for item in per_variant.values()),
        },
        "variants": per_variant,
    }

    if args.output:
        output = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = (
            ROOT
            / "benchmarks"
            / "guitar"
            / "gt_runs"
            / f"guitarset_fingering_validation_{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["summary"]["ok"], "output": str(output), "summary": report["summary"]}))

    if not report["summary"]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
