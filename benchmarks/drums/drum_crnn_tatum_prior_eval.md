# Drum CRNN: tatum-conditioned decode prior — evaluation (2026-07-07)

> **Result: regression. Do not enable.** `AURAL_DRUM_CRNN_TATUM_PRIOR=1` at its
> default parameterization (`boost=0.12`, `sigma=25ms`, 16th-note grid) drops
> test-30 overall F1 from **0.532 → 0.496** against the identical run-2
> checkpoint and thresholds. The prior stays implemented, tested, and wired
> as an opt-in env-gated feature (default off) — nothing about the negative
> result here requires removing the code — but it should not be turned on in
> production, and it does not need further tuning passes right now.

## What was tested

The idea (ported from arXiv 2010.03749 / 2105.05791): nudge per-frame class
probabilities upward near a beat-grid tatum position before threshold decode,
targeting run-2/3's observed precision-heavy / recall-limited shape. Full
implementation: [`training/drum_crnn/tatum.py`](../../python/ingest/src/aural_ingest/training/drum_crnn/tatum.py),
wired into [`algorithms/drum_crnn.py`](../../python/ingest/src/aural_ingest/algorithms/drum_crnn.py)
behind `AURAL_DRUM_CRNN_TATUM_PRIOR`. The beat grid comes from the real
production path — `meter_tracker.track_meter` (Beat This!, CPJKU/ISMIR 2024) —
not a synthetic stand-in; the `beat-this` package was missing from this venv
(declared in `pyproject.toml` but never installed) and has now been installed
so this path is actually exercised, not silently no-op'd.

**Isolation:** same ONNX checkpoint (`D:/drum_crnn_run2_full/model.onnx`),
same per-class decode thresholds (`kick=0.3, snare=0.35, hi_hat=0.2,
toms=0.2, cymbals=0.15` — run-2's own recalibrated values), same test-30
stratified case set, same real `gt-benchmark --algorithm drum_crnn` engine
path used for every other measurement in this line of work. The only
variable is the env flag.

## Numbers

| class   | baseline P | baseline R | baseline F1 | tatum P | tatum R | tatum F1 | ΔF1 |
|---------|-----------:|-----------:|-------------:|--------:|--------:|---------:|----:|
| kick    | 0.911 | 0.684 | 0.782 | 0.840 | 0.727 | 0.779 | -0.003 |
| snare   | 0.827 | 0.535 | 0.649 | 0.765 | 0.577 | 0.658 | +0.008 |
| hi_hat  | 0.847 | 0.629 | 0.722 | 0.689 | 0.713 | 0.701 | -0.020 |
| toms    | 0.894 | 0.758 | 0.821 | 0.688 | 0.847 | 0.759 | -0.061 |
| cymbals | 0.584 | 0.466 | 0.518 | 0.361 | 0.845 | 0.506 | -0.013 |
| **overall** | **0.708** | **0.426** | **0.532** | **0.529** | **0.466** | **0.496** | **-0.036** |

(baseline: `gt_runs/egmd_stratified_30_drum_crnn_run2_recalibrated.json`;
tatum: `gt_runs/egmd_stratified_30_drum_crnn_run2_tatum_prior.json`)

## Diagnosis

The pattern is consistent across every class: recall goes up, precision
collapses, and the collapse outweighs the gain everywhere except snare
(a marginal +0.008). Cymbals is the extreme case — recall nearly doubles
(0.466 → 0.845) but precision drops by almost half (0.584 → 0.361), a net
loss even though this class was exactly the target of the intervention.

Root cause: the grid is too dense relative to how low the calibrated
thresholds already sit. `DEFAULT_TATUM_SUBDIVISIONS=4` puts a boost center
at every sixteenth note; at ~120 BPM that's a boosted region roughly every
125ms, each with `sigma_sec=0.025` (so ±75ms of real width at 3σ) — the
boosted regions cover a large fraction of the timeline, not just plausible
onset positions. Layered on top of thresholds already calibrated down to
0.15–0.35 (because run-2's raw confidence is under-saturated, particularly
for cymbals), a uniform +0.12 additive bump is large enough to carry a lot
of noise frames over threshold along with genuine near-miss onsets. The
technique isn't wrong in principle — the papers it's ported from show real
recall gains from tatum-snapping — but the current combination of (already
low thresholds + dense 16th-note grid + boost magnitude tuned for a
better-saturated confidence distribution) is a bad fit for this specific
checkpoint's calibration.

## Recommendation

Leave `AURAL_DRUM_CRNN_TATUM_PRIOR` off (it already defaults off). Don't
invest more time tuning it right now — the run-4 from-scratch retrain (bigger
model, no pos_weight, windowed dataloader) is the active lead for improving
cymbals, and it's cleaner to let that result land before deciding whether
tatum-conditioning is worth another pass. If revisited later, the fix is not
"tune the boost down empirically here" but three real candidate changes, in
priority order:
1. Sparser grid (`subdivisions=1` or `2`, i.e. beat- or 8th-note-level, not
   16th) — cuts the boosted fraction of the timeline by 2-4x.
2. Only boost frames that are *already close to threshold* (e.g. within
   0.1 of the class's decode threshold) rather than every frame uniformly —
   turns this into a genuine tie-breaker instead of a blanket recall lever.
3. Re-tune against whatever checkpoint ends up in production (run-4 or
   later), since the right boost magnitude is a function of that model's own
   confidence calibration, not a fixed constant.

## Reproduce

```
# One-time (this venv was missing it despite pyproject.toml declaring it):
python/ingest/.venv/Scripts/python.exe -m pip install "beat-this>=1.1.0"

PYTHONPATH=<worktree>/python/ingest/src \
AURAL_DRUM_CRNN_ONNX=D:/drum_crnn_run2_full/model.onnx \
AURAL_DRUM_CRNN_THRESHOLDS="kick:0.3,snare:0.35,hi_hat:0.2,toms:0.2,cymbals:0.15" \
AURAL_DRUM_CRNN_TATUM_PRIOR=1 \
    python/ingest/.venv/Scripts/python.exe -m aural_ingest.cli gt-benchmark \
    --dataset egmd --corpus-root E:/AudioSourceOfTruthData/extracted/e_gmd \
    --algorithm drum_crnn --case-id-file benchmarks/drums/gt_runs/stratified_sample_test_30.json \
    --split test --tolerance-ms 50 --pitch-tolerance-semitones 0 \
    --output benchmarks/drums/gt_runs/egmd_stratified_30_drum_crnn_run2_tatum_prior.json
```

Note the `PYTHONPATH` override: this repo is checked out as a git worktree
whose venv's editable install (`__editable__.aural_ingest-0.0.0.pth`) points
at the *main* checkout's `src/`, not the worktree's. Direct script/CLI runs
need the override; `pytest` does its own rootdir-based path insertion and
does not.
