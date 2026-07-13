# GuitarSet key evaluation

Date: 2026-07-07

Purpose: evaluate the deterministic ingest key detector from T3.8a against
GuitarSet `key_mode` annotations now exposed by the dataset adapter.

Detector: feedpak writer's Krumhansl-Schmuckler note-profile pass.

Ground truth: GuitarSet `key_mode`, evaluated by pitch class + mode. Enharmonic
spellings such as `D#` and `Eb` count as the same pitch class; spelling accuracy
is reported separately.

## Full GuitarSet mic result

Raw report: `gt_runs/guitarset_key_mic_full.json`

| Cases | Key+mode accuracy | Pitch-class accuracy | Mode accuracy | Spelling accuracy | No prediction |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 360 | 0.658333 | 0.694444 | 0.777778 | 0.527778 | 0 |

By style:

| Style | Cases | Key+mode accuracy | Pitch-class accuracy | Mode accuracy |
| --- | ---: | ---: | ---: | ---: |
| comp | 180 | 0.805556 | 0.811111 | 0.844444 |
| solo | 180 | 0.511111 | 0.577778 | 0.711111 |

Top confusions:

| Count | Reference | Prediction |
| ---: | --- | --- |
| 7 | `Ab:minor` | `B:major` |
| 6 | `Eb:major` | `G:minor` |
| 6 | `G:minor` | `Bb:major` |
| 4 | `Eb:minor` | `F#:major` |
| 4 | `C#:major` | `F:minor` |
| 4 | `C:major` | `G:major` |
| 4 | `Bb:major` | `F:major` |
| 4 | `C:major` | `C:minor` |

## Fast-5 smoke

Raw report: `gt_runs/guitarset_key_fast5.json`

| Cases | Key+mode accuracy | Pitch-class accuracy | Mode accuracy | Spelling accuracy |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 0.800000 | 1.000000 | 0.800000 | 0.800000 |

## Commands

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\evaluate_guitarset_key.py `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_yourmt3_fast5_cases.txt `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_key_fast5.json

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\evaluate_guitarset_key.py `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_key_mic_full.json
```

## Interpretation

The deterministic note-profile key pass is strong enough for a visible T3.8a
HUD/key baseline, especially on chordal comp cases, but solo lines expose its
limits. This gives the later audio-based key/chord work a concrete baseline to
beat instead of relying on anecdotal pack checks.
