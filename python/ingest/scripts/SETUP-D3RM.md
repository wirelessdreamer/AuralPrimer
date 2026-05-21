# D3RM piano transcription — Windows + WSL2 setup

D3RM (Kim, Kwon, Nam — ICASSP 2025) is the SOTA piano transcription model on
MAESTRO (97.55 onset F1). It depends on `natten==0.15.1` built against
`torch==2.1.2+cu121`, and the only published wheel for that combo is **Linux-
only**. On Windows we therefore run D3RM inside a dedicated WSL2 Debian venv
and invoke it from the Windows ingest runtime via `wsl bash …`. The
aural_ingest code (`piano_d3rm.py`) is path-agnostic — set the env vars below
and the existing `piano_pti_clean` / `piano_d3rm_clean` chain picks it up.

## Current state on this machine (2026-05-19)

This setup was driven mostly automatically; here's what is and isn't done.

**Done:**
- Edwards-robust Kong checkpoint at
  `D:\AuralPrimer\assets\models\piano_pti\high_resolution_MAESTRO_augmentations.pth`
  (sha256 `b20f72053abc15b78f689b2a8b04c0a06529c8466e898b915803a1daa2011b9e`).
  `piano_pti` auto-discovers this — no env var needed.
- D3RM repo cloned at `D:\Code\d3rm\`.
- D3RM checkpoints at `D:\AuralPrimer\assets\models\piano_d3rm\`:
  - `D3RM.ckpt` (42 MB)
  - `NAR-HC_baseline.ckpt` (26 MB) — only needed if you fine-tune
- WSL Debian (trixie) venv at `~/.venvs/d3rm` (Python 3.11.15, managed by uv).
- `torch==2.1.2+cu121`, `torchaudio==2.1.2+cu121`, `torchvision==0.16.2+cu121`
  installed; CUDA verified (`torch.cuda.is_available() == True`).
- `numpy==1.26.4`, `setuptools==69.5.1`, `wheel`, `ninja`, `packaging`, `cmake==4.3.2`
  (PyPI cmake — works without sudo).
- All non-natten D3RM Python deps installed: wandb, adabelief_pytorch,
  nnaudio 0.3.2, librosa 0.9.2, mido 1.2, mir_eval 0.7, tqdm, termcolor,
  lightning, timm, pretty_midi (and their transitive deps).

**Blocked — needs one sudo command from you:**
- `natten==0.15.1` source build fails at the cmake/CUDA-kernels stage because
  there is **no system `nvcc`** on this WSL Debian. shi-labs.com's prebuilt
  wheel index is also unusable right now (their SSL cert is expired). The fix
  is one apt install (~2 GB download), then one pip install.

## Finish the install (5 minutes once you run sudo)

```bash
# Inside WSL Debian. The next two lines need your password.
sudo apt-get update
sudo apt-get install -y nvidia-cuda-toolkit

# Verify nvcc is on PATH
nvcc --version

# Rebuild natten 0.15.1 — venv has all build deps already
export PATH="$HOME/.local/bin:$PATH"
export VIRTUAL_ENV=$HOME/.venvs/d3rm
# Set CUDA arch for your GPU (RTX 5090 = 12.0, 4090 = 8.9, 3090 = 8.6, 2070 = 7.5)
export NATTEN_CUDA_ARCH_LIST="8.9"
uv pip install 'natten==0.15.1' --no-build-isolation
```

Compile takes 10–20 minutes on a typical desktop. Verify:

```bash
/home/dreamer/.venvs/d3rm/bin/python -c \
  "import torch, natten; print(torch.__version__, natten.__version__, torch.cuda.is_available())"
# Expect: 2.1.2+cu121  0.15.1  True
```

## Wire D3RM into aural_ingest (PowerShell — once natten compiles)

```powershell
$env:AURAL_PIANO_D3RM_CHECKPOINT = "D:\AuralPrimer\assets\models\piano_d3rm\D3RM.ckpt"
$env:AURAL_PIANO_D3RM_CONFIG     = "D:\Code\d3rm\configs\D3RM_cli.yaml"
$wrapper = "D:\AuralPrimer\python\ingest\scripts\d3rm_wsl_transcribe.sh"
$env:AURAL_PIANO_D3RM_COMMAND = "wsl --distribution Debian bash $wrapper {audio} {midi} {checkpoint} {config}"
```

Persist with `[Environment]::SetEnvironmentVariable(..., 'User')` once happy.

## If you want to skip D3RM

It's safe. The fallback chain in `_piano_auto` is

```
piano_pti_clean  →  piano_hft_clean  →  piano_transkun_clean  →  piano_d3rm_clean  →  piano_polyphonic_clean  → …
```

so when `AURAL_PIANO_D3RM_*` is unset, `piano_d3rm_clean` raises `RuntimeError`
and the chain silently moves on to the next producer. Edwards-robust Kong
(layer 1) is the meaningful quality win — D3RM adds ~0.2–0.8 F1 on MAESTRO
and its real-world gain over Edwards/Kong on stem-separated audio is
unproven. Don't feel obligated to finish this.

## Why isolate the D3RM venv?

`aural_ingest` pins `torch==2.11.0`, `numpy>=2.0.0`, `librosa>=0.11.0`.
D3RM pins `torch==2.1.2`, `numpy==1.25.2`, `librosa==0.9.2`. They cannot share
a venv. Running D3RM as a subprocess through WSL is the only path that lets
both stacks coexist on this machine without conflicts.

## Reference

- D3RM paper: https://arxiv.org/abs/2501.05068
- D3RM repo: https://github.com/hanshounsu/d3rm
- Edwards et al. robust Kong: https://arxiv.org/abs/2402.01424 ;
  https://zenodo.org/records/10610212
