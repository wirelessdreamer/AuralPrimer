# In-house drum-CRNN — training run 3 + threshold calibration (2026-07-06)

Step 2/3 of [`docs/drum-crnn-run3-plan-2026-07-06.md`](../../docs/drum-crnn-run3-plan-2026-07-06.md):
fix run-2's under-learned cymbals via per-class `pos_weight`, then calibrate
per-class decode thresholds. **Result: the pos_weight fine-tune did not
improve on run-2 and specifically failed to fix cymbals — the opposite of
its goal.** This report documents the negative result plainly, the
methodology built along the way (a reusable calibration script and per-class
threshold machinery), and what to try next.

## Headline

**Do not promote run-3. Run-2 remains the reigning in-house checkpoint.**
Success criteria from the plan (fine-overall F1 ≥ 0.57, cymbals ≥ 0.546, no
class regresses > 0.02) were **not met**. Worse, on a fair apples-to-apples
comparison (both models calibrated by the identical validation-30 sweep,
both scored on the untouched test-30), **cymbals — the whole point of this
run — regressed** rather than improved:

| metric (test-30, 50 ms, both calibrated the same way) | run-2 | run-3 |
|---|:--:|:--:|
| cymbals F1 | **0.518** | 0.464 |
| cymbals precision / recall | 0.584 / 0.466 | **0.919** / 0.311 |
| 5-class-aggregated F1 (fair metric, see caveat below) | **0.695** | 0.691 |
| Fine-overall F1 (headline metric used by run-1/run-2) | **0.532** | 0.542 |

The 5-class-aggregated gap (0.695 vs 0.691) is within noise on a 30-case
sample and does not support a promotion either way. Fine-overall nominally
favors run-3, but that metric has a known confound explained below that makes
it unreliable for judging *this specific* comparison — see "Why fine-overall
is misleading here."

## What was tried

Per [step 2 of the plan](../../docs/drum-crnn-run3-plan-2026-07-06.md#step-2--run-3-training-gpu-background-9–14-h):
fine-tuned run-2's best checkpoint (epoch 10) for up to 15 epochs with
`pos_weight="auto"` (per-class neg/pos frame-ratio estimated from a
500-file sample, capped at 25×) and `early_stop_patience=3`. Resolved
weights: `kick=19.06, snare=11.63, hi_hat=20.80, toms=25.0 (capped), cymbals=25.0 (capped)`.

Training ran 6 epochs (early-stopped), ~5.1 h wall clock (18,353 s), best at
**epoch 3** (val macro frame-F1 0.399; cymbals val frame-F1 climbed
**0.045 → 0.142 → 0.252** across epochs 1–3, vs run-2's own cymbals frame-F1
staying pinned at ≈0.01 for its entire 10-epoch run). This training-time
signal was genuinely encouraging and is why the run proceeded to calibration
rather than being abandoned early.

```
epoch 1: macro=0.326  cymbals=0.045
epoch 2: macro=0.328  cymbals=0.142
epoch 3: macro=0.399  cymbals=0.252   <- best checkpoint
epoch 4: macro=0.371  cymbals=0.211
epoch 5: macro=0.370  cymbals=0.201
epoch 6: macro=0.371  cymbals=0.112   <- early stop (patience=3 from epoch 3)
```

## Threshold calibration (new tool, worked as designed)

Built `benchmarks/drums/gt_runs/calibrate_drum_crnn_thresholds.py` (per step 3
of the plan) and a validation-split stratified-30 sample
(`stratified_sample_validation_30.json`, same params as the test-30 set) so
calibration never touches the report set. The script caches raw sigmoid
probabilities once per case, then sweeps a scalar threshold and reads off
each class's own resulting F1 independently — cheap, because `score_drum_case`
scores classes in separate buckets so they never interact.

**First finding: the plan's suggested grid (0.05–0.45) was too narrow.** Every
class was still climbing at the grid ceiling. Widened to 0.05–0.95 and found
genuine interior optima for all 5 classes between 0.75 and 0.90 — much higher
than run-2's natural range (0.15–0.35). This makes sense in hindsight: heavy
`pos_weight` training pushes the model to output higher sigmoid values more
liberally to avoid the heavily-penalized false negatives, so the "natural"
mid-range threshold no longer applies; run-3 needs a far more aggressive
filter to compensate.

Winning per-class thresholds for run-3 (validation-30):
`kick=0.85, snare=0.75, hi_hat=0.90, toms=0.90, cymbals=0.85`
(full sweep table: `drum_crnn_run3_calibration.json`).

## The controlled comparison (why the result is trustworthy, not noise)

To rule out "run-3 just generalizes worse" vs "the calibration methodology
itself is flawed," the identical calibration sweep was run on **run-2's own
checkpoint** using the same validation-30 set, then both models' calibrated
thresholds were scored on test-30 through the real `gt-benchmark --algorithm
drum_crnn` engine path (not the standalone eval) via `AURAL_DRUM_CRNN_ONNX` +
`AURAL_DRUM_CRNN_THRESHOLDS` env overrides — proving the adapter's new
per-class resolution code is exercised correctly, not just the offline sweep
script.

Run-2 calibrated on validation-30: `kick=0.30, snare=0.35, hi_hat=0.20,
toms=0.20, cymbals=0.15` (sweep: `drum_crnn_run2_validation_split_calibration_sweep.json`).

Per-class F1 on test-30, both calibrated the same way (raw JSONs:
`egmd_stratified_30_drum_crnn_run2_recalibrated.json` /
`egmd_stratified_30_drum_crnn_run3_calibrated.json`):

| class | run-2 (calibrated) | run-3 (calibrated) | Δ |
|---|:--:|:--:|:--:|
| kick | 0.782 | 0.763 | −0.019 |
| snare | 0.649 | 0.658 | +0.009 |
| hi_hat | 0.722 | 0.713 | −0.009 |
| toms | 0.821 | 0.813 | −0.008 |
| **cymbals** | **0.518** | 0.464 | **−0.054** |

Run-2 wins 4 of 5 classes. Cymbals — the class run-3 was specifically built
to fix — regressed the most. This is not a validation/test split fluke: a
diagnostic sweep of run-2 directly on test-30
(`drum_crnn_run2_test_split_diagnostic_sweep.json`) reproduces the original
run-2 report's numbers exactly (kick 0.782, snare 0.618, hi_hat 0.737, toms
0.845, cymbals 0.518 at thr 0.15), confirming the calibration script and the
adapter's threshold resolution are both correct — the regression is real,
not a measurement artifact.

## Why fine-overall is misleading here (methodology note)

The "fine-overall" metric scores predictions against the benchmark's fine
(9-class-ish) taxonomy. Our 5-class decoder always emits **one canonical MIDI
note per class** (`DRUM_5CLASS_TO_MIDI_FALLBACK`: cymbals → note 49 / crash,
never note 51 / ride). A cymbals prediction can therefore only ever match
reference **crash** onsets on this metric, even when it correctly identified
a **ride** hit as "some cymbal" — a real 5-class-correct detection scores
zero on fine-overall depending on which specific reference note happens to be
in a given clip. This is why the 5-class-aggregated metric (bucketing both
prediction and reference into the same 5 canonical classes) is the fair
comparison, and why fine-overall nominally favoring run-3 (0.542 vs 0.532)
does not survive scrutiny: on the fair metric the two are statistically tied
(0.691 vs 0.695), and per-class the picture is unambiguous — run-2 wins.

A second-order finding from this exercise: per-class calibration on a 30-case
validation set did **not** reliably beat run-2's original single ad hoc
scalar threshold either (run-2 recalibrated: fine-overall 0.532 vs run-2's
original scalar-0.25 pick: 0.547). With only ~2–12 cymbal occurrences per
clip, a 30-case calibration set is small enough that per-class optima can
overfit to sampling noise. Calibration is sound machinery but should be
re-run on a larger validation sample (or averaged over multiple stratified
draws) before being trusted to beat a reasonable scalar default.

## Diagnosis: why heavier pos_weight hurt instead of helped

Cymbals at run-3's calibrated threshold (0.85): **precision 0.919, recall
0.311** — the model fires rarely but almost always correctly. This is the
signature of `pos_weight` widening the confidence distribution rather than
sharpening it: with a 25× cap, the model learns to nudge *many* frames'
cymbals logits upward (which is exactly what improves the *frame-level*
training metric, since that's measured at a low, fixed threshold of 0.5),
but true positives don't reliably separate from this now-noisier field at a
high decode threshold, so recall collapses once the threshold is raised
enough to reject the noise. The frame-F1 training signal improved for the
wrong reason — it doesn't imply the event-level decision boundary got
better calibrated.

## Recommendation (unchanged production state)

- **Ship nothing new.** Run-2's existing checkpoint/modelpack remains the
  reigning in-house model; no default-engine changes, no modelpack version
  bump. The `drum_crnn` engine stays opt-in exactly as it was.
- **Keep the harness + calibration machinery.** `pos_weight`, `init_checkpoint`,
  `EarlyStopper`, and the per-class threshold resolution code (decode.py,
  the adapter, the installer's `--thresholds` flag) are all sound,
  independently tested, and reusable for the next attempt — only *this*
  run-3 checkpoint is being shelved, not the tooling built to produce it.
- **Follow-up experiments** (not attempted this session, in priority order):
  1. A much lower `pos_weight_cap` (try 3.0–8.0 instead of 25.0) — the
     current cap may simply be too aggressive; a gentler nudge might fix
     cymbals without destabilizing the decision boundary.
  2. A full **from-scratch** run with `pos_weight` baked in from epoch 1,
     rather than a brief fine-tune perturbing an already-converged
     unweighted optimum — the model may need more gradient steps to find a
     new stable optimum under the reweighted loss, not just 3 effective
     epochs.
  3. If pos_weight remains unstable, try **focal loss** or oversampling
     cymbal-dense clips instead — techniques designed to sharpen rather than
     widen the confident-positive distribution.
  4. Re-run calibration on a larger validation sample (e.g. 100+ cases) before
     trusting per-class thresholds to beat a scalar default.

## Reproduce

```
# Fine-tune (what produced run-3; kept for the record, not recommended to reuse as-is):
python/ingest/.venv/Scripts/python.exe python/ingest/scripts/train_drum_crnn.py \
    --out D:/drum_crnn_run3 --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --init-checkpoint D:/drum_crnn_run2_full/checkpoint_best.pt \
    --epochs 15 --early-stop-patience 3 --pos-weight auto --batch 16 --workers 16 --val-limit 800

# Calibration sweep (validation-30, never the report set):
python/ingest/.venv/Scripts/python.exe benchmarks/drums/gt_runs/calibrate_drum_crnn_thresholds.py \
    --onnx <exported .onnx> --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --case-id-file benchmarks/drums/gt_runs/stratified_sample_validation_30.json \
    --output <calibration.json> --grid-min 0.05 --grid-max 0.95 --grid-step 0.05

# Verify calibrated thresholds through the REAL engine path on test-30:
AURAL_DRUM_CRNN_ONNX=<path> AURAL_DRUM_CRNN_THRESHOLDS="kick:K,snare:S,..." \
    python/ingest/.venv/Scripts/python.exe -m aural_ingest.cli gt-benchmark \
    --dataset egmd --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --algorithm drum_crnn --case-id-file benchmarks/drums/gt_runs/stratified_sample_test_30.json \
    --split test --tolerance-ms 50 --pitch-tolerance-semitones 0 --output <out.json>
```

Weights and E-GMD audio are never committed; E-GMD usage requires CC BY 4.0
attribution.
