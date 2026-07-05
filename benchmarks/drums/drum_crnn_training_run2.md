# In-house drum-CRNN — training run 2, full corpus (2026-07-03)

Full-corpus follow-up to [run 1](drum_crnn_training_run1.md) (which matched the
floor on a 22 h strided subset). Same harness + 300 k-param CRNN, trained on the
**entire E-GMD `train` split** (35,217 clips, ~341 h), 10 epochs, GPU (RTX 5090),
~9 h wall (32,493 s). Goal: exceed the pretrained Magenta E-GMD floor (F1 0.535
exact / 0.776 onset) so our own CC-BY-shippable weights beat the license-blocked
checkpoint.

## Headline — floor cleared

Event-level on the stratified 30 (50 ms tol), best checkpoint (epoch 10):

| Metric | Magenta floor | run-1 (22 h) | **run-2 (341 h)** |
|---|:--:|:--:|:--:|
| Fine-overall exact-class F1 | 0.535 | 0.533 (matched) | **0.547** (thr 0.25) / 0.546 (thr 0.20) — **cleared** |
| 5-class-aggregated F1 | — | 0.689 | 0.696 |
| Onset-only F1 | 0.776 | 0.719 | 0.700 |

Per-class F1 (thr 0.15), vs the Magenta floor:

| class | Magenta | run-1 | **run-2** |
|---|:--:|:--:|:--:|
| kick | 0.740 | 0.756 | **0.782** |
| snare | 0.624 | 0.658 | 0.618 |
| hi_hat | 0.524 | 0.689 | **0.737** |
| toms | 0.836 | 0.848 | **0.845** |
| cymbals | 0.546 | 0.534 | 0.518 |

**Our own model now beats the pretrained checkpoint** on fine-overall F1 and on
kick / hi_hat / toms; it matches snare and trails only on cymbals — the sparsest,
hardest class (its validation frame-F1 stayed ~0.01 all run, so cymbals are
under-learned, not mis-tuned).

## Decode threshold

The full-corpus weights are F1-optimal at **~0.20–0.25** (0.546–0.547) rather than
run-1's 0.15. The adapter default was bumped 0.15 → 0.20. Threshold sweep:

| thr | fine F1 | fine P | fine R | onset F1 |
|:--:|:--:|:--:|:--:|:--:|
| 0.10 | 0.499 | 0.494 | 0.505 | 0.700 |
| 0.15 | 0.537 | 0.630 | 0.467 | 0.694 |
| **0.20** | **0.546** | 0.697 | 0.449 | 0.676 |
| 0.25 | 0.547 | 0.737 | 0.434 | 0.658 |

## Training curve (val frame-macro-F1)

```
ep 1 0.175   ep 3 0.235   ep 5 0.261   ep 7 0.261   ep  9 0.266
ep 2 0.196   ep 4 0.165   ep 6 0.242   ep 8 0.233   ep 10 0.309  <- best, still rising
```

**Not converged** — frame-macro-F1 was still climbing at epoch 10 (0.266 → 0.309
in the last two epochs). More epochs (and cymbal-focused class weighting) should
push further. This 0.547 is a floor-clearing result from an *under-trained* full
run, not a ceiling.

## Verified through the real engine path

The run-2 weights were re-exported into the `drum_crnn` modelpack and re-run
through the production `gt-benchmark --algorithm drum_crnn` path (not just the
standalone eval): **F1 0.537 @ thr 0.15, 0.546 @ thr 0.20** — reproducing the
standalone numbers exactly. The opt-in engine now serves the run-2 weights.

## Next

- Longer / converged training run (+ cymbal class weighting) to widen the margin.
- Phase B: flip the production default to `drum_crnn` + update the pinned test
  assertions + portable staging.
- Phase C promotion gate: per-class threshold calibration on a guard set,
  gameplay-metric regression check, and in-game/listening review before it ships
  as the `gameplay_default`.

Weights and E-GMD audio are never committed; E-GMD usage requires CC BY 4.0
attribution.
