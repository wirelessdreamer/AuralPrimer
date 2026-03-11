# Hybrid Research And Tracker

## Goal

Find a hybrid drum-transcription approach that improves `kick`, `snare`, and `hi-hat` first, without losing visibility into `crash`, `ride`, and tom lanes.

This document is the working research note for the current `aural_onset` + `adaptive_beat_grid` investigation. It is not a benchmark report. Use it to keep the method direction, evidence, and experiment queue coherent over time.

## Benchmark Rule

Every run must follow [PROCESS.md](PROCESS.md). Static output review is mandatory after each run. No webserver is required.

For hybrid work, the minimum review set is:

- `overall_f1_heatmap.svg`
- `kick_f1_heatmap.svg`
- `snare_f1_heatmap.svg`
- `hi_hat_f1_heatmap.svg`
- `algorithm_summary.svg`
- `core_lane_summary.svg`
- `timing_mae.svg`
- `snare_confusion_heatmap.svg`
- `hi_hat_confusion_heatmap.svg`

## Current Local Evidence

Focused comparison run:

- [runs/20260310_113817_hybrid-research-core-lanes/report.md](runs/20260310_113817_hybrid-research-core-lanes/report.md)
- [runs/20260310_113817_hybrid-research-core-lanes/report.html](runs/20260310_113817_hybrid-research-core-lanes/report.html)
- [runs/20260310_113817_hybrid-research-core-lanes/core_lane_summary.svg](runs/20260310_113817_hybrid-research-core-lanes/core_lane_summary.svg)
- [runs/20260310_113817_hybrid-research-core-lanes/kick_f1_heatmap.svg](runs/20260310_113817_hybrid-research-core-lanes/kick_f1_heatmap.svg)
- [runs/20260310_113817_hybrid-research-core-lanes/snare_f1_heatmap.svg](runs/20260310_113817_hybrid-research-core-lanes/snare_f1_heatmap.svg)
- [runs/20260310_113817_hybrid-research-core-lanes/hi_hat_f1_heatmap.svg](runs/20260310_113817_hybrid-research-core-lanes/hi_hat_f1_heatmap.svg)

Mean scores across the 10 rendered benchmark cases:

| Algorithm | Mean Overall F1 | Mean Kick F1 | Mean Snare F1 | Mean Hi-Hat F1 | Mean Core F1 | Mean Timing MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `aural_onset` | 0.203 | 0.369 | 0.252 | 0.002 | 0.208 | 28.3 ms |
| `adaptive_beat_grid` | 0.234 | 0.406 | 0.266 | 0.018 | 0.230 | 26.4 ms |
| case-wise best of the two | 0.239 | 0.416 | 0.293 | 0.018 | 0.242 | n/a |

The upper-bound row matters most. A naive two-model fusion barely changes `overall`, helps `snare` a little, helps `kick` a little, and does essentially nothing for `hi-hat`. That means the next step is not "ensemble harder." It is "change the model structure."

Implemented prototype run:

- [runs/20260310_124533_hybrid-mvp/report.md](runs/20260310_124533_hybrid-mvp/report.md)
- [runs/20260310_124533_hybrid-mvp/report.html](runs/20260310_124533_hybrid-mvp/report.html)
- [runs/20260310_124533_hybrid-mvp/core_lane_summary.svg](runs/20260310_124533_hybrid-mvp/core_lane_summary.svg)
- [runs/20260310_124533_hybrid-mvp/hi_hat_f1_heatmap.svg](runs/20260310_124533_hybrid-mvp/hi_hat_f1_heatmap.svg)
- [runs/20260310_124533_hybrid-mvp/hi_hat_confusion_heatmap.svg](runs/20260310_124533_hybrid-mvp/hi_hat_confusion_heatmap.svg)

Prototype `beat_conditioned_multiband_decoder` result:

| Algorithm | Mean Overall F1 | Mean Kick F1 | Mean Snare F1 | Mean Hi-Hat F1 | Mean Core F1 | Mean Timing MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `beat_conditioned_multiband_decoder` | 0.351 | 0.409 | 0.270 | 0.345 | 0.342 | 27.4 ms |

This clears the current `adaptive_beat_grid` baseline on `overall`, `kick`, `snare`, and `hi-hat` while staying within the timing budget. The largest visible win is that `hi_hat -> snare` collapses from `149` to `6`, though `hi_hat -> kick` is still too high and some extra `snare -> kick` / `snare -> hi_hat` confusion remains.

Adaptive kick-grid refinement:

- [runs/20260311_134907_adaptive-double-bass-fix-v2/report.md](runs/20260311_134907_adaptive-double-bass-fix-v2/report.md)
- [runs/20260311_134907_adaptive-double-bass-fix-v2/report.html](runs/20260311_134907_adaptive-double-bass-fix-v2/report.html)
- [runs/20260311_134907_adaptive-double-bass-fix-v2/kick_f1_heatmap.svg](runs/20260311_134907_adaptive-double-bass-fix-v2/kick_f1_heatmap.svg)

Retained `adaptive_beat_grid` update:

- Keep raw onset-time features instead of measuring timbre after an incorrect grid snap.
- Detect an explicit dense kick sub-grid from consistent low-band pulse intervals.
- Use that denser grid and a shorter kick-only refractory only when the low-band pulse is highly regular.

Measured effect versus the older stored adaptive baseline:

| Algorithm | Mean Overall F1 | Mean Kick F1 | Mean Snare F1 | Mean Hi-Hat F1 | Mean Timing MAE |
| --- | ---: | ---: | ---: | ---: | ---: |
| older `adaptive_beat_grid` baseline | 0.234 | 0.406 | 0.266 | 0.018 | 26.4 ms |
| refined `adaptive_beat_grid` | 0.274 | 0.449 | 0.212 | 0.104 | 25.7 ms |

Important tradeoff:

- This is a real improvement for `kick`, especially the `05_metal_double_bass_190` case, where overall F1 moved from `0.350` to `0.516`.
- The refinement gives back some mean `snare` compared with the older baseline, so it should be treated as a kick-focused improvement, not a universal adaptive win.

## Current Retry Status

Latest retained retry:

- [runs/20260310_213515_final-retry-validation/report.md](runs/20260310_213515_final-retry-validation/report.md)
- [runs/20260310_213515_final-retry-validation/report.html](runs/20260310_213515_final-retry-validation/report.html)
- [../king_in_zion_final_retry_validation.json](../king_in_zion_final_retry_validation.json)

Retained change:

- `beat_conditioned_multiband_decoder` now permits a second core hit only when both `kick` and `snare` evidence are independently strong in the same onset cluster.
- This is intentionally strict. The broader snare-biased retry improved the rendered suite but regressed the King in Zion real-audio holdout, so it was rejected.

Measured effect of the retained retry versus the prior hybrid baseline:

| Benchmark | Overall F1 | Kick F1 | Snare F1 | Hi-Hat F1 | Timing MAE |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rendered suite, prior hybrid | 0.324 | 0.425 | 0.250 | 0.299 | 27.5 ms |
| Rendered suite, retained retry | 0.327 | 0.426 | 0.254 | 0.299 | 27.5 ms |
| King in Zion holdout, prior hybrid | 0.175 | 0.250 | 0.244 | 0.124 | n/a |
| King in Zion holdout, retained retry | 0.178 | 0.255 | 0.246 | 0.124 | n/a |

Interpretation:

- The retained retry is a real improvement, but it is small.
- We are still far from the historical `~0.67` level the older codebase reached, especially on real-audio holdouts.
- King in Zion must remain a gate for future hybrid work; synthetic-suite-only wins are not good enough.

STAR note for later:

- Do not start STAR-driven model training work until the heuristic MVP is stable.
- When training starts, use STAR for pretraining and remapped lane coverage, not as the only benchmark truth set.
- Keep the rendered 10-case suite and a small manually audited real-audio holdout out of training.

## Failure Pattern Summary

Genre-level pattern from the current fixture set:

| Genre | Reading |
| --- | --- |
| `jrock` | `adaptive_beat_grid` improves `overall` and `kick`, but `aural_onset` can win on `snare` placement. `hi-hat` is still near zero. |
| `mathrock` | Hard grid assumptions become risky. `aural_onset` slightly wins the `7/8` case and stays competitive in `5/4`. |
| `metal` | `adaptive_beat_grid` helps `kick` a lot, especially double-bass, but `aural_onset` is stronger on the blast-beat snare stream. |
| `funk` | `adaptive_beat_grid` wins on `snare`, but `hi-hat` still stays very weak. |
| `blues` | Both methods are decent on `kick`; the grid helps the pocket and backbeat. |
| `pop` | Nearly a wash. Neither method solves the hats. |
| `rnb` | `adaptive_beat_grid` wins overall and on `snare`, but `aural_onset` remains useful for non-grid feel. |

Aggregated confusion totals:

| Algorithm | Key confusion pattern |
| --- | --- |
| `aural_onset` | `kick -> snare = 195`, `hi_hat -> snare = 225`, `hi_hat -> kick = 66` |
| `adaptive_beat_grid` | `kick -> snare = 133`, `hi_hat -> snare = 149`, `hi_hat -> kick = 71` |

Interpretation:

- `adaptive_beat_grid` is the better timing and kick prior.
- `aural_onset` remains useful where the groove bends away from a rigid grid.
- Both methods collapse `hi-hat` into `snare` or `kick`, so the current class decision logic is structurally wrong for hats.
- Winner-take-all lane assignment is a problem. Real drum audio often contains simultaneous `kick + hi-hat` or `snare + hi-hat` events.

## Research Synthesis

Primary-source reading that matters for this work:

| Source | Why it matters here |
| --- | --- |
| Wu et al., [A Review of Automatic Drum Transcription](https://doi.org/10.1109/TASLP.2018.2830113) | Good framing for the main approach families: onset/rule systems, NMF/template methods, and learned sequence models. Also reinforces that overlap, class imbalance, and dataset mismatch remain major failure sources. |
| Vogl et al., [Drum transcription from polyphonic music with recurrent neural networks](https://doi.org/10.1109/ICASSP.2017.7952146) | Shows that sequence-aware learned models can outperform frame-local decisions on polyphonic material. Relevant because our `kick/snare/hi-hat` errors are not independent frame errors; they are sequence errors. |
| Yeh et al., [Joint Drum Transcription and Metrical Analysis Based on Periodicity-Aware Multi-Task Learning](https://doi.org/10.1109/APSIPAASC58517.2023.10317285) | Strong evidence that metrical context should be part of the model, not just a post-hoc quantizer. This supports using beat/tatum state as a feature or latent variable instead of hard-snapping output times. |
| Ishizuka et al., [Global Structure-Aware Drum Transcription Based on Self-Attention Mechanisms](https://doi.org/10.3390/signals2030031) | Supports the idea that global structure and longer-range context help disambiguate drum classes. Important for odd meter, repeated sections, and phrase-level context. |
| Roebel et al., [Drum transcription using partially fixed non-negative matrix factorization](https://doi.org/10.1109/EUSIPCO.2015.7362590) | PFNMF is still a viable local ambiguity solver. It is especially attractive for explainable `kick/snare/hi-hat` correction in dense overlaps. |
| Foscarin et al., [STAR Drums: A Dataset for Automatic Drum Transcription](https://doi.org/10.5334/TISMIR.244) | Benchmarking and labeling quality matter. This is useful for future external evaluation and for checking whether our synthetic suite is too narrow. |
| Heyen et al., [High-Quality and Reproducible Automatic Drum Transcription from Crowdsourced Data](https://doi.org/10.3390/app13031549) | Reinforces the importance of reproducible evaluation protocols and high-quality data pipelines. This aligns with our static-report benchmark workflow. |
| Elhussein et al., [Enhanced Automatic Drum Transcription via Drum Stem Source Separation](http://arxiv.org/abs/2509.24853v1) | Emerging direction. Not a mature baseline yet, but separation-assisted ADT is worth tracking because it can improve overlap handling for `kick + hi-hat` and `snare + hi-hat`. |

Implications for this repo:

- A beat grid is useful, but literature favors metrical context as a soft conditioning signal, not as a blunt overwrite.
- Sequence-aware models keep recurring lane patterns coherent better than isolated heuristics.
- Template or factorization methods are still relevant when the problem is local overlap rather than global phrasing.
- Better data and broader evaluation matter. Synthetic rendered cases are necessary, but they are not sufficient as the long-term only benchmark.

## Proposed Novel Direction

Working name: `beat_conditioned_multiband_decoder`

This is the hybrid we should try to build, because it fits both the local evidence and the literature.

### 1. Multi-band onset proposal

Generate several candidate streams instead of one blended onset stream:

- `kick` stream: low-band transient emphasis
- `snare` stream: crack-band plus wideband transient emphasis
- `hi_hat/cymbal` stream: dedicated high-band onset function with high-decay follow-up features
- `global` stream: full-band onset support for fills and rare classes

Reason: the current blended onset logic underweights the high-frequency stream, which is exactly where the `hi-hat` evidence lives.

### 2. Soft beat and tatum lattice

Infer beat, downbeat, and tatum hypotheses, but do not hard-snap every onset.

Store for each candidate:

- raw onset time
- nearest tatum
- distance to nearest tatum
- beat phase probability
- local tempo confidence

Reason: hard snap helps `kick` in stable genres, but it hurts odd meter, blast beats, ghost notes, shuffle feel, and any off-grid nuance.

### 3. Core-lane decoder with asymmetric penalties

Decode `kick`, `snare`, and `hi-hat` first with class-specific features and class-specific penalties.

Features to use:

- `low`, `mid`, `high`, `snare_crack`
- `high_decay`, `centroid`, `zcr`, `sharpness`
- beat phase
- inter-hit interval
- neighborhood pattern, such as `kick + hat` pairs and backbeat likelihood

Penalties to emphasize:

- `hi_hat -> snare`
- `hi_hat -> kick`
- `kick -> snare`
- `snare -> kick`

Reason: the current confusion matrix shows these are the dominant business failures.

### 4. Simultaneous-hit resolver

Allow more than one class inside a short time window when the evidence supports it.

Two practical ways to do that:

- light-weight rule-based co-occurrence logic for `kick + hi_hat` and `snare + hi_hat`
- short-window PFNMF or template decomposition only on ambiguous windows

Reason: our current one-label-per-onset logic forces hats to disappear whenever a stronger `kick` or `snare` transient is nearby.

### 5. Residual timing output

After the lattice assignment, keep a residual microtiming offset instead of replacing the onset time with the grid time.

Reason: this preserves shuffle, pocket, ghost-note feel, and non-isochronous patterns while still using the rhythmic prior.

### 6. Full-lane backfill

Once core lanes are stable, resolve `crash`, `ride`, and toms from longer-decay and spectral-shape cues. Do not let full-lane decoding destabilize core-lane decoding.

## Why This Is Different

The novelty is not just "mix two existing heuristics."

The proposed hybrid combines:

- separate onset streams instead of one blended novelty curve
- a soft rhythmic lattice instead of hard quantization
- class-specific decoding for `kick/snare/hi-hat`
- explicit multi-hit handling for overlap windows
- preserved microtiming after rhythm conditioning

That combination directly targets the failure pattern we measured, especially `hi_hat -> snare` and `hi_hat -> kick`.

## Tracked Approach Queue

| Approach | Status | Why keep it | Next benchmark gate |
| --- | --- | --- | --- |
| `aural_onset` | baseline | Good off-grid sensitivity and useful snare behavior in some cases | Must stay in every shootout as the timing-sensitive baseline |
| `adaptive_beat_grid` | baseline | Best current overall and kick baseline | Must stay in every shootout as the rhythm-prior baseline |
| `beat_conditioned_multiband_decoder v0` | active | Most directly addresses current K/S/HH failures | Beat `adaptive_beat_grid` on `mean_kick_f1` and `mean_snare_f1` without worse timing by more than `2 ms` |
| `beat_conditioned_multiband_decoder v1` with soft decoding | queued | Needed if v0 still hard-collapses hats | Raise `mean_hi_hat_f1` above `0.10` and cut `hi_hat -> snare` by at least `40%` |
| local PFNMF ambiguity resolver | queued | Explainable overlap repair for `kick + hi_hat` and `snare + hi_hat` | Improve `hi_hat_f1` on dense cases without hurting `kick_f1` |
| CRNN / recurrent decoder | queued | Literature-supported sequence model for polyphonic ADT | Beat both baselines on `mean_core_f1` across all 10 cases |
| periodicity-aware multi-task model | queued | Strong fit for rhythm-conditioned decoding | Improve `metal` and `rnb` together, not just one |
| source-separation-assisted ADT | watch | Promising for overlap-heavy material | Any gain must survive the full suite, not just the metal cases |
| student-teacher / pseudo-label expansion | watch | Useful once we have a stronger teacher model | Do not start until v0 or v1 hybrid produces reliable pseudo-labels |
| external data normalization with STAR / crowdsourced sets | deferred until MVP | Needed for long-term generalization checks | Add one external benchmark slice after the core hybrid is stable |

## Immediate Experiment Order

1. Build `beat_conditioned_multiband_decoder v0` as a rule-based hybrid inside the current ingest stack.
2. Add a dedicated high-frequency onset stream and a simultaneous-hit path for `kick + hi_hat` and `snare + hi_hat`.
3. Replace hard snap with soft tatum features and microtiming residual output.
4. Re-run the full rendered suite and inspect the mandatory static charts.
5. If `hi-hat` is still weak, add the local PFNMF ambiguity resolver only on overlap windows.
6. Only after the heuristic hybrid stabilizes should we start the learned-sequence track.

## Acceptance Targets For The Next Hybrid

- Beat `adaptive_beat_grid` on `mean_kick_f1`.
- Beat `adaptive_beat_grid` on `mean_snare_f1`.
- Raise `mean_hi_hat_f1` from `0.018` to at least `0.10`.
- Reduce `hi_hat -> snare` and `hi_hat -> kick` confusions materially on the full suite.
- Keep `mean_timing_mae_ms` within `2 ms` of the current `adaptive_beat_grid` baseline.
- Keep full-lane reporting in place even while optimizing around `kick`, `snare`, and `hi-hat`.

## `spectral_flux_multiband` — Novel Algorithm (2026-03-11)

### Motivation

Deep SOTA research (2024-2026) identified three structural weaknesses in the existing algorithms:

1. **Winner-take-all onset assignment**: prevents detecting simultaneous `kick + hi-hat` / `snare + hi-hat` events.
2. **No per-band onset detection**: one blended novelty curve underweights hi-hat evidence.
3. **No hi-hat vs snare disambiguation**: the `snare_crack` band (1800-4500 Hz) is triggered by both.

### Architecture

```
                   ┌─ kick_low (35-120 Hz) ──┐    ┌─ kick_novelty ──┐
samples ─► bands ──┤  kick_high (120-200)    ├──►│  snare_novelty  ├──► peak_pick ──► clusters ──► decode
                   │  snare_mid (200-2200)   │    │  hat_novelty    │      ▲               │
                   │  snare_crack (1800-4500)│    │  full_novelty   │      │               ▼
                   │  hat_main (5000-12000)  │    └─────────────────┘  tempo/grid    multi-label
                   │  hat_air (7000-16000)   │                                      emit events
                   └─ cym (3500-10000) ──────┘
```

Three novel features bundled as one algorithm:

1. **Per-band independent peak picking**: separate `k`, `percentile`, and `min_gap` per band.
2. **Multi-label onset assembly**: 25ms clustering window allows emitting kick+hat or snare+hat simultaneously.
3. **Decay-gated hi-hat classification**: uses `high_decay_fine()` (0-5ms/5-15ms/15-30ms micro-windows) and `centroid_trajectory()` to distinguish hi-hat from snare crack and crash.

### Rendered Benchmark Results

Run: `20260311_152042_novel-spectral-flux-shootout`

| Algorithm | Mean Overall F1 | Mean Kick F1 | Mean Snare F1 | Mean Hi-Hat F1 | Mean Timing MAE |
| --- | ---: | ---: | ---: | ---: | ---: |
| `spectral_flux_multiband` | **0.334** | 0.404 | **0.280** | **0.373** | 27.2 ms |
| `beat_conditioned_multiband_decoder` | 0.329 | 0.431 | 0.256 | 0.299 | 27.2 ms |
| `adaptive_beat_grid` | 0.274 | **0.449** | 0.212 | 0.104 | **25.7 ms** |

Key win: hi-hat F1 improved from 0.299 → 0.373 (+25%).

### Real-World Regression Suite (Psalms)

Created a 3-song regression suite using human-authored MIDI references:

| Song | Reference Events | Sync Offset |
| --- | ---: | --- |
| Psalm 1 | 559 | -0.540s |
| Psalm 2 (King in Zion) | 1020 | -0.480s |
| Psalm 4 (Trouble Again) | 1175 | -0.540s |

All songs had a consistent ~0.5s offset (approximately one beat at 120 BPM) between MIDI and audio, determined by cross-correlating MIDI onset times against audio onset envelopes with 90+% match rates at best offset.

Runner script: `benchmarks/drums/run_realworld_regression.py`

### Tuning Iterations on Real-World Audio

**v1 (initial)**: Raw thresholds tuned on rendered suite.
- Psalm 1: F1=0.305, P=0.200, FP=1444 — massive false positive explosion
- Top confusions: hi-hat→snare x162, kick→snare x58

**v2 (tightened peaks)**: Raised peak-picking `k` from 1.38→1.85 (hat), 1.68→2.10 (kick), 1.72→2.15 (snare). Raised emission gates. Added crash detection.
- Psalm 1: F1=0.335, P=0.261, FP=743 — 49% FP reduction
- Top confusions: kick→snare x80, kick→crash x26

**v3 (snare guard)**: Raised snare `k=2.40`, `percentile=0.84`. Added `low_dom > snare_dom * 1.3` guard.
- Psalm 1: F1=0.341, P=0.264, FP=747 — marginal improvement

### Remaining Failure Patterns

1. **kick→snare confusion (x78 on Psalm 1, x235 on King in Zion)**: Kick transients have mid-frequency harmonics that trigger the `snare_crack` band. The snare peak picker correctly detects energy here, but classification is wrong.
2. **FP explosion on real audio**: The multi-label assembly emits too many events. Real drum stems have spectral bleed that rendered audio lacks. Need stronger joint evidence requirements.
3. **Crash vs hi-hat**: The `sustained_high` detection helps but doesn't catch all cymbals.

### Key Learnings

1. Pure-Python DFT (STFT) is prohibitively slow: O(N²) per frame made benchmark runs take 30+ minutes per song. Replaced with the proven-fast `compute_band_envelopes()` + `onset_novelty()` infrastructure. Performance went from 197s → 36s for unit tests.
2. Thresholds tuned on rendered audio transfer poorly to real-world audio. The rendered benchmark fixtures have clean separation; real drum stems have significant cross-band spectral bleed.
3. The `snare_crack` band overlaps heavily with kick harmonics. This is a fundamental issue — the 1800-4500 Hz range contains both snare wire resonance and kick attack harmonics.
4. The multi-label approach works structurally (confirmed by unit tests) but needs much tighter joint evidence gates for real audio.

### Next Steps

1. Continue tuning on the 3-song regression suite to prevent single-song overfitting.
2. Investigate a tighter kick-vs-snare spectral separator — possibly using the ratio of energy below vs above 1000 Hz within the snare band.
3. Consider a higher minimum onset strength threshold to reduce spurious detections.
4. Re-run the rendered benchmark suite after each real-world tuning iteration to prevent regression.
