# SETUP - ADTOF (official MZehren/ADTOF)

This document explains how to make the `adtof_drums` benchmark engine live.
The engine is opt-in and inert by default: if the external runtime is absent,
`python/ingest/src/aural_ingest/algorithms/adtof_drums.py` returns `[]`.

ADTOF is not part of the frozen sidecar. It uses TensorFlow, tapcorrect, and
madmom in a dedicated Python environment, then AuralPrimer talks to it through
a subprocess JSON contract.

## License and source

Use the official repository only:

```
https://github.com/MZehren/ADTOF
```

The repository content is licensed CC BY-NC-SA 4.0. The setup script checks out
and installs the source repositories at reviewed commits used for this scaffold:

- `MZehren/ADTOF`: `b3968fb332f69b65ee07c089fc62f436503755db`
- `MZehren/tapcorrect`: `4f2d21e73fb0137119a4136513c42936b322fc0b`
- `CPJKU/madmom`: `27f032e8947204902c675e5e341a3faf5dc86dae`

Do not use `xavriley/ADTOF-pytorch` for this project unless its licensing
changes and is reviewed.

## Install

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File python\ingest\scripts\setup_adtof_env.ps1
```

The default script expects the Windows Python launcher to provide Python 3.10.
The official ADTOF README says the project was tested with Python 3.10, and
TensorFlow compatibility is the reason this runtime is kept outside the ingest
venv.

When the script finishes, set the printed environment variables:

```powershell
$env:AURAL_ADTOF_PYTHON = "D:\AuralPrimer\.external\adtof\.venv\Scripts\python.exe"
$env:AURAL_ADTOF_REPO = "D:\AuralPrimer\.external\adtof\ADTOF"
```

Use persistent user-level variables only after a successful smoke test:

```powershell
[Environment]::SetEnvironmentVariable("AURAL_ADTOF_PYTHON", $env:AURAL_ADTOF_PYTHON, "User")
[Environment]::SetEnvironmentVariable("AURAL_ADTOF_REPO", $env:AURAL_ADTOF_REPO, "User")
```

## Smoke test

With the env vars set:

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
$drumStem = "D:\path\to\one\drum-stem.wav"

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_adtof_runtime.py `
  $drumStem `
  --require-events `
  --write-gate-evidence
```

The validator prints the report path it wrote under
`benchmarks/runtime/runs/*_adtof_runtime.json`. After it reports `"ok": true`,
run the E-GMD sample through the normal benchmark path:

```powershell
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

If `adtof_drums` returns no events, check in order:

1. `AURAL_ADTOF_PYTHON` points at the dedicated venv's Python.
2. `AURAL_ADTOF_REPO` points at the cloned official repo root.
3. `AURAL_ADTOF_REPO\adtof\model\model.py` exists.
4. `AURAL_ADTOF_REPO\adtof\models\Frame_RNN_adtofAll_0.index` and its
   matching `.data-*` checkpoint file exist.
5. The external venv can run `python\ingest\scripts\run_adtof_adapter.py --help`.

The model emits five drum classes: BD/KD, SD, HH, TT, and CY+RD. The adapter
maps them to AuralPrimer's canonical notes 36, 38, 42, 47, and 49, so the
benchmark should compare ADTOF primarily on the five-class buckets.
