"""Multi-song real-world holdout regression suite for drum transcription.

Uses human-authored MIDI references from the Psalms project with
sync-corrected offsets.  Run this after every algorithm change to
prevent overfitting to one song.

Usage:
    py -3 benchmarks/drums/run_realworld_regression.py
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.drum_benchmark import (
    BenchmarkEvent,
    _parse_midi_note_ons,
    _tick_to_seconds,
    _compress_tempo_changes,
    normalize_drum_note,
    benchmark_algorithms,
    format_benchmark_summary,
)
from aural_ingest.transcription import build_default_drum_algorithm_registry

# -----------------------------------------------------------------------
# Song manifest: each entry has a sync-corrected offset determined by
# cross-correlating MIDI onsets against audio onset envelopes.
# -----------------------------------------------------------------------
SONGS = [
    {
        "id": "psalm_1",
        "name": "Psalm 1",
        "midi": Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).wav"),
        "offset_sec": -0.540,
    },
    {
        "id": "psalm_2_king_in_zion",
        "name": "Psalm 2 (King in Zion)",
        "midi": Path(r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).wav"),
        "offset_sec": -0.480,
    },
    {
        "id": "psalm_4_trouble_again",
        "name": "Psalm 4 (Trouble Again)",
        "midi": Path(r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Drums).wav"),
        "offset_sec": -0.540,
    },
    {
        "id": "psalm_5_every_morning",
        "name": "Psalm 5 (Every Morning)",
        "midi": Path(r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Drums).wav"),
        "offset_sec": -0.550,
    },
    {
        "id": "psalm_6_break_in",
        "name": "Psalm 6 (Break In)",
        "midi": Path(r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Drums).wav"),
        "offset_sec": -0.490,
    },
    {
        "id": "psalm_7_the_chase",
        "name": "Psalm 7 (The Chase)",
        "midi": Path(r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Drums).wav"),
        "offset_sec": -0.540,
    },
]

DEFAULT_ALGORITHMS = [
    "spectral_flux_multiband",
    "beat_conditioned_multiband_decoder",
    "adaptive_beat_grid",
    "spectral_template_multipass",
    "spectral_template_with_grid",
]


def load_reference_with_offset(midi_path: Path, offset_sec: float) -> list[BenchmarkEvent]:
    note_ons, tempo_changes_raw, tpq = _parse_midi_note_ons(midi_path)
    tempo_changes = _compress_tempo_changes(tempo_changes_raw)
    events = []
    for n in note_ons:
        t = _tick_to_seconds(n.tick, tempo_changes, tpq) + offset_sec
        if t < 0:
            continue
        drum_class = normalize_drum_note(n.note)
        if drum_class is None:
            continue
        events.append(BenchmarkEvent(time=round(t, 6), drum_class=drum_class))
    return sorted(events, key=lambda e: e.time)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm", dest="algorithms", action="append", default=None)
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--label", default="realworld-regression")
    args = parser.parse_args()

    algorithms = args.algorithms or DEFAULT_ALGORITHMS
    tolerance_sec = args.tolerance_ms / 1000.0

    registry = build_default_drum_algorithm_registry()
    all_results: list[dict] = []

    print(f"Algorithms: {', '.join(algorithms)}")
    print(f"Tolerance:  {args.tolerance_ms}ms")
    print(f"Songs:      {len(SONGS)}")
    print()

    for song in SONGS:
        if not song["midi"].exists() or not song["wav"].exists():
            print(f"SKIP {song['name']} — files not found")
            continue

        print(f"{'='*60}")
        print(f"  {song['name']}  (offset: {song['offset_sec']:+.3f}s)")
        print(f"{'='*60}")

        ref_events = load_reference_with_offset(song["midi"], song["offset_sec"])
        print(f"  Reference events: {len(ref_events)}")

        t0 = time.time()
        results = benchmark_algorithms(
            song["wav"], ref_events, algorithms, registry,
            tolerance_sec=tolerance_sec,
        )
        elapsed = time.time() - t0
        print(f"  Evaluated in {elapsed:.1f}s\n")

        payload = {
            "song_id": song["id"],
            "song_name": song["name"],
            "reference_path": str(song["midi"]),
            "reference_count": len(ref_events),
            "tolerance_ms": args.tolerance_ms,
            "midi_offset_sec": song["offset_sec"],
            "results": results,
        }

        print(format_benchmark_summary(payload))
        print()
        all_results.append(payload)

    # -----------------------------------------------------------------------
    # Aggregate summary
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  AGGREGATE SUMMARY (mean across {len(all_results)} songs)")
    print(f"{'='*60}\n")

    alg_stats: dict[str, dict[str, list[float]]] = {}
    for payload in all_results:
        for r in payload["results"]:
            alg = r["algorithm"]
            if alg not in alg_stats:
                alg_stats[alg] = {"f1": [], "precision": [], "recall": [], "tp": [], "fp": [], "fn": [], "mae": []}
            overall = r.get("overall", {})
            if "f1" in overall:
                alg_stats[alg]["f1"].append(float(overall["f1"]))
                alg_stats[alg]["precision"].append(float(overall["precision"]))
                alg_stats[alg]["recall"].append(float(overall["recall"]))
                alg_stats[alg]["tp"].append(int(overall["tp"]))
                alg_stats[alg]["fp"].append(int(overall["fp"]))
                alg_stats[alg]["fn"].append(int(overall["fn"]))
                if overall.get("timing_mae_ms") is not None:
                    alg_stats[alg]["mae"].append(float(overall["timing_mae_ms"]))

    print(f"{'Algorithm':<42} {'F1':>6} {'Prec':>6} {'Rec':>6} {'TP':>6} {'FP':>6} {'FN':>6} {'MAE':>7}")
    print("-" * 95)
    for alg, stats in sorted(alg_stats.items(), key=lambda x: -sum(x[1]["f1"])/max(1,len(x[1]["f1"]))):
        n = len(stats["f1"])
        if n == 0:
            continue
        f1 = sum(stats["f1"]) / n
        prec = sum(stats["precision"]) / n
        rec = sum(stats["recall"]) / n
        tp = sum(stats["tp"])
        fp = sum(stats["fp"])
        fn = sum(stats["fn"])
        mae = sum(stats["mae"]) / len(stats["mae"]) if stats["mae"] else 0
        print(f"{alg:<42} {f1:>6.3f} {prec:>6.3f} {rec:>6.3f} {tp:>6} {fp:>6} {fn:>6} {mae:>6.1f}ms")

    # Save full results
    out_dir = Path(r"C:\Users\dreamer\Documents\drum_benchmark_runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{ts}_{args.label}.json"
    out_path.write_text(json.dumps({
        "label": args.label,
        "tolerance_ms": args.tolerance_ms,
        "songs": all_results,
        "aggregate": {alg: {k: sum(v)/max(1,len(v)) for k,v in stats.items()} for alg, stats in alg_stats.items()},
    }, indent=2), encoding="utf-8")
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    main()
