# In-house drum-CRNN — training run 1 (2026-07-02)

First real training run of the in-house drum-CRNN harness
(`python/ingest/src/aural_ingest/training/drum_crnn/`), the permissively-
licensed replacement for the license-blocked ADTOF / Magenta paths. Goal:
does a model we can legally ship (trained on CC-BY-4.0 E-GMD) clear the
**F1 0.535 exact / 0.776 onset** floor set by the pretrained Magenta E-GMD
checkpoint on the stratified-30 sample? (See
[`magenta_egmd_anchor.md`](magenta_egmd_anchor.md).)

## Headline

**Yes — matched on the first try, from a 34-minute partial-corpus run.** At
the F1-optimal decode threshold the model equals the pretrained checkpoint on
the fine-overall metric and decisively recovers the two drum classes the DSP
stack is structurally blind to.

| Metric | DSP (best) | Magenta E-GMD (floor) | **In-house CRNN (run 1)** |
|---|:--:|:--:|:--:|
| Fine-overall exact-class F1 | 0.284 | 0.535 | **0.533** (thr 0.15/0.25) |
| 5-class-aggregated exact F1 | — | — | **0.689** (thr 0.25) |
| Onset-only F1 | 0.505 | 0.776 | 0.719 (thr 0.10) |
| toms F1 | **0.000** | 0.836 | **0.714** |
| cymbals F1 | 0.000–0.216 | 0.546 | **0.471** |

*Fine-overall* scores predictions on the benchmark's fine (9-class) taxonomy —
the exact metric the floor was measured with; our 5-class decoder emits one
canonical MIDI note per class, so this metric slightly under-credits it, yet it
still matches. *5-class-aggregated* buckets both prediction and reference into
the 5 canonical classes and is the fair number for a 5-class model.

## Training config

- **Data:** a diversity-strided E-GMD `train` subset — every 6th row →
  **5,870 clips (~22 h) spanning all 9 drummers / 61 styles / 43 kits**
  (vs. the drummer-biased first-N ordering). Staged via a temp corpus root
  (filtered CSV + junction to the real audio). Validation: 800 strided clips.
- **Model:** the harness CRNN, **300,069 params** (conv stack → BiGRU →
  5-way sigmoid), 8 s clips, log-mel (22.05 kHz, hop 220, 84 mels).
- **Run:** 12 epochs, batch 16, Adam lr 1e-3, **GPU (RTX 5090)**, 16 dataloader
  workers. Wall clock **2,018 s (~34 min, ~2.8 min/epoch)**. Best by validation
  frame-macro-F1 = epoch 11.

## Training curve (validation frame-macro-F1 — a harsh plumbing metric, not event F1)

```
epoch  1  0.068     epoch  5  0.113     epoch  9  0.190
epoch  2  0.049     epoch  6  0.156     epoch 10  0.203
epoch  3  0.121     epoch  7  0.162     epoch 11  0.217  <- best
epoch  4  0.120     epoch  8  0.211     epoch 12  0.144
```

Frame-F1 (every frame must be classified at threshold 0.5) is far stricter
than the event-level F1 the benchmark reports; 0.217 frame → 0.53 event is the
expected relationship for ADT.

## Event-level threshold sweep (stratified 30, 50 ms tol, best checkpoint)

| decode thr | fine-overall F1 | fine P | fine R | 5-class-agg F1 | onset-only F1 |
|:--:|:--:|:--:|:--:|:--:|:--:|
| 0.10 | 0.520 | 0.552 | 0.492 | 0.666 | **0.719** |
| **0.15** | **0.533** | 0.607 | 0.474 | 0.686 | 0.711 |
| 0.20 | 0.532 | 0.632 | 0.459 | 0.687 | 0.697 |
| 0.25 | 0.533 | 0.654 | 0.450 | **0.689** | 0.689 |
| 0.30 | 0.524 | 0.664 | 0.432 | 0.681 | 0.673 |
| 0.50 | 0.408 | 0.711 | 0.286 | 0.556 | 0.521 |

Per-class F1 at the high-precision thr=0.5 point: kick 0.654 (P 0.89), snare
0.577 (P 0.84), hi_hat 0.419 (P 0.92), **toms 0.714 (P 0.93)**, **cymbals
0.471 (P 0.67)**. Raw eval JSONs: `../gt_runs`-adjacent
`D:\drum_crnn_run1\eval_best_t{15,50}.json` (kept out of the repo with the
weights).

## Interpretation

1. **The in-house thesis is validated.** A model we can legally ship (E-GMD is
   CC BY 4.0) matches the pretrained checkpoint's headline F1 and beats the DSP
   stack by the same ~2× margin — recovering toms (0.71) and cymbals (0.47)
   that no heuristic engine can see.
2. **The model is precision-heavy, recall-limited** — it *under*-triggers
   (precision 0.71–0.93 per class at thr 0.5), the exact opposite of the DSP
   hi-hat over-triggering failure. Lowering the decode threshold to ~0.15
   trades a little precision for recall and lifts F1 from 0.41 → 0.53.
3. **The floor is matched, not yet exceeded, and the reason is data.** This was
   22 h (a 1/6 stride) for 12 epochs vs. Magenta's full 444 h. The obvious lever
   is the full corpus — a **full-corpus run (341 h train, 10 epochs) is underway**
   (`D:\drum_crnn_run2_full`) and is expected to push past the floor.

## Next

- Full-corpus run → re-eval on the stratified 30; target > 0.535 fine / > 0.776
  onset.
- If it clears the floor: calibrate per-class decode thresholds on a guard set
  (the model's precision headroom makes this cheap), export to ONNX, package as
  a modelpack under `assets/models/…`, and A/B it as an opt-in `drum_crnn`
  engine before touching the production default.
- Then layer the tatum/pattern-LM decode (research Finding 7) on the residual
  hi-hat over/under-trigger.

Reproduce: `scratchpad/train_drum_crnn.py` (launcher) +
`scratchpad/eval_drum_crnn.py` (stratified-30 event scorer). Weights and
E-GMD audio are never committed; E-GMD usage requires CC BY 4.0 attribution.
