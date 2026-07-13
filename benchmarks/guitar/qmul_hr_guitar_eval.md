# QMUL high-resolution guitar benchmarks (2026-07-09)

> **Result: strong research candidate, still external-runtime gated.**
> The public `hf_midi_transcription` guitar runtime with `guitar-fl.pth`
> substantially beats the current GuitarSet and Guitar-TECHS benchmark
> baselines on note transcription quality. Keep it research-only until license,
> packaging, and gameplay/listening policy are reviewed.

## Runtime

- Code: `D:\AuralPrimer\.external\qmul-hf-midi\hf_midi_transcription`
- Checkpoint: `D:\AuralPrimer\.external\qmul-hf-midi\checkpoints\guitar-fl.pth`
- Checkpoint SHA-256:
  `50d93dba89bdd3401849bc735614478e83d9f46d21fa3f71d8aca5acc0a52028`
- Wrapper: `python/ingest/scripts/run_qmul_hf_midi.py`

## GuitarSet mic limit-40

Command:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = 'D:\AuralPrimer\python\ingest\src'
$env:AURAL_QMUL_GUITAR_PYTHON = 'D:\AuralPrimer\.external\qmul-hf-midi\.venv\Scripts\python.exe'
$env:AURAL_QMUL_GUITAR_REPO = 'D:\AuralPrimer\.external\qmul-hf-midi\hf_midi_transcription'
$env:AURAL_QMUL_GUITAR_COMMAND = '{python_q} D:\AuralPrimer\python\ingest\scripts\run_qmul_hf_midi.py --audio {wav_path_q} --out-midi {out_midi_q} --instrument guitar --checkpoint D:\AuralPrimer\.external\qmul-hf-midi\checkpoints\guitar-fl.pth --device cpu --batch-size 8'
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
```

| Engine | F1 | P / R | Onset MAE | Mean runtime |
|---|---:|---:|---:|---:|
| `qmul_hr_guitar` | **0.880** | 0.866 / 0.894 | 6.9 ms | 14.693 s |
| `yourmt3_guitar` | 0.688 | 0.723 / 0.657 | 7.8 ms | 12.530 s |
| `melodic_combined_guitar` | 0.227 | 0.301 / 0.182 | 20.3 ms | 13.422 s |
| `melodic_combined` | 0.222 | 0.345 / 0.163 | 19.4 ms | 13.319 s |

QMUL buckets: comp F1 0.869, solo F1 0.918.

Raw JSON: `benchmarks/guitar/gt_runs/guitarset_mic_limit40_qmul_hr_guitar.json`.

## Guitar-TECHS directinput full 104

Command:

```powershell
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

| Engine | F1 | P / R | Onset MAE | Mean runtime |
|---|---:|---:|---:|---:|
| `qmul_hr_guitar` | **0.861** | 0.908 / 0.819 | 25.9 ms | 42.557 s |
| `melodic_combined_guitar` | 0.347 | 0.288 / 0.436 | 21.8 ms | 107.031 s |
| `yourmt3_guitar` | 0.287 | 0.393 / 0.226 | 27.7 ms | 71.195 s |

QMUL category buckets:

| Chords | Music | Scales | Single notes | Techniques |
|---:|---:|---:|---:|---:|
| 0.853 | 0.710 | 0.937 | 0.984 | 0.648 |

Raw JSON: `benchmarks/guitar/gt_runs/guitar_techs_directinput_qmul_hr_guitar.json`.

## Interpretation

QMUL is the first evaluated guitar candidate in this plan that is strongly
positive on both GuitarSet and the full Guitar-TECHS direct-input suite. It
also fixes the prior broad-promotion problem with YourMT3 on Guitar-TECHS.

Remaining caveats:

- The runtime is still external and heavy.
- The public package and checkpoint need final license/shipping review before
  any bundled/default route.
- Guitar-TECHS music case `P3_music:midi_12` failed completely in this run,
  so a gameplay/listening pass is still required before profile promotion.
