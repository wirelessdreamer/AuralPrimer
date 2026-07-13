# GuitarSet chord evaluation

Date: 2026-07-07

Purpose: establish a T3.8b baseline for chord labels now that GuitarSet chord
ground truth is available through the dataset adapter.

Detector: `measure_note_profile_chords_v1`, a deterministic note-profile chord
baseline used to populate feedpak `harmony.json` events when MIDI notes are
available.

Ground truth: GuitarSet `chord` annotations, `mireval` source.

Important scope note: this benchmark uses the GuitarSet reference chord segment
boundaries and evaluates the inferred chord label inside each segment. It
measures chord-label quality before full chord segmentation/audio-model work.

## Full GuitarSet mic result

Raw report: `gt_runs/guitarset_chords_mireval_full.json`

| Cases | Events | Scored | No prediction | Root+quality accuracy | Root accuracy | Quality accuracy |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 360 | 4320 | 4251 | 69 | 0.266290 | 0.529758 | 0.288403 |

By style:

| Style | Events | Root+quality accuracy | Root accuracy | Quality accuracy |
| --- | ---: | ---: | ---: | ---: |
| comp | 2146 | 0.453402 | 0.780988 | 0.455732 |
| solo | 2105 | 0.075534 | 0.273634 | 0.117815 |

Top root confusions:

| Count | Reference | Prediction |
| ---: | --- | --- |
| 53 | `G` | `C` |
| 47 | `F` | `C` |
| 43 | `G` | `E` |
| 40 | `B` | `E` |
| 40 | `A#` | `Eb` |
| 39 | `G` | `D` |
| 36 | `E` | `C#` |
| 35 | `A` | `D` |

Top quality confusions:

| Count | Reference | Prediction |
| ---: | --- | --- |
| 451 | `maj` | `maj7` |
| 378 | `maj` | `min7` |
| 271 | `maj` | `7` |
| 262 | `min` | `min7` |
| 239 | `maj` | `sus4` |
| 191 | `maj` | `sus2` |

## Fast-5 smoke

Raw report: `gt_runs/guitarset_chords_mireval_fast5.json`

| Cases | Events | Root+quality accuracy | Root accuracy | Quality accuracy |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 30 | 0.333333 | 0.766667 | 0.333333 |

## Feedpak Output

`write_feedpak()` now writes non-empty `harmony.json` chord events when notes
are present and a key can be inferred. The event objects are schema-valid and
include:

- `t`
- `duration`
- `root`
- `quality`
- `rn`
- `bass`
- `confidence`
- `score`
- `method`

This is a baseline, not a replacement for the planned madmom/BTC-style audio
chord model. The current venv did not have `madmom` importable during this
pass, so this deterministic path is the locally verifiable T3.8b floor.

## Commands

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\evaluate_guitarset_chords.py `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --source mireval `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_yourmt3_fast5_cases.txt `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_chords_mireval_fast5.json

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\evaluate_guitarset_chords.py `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --source mireval `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_chords_mireval_full.json
```
