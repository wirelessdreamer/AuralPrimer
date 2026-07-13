# MIR-ST500 vocals evaluation scaffold

MIR-ST500 is now wired as a vocal-note ground-truth dataset for
`gt-benchmark`. The adapter reads `MIR-ST500_corrected.json`, uses the official
train/test split (`1..400` train, `401..500` test), and defaults to separated
`Vocal.wav` files produced by the upstream prep flow.

Run once a local MIR-ST500 mirror is prepared:

```powershell
$env:AURAL_MIR_ST500_ROOT = "E:\AudioSourceOfTruthData\extracted\mir_st500"
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  python\ingest\scripts\validate_rmvpe_runtime.py `
  --write-gate-evidence
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  benchmarks\vocals\run_mir_st500_vocals.py `
  --split test `
  --variant vocal `
  --algorithm melodic_rmvpe `
  --write-gate-evidence `
  --progress
```

Equivalent direct CLI path:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli `
  gt-benchmark `
  --dataset mir_st500 `
  --corpus-root $env:AURAL_MIR_ST500_ROOT `
  --split test `
  --variant vocal `
  --algorithm melodic_rmvpe `
  --output benchmarks\vocals\gt_runs\manual_mir_st500_vocals.json
```

Remaining gates:

- Local dataset mirror is not present in this workspace.
- RMVPE checkpoint/repo is still required for non-empty `melodic_rmvpe`
  predictions; `python/ingest/scripts/validate_rmvpe_runtime.py` exits nonzero
  and reports the missing evidence until that runtime is ready.
- Gate evidence must be an unbounded test/vocal `melodic_rmvpe` run; use
  `--output` instead of `--write-gate-evidence` for limited smoke runs.
