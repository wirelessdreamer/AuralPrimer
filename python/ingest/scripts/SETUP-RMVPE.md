# SETUP - RMVPE vocals

This document explains how to make the `melodic_rmvpe` vocal pitch adapter
live. It is inert by default: without a local checkpoint,
`python/ingest/src/aural_ingest/algorithms/melodic_rmvpe.py` returns `[]`, and
the vocals chain falls back to `torchcrepe` and `pyin`.

## Source and license

Official code:

```
https://github.com/Dream-High/RMVPE
```

The official repository is Apache-2.0. The checkpoint distribution still needs
a source-by-source license audit before this repo should hardcode a download
URL. For now, install a checkpoint only from a source you have reviewed.

Reviewed official repo commit used for this scaffold:

```text
a6db1cd7d26014aa739383367afd9bab57fc624c
```

## Checkpoint layout

The adapter resolves checkpoints in this order:

1. `AURAL_RMVPE_CHECKPOINT` pointing directly at a `.pt`/`.pth` file.
2. A local model directory:

```text
<repo-root>/assets/models/rmvpe/rmvpe.pt
```

It also searches portable/modelpack-style roots via the same model-root
expansion used by other melodic checkpoints.

Before inference, the adapter verifies the checkpoint's current SHA-256 against
either the installer-written `rmvpe.checkpoint.json` beside the checkpoint or
`AURAL_RMVPE_CHECKPOINT_SHA256`. This is required because the RMVPE checkpoint
is loaded through PyTorch.

## Optional RMVPE repo path

The adapter requires an editable official repo checkout. Set
`AURAL_RMVPE_REPO` to the reviewed checkout:

```powershell
git clone https://github.com/Dream-High/RMVPE.git D:\AuralPrimer\.external\RMVPE
git -C D:\AuralPrimer\.external\RMVPE checkout a6db1cd7d26014aa739383367afd9bab57fc624c
$env:AURAL_RMVPE_REPO = "D:\AuralPrimer\.external\RMVPE"
```

Ambient importable modules named `src` are not accepted; the env var keeps the
runtime source identity explicit.

## Install a reviewed checkpoint

From the repo root, using a reviewed local file:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\install_rmvpe_checkpoint.py `
  --source-file D:\Downloads\rmvpe.pt `
  --expected-sha256 <reviewed_sha256> `
  --license-confirmed
```

Or using a reviewed URL:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\install_rmvpe_checkpoint.py `
  --source-url https://example.invalid/reviewed/rmvpe.pt `
  --expected-sha256 <reviewed_sha256> `
  --timeout-sec 600 `
  --license-confirmed
```

`--expected-sha256` is required for both local files and URLs. URL sources must
use HTTPS. A mismatch fails the install and leaves the target checkpoint
untouched. A successful install writes `rmvpe.checkpoint.json` beside the
checkpoint so the runtime can verify the file before inference.

If you point `AURAL_RMVPE_CHECKPOINT` at a checkpoint installed outside this
helper, also set:

```powershell
$env:AURAL_RMVPE_CHECKPOINT_SHA256 = "<reviewed_sha256>"
```

## Runtime knobs

```powershell
$env:AURAL_RMVPE_DEVICE = "cuda"      # optional; defaults via device auto-detect
$env:AURAL_RMVPE_BATCH_SIZE = "4"     # optional
$env:AURAL_RMVPE_PITCH_THRESHOLD = "0.03"
```

## Verification

First verify the configured RMVPE evidence before running MIR-ST500 or a full
import:

```powershell
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_rmvpe_runtime.py `
  --write-gate-evidence
```

The command prints the `benchmarks/runtime/runs/*_rmvpe_runtime.json` report
path and exits nonzero until the checkpoint, reviewed checkpoint hash/manifest,
explicit `AURAL_RMVPE_REPO`, and PyTorch runtime are all ready. To prove
inference on one local vocal stem:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_rmvpe_runtime.py `
  path\to\vocal_stem.wav `
  --require-notes `
  --write-gate-evidence
```

To clear the `rmvpe_mir_st500_vocals` model-upgrade gate, also point the
runner at a prepared MIR-ST500 mirror and write the unbounded test/vocal report
to the strict gate-evidence glob:

```powershell
$env:AURAL_MIR_ST500_ROOT = "E:\AudioSourceOfTruthData\extracted\mir_st500"

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\vocals\run_mir_st500_vocals.py `
  --split test `
  --variant vocal `
  --algorithm melodic_rmvpe `
  --write-gate-evidence `
  --progress
```

Use `--output` instead of `--write-gate-evidence` for limited smoke runs. The
gate report must cover the full discovered test/vocal case set with no case
errors.

Run a focused import of a vocals-heavy song and inspect `features/notes.mid`
for a `vocals` track on MIDI channel 6 (zero-based channel 5). When RMVPE
returns F0 frames, new imports should also write
`features/vocal_pitch_contour.json`; FeedPak conversion preserves that as
`vocal_pitch_contour` and emits `vocal_pitch.json` from the vocals MIDI track
when note segmentation succeeds. New imports also build
`features/spectrogram/vocals/`, which the lyric timing workspace already
probes.
