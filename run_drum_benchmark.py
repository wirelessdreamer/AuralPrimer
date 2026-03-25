"""Benchmark new drum transcription algorithms against existing ones.

Runs on Psalm drum stems with ground-truth MIDI files.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent / "python" / "ingest" / "src"))

from aural_ingest.drum_benchmark import load_drum_reference, evaluate_drum_transcription, normalize_transcribed_events
from aural_ingest.transcription import build_default_drum_algorithm_registry

# Psalm test cases: (stem_wav, reference_mid, label)
PSALMS_ROOT = Path(r"D:\Psalms")
PSALM_CASES = [
    ("Psalm 1", "Psalm1_Stems", "Psalm 1 (Drums)"),
    ("Psalm 2", "Book of Psalms - Psalm 2 - King in Zion Stems", "Book of Psalms - Psalm 2 - King in Zion (Drums)"),
    ("Psalm 3", None, "Book of Psalms - Psalms 3 - Shield Me On All Sides (Drums)"),
    ("Psalm 4", "Book of Psalms - Psalm 4 - Trouble Again Stems", "Book of Psalms - Psalm 4 - Trouble Again (Drums)"),
    ("Psalm 5", "Book of Psalms - Psalm 5 - Every Morning Stems", "Book of Psalms - Psalm 5 - Every Morning (Drums)"),
    ("Psalm 6", "Book of Psalms - Psalm 6 - Break In Stems", "Book of Psalms - Psalm 6 - Break In (Drums)"),
    ("Psalm 7", "Psalm 7 - The Chase (Edit) Stems", "Psalm 7 - The Chase (Edit) (Drums)"),
]

# Algorithms to benchmark -- 3 new + top existing baseline
ALGORITHMS = [
    "adaptive_beat_grid",        # current best (F1=0.403)
    "combined_filter",           # expanded kit reference
    "spectral_template_multipass",  # multi-pass adaptive
    "mfcc_cepstral",             # NEW: MFCC classification
    "nmf_decomposition",         # NEW: NMF decomposition
    "hpss_percussive",           # NEW: HPSS isolation
]

def find_psalm_files(psalm_dir: str, stems_subdir: str | None, base_name: str) -> tuple[Path | None, Path | None]:
    """Find the .wav and .mid files for a psalm."""
    psalm_path = PSALMS_ROOT / psalm_dir
    if not psalm_path.exists():
        return None, None

    # Try stems subdir first, then root
    if stems_subdir:
        stems_path = psalm_path / stems_subdir
    else:
        stems_path = psalm_path

    wav = stems_path / f"{base_name}.wav"
    mid = stems_path / f"{base_name}.mid"

    # Also check root
    if not wav.exists():
        wav = psalm_path / f"{base_name}.wav"
    if not mid.exists():
        mid = psalm_path / f"{base_name}.mid"

    return (wav if wav.exists() else None, mid if mid.exists() else None)


def main():
    print("=" * 80)
    print("DRUM TRANSCRIPTION BENCHMARK — Novel Algorithms vs Existing")
    print("=" * 80)

    registry = build_default_drum_algorithm_registry()

    # Discover test cases
    cases: list[tuple[str, Path, Path]] = []
    for psalm_dir, stems_subdir, base_name in PSALM_CASES:
        wav, mid = find_psalm_files(psalm_dir, stems_subdir, base_name)
        if wav and mid:
            cases.append((psalm_dir, wav, mid))
            print(f"  ✓ {psalm_dir}: {wav.name}")
        else:
            print(f"  ✗ {psalm_dir}: missing wav={wav} mid={mid}")

    if not cases:
        print("No test cases found!")
        return

    print(f"\nFound {len(cases)} test cases")
    print(f"Algorithms: {', '.join(ALGORITHMS)}")
    print()

    # Results storage
    all_results: dict[str, dict[str, dict]] = {}  # algo -> psalm -> metrics

    for algo_id in ALGORITHMS:
        if algo_id not in registry:
            print(f"  ⚠ Algorithm '{algo_id}' not in registry, skipping")
            continue

        all_results[algo_id] = {}
        transcriber = registry[algo_id]

        for psalm_label, stem_wav, ref_mid in cases:
            print(f"  [{algo_id}] {psalm_label}...", end=" ", flush=True)
            t0 = time.time()

            try:
                # Load reference
                ref_events, ref_meta = load_drum_reference(ref_mid)

                # Run transcription
                predicted_raw = transcriber(stem_wav)
                predicted_events, ignored = normalize_transcribed_events(predicted_raw)

                # Evaluate
                result = evaluate_drum_transcription(
                    ref_events, predicted_events, tolerance_sec=0.060,
                )

                elapsed = time.time() - t0
                overall = result.get("overall", {})
                f1 = overall.get("f1", 0.0)
                precision = overall.get("precision", 0.0)
                recall = overall.get("recall", 0.0)
                mae = overall.get("timing_mae_ms")

                print(f"F1={f1:.3f}  P={precision:.3f}  R={recall:.3f}  MAE={mae:.1f}ms  ({elapsed:.1f}s)")
                all_results[algo_id][psalm_label] = {
                    "f1": f1,
                    "precision": precision,
                    "recall": recall,
                    "timing_mae_ms": mae,
                    "elapsed_sec": round(elapsed, 1),
                    "per_class": result.get("per_class", {}),
                    "predicted_count": len(predicted_events),
                    "reference_count": len(ref_events),
                }

            except Exception as e:
                elapsed = time.time() - t0
                print(f"ERROR: {e} ({elapsed:.1f}s)")
                all_results[algo_id][psalm_label] = {"error": str(e), "elapsed_sec": round(elapsed, 1)}

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY — Mean F1 across all Psalms")
    print("=" * 80)

    summary_rows = []
    for algo_id in ALGORITHMS:
        if algo_id not in all_results:
            continue
        f1_values = [r.get("f1", 0.0) for r in all_results[algo_id].values() if "f1" in r]
        p_values = [r.get("precision", 0.0) for r in all_results[algo_id].values() if "precision" in r]
        r_values = [r.get("recall", 0.0) for r in all_results[algo_id].values() if "recall" in r]
        mae_values = [r.get("timing_mae_ms", 0.0) for r in all_results[algo_id].values() if "timing_mae_ms" in r and r.get("timing_mae_ms") is not None]
        time_values = [r.get("elapsed_sec", 0.0) for r in all_results[algo_id].values()]

        mean_f1 = sum(f1_values) / max(1, len(f1_values))
        mean_p = sum(p_values) / max(1, len(p_values))
        mean_r = sum(r_values) / max(1, len(r_values))
        mean_mae = sum(mae_values) / max(1, len(mae_values))
        total_time = sum(time_values)

        summary_rows.append((algo_id, mean_f1, mean_p, mean_r, mean_mae, total_time))

    # Sort by F1
    summary_rows.sort(key=lambda x: -x[1])

    print(f"\n{'Algorithm':<35} {'F1':>6} {'Prec':>6} {'Rec':>6} {'MAE':>7} {'Time':>7}")
    print("-" * 75)
    for algo_id, f1, p, r, mae, tt in summary_rows:
        marker = " ★" if algo_id in ("nmf_decomposition", "mfcc_cepstral", "hpss_percussive") else ""
        print(f"{algo_id + marker:<35} {f1:>6.3f} {p:>6.3f} {r:>6.3f} {mae:>6.1f}ms {tt:>6.1f}s")

    # Per-class breakdown for top algorithms
    print("\n" + "=" * 80)
    print("PER-CLASS F1 — Top 3 algorithms + baseline")
    print("=" * 80)

    classes = ["kick", "snare", "hi_hat", "crash", "ride", "tom1", "tom2", "tom3"]
    top_algos = [row[0] for row in summary_rows[:6]]

    header = f"{'Class':<12}" + "".join(f"{a[:18]:>20}" for a in top_algos)
    print(header)
    print("-" * len(header))

    for cls in classes:
        row = f"{cls:<12}"
        for algo_id in top_algos:
            cls_f1s = []
            for psalm_data in all_results.get(algo_id, {}).values():
                per_class = psalm_data.get("per_class", {})
                if cls in per_class:
                    cls_f1s.append(per_class[cls].get("f1", 0.0))
            mean_cls_f1 = sum(cls_f1s) / max(1, len(cls_f1s))
            row += f"{mean_cls_f1:>20.3f}"
        print(row)

    # Save full results
    output_path = Path(__file__).parent / "benchmark_results.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
