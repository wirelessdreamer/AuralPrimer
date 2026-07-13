# Bass torchcrepe evaluation

Date: 2026-07-08

Purpose: record the local evidence behind the current bass default
(`torchcrepe`) and the adapter-local 200 Hz bass `fmax` clamp.

Dataset: GuitarSet low-string proxy through `yield_low_string_cases`, using the
`hex_debleeded` audio variant. This is not a full electric-bass corpus; it is a
repeatable low-register proxy from data already on disk.

## Results

### Strict pitch, 60 cases

Raw report: `gt_runs/bass_hexdebleed_60_strict.json`

| Algorithm | Cases | Precision | Recall | F1 | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: |
| `melodic_torchcrepe` | 60 | 0.452335 | 0.140399 | 0.214286 | 2.1693 s |
| `melodic_pyin_bass_strict` | 60 | 0.183310 | 0.118056 | 0.143618 | 3.8742 s |
| `melodic_combined` | 60 | 0.139692 | 0.230374 | 0.173923 | 14.2309 s |

Torchcrepe leads this strict low-string proxy and is substantially faster than
the older combined chain. Its advantage is strongest on solo cases; comp cases
remain weak because this proxy asks a monophonic bass tracker to score against
busy chordal low-string material.

### Octave-forgiving pitch, 60 cases

Raw report: `gt_runs/bass_hexdebleed_60_octaveforgiving.json`

| Algorithm | Cases | Precision | Recall | F1 | Mean runtime |
| --- | ---: | ---: | ---: | ---: | ---: |
| `melodic_torchcrepe` | 60 | 0.565302 | 0.175121 | 0.267404 | 2.5481 s |
| `melodic_pyin_bass_strict` | 60 | 0.203000 | 0.130737 | 0.159045 | 3.7078 s |
| `melodic_combined` | 60 | 0.326254 | 0.538043 | 0.406200 | 14.1131 s |

The octave-forgiving metric rewards the denser combined chain, but that is not
the production default criterion for bass gameplay: octave mirrors produce bad
lanes even when an octave-forgiving benchmark counts them as hits.

## Bass fmax sweep

Raw log: `gt_runs/bass_hexdebleed_20_fmax_sweep.log`

| Adapter fmax | Macro F1 | Micro F1 | TP | FP | FN |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 400 Hz | 0.229 | 0.230 | 161 | 115 | 961 |
| 250 Hz | 0.220 | 0.227 | 159 | 117 | 963 |
| 200 Hz | 0.234 | 0.236 | 165 | 110 | 957 |
| 180 Hz | 0.244 | 0.231 | 157 | 78 | 965 |
| 165 Hz | 0.223 | 0.219 | 145 | 59 | 977 |

The 200 Hz clamp is the production compromise: it improves the strict
low-string micro-F1 over the 400 Hz range while preserving more recall than
the tighter 180/165 Hz sweeps. A focused regression test now pins this clamp
inside `test_melodic_torchcrepe_fmin.py`.
