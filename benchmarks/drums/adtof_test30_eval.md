# ADTOF drums on E-GMD test-30 (2026-07-09)

> **Result: useful research adapter, no profile/default promotion.**
> `adtof_drums` beats the current DSP drum default on this deterministic
> E-GMD test-30 sample, but trails both `yourmt3_drums` and drum-CRNN run-4 on
> aggregate F1 and macro-5 class F1. Keep ADTOF explicit/research-only.

## Method

Real `gt-benchmark` path, official MZehren/ADTOF external runtime:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'
$env:AURAL_ADTOF_PYTHON = 'D:\AuralPrimer\.external\adtof\.venv\Scripts\python.exe'
$env:AURAL_ADTOF_REPO = 'D:\AuralPrimer\.external\adtof\ADTOF'
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset egmd `
  --corpus-root E:\AudioSourceOfTruthData\extracted\e_gmd `
  --algorithm adtof_drums `
  --case-id-file D:\AuralPrimer\benchmarks\drums\gt_runs\stratified_sample_test_30.json `
  --split test `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\drums\gt_runs\adtof_test30.json `
  --progress
```

Run completed with 30 cases, 30 successful cases, and zero case errors.

## Results

| Engine | Aggregate F1 | Macro-5 F1 | P / R | Mean runtime / case |
|---|---:|---:|---:|---:|
| `adtof_drums` | 0.422 | 0.539 | 0.487 / 0.372 | 11.238 s |
| `yourmt3_drums` | 0.590 | 0.668 | 0.619 / 0.564 | 3.027 s |
| drum-CRNN run-4 | 0.576 | 0.706 | 0.737 / 0.473 | 0.235 s |
| current DSP default | 0.284 | 0.216 | 0.392 / 0.222 | 6.398 s |

ADTOF per-class F1:

| Kick | Snare | Hi-hat | Toms | Cymbals |
|---:|---:|---:|---:|---:|
| 0.693 | 0.489 | 0.487 | 0.470 | 0.556 |

Raw JSON: `benchmarks/drums/gt_runs/adtof_test30.json`.

## Interpretation

ADTOF is directionally valuable versus the DSP default, especially cymbals, but
it is not competitive with the best local neural drum routes on this test set.
It also has the slowest mean runtime in this comparison and carries
CC BY-NC-SA obligations through the official runtime/assets.

Keep the adapter available for research and A/B listening, but do not promote
it into `gameplay_default` or a shipped default profile.
