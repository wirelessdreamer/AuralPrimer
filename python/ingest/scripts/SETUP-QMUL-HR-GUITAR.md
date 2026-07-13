# QMUL High-Resolution Guitar Adapter Setup

This is a research-only benchmark adapter for the ICASSP 2024 QMUL
high-resolution guitar transcription work by Riley, Edwards, and Dixon.

The public project page and paper describe the model and GuitarSet results, but
the ingest sidecar does not vendor or import that runtime. The adapter is an
external command wrapper so a local checkout/checkpoint can be evaluated through
the same `gt-benchmark` path without adding heavy or uncertain dependencies.

## License Gate

Before running or packaging anything, verify the exact code and checkpoint
license for the checkout you use. The project website is CC BY-SA 4.0 for the
website itself; that is not a model/runtime license. The François Leduc dataset
record is research-restricted on Zenodo, and the project page points to a newer
Hugging Face dataset copy, so dataset and model artifacts need explicit review
before any shipping decision.

Useful references:

- Project page: https://xavriley.github.io/HighResolutionGuitarTranscription/
- Paper: https://arxiv.org/abs/2402.15258
- Dataset record: https://zenodo.org/records/10984521
- Usable public runtime candidate: https://github.com/xavriley/hf_midi_transcription
- Public checkpoint repository: https://huggingface.co/xavriley/midi-transcription-models
- Francois Leduc guitar checkpoint: https://huggingface.co/xavriley/midi-transcription-models/blob/main/guitar-fl.pth

## Environment

Set these variables before using `qmul_hr_guitar`:

```powershell
$env:AURAL_QMUL_GUITAR_PYTHON = 'D:\path\to\qmul-hf-midi\.venv\Scripts\python.exe'
$env:AURAL_QMUL_GUITAR_REPO = 'D:\path\to\qmul-hf-midi\hf_midi_transcription'
$env:AURAL_QMUL_GUITAR_COMMAND = '{python_q} D:\AuralPrimer\python\ingest\scripts\run_qmul_hf_midi.py --audio {wav_path_q} --out-midi {out_midi_q} --instrument guitar --checkpoint D:\path\to\qmul-hf-midi\checkpoints\guitar-fl.pth --device cpu --batch-size 8'
```

The `run_qmul_hf_midi.py` wrapper invokes the public
`hf_midi_transcription` CLI and reconfigures stdout/stderr to UTF-8 so the
upstream status glyphs do not fail under Windows' default console code page.
Use `--instrument guitar --checkpoint <guitar-fl.pth>` for the Francois Leduc
checkpoint; upstream's default `--instrument guitar` currently resolves a
different guitar checkpoint when `--checkpoint` is omitted.

One compatible local route is:

```powershell
$root = 'D:\ExternalRuntimes\qmul-hf-midi'
New-Item -ItemType Directory -Force $root | Out-Null
git clone https://github.com/xavriley/hf_midi_transcription.git "$root\hf_midi_transcription"
py -3.11 -m venv "$root\.venv"
& "$root\.venv\Scripts\python.exe" -m pip install -U pip
& "$root\.venv\Scripts\python.exe" -m pip install -e "$root\hf_midi_transcription"
New-Item -ItemType Directory -Force "$root\checkpoints" | Out-Null
& "$root\.venv\Scripts\python.exe" -c "from huggingface_hub import hf_hub_download; print(hf_hub_download(repo_id='xavriley/midi-transcription-models', filename='guitar-fl.pth', revision='689e773723bcafd8c81015b10c03f12675ce16ec', local_dir=r'D:\ExternalRuntimes\qmul-hf-midi\checkpoints'))"
```

The 2026-07-09 local smoke used `hf_midi_transcription` commit
`96f6797881e9497cbfc8f8e5deccea9c1f2f7adc` and `guitar-fl.pth` SHA-256
`50d93dba89bdd3401849bc735614478e83d9f46d21fa3f71d8aca5acc0a52028`. The
checkout required a local compatibility patch from `from librosa.core import
load` to `from librosa.core.audio import load`; keep that patch local unless
upstream accepts an equivalent fix.

`AURAL_QMUL_GUITAR_COMMAND` is formatted by the adapter. Supported tokens:

- `{python}` / `{python_q}`
- `{repo_path}` / `{repo_path_q}`
- `{wav_path}` / `{wav_path_q}`
- `{out_midi}` / `{out_midi_q}`
- `{out_json}` / `{out_json_q}`

Use the `_q` token variants when the command is executed by a shell. The
adapter prepends `AURAL_QMUL_GUITAR_REPO` to `PYTHONPATH`, runs the command in
that repo, and accepts either:

- a MIDI file written to `{out_midi}`, or
- a JSON file written to `{out_json}`.

The JSON format can be either `{"notes": [...]}` or `{"events": [...]}`. Each
note should contain `onset`/`offset`/`pitch` plus optional `velocity`, with
aliases such as `t_on`, `t_off`, `midi_note`, and `duration` accepted.

Optional timeout override:

```powershell
$env:AURAL_QMUL_GUITAR_TIMEOUT_SEC = '1800'
```

## Smoke Test

With the repo/checkpoint installed and the command configured:

```powershell
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = 'D:\AuralPrimer'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  D:\AuralPrimer\python\ingest\scripts\validate_qmul_hr_guitar_runtime.py `
  E:\AudioSourceOfTruthData\extracted\guitarset\guitarset_mono_mic\00_BN1-129-Eb_comp_mic.wav `
  --instrument lead_guitar `
  --require-notes `
  --write-gate-evidence
```

The report should have `"ok": true`, `"status": "ok"`, the resolved external
runtime paths under `runtime`, and a nonzero `note_count`. The validator prints
the `benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json` report path strict
`runtime-check` consumes before you schedule the larger GuitarSet or
Guitar-TECHS benchmarks.

## Benchmark Commands

```powershell
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitarset `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --algorithm qmul_hr_guitar `
  --limit 40 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_limit40_qmul_hr_guitar.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm qmul_hr_guitar `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_qmul_hr_guitar.json `
  --progress
```

Local benchmark evidence from 2026-07-09:

- `benchmarks/guitar/gt_runs/guitarset_mic_limit40_qmul_hr_guitar.json`:
  40/40 cases OK, F1 0.879537, precision 0.865792, recall 0.893725.
- `benchmarks/guitar/gt_runs/guitar_techs_directinput_qmul_hr_guitar.json`:
  104/104 cases OK, F1 0.861273, precision 0.907608, recall 0.819439.
- Summary: `benchmarks/guitar/qmul_hr_guitar_eval.md`.
