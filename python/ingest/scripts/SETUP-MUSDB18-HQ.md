# MUSDB18-HQ Setup

MUSDB18-HQ is required for the strict source-separation promotion gates:

- `musdb_sdr_baseline`
- `demucs_ft_drums_sdr`
- `roformer_musdb_comparison`

The corpus is not bundled. Keep it under local dataset storage and point the
benchmark runner at it with `AURAL_MUSDB18_HQ_ROOT` or `--musdb-root`.

Official source:

```text
https://zenodo.org/records/3338373
```

Direct archive URL:

```text
https://zenodo.org/records/3338373/files/musdb18hq.zip?download=1
```

Expected archive:

```text
musdb18hq.zip
md5: 12d4f2ecd55245a4688754dd76363103
size: 22.7 GB
```

Zenodo describes the extracted layout as `train/` and `test/` folders with
track subdirectories containing:

```text
mixture.wav
drums.wav
bass.wav
other.wav
vocals.wav
```

License note: MUSDB18-HQ is provided for educational purposes only and is not a
commercial redistribution asset. Do not package the corpus into modelpacks,
sidecars, app bundles, or release artifacts.

## Download and Extract

The helper below uses `curl --continue-at -`, verifies the MD5 digest, extracts
the archive, and prints the environment variable to use for benchmarks:

```powershell
powershell -ExecutionPolicy Bypass -File D:\AuralPrimer\python\ingest\scripts\setup_musdb18_hq.ps1
```

Default local paths:

```text
raw zip:    E:\AudioSourceOfTruthData\raw_datasets\musdb18_hq\musdb18hq.zip
extracted: E:\AudioSourceOfTruthData\extracted\musdb18_hq
```

Override them if needed:

```powershell
powershell -ExecutionPolicy Bypass -File D:\AuralPrimer\python\ingest\scripts\setup_musdb18_hq.ps1 `
  -DownloadRoot D:\datasets\raw\musdb18_hq `
  -ExtractRoot D:\datasets\extracted\musdb18_hq
```

To download and verify without extracting:

```powershell
powershell -ExecutionPolicy Bypass -File D:\AuralPrimer\python\ingest\scripts\setup_musdb18_hq.ps1 -SkipExtract
```

## Gate Runs

Set the extracted root before running promotion evidence:

```powershell
$env:AURAL_MUSDB18_HQ_ROOT = "E:\AudioSourceOfTruthData\extracted\musdb18_hq\musdb18hq"
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
```

First write the default Demucs test-split baseline:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider demucs `
  --split test `
  --limit 10 `
  --shifts 1 `
  --write-gate-evidence
```

Then run the staged `demucs_ft_drums` modelpack with a config JSON that records
`stem_separation_modelpack_id: demucs_ft_drums`, and run RoFormer after its
external runtime variables are set. The strict gate consumes reports written to
`benchmarks/quality/runs/*_musdb_separation_sdr.json`.

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider demucs `
  --split test `
  --limit 10 `
  --shifts 1 `
  --config-json D:\AuralPrimer\benchmarks\quality\configs\demucs_ft_drums.json `
  --write-gate-evidence
```
