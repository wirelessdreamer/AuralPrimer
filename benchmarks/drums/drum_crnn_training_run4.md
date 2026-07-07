# Drum CRNN training run 4 — bigger model, from scratch, windowed dataloader (2026-07-07)

> **Result: positive. Run-4 is the new best in-house checkpoint and the first
> to clear the license-blocked Magenta floor.** Test-30 overall F1 **0.576**
> vs run-2's 0.534 (both calibrated identically on validation-100) and vs
> Magenta E-GMD's 0.535 (class-aware, same case set). Better on 4 of 5
> classes; cymbals is the one remaining regression vs run-2 (0.464 vs 0.509)
> and stays the weak class overall. Promotion to `gameplay_default` remains
> gated on human listening / in-game review per repo policy — F1 alone
> doesn't flip the default.

## What changed vs run-2

| | run-2 | run-4 |
|---|---|---|
| conv_channels | (32, 32, 64) | (32, 64, 128) |
| gru_hidden | 64 | 128 |
| params | ~300K | 1,177,285 |
| init | from scratch | from scratch |
| pos_weight | none | none (run-3 showed it harmful) |
| dataloader | full-file decode + crop | windowed partial reads |
| min/epoch (full 35,217-file corpus) | ~51 | **~4.8** |
| epochs | 10 (fixed) | 19 (early stop, patience 5) |
| total wall clock | ~8.5 h | **91 min** |

The windowed dataloader (commit `b1a77bc`) is what made the bigger model
practical: 19 epochs in 91 minutes vs run-2's 10 epochs in 8.5 hours.

## Training

```
python/ingest/scripts/train_drum_crnn.py --out D:/drum_crnn_run4 \
    --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --epochs 30 --early-stop-patience 5 --batch 16 --workers 8 \
    --pos-weight none --val-limit 800
```

Device cuda; train=35,217 / val=800. Best epoch 14 (val frame macroF1
0.344); LR halved to 5e-4 at epoch 8; early-stopped at epoch 19. Cymbals
frame-F1 came off zero at epoch 4 and peaked at 0.182 (epoch 14) — for
reference, run-2's training-time cymbals frame-F1 never exceeded ~0.01
across its whole run, and run-3's pos_weight fine-tune forced it to 0.252
at the cost of an event-level regression. Full curve: `D:/drum_crnn_run4/history.json`.

**Ops note (Windows):** the first launch with `--workers 16` hung
indefinitely before epoch 1 — ~0 CPU for 20+ minutes, no dataloader worker
processes spawned, nothing in the log past the launch line. Killed and
relaunched with `-u` (unbuffered) and `--workers 8`; started training within
a minute. If a run sits at the launch line with no `device=` print, suspect
Windows DataLoader spawn and reduce workers.

ONNX export from `checkpoint_best.pt` (epoch 14) verified against torch at
max output diff 5.7e-6 (`D:/drum_crnn_run4/model.onnx`, 4.7 MB).

## Calibration — validation-100 (methodology upgrade)

Run-3's report flagged that 30-case per-class calibration is fragile for
rare classes. That fragility bit immediately here: on validation-30,
run-4's cymbals F1 curve is flat (~0.56–0.57 everywhere from t=0.15 to
t=0.50) and argmax picked **t=0.50 by a 0.001 margin** — which on test-30
produced P=0.982 / R=0.300. Per run-3's follow-up recommendation, built a
stratified **validation-100** sample (`stratified_sample_validation_100.json`,
same deterministic sampler, zero test-set contact) and recalibrated **both**
models on it for a controlled comparison:

- run-4: `kick:0.25, snare:0.2, hi_hat:0.2, toms:0.15, cymbals:0.35`
- run-2: `kick:0.3, snare:0.4, hi_hat:0.2, toms:0.2, cymbals:0.2`

(Sweeps: `drum_crnn_run4_calibration.json` [val-30],
`drum_crnn_run4_calibration_val100.json`, `drum_crnn_run2_calibration_val100.json`.)

## Test-30 results (report set, real `gt-benchmark` engine path)

Both models scored with their own validation-100 thresholds; Magenta is the
license-blocked reference (class-aware scoring, same 30 cases, same 3,904
reference events, tol 50 ms):

| | Magenta (blocked) | run-2 | run-4 | Δ (run-4 − run-2) |
|---|---:|---:|---:|---:|
| **overall F1** | **0.535** | 0.534 | **0.576** | **+0.042** |
| overall P / R | 0.581 / 0.496 | 0.743 / 0.416 | 0.737 / 0.473 | — |
| kick F1 | — | 0.782 | **0.799** | +0.017 |
| snare F1 | — | 0.629 | **0.698** | +0.069 |
| hi_hat F1 | — | 0.722 | **0.744** | +0.022 |
| toms F1 | — | 0.821 | **0.822** | +0.001 |
| cymbals F1 | — | **0.509** | 0.464 | −0.045 |
| macro-5class F1 | — | 0.692 | **0.706** | +0.014 |

(Result JSONs: `egmd_stratified_30_drum_crnn_run4_val100.json`,
`egmd_stratified_30_drum_crnn_run2_val100.json`; the val-30-calibrated
variant `egmd_stratified_30_drum_crnn_run4_calibrated.json` scores 0.577
overall — same headline, worse cymbals threshold choice.)

The gains are recall-driven where run-2 was recall-starved: snare recall
0.500 → 0.614, kick 0.684 → 0.716, hi_hat 0.629 → 0.710, toms 0.758 →
0.816 — with precision staying in the 0.78–0.90 band.

## Cymbals: the remaining weakness

Run-4's cymbal head is extremely precision-heavy: at its calibrated t=0.35,
P=0.919 / R=0.311 on test-30. Lowering the threshold doesn't rescue it —
the validation-100 curve *drops* below t=0.15 (0.468 at t=0.05), meaning
the model simply doesn't produce confident activations for a large share of
cymbal onsets; there is no threshold that recovers them. This is a model /
data problem (cymbals span crash/ride/china/splash with long decays and
heavy spectral overlap with hi_hat), not a calibration problem. Two honest
caveats cut in opposite directions:

- The 5-class canonical-note confound (cymbals always emitted as crash,
  note 49) depresses the *overall* column for both in-house models vs
  Magenta, which emits fine taxonomy. Per-class buckets are free of it.
- Run-2's cymbals "advantage" (0.509 vs 0.464) comes from a lower-precision
  operating point (P=0.743 vs 0.919); which trade plays better in-game is
  exactly the kind of question the human-review gate exists to answer.

## Recommendation

1. **Ship run-4 as a modelpack** (`install_drum_crnn_modelpack.py` with the
   validation-100 thresholds baked into `decode_thresholds`) so it's
   installable for AuralStudio testing. It beats run-2 on every axis that
   matters except cymbals and is the first checkpoint to clear the Magenta
   floor.
2. **Promotion to `gameplay_default` stays gated** on the repo's standing
   policy: gameplay-metric regression check + human listening / in-game
   review. Not flippable on these numbers alone.
3. **Next cymbals leads** (in priority order): (a) longer training at the
   current scale — the run was still setting new bests at epoch 14 of 19
   and the stopper may have been aggressive for the noisy macro; (b) merge
   crash/ride sub-targets during training (multi-label cymbals) so the loss
   stops splitting credit across confusable cymbal types; (c) revisit
   focal loss, which unlike pos_weight sharpens rather than widens the
   confidence distribution (run-3's diagnosis).

## Reproduce

```
# Train (expect ~91 min on an RTX-class GPU, Windows: keep workers <= 8):
python/ingest/.venv/Scripts/python.exe -u python/ingest/scripts/train_drum_crnn.py \
    --out D:/drum_crnn_run4 --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --epochs 30 --early-stop-patience 5 --batch 16 --workers 8 \
    --pos-weight none --val-limit 800

# Stratified validation-100 (calibration set; deterministic, no test contact):
python/ingest/.venv/Scripts/python.exe benchmarks/drums/gt_runs/stratified_egmd.py \
    --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd --split validation \
    --size 100 --max-duration 45 \
    --output benchmarks/drums/gt_runs/stratified_sample_validation_100.json

# Calibrate (repeat with run-2's ONNX for the controlled comparison):
python/ingest/.venv/Scripts/python.exe benchmarks/drums/gt_runs/calibrate_drum_crnn_thresholds.py \
    --onnx D:/drum_crnn_run4/model.onnx --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --case-id-file benchmarks/drums/gt_runs/stratified_sample_validation_100.json \
    --output benchmarks/drums/gt_runs/drum_crnn_run4_calibration_val100.json \
    --grid-min 0.05 --grid-max 0.95 --grid-step 0.05

# Score on test-30 through the real engine path:
AURAL_DRUM_CRNN_ONNX=D:/drum_crnn_run4/model.onnx \
AURAL_DRUM_CRNN_THRESHOLDS="kick:0.25,snare:0.2,hi_hat:0.2,toms:0.15,cymbals:0.35" \
    python/ingest/.venv/Scripts/python.exe -m aural_ingest.cli gt-benchmark \
    --dataset egmd --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --algorithm drum_crnn --case-id-file benchmarks/drums/gt_runs/stratified_sample_test_30.json \
    --split test --tolerance-ms 50 --pitch-tolerance-semitones 0 \
    --output benchmarks/drums/gt_runs/egmd_stratified_30_drum_crnn_run4_val100.json
```

(In a git worktree, prefix commands with
`PYTHONPATH=<worktree>/python/ingest/src` — the shared venv's editable
install points at the main checkout's `src/`.)
