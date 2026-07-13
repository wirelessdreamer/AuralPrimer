# YourMT3+ vs MR-MT3 drums on E-GMD test-30 (2026-07-07)

> **Result: mixed / no default promotion yet.** `yourmt3_drums` beats
> `mr_mt3_drums` decisively and edges drum-CRNN run-4 on aggregate F1
> (0.590 vs 0.576), but the plan's primary comparison is 5-class per-class
> buckets. On that metric it trails run-4 macro-5 (0.668 vs 0.707) and wins
> only cymbals. Keep drum-CRNN run-4 as the current benchmark leader pending
> psalm listening / gameplay review.

## Method

Real `gt-benchmark` engine path, same deterministic 30-case E-GMD test sample
used by drum-CRNN run-4:

```powershell
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'
$env:MT3_CHECKPOINT_DIR = 'D:\AuralPrimer\assets\models'
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset egmd `
  --corpus-root E:\AudioSourceOfTruthData\extracted\e_gmd `
  --algorithm yourmt3_drums `
  --algorithm mr_mt3_drums `
  --case-id-file D:\AuralPrimer\benchmarks\drums\gt_runs\stratified_sample_test_30.json `
  --split test `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\drums\gt_runs\yourmt3_mr_mt3_test30.json `
  --progress
```

Run completed with 30 cases per engine and zero case errors.

## Results

| Engine | Aggregate F1 | Macro-5 F1 | P / R | Mean runtime / case |
|---|---:|---:|---:|---:|
| `yourmt3_drums` | **0.590** | 0.668 | 0.619 / 0.564 | 3.027 s |
| `mr_mt3_drums` | 0.234 | 0.281 | 0.227 / 0.243 | 0.722 s |
| drum-CRNN run-4 | 0.576 | **0.707** | 0.737 / 0.473 | sidecar ONNX path |

Per-class F1:

| Engine | Kick | Snare | Hi-hat | Toms | Cymbals |
|---|---:|---:|---:|---:|---:|
| `yourmt3_drums` | 0.750 | 0.667 | 0.577 | 0.767 | **0.576** |
| `mr_mt3_drums` | 0.275 | 0.248 | 0.273 | 0.359 | 0.250 |
| drum-CRNN run-4 | **0.799** | **0.698** | **0.744** | **0.822** | 0.464 |

Raw JSON:
`benchmarks/drums/gt_runs/yourmt3_mr_mt3_test30.json`.

## Interpretation

`yourmt3_drums` is clearly worth keeping available: it is much better than
`mr_mt3_drums` on this report set and materially improves cymbal F1 over
drum-CRNN run-4. It is not a clean `gameplay_default` lead, because the
primary 5-class bucket comparison favors run-4 on kick, snare, hi-hat, and
toms, and run-4 keeps a higher macro-5 F1.

The aggregate F1 advantage is useful but not promotion-grade by itself. MT3
engines emit finer drum taxonomy than the 5-class CRNN, so the aggregate
column is not the cross-family primary metric in the model-upgrade plan.

## Next

1. Keep `yourmt3_drums` as a benchmarkable / manually selectable engine.
2. Reimport 1-2 drum-heavy psalms with `--drum-filter yourmt3_drums` for the
   listening review before any profile reorder.
3. Consider a hybrid or class-specific fallback later: run-4 for the default
   baseline, YourMT3 evidence for the cymbals problem.
