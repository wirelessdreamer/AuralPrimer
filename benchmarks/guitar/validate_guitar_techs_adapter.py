"""Validate Guitar-TECHS adapter discovery and MIDI reference parsing."""
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
from aural_ingest.dataset_adapters.guitar_techs import yield_cases  # noqa: E402


GUITAR_TECHS_SIGNALS = ("directinput", "micamp")


def _issue(case: GroundTruthCase, reason: str, *, note_index: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"case_id": case.case_id, "reason": reason}
    if note_index is not None:
        out["note_index"] = note_index
    return out


def validate_cases(
    cases: Iterable[GroundTruthCase],
    *,
    max_examples: int = 20,
) -> dict[str, Any]:
    case_count = 0
    note_count = 0
    invalid_count = 0
    examples: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    player_counts: Counter[str] = Counter()
    duration_min: float | None = None
    duration_max: float | None = None

    def add_issue(case: GroundTruthCase, reason: str, *, note_index: int | None = None) -> None:
        nonlocal invalid_count
        invalid_count += 1
        if len(examples) < max_examples:
            examples.append(_issue(case, reason, note_index=note_index))

    for case in cases:
        case_count += 1
        note_count += len(case.melodic_notes)
        category = case.metadata.get("category")
        player = case.metadata.get("player")
        signal = case.metadata.get("signal")
        if category:
            category_counts[str(category)] += 1
        if player:
            player_counts[str(player)] += 1

        if not case.audio_path.is_file():
            add_issue(case, f"missing audio path {case.audio_path}")
        if signal not in GUITAR_TECHS_SIGNALS:
            add_issue(case, f"unexpected signal {signal!r}")
        if not case.melodic_notes:
            add_issue(case, "no melodic notes")
        if case.duration_sec <= 0:
            add_issue(case, f"non-positive duration {case.duration_sec!r}")

        duration_min = case.duration_sec if duration_min is None else min(duration_min, case.duration_sec)
        duration_max = case.duration_sec if duration_max is None else max(duration_max, case.duration_sec)

        for index, note in enumerate(case.melodic_notes):
            if note.t_off <= note.t_on:
                add_issue(case, f"non-positive note duration {note.t_on!r}->{note.t_off!r}", note_index=index)
            if not 0 <= int(note.pitch) <= 127:
                add_issue(case, f"invalid MIDI pitch {note.pitch!r}", note_index=index)
            if not 1 <= int(note.velocity) <= 127:
                add_issue(case, f"invalid MIDI velocity {note.velocity!r}", note_index=index)

    return {
        "ok": case_count > 0 and invalid_count == 0,
        "case_count": case_count,
        "note_count": note_count,
        "invalid_item_count": invalid_count,
        "duration_min_sec": round(duration_min, 6) if duration_min is not None else None,
        "duration_max_sec": round(duration_max, 6) if duration_max is not None else None,
        "category_counts": {k: category_counts[k] for k in sorted(category_counts)},
        "player_counts": {k: player_counts[k] for k in sorted(player_counts)},
        "invalid_examples": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="validate_guitar_techs_adapter")
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--signal", choices=(*GUITAR_TECHS_SIGNALS, "all"), default="directinput")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    signals = GUITAR_TECHS_SIGNALS if args.signal == "all" else (args.signal,)
    per_signal: dict[str, dict[str, Any]] = {}
    for signal in signals:
        per_signal[signal] = validate_cases(
            yield_cases(Path(args.corpus_root), signal=signal, limit=args.limit)
        )

    total_cases = sum(item["case_count"] for item in per_signal.values())
    if total_cases == 0:
        raise SystemExit("no Guitar-TECHS cases matched corpus/filter settings")

    report = {
        "dataset": "guitar_techs",
        "task": "adapter_validation",
        "corpus_root": str(Path(args.corpus_root)),
        "signal": args.signal,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ok": all(item["ok"] for item in per_signal.values()),
            "signal_count": len(per_signal),
            "case_count": total_cases,
            "note_count": sum(item["note_count"] for item in per_signal.values()),
            "invalid_item_count": sum(item["invalid_item_count"] for item in per_signal.values()),
        },
        "signals": per_signal,
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
            / f"guitar_techs_adapter_validation_{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["summary"]["ok"], "output": str(output), "summary": report["summary"]}))

    if not report["summary"]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
