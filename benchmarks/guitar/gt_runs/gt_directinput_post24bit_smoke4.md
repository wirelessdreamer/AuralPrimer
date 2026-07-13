# Guitar-TECHS directinput post-24-bit smoke

Date: 2026-07-07

Purpose: verify the T3.7 24-bit WAV reader fix against real Guitar-TECHS
directinput audio. The old `gt_directinput_full.json` reported F1 0.000 for
both algorithms because 24-bit DI WAVs decoded to empty audio. The current
reader decodes 24-bit mono/stereo WAVs, and these smoke runs produce nonzero
predictions on the shortest directinput cases.

Case list: `directinput_post24bit_smoke_cases.txt`

## Results

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 4 | 0.297994 | 0.317073 | 0.281081 | 104 | 224 | 266 | 13.8761 s |
| `melodic_combined` | 4 | 0.289348 | 0.351351 | 0.245946 | 91 | 168 | 279 | 13.8375 s |

Per-case highlights:

- `guitar_techs:P1_chords:midi_Set1_aug:directinput`: F1 0.280
  (`melodic_combined_guitar`) / 0.301 (`melodic_combined`)
- `guitar_techs:P3_music:midi_08:directinput`: F1 0.408
  (`melodic_combined_guitar`) / 0.398 (`melodic_combined`)

## Full-run note

The all-104 directinput sweep was attempted after the fix but did not finish
within a 30-minute timeout. That is expected to be much slower than the stale
report: the old path was benchmarking empty decoded audio. Several DI files
are 200-550 seconds long, so the full post-fix run should be scheduled in
chunks or left running without the short command timeout.

Commands:

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm melodic_combined_guitar `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\directinput_post24bit_smoke_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\gt_directinput_post24bit_smoke4_guitar.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm melodic_combined `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\directinput_post24bit_smoke_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\gt_directinput_post24bit_smoke4_combined.json `
  --progress
```
