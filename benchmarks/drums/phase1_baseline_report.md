# Drums Phase 1 — Baseline (measurement only)

Representative drum-transcription baseline on **E-GMD** (Expanded Groove MIDI
Dataset v1.0.0, CC BY 4.0). This report is measurement only — no transcription
algorithm was changed.

## What was measured and why

The prior baseline (`gt_runs/egmd_baseline_20.json`) used `--limit 20`, and the
E-GMD adapter iterates the metadata CSV in file order, so all 20 cases were the
**same groove** (funk/groove1), **same drummer** (drummer1), **same tempo**
(138 bpm), recorded against 20 kits — 20 variants of one performance, and the
current production default engine wasn't even in it.

This run replaces that with a **stratified sample** and adds two diagnostics the
old benchmark lacked: **per-drum-class** P/R/F and **onset-only (class-agnostic)**
scoring.

### Sample

- Script: `gt_runs/stratified_egmd.py` (deterministic; no randomness). Selects a
  round-robin sample across distinct **(style-family, drummer, bpm-bucket)**
  strata, rotating the acoustic kit so the sample spans many kits.
- Manifest run here: `gt_runs/stratified_sample_test_30.json`
  (`--size 30 --max-duration 45`, split=test). A fuller 40-case manifest without
  the duration cap is also emitted: `gt_runs/stratified_sample_test_40.json`.
- **30 cases → 30 distinct (style,drummer,bpm) strata, 18 full styles, 30 kits,
  5 of 6 drummers, 19 distinct BPMs.** (Capping clips at 45 s to bound runtime
  dropped drummer9 and the fast-tempo band `bpm≥150`; the 40-case manifest keeps
  all 6 drummers.) Contrast with the old baseline: 1 style / 1 drummer / 1 bpm.

### Run parameters

`aural_ingest gt-benchmark --dataset egmd --split test --tolerance-ms 50
--pitch-tolerance-semitones 0 --case-id-file stratified_sample_test_30.json`,
one invocation per engine. Class-aware scoring buckets predictions by drum class
(a kick predicted at a snare's time is **not** a match). Total wall-clock for the
6-engine sweep: **~13 min**.

### Engine scope (bounding)

The full 11-engine sweep on this sample was projected to exceed the ~60 min
wall-clock bound under concurrent-agent CPU contention (an initial all-11 attempt
took ~5 min for a *single* case). Per the task's bounding rule it was reduced to
the **6 most-relevant engines**: the production default + the prior baseline's
top-4 + `spectral_flux_multiband`. Engines **not** run this pass:
`adaptive_beat_grid_multilabel`, `librosa_superflux_dense`, `dsp_spectral_flux`,
`hybrid_kick_grid`, `aural_onset` (all registered; deferred to a later pass).

## Results

### Overall (per engine, micro-averaged, sorted by F1)

| Engine | F1 | Precision | Recall | TP | FP | FN | mean rt (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `adaptive_beat_grid` | **0.284** | 0.366 | 0.232 | 906 | 1566 | 2998 | 1.8 |
| `beat_conditioned_multiband_decoder` *(DEFAULT)* | **0.284** | 0.392 | 0.222 | 867 | 1343 | 3037 | 6.4 |
| `spectral_flux_multiband` | 0.236 | 0.273 | 0.208 | 811 | 2158 | 3093 | 4.8 |
| `dsp_bandpass_improved` | 0.232 | 0.280 | 0.198 | 773 | 1986 | 3131 | 3.8 |
| `librosa_superflux` | 0.171 | 0.343 | 0.114 | 443 | 849 | 3461 | 1.4 |
| `combined_filter` | 0.123 | 0.248 | 0.082 | 319 | 966 | 3585 | 7.8 |

Onset-match MAE for the matched events is tight for the stronger engines
(~16–19 ms; `librosa_superflux` 26 ms), so timing jitter is **not** the problem —
coverage and class are.

Note the reversal vs. the old single-groove baseline: `combined_filter`, which
looked strongest there, is **last** here (F1 0.123), and the fast
`adaptive_beat_grid` ties the heavier production default.

### Per-class F1 (5-class: kick / snare / hi_hat / toms / cymbals)

| Engine | kick | snare | hi_hat | toms | cymbals |
|---|---:|---:|---:|---:|---:|
| `beat_conditioned_multiband_decoder` *(DEFAULT)* | 0.400 | 0.376 | 0.305 | **0.000** | **0.000** |
| `adaptive_beat_grid` *(best overall)* | 0.509 | 0.310 | 0.267 | **0.000** | 0.061 |
| `spectral_flux_multiband` | 0.436 | 0.336 | 0.108 | 0.000 | 0.216 |
| `dsp_bandpass_improved` | 0.501 | 0.250 | 0.162 | 0.127 | 0.128 |
| `librosa_superflux` | 0.013 | 0.379 | 0.000 | 0.032 | 0.000 |
| `combined_filter` | 0.356 | 0.135 | 0.009 | 0.028 | 0.143 |

Per-class detail for the two leaders (support = reference-event count):

**`beat_conditioned_multiband_decoder` (DEFAULT)**

| class | F1 | P | R | support | tp | fp | fn |
|---|---:|---:|---:|---:|---:|---:|---:|
| kick | 0.400 | 0.661 | 0.286 | 751 | 215 | 110 | 536 |
| snare | 0.376 | 0.420 | 0.340 | 1083 | 368 | 508 | 715 |
| hi_hat | 0.305 | 0.281 | 0.334 | 851 | 284 | 725 | 567 |
| toms | 0.000 | 0.000 | 0.000 | 190 | 0 | 0 | 190 |
| cymbals | 0.000 | 0.000 | 0.000 | 367 | 0 | 0 | 367 |

**`adaptive_beat_grid` (best overall F1)**

| class | F1 | P | R | support | tp | fp | fn |
|---|---:|---:|---:|---:|---:|---:|---:|
| kick | 0.509 | 0.497 | 0.522 | 751 | 392 | 397 | 359 |
| snare | 0.310 | 0.407 | 0.250 | 1083 | 271 | 394 | 812 |
| hi_hat | 0.267 | 0.252 | 0.284 | 851 | 242 | 718 | 609 |
| toms | 0.000 | 0.000 | 0.000 | 190 | 0 | 0 | 190 |
| cymbals | 0.061 | 0.224 | 0.035 | 367 | 13 | 45 | 354 |

### Onset-only vs. exact-class (production default)

Same predictions, scored two ways over the 30-case sample:

| Scoring | Precision | Recall | F1 |
|---|---:|---:|---:|
| Class-aware (exact drum class) | 0.392 | 0.222 | **0.284** |
| Onset-only (class-agnostic) | 0.699 | 0.396 | **0.505** |

Raw: `gt_runs/egmd_stratified_30_default_onset_vs_class.json`.

## Interpretation

**(a) Which engine is actually best on representative data?** It's a tie at the
top: `adaptive_beat_grid` and the production default
`beat_conditioned_multiband_decoder` both land at **F1 0.284**. The default has
higher precision (0.392 vs 0.366); `adaptive_beat_grid` has higher recall and is
~3.5× faster (1.8 s vs 6.4 s/case). No engine is good in absolute terms — the
whole field sits at F1 0.12–0.28 on real grooves, far below the single-groove
baseline's flattering numbers.

**(b) Detection or classification bottleneck?** **Both, with classification the
larger *removable* gap.** For the default, dropping class constraints lifts F1
from 0.284 → 0.505 (~+78%), so a large share of detected onsets are being put on
the wrong drum. But onset-only recall is still only 0.396 — even ignoring class,
~60% of onsets are missed — so raw detection is genuinely weak too, especially on
dense fills.

**(c) Which classes collapse worst?** **Toms and cymbals collapse completely.**
The default scores **exactly 0.000** on both (0 true positives across 190 tom and
367 cymbal reference events); `adaptive_beat_grid` manages a trace of cymbals
(13 TP) and still 0 toms. Only `dsp_bandpass_improved` registers any toms at all
(F1 0.127). Every engine is effectively a **kick + snare (+ partial hi-hat)**
detector; the tom/cymbal vocabulary that distinguishes a real kit from a
kick/snare machine is absent. hi_hat is mediocre-but-present (default F1 0.305).

**(d) Does the production default beat the alternatives?** No, but it isn't beaten
either — it's **statistically tied for first** and leads on precision, so keeping
it as default is defensible. The load-bearing finding is not the ranking but that
**the default's tom and cymbal recall are zero** and its onset recall is ~0.40;
the highest-leverage work is (1) recovering tom/cymbal classification and (2)
lifting onset recall on dense material, not swapping between these near-equivalent
heuristic engines.

## Artifacts

- Sampler: `gt_runs/stratified_egmd.py`
- Sample manifests: `gt_runs/stratified_sample_test_30.json` (run here),
  `gt_runs/stratified_sample_test_40.json` (fuller, all 6 drummers)
- Raw per-engine runs: `gt_runs/per_engine/egmd_stratified_30_<engine>.json` (×6)
- Combined overall + per-class: `gt_runs/egmd_stratified_30_combined.json`
- Onset-only reconciliation (default): `gt_runs/egmd_stratified_30_default_onset_vs_class.json`
- New code (additive): `case_ids`/`--case-id-file`/`--style-filter` filter in the
  E-GMD adapter + CLI; per-5-class scoring in `ground_truth_benchmark.py`. Tests:
  `python/ingest/tests/test_egmd_stratified_and_perclass.py`.

### Reproduce

```
# 1. Build the stratified sample (deterministic)
python benchmarks/drums/gt_runs/stratified_egmd.py \
  --corpus-root "E:\AudioSourceOfTruthData\extracted\e_gmd" \
  --size 30 --max-duration 45 \
  --output benchmarks/drums/gt_runs/stratified_sample_test_30.json

# 2. Benchmark an engine on the sample (repeat per engine)
aural_ingest gt-benchmark --dataset egmd \
  --corpus-root "E:\AudioSourceOfTruthData\extracted\e_gmd" \
  --algorithm beat_conditioned_multiband_decoder \
  --split test --case-id-file benchmarks/drums/gt_runs/stratified_sample_test_30.json \
  --tolerance-ms 50 --pitch-tolerance-semitones 0 \
  --output benchmarks/drums/gt_runs/per_engine/egmd_stratified_30_beat_conditioned_multiband_decoder.json
```
