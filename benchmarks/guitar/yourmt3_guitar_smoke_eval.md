# YourMT3 guitar smoke evaluation

Date: 2026-07-07

Purpose: verify the T3.7.1 YourMT3 guitar benchmark adapter through the real
`gt-benchmark` path before scheduling larger GuitarSet / Guitar-TECHS sweeps.

Adapter: `yourmt3_guitar`

Modelpack: `assets/models/yourmt3/hf-main-20260325`

## Results

### GuitarSet mic fast-5

Case list: `gt_runs/guitarset_mic_yourmt3_fast5_cases.txt`

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | 5 | 0.708296 | 0.778431 | 0.649755 | 397 | 113 | 214 | 0.008441 s | 7.2458 s |
| `melodic_combined` | 5 | 0.219457 | 0.355311 | 0.158756 | 97 | 176 | 514 | 0.018916 s | 5.9582 s |
| `melodic_combined_guitar` | 5 | 0.219342 | 0.280612 | 0.180033 | 110 | 282 | 501 | 0.025404 s | 5.9585 s |

Raw reports:

- `gt_runs/guitarset_mic_yourmt3_fast5.json`
- `gt_runs/guitarset_mic_fast5_baselines.json`

### GuitarSet mic limit-40

Selection: first 40 GuitarSet mic cases through the real `gt-benchmark` path
with the same deterministic dataset order for all algorithms.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | 40 | 0.688366 | 0.723019 | 0.656883 | 5192 | 1989 | 2712 | 0.007797 s | 12.5298 s |
| `melodic_combined_guitar` | 40 | 0.227004 | 0.301066 | 0.182186 | 1440 | 3343 | 6464 | 0.020283 s | 13.4216 s |
| `melodic_combined` | 40 | 0.221650 | 0.344726 | 0.163335 | 1291 | 2454 | 6613 | 0.019391 s | 13.3186 s |

Style buckets:

| Algorithm | Style | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | comp | 20 | 0.675793 | 0.737199 | 0.623831 | 3801 | 1355 | 2292 |
| `melodic_combined_guitar` | comp | 20 | 0.106329 | 0.164941 | 0.078451 | 478 | 2420 | 5615 |
| `melodic_combined` | comp | 20 | 0.096275 | 0.182989 | 0.065321 | 398 | 1777 | 5695 |
| `yourmt3_guitar` | solo | 20 | 0.725235 | 0.686914 | 0.768084 | 1391 | 634 | 420 |
| `melodic_combined_guitar` | solo | 20 | 0.520563 | 0.510345 | 0.531198 | 962 | 923 | 849 |
| `melodic_combined` | solo | 20 | 0.528246 | 0.568790 | 0.493098 | 893 | 677 | 918 |

Raw report:

- `gt_runs/guitarset_mic_limit40_yourmt3_vs_baselines.json`

### Guitar-TECHS directinput smoke-4

Case list: `gt_runs/directinput_post24bit_smoke_cases.txt`

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | 4 | 0.523726 | 0.748744 | 0.402703 | 149 | 50 | 221 | 0.019191 s | 15.5703 s |
| `melodic_combined_guitar` | 4 | 0.297994 | 0.317073 | 0.281081 | 104 | 224 | 266 | 0.021080 s | 13.8761 s |
| `melodic_combined` | 4 | 0.289348 | 0.351351 | 0.245946 | 91 | 168 | 279 | 0.023643 s | 13.8375 s |

Raw reports:

- `gt_runs/yourmt3_guitar_techs_directinput_smoke4.json`
- `gt_runs/gt_directinput_post24bit_smoke4_guitar.json`
- `gt_runs/gt_directinput_post24bit_smoke4_combined.json`

### Guitar-TECHS directinput short-20

Case list: `gt_runs/directinput_post24bit_short20_cases.txt`

Selection: the 20 shortest Guitar-TECHS directinput cases by MIDI duration,
to widen post-24-bit validation without restarting the longest full-corpus
files. This shard contains 8 augmented-chord drill cases and all 12 P3 music
cases.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | 20 | 0.438343 | 0.484816 | 0.400000 | 910 | 967 | 1365 | 0.022419 s | 20.2023 s |
| `melodic_combined_guitar` | 20 | 0.208092 | 0.210526 | 0.205714 | 468 | 1755 | 1807 | 0.020689 s | 20.6142 s |

Category buckets:

| Algorithm | Category | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `yourmt3_guitar` | chords | 8 | 0.133874 | 0.198795 | 0.100917 | 33 | 133 | 294 |
| `melodic_combined_guitar` | chords | 8 | 0.142857 | 0.099401 | 0.253823 | 83 | 752 | 244 |
| `yourmt3_guitar` | music | 12 | 0.479366 | 0.512566 | 0.450205 | 877 | 834 | 1071 |
| `melodic_combined_guitar` | music | 12 | 0.230815 | 0.277378 | 0.197639 | 385 | 1003 | 1563 |

Raw report:

- `gt_runs/guitar_techs_directinput_post24bit_short20.json`

### Guitar-TECHS directinput 80-160s shard

Selection: `--min-duration-sec 80 --max-duration-sec 160`, which currently
selects 45 P2 chord, scale, and technique cases.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 45 | 0.427664 | 0.355318 | 0.537003 | 4644 | 8426 | 4004 | 0.020290 s | 94.1623 s |
| `yourmt3_guitar` | 45 | 0.355781 | 0.503065 | 0.275208 | 2380 | 2351 | 6268 | 0.029532 s | 64.3462 s |

Category buckets:

| Algorithm | Category | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | chords | 24 | 0.172562 | 0.123046 | 0.288763 | 866 | 6172 | 2133 |
| `yourmt3_guitar` | chords | 24 | 0.085747 | 0.326711 | 0.049350 | 148 | 305 | 2851 |
| `melodic_combined_guitar` | scales | 19 | 0.654609 | 0.639467 | 0.670485 | 3746 | 2112 | 1841 |
| `yourmt3_guitar` | scales | 19 | 0.455567 | 0.530573 | 0.399141 | 2230 | 1973 | 3357 |
| `melodic_combined_guitar` | techniques | 2 | 0.271186 | 0.183908 | 0.516129 | 32 | 142 | 30 |
| `yourmt3_guitar` | techniques | 2 | 0.029197 | 0.026667 | 0.032258 | 2 | 73 | 60 |

Raw report:

- `gt_runs/guitar_techs_directinput_80_160_yourmt3_vs_baseline.json`

### Guitar-TECHS directinput 160-240s shard

Selection: `--min-duration-sec 160 --max-duration-sec 240`, which currently
selects 30 P1/P2 chord, scale, and technique cases.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 30 | 0.259134 | 0.209260 | 0.340221 | 2305 | 8710 | 4470 | 0.026660 s | 119.5184 s |
| `yourmt3_guitar` | 30 | 0.186340 | 0.272598 | 0.141550 | 959 | 2559 | 5816 | 0.027767 s | 93.4084 s |

Category buckets:

| Algorithm | Category | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | chords | 24 | 0.168325 | 0.130407 | 0.237334 | 1218 | 8122 | 3914 |
| `yourmt3_guitar` | chords | 24 | 0.136152 | 0.214316 | 0.099766 | 512 | 1877 | 4620 |
| `melodic_combined_guitar` | scales | 5 | 0.692586 | 0.698339 | 0.686928 | 1051 | 454 | 479 |
| `yourmt3_guitar` | scales | 5 | 0.345554 | 0.428433 | 0.289542 | 443 | 591 | 1087 |
| `melodic_combined_guitar` | techniques | 1 | 0.254417 | 0.211765 | 0.318584 | 36 | 134 | 77 |
| `yourmt3_guitar` | techniques | 1 | 0.038462 | 0.042105 | 0.035398 | 4 | 91 | 109 |

Raw report:

- `gt_runs/guitar_techs_directinput_160_240_yourmt3_vs_baseline.json`

### Guitar-TECHS directinput 240-360s shard

Selection: `--min-duration-sec 240 --max-duration-sec 360`, which currently
selects two P1 technique cases.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 2 | 0.316129 | 0.310127 | 0.322368 | 98 | 218 | 206 | 0.023750 s | 193.9632 s |
| `yourmt3_guitar` | 2 | 0.009615 | 0.017857 | 0.006579 | 2 | 110 | 302 | 0.035417 s | 74.7332 s |

Raw report:

- `gt_runs/guitar_techs_directinput_240_360_yourmt3_vs_baseline.json`

### Guitar-TECHS directinput >=360s shard

Selection: `--min-duration-sec 360`, which currently selects seven P1/P2
single-note and technique cases.

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Onset MAE | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 7 | 0.492916 | 0.356870 | 0.796592 | 748 | 1348 | 191 | 0.013385 s | 358.3098 s |
| `yourmt3_guitar` | 7 | 0.033920 | 0.041348 | 0.028754 | 27 | 626 | 912 | 0.029374 s | 164.7050 s |

Category buckets:

| Algorithm | Category | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | singlenotes | 2 | 0.851064 | 0.783133 | 0.931900 | 260 | 72 | 19 |
| `yourmt3_guitar` | singlenotes | 2 | 0.036630 | 0.037453 | 0.035842 | 10 | 257 | 269 |
| `melodic_combined_guitar` | techniques | 5 | 0.402640 | 0.276644 | 0.739394 | 488 | 1276 | 172 |
| `yourmt3_guitar` | techniques | 5 | 0.032505 | 0.044041 | 0.025758 | 17 | 369 | 643 |

Raw report:

- `gt_runs/guitar_techs_directinput_ge360_yourmt3_vs_baseline.json`

### Guitar-TECHS directinput full duration shards 104

Combined from:

- `gt_runs/guitar_techs_directinput_post24bit_short20.json`
- `gt_runs/guitar_techs_directinput_80_160_yourmt3_vs_baseline.json`
- `gt_runs/guitar_techs_directinput_160_240_yourmt3_vs_baseline.json`
- `gt_runs/guitar_techs_directinput_240_360_yourmt3_vs_baseline.json`
- `gt_runs/guitar_techs_directinput_ge360_yourmt3_vs_baseline.json`

| Algorithm | Cases | F1 | Precision | Recall | TP | FP | FN | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | 104 | 0.346741 | 0.287709 | 0.436249 | 8263 | 20457 | 10678 | 107.0311 s |
| `yourmt3_guitar` | 104 | 0.286806 | 0.392801 | 0.225859 | 4278 | 6613 | 14663 | 71.1950 s |

Category buckets:

| Algorithm | Category | Cases | F1 | Precision | Recall | TP | FP | FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `melodic_combined_guitar` | chords | 56 | 0.168829 | 0.125893 | 0.256207 | 2167 | 15046 | 6291 |
| `yourmt3_guitar` | chords | 56 | 0.120879 | 0.230386 | 0.081934 | 693 | 2315 | 7765 |
| `yourmt3_guitar` | music | 12 | 0.479366 | 0.512566 | 0.450205 | 877 | 834 | 1071 |
| `melodic_combined_guitar` | music | 12 | 0.230815 | 0.277378 | 0.197639 | 385 | 1003 | 1563 |
| `melodic_combined_guitar` | scales | 24 | 0.662569 | 0.651501 | 0.674020 | 4797 | 2566 | 2320 |
| `yourmt3_guitar` | scales | 24 | 0.432734 | 0.510407 | 0.375580 | 2673 | 2564 | 4444 |
| `melodic_combined_guitar` | singlenotes | 2 | 0.851064 | 0.783133 | 0.931900 | 260 | 72 | 19 |
| `yourmt3_guitar` | singlenotes | 2 | 0.036630 | 0.037453 | 0.035842 | 10 | 257 | 269 |
| `melodic_combined_guitar` | techniques | 10 | 0.367106 | 0.269802 | 0.574188 | 654 | 1770 | 485 |
| `yourmt3_guitar` | techniques | 10 | 0.027670 | 0.037425 | 0.021949 | 25 | 643 | 1114 |

Raw report:

- `gt_runs/guitar_techs_directinput_full_duration_shards_104.json`

## Notes

- The GuitarSet and Guitar-TECHS smoke slices prove the adapter loads the local
  YourMT3 checkpoint and emits melodic notes. The full Guitar-TECHS directinput
  sweep is now a negative promotion gate for broad replacement of the current
  guitar baseline.
- The GuitarSet limit-40 result is materially stronger than the fast-5 smoke
  and covers both comp and solo styles. It still does not replace a full sweep
  or listening review.
- The short-20 shard is a wider validation slice, not a full promotion gate.
  It strengthens the Guitar-TECHS music-case signal for YourMT3 but also shows
  that augmented chord drills remain weak and need separate treatment.
- `gt-benchmark` gained `--min-duration-sec` and `--max-duration-sec` so the
  full Guitar-TECHS directinput set could be swept as non-overlapping duration
  shards instead of one timeout-prone full-corpus run.
- The full 104-case Guitar-TECHS directinput aggregate is negative for
  YourMT3 overall. The current guitar baseline is materially stronger on
  chords, scales, single-note drills, and techniques; YourMT3's advantage is
  limited to the 12 P3 music cases in the short-20 shard.
- `benchmarks/guitar/validate_guitar_techs_adapter.py` now validates the local
  Guitar-TECHS adapter surface independently of transcription models. The
  current all-signal report covers 104 `directinput` cases and 104 `micamp`
  cases, 37,882 parsed MIDI reference notes total, the expected
  chords/music/scales/singlenotes/techniques buckets, and zero invalid items.
- A forced `AURAL_MT3_DEVICE=cpu` GuitarSet fast-5 run was stopped after a
  20-minute timeout before producing output. The completed numbers above used
  the default device selection path.
- Guitar-TECHS directinput results are now meaningful because the 24-bit WAV
  reader fix makes these files decode to non-empty audio.

## Commands

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'
$env:MT3_CHECKPOINT_DIR='D:\AuralPrimer\assets\models\yourmt3\hf-main-20260325'
Remove-Item Env:\AURAL_MT3_DEVICE -ErrorAction SilentlyContinue

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitarset `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --algorithm yourmt3_guitar `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_yourmt3_fast5_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_yourmt3_fast5.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitarset `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --algorithm melodic_combined `
  --algorithm melodic_combined_guitar `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_yourmt3_fast5_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_fast5_baselines.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitarset `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined `
  --algorithm melodic_combined_guitar `
  --limit 40 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitarset_mic_limit40_yourmt3_vs_baselines.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\directinput_post24bit_smoke_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\yourmt3_guitar_techs_directinput_smoke4.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined_guitar `
  --case-id-file D:\AuralPrimer\benchmarks\guitar\gt_runs\directinput_post24bit_short20_cases.txt `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_post24bit_short20.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined_guitar `
  --min-duration-sec 80 `
  --max-duration-sec 160 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_80_160_yourmt3_vs_baseline.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined_guitar `
  --min-duration-sec 160 `
  --max-duration-sec 240 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_160_240_yourmt3_vs_baseline.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined_guitar `
  --min-duration-sec 240 `
  --max-duration-sec 360 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_240_360_yourmt3_vs_baseline.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli gt-benchmark `
  --dataset guitar_techs `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitar_techs `
  --variant directinput `
  --algorithm yourmt3_guitar `
  --algorithm melodic_combined_guitar `
  --min-duration-sec 360 `
  --tolerance-ms 50 `
  --pitch-tolerance-semitones 0 `
  --output D:\AuralPrimer\benchmarks\guitar\gt_runs\guitar_techs_directinput_ge360_yourmt3_vs_baseline.json `
  --progress

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\guitar\combine_gt_shards.py `
  --label guitar_techs_directinput_full_duration_shards `
  --output benchmarks\guitar\gt_runs\guitar_techs_directinput_full_duration_shards_104.json `
  benchmarks\guitar\gt_runs\guitar_techs_directinput_post24bit_short20.json `
  benchmarks\guitar\gt_runs\guitar_techs_directinput_80_160_yourmt3_vs_baseline.json `
  benchmarks\guitar\gt_runs\guitar_techs_directinput_160_240_yourmt3_vs_baseline.json `
  benchmarks\guitar\gt_runs\guitar_techs_directinput_240_360_yourmt3_vs_baseline.json `
  benchmarks\guitar\gt_runs\guitar_techs_directinput_ge360_yourmt3_vs_baseline.json
```
