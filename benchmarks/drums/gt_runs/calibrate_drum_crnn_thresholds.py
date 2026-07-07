"""Calibrate per-class decode thresholds for the drum-CRNN.

Sweeps a decode-threshold grid on the VALIDATION stratified-30 sample
(``stratified_sample_validation_30.json``) -- never the test-30 report set --
and picks the per-class F1-maximizing threshold for each of the 5 drum
classes independently.

Per-class scoring in ``score_drum_case`` buckets predictions/references by
class before matching, so classes never interact there: sweeping a single
SCALAR threshold uniformly across all classes and reading off each class's
OWN resulting F1 at that value is equivalent to (and far cheaper than) five
independent per-class grid searches. Raw sigmoid probabilities are computed
once per case and cached; the sweep only re-decodes (numpy peak-picking), it
never re-runs inference.

Run (from the repo root, with the ingest venv):

    python/ingest/.venv/Scripts/python.exe \
        benchmarks/drums/gt_runs/calibrate_drum_crnn_thresholds.py \
        --onnx D:/drum_crnn_run3/model.onnx \
        --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
        --case-id-file benchmarks/drums/gt_runs/stratified_sample_validation_30.json \
        --output benchmarks/drums/gt_runs/drum_crnn_run3_calibration.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable when run as a plain script (no install needed),
# and put the LOCAL worktree's src ahead of any editable install elsewhere:
# benchmarks/drums/gt_runs/ -> repo root -> python/ingest/src.
_SRC = Path(__file__).resolve().parents[3] / "python" / "ingest" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--onnx", required=True, help="Trained drum_crnn ONNX (raw export, not a modelpack).")
    ap.add_argument("--corpus-root", required=True, help="E-GMD root.")
    ap.add_argument("--case-id-file", required=True, help="Stratified manifest JSON (validation split).")
    ap.add_argument("--output", required=True)
    ap.add_argument("--grid-min", type=float, default=0.05)
    ap.add_argument("--grid-max", type=float, default=0.45)
    ap.add_argument("--grid-step", type=float, default=0.05)
    ap.add_argument("--tolerance-ms", type=float, default=50.0)
    ap.add_argument("--min-gap-sec", type=float, default=0.02)
    args = ap.parse_args()

    import numpy as np
    import onnxruntime as ort

    from aural_ingest.dataset_adapters.egmd import yield_cases
    from aural_ingest.ground_truth_benchmark import score_drum_case
    from aural_ingest.training.drum_crnn.config import CLASSES, FeatureConfig
    from aural_ingest.training.drum_crnn.decode import decode_events
    from aural_ingest.training.drum_crnn.features import load_audio_mono, logmel_from_audio

    manifest = json.loads(Path(args.case_id_file).read_text(encoding="utf-8"))
    case_ids = {c["case_id"] for c in manifest["cases"]}
    split = manifest.get("split", "validation")

    cases = list(yield_cases(Path(args.corpus_root), split=split, case_ids=case_ids))
    resolved_ids = {c.case_id for c in cases}
    if resolved_ids != case_ids:
        missing = sorted(case_ids - resolved_ids)
        print(
            f"[calibrate] warning: {len(missing)}/{len(case_ids)} case_ids "
            f"not resolved (showing up to 5): {missing[:5]}",
            file=sys.stderr,
        )
    print(f"[calibrate] {len(cases)} cases resolved from {args.case_id_file} (split={split!r})")

    session = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    feat = FeatureConfig()

    # Cache raw sigmoid probs per case ONCE -- the threshold sweep below only
    # re-decodes cached probabilities, it never re-runs inference.
    cached: list[tuple[object, np.ndarray]] = []
    for case in cases:
        audio = load_audio_mono(case.audio_path, feat.sample_rate)
        logmel = logmel_from_audio(audio, feat)  # (n_frames, n_mels), n_frames may be 0
        if logmel.shape[0] == 0:
            cached.append((case, np.zeros((0, len(CLASSES)), dtype=np.float32)))
            continue
        mel = logmel[np.newaxis, :, :].astype(np.float32)
        logits = session.run(["logits"], {"mel": mel})[0]
        probs = 1.0 / (1.0 + np.exp(-logits[0]))
        cached.append((case, probs.astype(np.float32)))
    print(f"[calibrate] cached probs for {len(cached)} cases")

    grid: list[float] = []
    t = args.grid_min
    while t <= args.grid_max + 1e-9:
        grid.append(round(t, 4))
        t += args.grid_step

    tol_sec = args.tolerance_ms / 1000.0
    sweep: dict[str, dict[str, float]] = {name: {} for name in CLASSES}
    best: dict[str, tuple[float, float]] = {name: (0.0, -1.0) for name in CLASSES}

    for thr in grid:
        agg: dict[str, dict[str, int]] = {name: {"tp": 0, "fp": 0, "fn": 0} for name in CLASSES}
        for case, probs in cached:
            events = decode_events(probs, feat, threshold=thr, min_gap_sec=args.min_gap_sec)
            score = score_drum_case(
                case,
                events,
                algorithm_id="drum_crnn_calibrate",
                runtime_sec=0.0,
                tolerance_sec=tol_sec,
                pitch_aware=True,
            )
            for cls, counts in score.per_class.items():
                if cls in agg:
                    agg[cls]["tp"] += counts["tp"]
                    agg[cls]["fp"] += counts["fp"]
                    agg[cls]["fn"] += counts["fn"]

        line = [f"thr={thr:.2f}"]
        for cls in CLASSES:
            c = agg[cls]
            denom = 2 * c["tp"] + c["fp"] + c["fn"]
            f1 = (2 * c["tp"] / denom) if denom > 0 else 0.0
            sweep[cls][f"{thr:.2f}"] = round(f1, 4)
            if f1 > best[cls][1]:
                best[cls] = (thr, f1)
            line.append(f"{cls}={f1:.3f}")
        print("[calibrate] " + "  ".join(line))

    winning_thresholds = {cls: best[cls][0] for cls in CLASSES}
    winning_f1 = {cls: round(best[cls][1], 4) for cls in CLASSES}

    out = {
        "onnx": str(args.onnx),
        "case_id_file": str(args.case_id_file),
        "split": split,
        "n_cases": len(cached),
        "grid": grid,
        "tolerance_ms": args.tolerance_ms,
        "min_gap_sec": args.min_gap_sec,
        "sweep_per_class_f1": sweep,
        "winning_thresholds": winning_thresholds,
        "winning_f1_at_threshold": winning_f1,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[calibrate] wrote {out_path}")
    print(f"[calibrate] winning thresholds: {winning_thresholds}")
    thresholds_str = ",".join(f"{k}:{v}" for k, v in winning_thresholds.items())
    print(f"[calibrate] --thresholds string: {thresholds_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
