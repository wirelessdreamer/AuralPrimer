# MIR-ST500 Setup

MIR-ST500 is required to clear the `rmvpe_mir_st500_vocals` gate. The RMVPE
checkpoint can be validated independently, but the strict gate also requires a
full unbounded `test`/`vocal` benchmark report from a prepared MIR-ST500 mirror.

Official source:

```text
https://github.com/york135/singing_transcription_ICASSP2021
```

Local metadata checkout staged on 2026-07-09:

```text
D:\AuralPrimer\.external\singing_transcription_ICASSP2021
commit: 680313740ec6792dc6358c3c722c63bd7d03159e
```

The repository provides:

```text
MIR-ST500_20210206\MIR-ST500_corrected.json
MIR-ST500_20210206\MIR-ST500_link.json
MIR-ST500_20210206\metadata.csv
get_youtube.py
do_spleeter.py
```

The official dataset source does not provide a direct bulk audio archive. It
publishes note annotations and YouTube links. Its README instructs users to
download audio with `get_youtube.py`; if links fail, it asks users to contact
the maintainer, especially for test-set songs. Treat audio reconstruction as a
review-sensitive local dataset-preparation step. Do not package MIR-ST500 audio
into modelpacks, sidecars, app bundles, or release artifacts.

## Expected Layout

The AuralPrimer adapter accepts either the repository root or the
`MIR-ST500_20210206` directory as `AURAL_MIR_ST500_ROOT` for annotations, but
audio discovery expects prepared song directories:

```text
<root>\train\1\Mixture.mp3
<root>\train\1\Vocal.wav
...
<root>\test\401\Mixture.mp3
<root>\test\401\Vocal.wav
```

The strict RMVPE gate targets the separated vocal files for songs 401-500.

## Local Audit Helper

Copy the official metadata into the local dataset root and write a preparation
status report without downloading audio:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  D:\AuralPrimer\python\ingest\scripts\prepare_mir_st500_root.py `
  --copy-metadata
```

Default local paths:

```text
metadata checkout: D:\AuralPrimer\.external\singing_transcription_ICASSP2021
target root:       E:\AudioSourceOfTruthData\extracted\mir_st500
status report:     D:\AuralPrimer\benchmarks\vocals\mir_st500_preparation_status.json
```

The helper records annotation counts, missing test-set `Vocal.wav` and
`Mixture.*` files, and whether `yt_dlp`, `spleeter`, and `tensorflow` are
available in the current Python environment. It exits nonzero until every
test-set vocal file is present, which is expected before audio reconstruction.
When `--dependency-python` is supplied, the report also records
`external_dependencies_ready` and `reconstruction_dependencies_ready` so the
heavy reconstruction packages can stay out of the ingest sidecar while the
dataset-prep environment is still auditable.

Install the optional isolated reconstruction environment outside the ingest
sidecar:

```powershell
powershell -ExecutionPolicy Bypass -File D:\AuralPrimer\python\ingest\scripts\setup_mir_st500_env.ps1 -Recreate
```

The setup script defaults to Python 3.11. It installs the Windows-compatible
TensorFlow stack explicitly, then installs Spleeter without its broken
`tensorflow-io-gcs-filesystem==0.32.0` Windows pin. Use `-Recreate` when a
previous failed or wrong-Python venv already exists.

Then include that Python in future preparation status reports:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  D:\AuralPrimer\python\ingest\scripts\prepare_mir_st500_root.py `
  --dependency-python D:\AuralPrimer\.external\mir-st500\.venv\Scripts\python.exe
```

Do not install `spleeter` or TensorFlow into the frozen ingest sidecar just to
prepare this benchmark corpus.

## Official Reconstruction Path

From the official checkout, after confirming the local review/licensing policy
for the audio source:

```powershell
cd D:\AuralPrimer\.external\singing_transcription_ICASSP2021

$prepPython = "D:\AuralPrimer\.external\mir-st500\.venv\Scripts\python.exe"

& $prepPython get_youtube.py `
  MIR-ST500_20210206\MIR-ST500_link.json `
  train `
  test

& $prepPython do_spleeter.py train
& $prepPython do_spleeter.py test
```

If the generated audio is stored elsewhere, keep
`MIR-ST500_corrected.json`, `metadata.csv`, and the `train`/`test` audio folders
under a single root, then set:

```powershell
$env:AURAL_MIR_ST500_ROOT = "E:\AudioSourceOfTruthData\extracted\mir_st500"
```

## Gate Run

With RMVPE configured and MIR-ST500 prepared:

```powershell
$env:AURAL_RMVPE_REPO = "D:\AuralPrimer\.external\RMVPE"
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
$env:AURAL_MIR_ST500_ROOT = "E:\AudioSourceOfTruthData\extracted\mir_st500"

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\vocals\run_mir_st500_vocals.py `
  --split test `
  --variant vocal `
  --algorithm melodic_rmvpe `
  --write-gate-evidence `
  --progress
```

Use `--output` and a small `--limit` only for exploratory smoke runs. Strict
gate evidence requires an unbounded test/vocal `melodic_rmvpe` run with no case
errors.
