# RoFormer / MSST Stem-Separation Provider Setup

This is a research-only provider for evaluating RoFormer/MSST-style source
separation through AuralPrimer's existing stem-separation contract.

The provider is an external command wrapper. The ingest sidecar does not import
MSST, PyTorch separator code, or checkpoint-specific packages. When the runtime
is not configured, `--stem-separation-provider roformer` returns a structured
`skipped` result and import continues.

Useful references:

- MSST repository: https://github.com/ZFTurbo/Music-Source-Separation-Training
- Pretrained model list: https://raw.githubusercontent.com/ZFTurbo/Music-Source-Separation-Training/main/docs/pretrained_models.md
- BS RoFormer MUSDB18HQ config: https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v1.0.12/config_bs_roformer_384_8_2_485100.yaml
- BS RoFormer MUSDB18HQ checkpoint: https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v1.0.12/model_bs_roformer_ep_17_sdr_9.6568.ckpt

## License Gate

Verify the exact repo revision, config, checkpoint URL, and license before any
packaging or default-profile decision. The MSST repository currently advertises
an MIT license, but individual linked checkpoints can be hosted in separate
repositories or Hugging Face spaces. Treat every config/checkpoint pair as its
own artifact until the license and checksum are pinned.

## Environment

Set these variables before using the provider:

```powershell
$env:AURAL_ROFORMER_PYTHON = 'D:\path\to\msst-env\Scripts\python.exe'
$env:AURAL_ROFORMER_REPO = 'D:\path\to\Music-Source-Separation-Training'
$env:AURAL_ROFORMER_COMMAND = '{python_q} D:\AuralPrimer\python\ingest\scripts\run_roformer_msst.py --repo {repo_path_q} --mix-wav {mix_wav_q} --out-dir {out_dir_q} --config-path D:\models\roformer\config_bs_roformer_384_8_2_485100.yaml --model-path D:\models\roformer\model_bs_roformer_ep_17_sdr_9.6568.ckpt --shifts {shifts}'
```

Current MSST `inference.py` expects `--input_folder`,
`--start_check_point`, and `--bigshifts`; older examples using `--input`,
`--model_path`, or `--shifts` do not match current upstream. The
`run_roformer_msst.py` wrapper stages AuralPrimer's single `{mix_wav}` into a
temporary input folder, calls MSST, and normalizes outputs to role-named
`bass.wav`, `drums.wav`, `other.wav`, and `vocals.wav`.

The 2026-07-09 local smoke used MSST commit
`ccf86c105f55a03e4df3b294e8d27613fef80c1f`, config SHA-256
`d8afb980318d0c08b9c2e24a7adc00d4f3150320c127a7e4de861800d1321939`, and
checkpoint SHA-256
`3e9daecd70aaed5b5a0d1f861cc4d77eaa45afb3fc6301b1cf32c1be0f5868fb`.

`AURAL_ROFORMER_COMMAND` is formatted by the provider. Supported tokens:

- `{python}` / `{python_q}`
- `{repo_path}` / `{repo_path_q}`
- `{mix_wav}` / `{mix_wav_q}`
- `{out_dir}` / `{out_dir_q}`
- `{stems_dir}` / `{stems_dir_q}`
- `{config_json}` / `{config_json_q}`
- `{mix_sha256}`
- `{shifts}`

Use the `_q` token variants when the command is executed by a shell. The
provider prepends `AURAL_ROFORMER_REPO` to `PYTHONPATH`, runs the command in
that repo, and scans `{out_dir}` plus `{stems_dir}` for role-named `.wav`
outputs. Prefer writing to `{out_dir}` so AuralPrimer can preserve any
user-supplied protected stem before copying outputs into `audio/stems/`.

Recognized output names include:

- `drums.wav`
- `bass.wav`
- `guitar.wav`
- `vocals.wav` or `vocal.wav`
- `keys.wav`, `piano.wav`, or `keyboard.wav`
- `other.wav`, `accompaniment.wav`, `instrumental.wav`, or `no_vocals.wav`

Optional timeout override:

```powershell
$env:AURAL_ROFORMER_TIMEOUT_SEC = '3600'
```

The same values can be supplied in import config JSON using
`roformer_python`, `roformer_repo`, `roformer_command`, and
`roformer_timeout_sec`.

## Runtime Smoke

```powershell
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = 'D:\AuralPrimer'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  D:\AuralPrimer\python\ingest\scripts\validate_roformer_runtime.py `
  D:\path\to\mix.wav `
  --stems-dir D:\AuralPrimer\tmp\roformer-smoke-stems `
  --require-role drums `
  --require-role bass `
  --require-role vocals `
  --require-role other `
  --shifts 1 `
  --write-gate-evidence
```

The report should have `"ok": true`, `"status": "fresh"`, the resolved
external runtime paths under `runtime`, and all required roles listed under
`roles`. The validator prints the
`benchmarks/runtime/runs/*_roformer_runtime.json` report path strict
`runtime-check` consumes before you schedule MUSDB SDR.

For a full import smoke after the runtime contract passes:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli import `
  D:\path\to\mix.wav `
  --out D:\AuralPrimer\tmp\roformer-smoke.auralsong `
  --stem-separation-provider roformer `
  --shifts 1
```

Then check `manifest.json`:

- `pipeline.stem_separation.provider` should be `roformer`
- `pipeline.stem_separation.ok` should be true for a configured runtime
- `assets.audio.stems.*_path` should point at copied outputs

## MUSDB SDR Benchmark

After setting `AURAL_MUSDB18_HQ_ROOT` or passing `--musdb-root`, first run a
single-track exploratory smoke outside the strict gate glob:

```powershell
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider roformer `
  --split test `
  --limit 1 `
  --shifts 1 `
  --output D:\AuralPrimer\benchmarks\quality\exploratory_runs\roformer_musdb_smoke.json
```

Widen only after the single-track run succeeds and the output roles match the
expected MUSDB roles. To write strict promotion evidence, run at least the
10-track gate sample:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider roformer `
  --split test `
  --limit 10 `
  --shifts 1 `
  --write-gate-evidence
```
