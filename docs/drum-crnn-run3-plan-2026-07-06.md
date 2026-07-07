# Plan — drum-CRNN run 3: converged E-GMD training → calibrated modelpack → (gated) default promotion

Written 2026-07-06 for the implementing session. This is the "★ HIGH PRIORITY —
finish the in-house drum-CRNN engine" item from
[`deferred-work-2026-07-05.md`](deferred-work-2026-07-05.md), turned into a
concrete, ordered implementation plan.

> **Outcome (2026-07-06):** Steps 1–3 executed as planned; the harness
> upgrades (`pos_weight`, `init_checkpoint`, `EarlyStopper`) and per-class
> threshold calibration machinery all work correctly and are merged. **The
> run-3 checkpoint itself did NOT clear the plan's success criteria and is
> NOT promoted** — on a controlled apples-to-apples comparison, cymbals (the
> whole point of the `pos_weight` fix) regressed rather than improved
> (F1 0.518 → 0.464), and the overall 5-class-aggregated F1 is a statistical
> tie with run-2 (0.695 vs 0.691). Run-2 remains the reigning checkpoint; no
> default-engine or modelpack changes shipped. Full analysis + follow-up
> recommendations (lower `pos_weight_cap`, a from-scratch run instead of a
> fine-tune, or focal loss) in
> [`benchmarks/drums/drum_crnn_training_run3.md`](../benchmarks/drums/drum_crnn_training_run3.md).
> Steps 4–5 (ship modelpack, promote to default) are accordingly **not
> started** — they're blocked on a run that actually beats run-2.

## Where we are (verified against main @ `1e3034c`)

- The `drum_crnn` **opt-in engine is complete on main**: ONNX adapter
  ([`algorithms/drum_crnn.py`](../python/ingest/src/aural_ingest/algorithms/drum_crnn.py),
  lazy-gated, DSP fall-through), training harness
  (`python/ingest/src/aural_ingest/training/drum_crnn/`), installer
  ([`install_drum_crnn_modelpack.py`](../python/ingest/scripts/install_drum_crnn_modelpack.py)),
  tests. `DEFAULT_DRUM_ENGINE` is still `beat_conditioned_multiband_decoder`.
- `create_portable.ps1` **already scans `assets/models/drum_crnn/`** (PR #8) —
  shipping is automatic once a modelpack exists.
- **Run-2** (full 341 h E-GMD train split, 10 epochs, ~54 min/epoch on the
  RTX 5090, ~9 h total): stratified-30 event F1 **0.547** @ thr 0.25 / 0.546
  @ 0.20 — clears the Magenta floor (0.535), beats it on kick/hi-hat/toms.
  **Not converged**: val frame-macro-F1 went 0.266 → 0.309 over the last two
  epochs and was still rising. **Cymbals under-learned** (val frame-F1 ≈ 0.01
  all run; event F1 0.518 vs Magenta 0.546) — classic class imbalance; the
  loss is plain unweighted `BCEWithLogitsLoss` (train.py:126).
- Checkpoints on disk (NOT in git): `D:\drum_crnn_run1\` (subset run),
  `D:\drum_crnn_run2_full\` (full run; `checkpoint_best.pt` = epoch 10).
- Session scratchpad has working launcher/eval scripts to repo-ify (step 1d):
  `C:\Users\dreamer\AppData\Local\Temp\claude\D--AuralPrimer--claude-worktrees-friendly-mcclintock-943e01\b6005b25-6f0f-4b46-84b0-f97e9d1b5868\scratchpad\train_drum_crnn.py`
  and `eval_drum_crnn.py`. (If the scratchpad is gone, both are small and
  described below; the eval logic also exists in the repo benchmark via
  `gt-benchmark --algorithm drum_crnn`.)
- Venv: `D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe`
  (Python 3.13, torch 2.11.0+cu128, CUDA on the 5090 works, onnxruntime 1.26).
- E-GMD at `E:\AudioSourceOfTruthData\extracted\e_gmd` (CC BY 4.0 — keep the
  attribution note in the harness README).

## Goal + success criteria

Produce a **converged, calibrated** model and ship it as modelpack `0.2.0`.

| Metric (stratified-30 test, 50 ms, exact-class) | run-2 | run-3 target |
|---|:--:|:--:|
| Fine-overall F1 | 0.547 | **≥ 0.57** (hard floor: > 0.547) |
| Cymbals F1 | 0.518 | **≥ 0.546** (Magenta parity) |
| Other classes | kick .782 / snare .618 / hi-hat .737 / toms .845 | no class regresses > 0.02 |
| Onset-only F1 | 0.700 | ≥ 0.72 |

Hard rule: **never report and calibrate on the same set** (see step 3).

---

## Step 1 — harness upgrades (small, test-covered; ~2 h)

All in `python/ingest/src/aural_ingest/training/drum_crnn/`:

1a. **Per-class `pos_weight`** in the loss. `TrainConfig` gains
    `pos_weight: tuple[float, ...] | str | None = "auto"`:
    - `"auto"` → compute per-class positive-frame frequency over (a sample of)
      the training targets, use `min(neg/pos, cap)` with `cap = 25.0`;
    - explicit tuple → use as-is; `None` → current behavior.
    Wire into `nn.BCEWithLogitsLoss(pos_weight=torch.tensor(...))` in
    `train.py`. Log the resolved weights into `history.json`.

1b. **Init-from / fine-tune support.** `TrainConfig.init_checkpoint: str = ""`;
    when set, `train()` loads `model_state` from that checkpoint (fresh
    optimizer). Enables run-3 as a fine-tune of run-2 best instead of a fresh
    22–27 h pass.

1c. **Early stopping + LR decay.** `TrainConfig.early_stop_patience: int = 3`
    (0 = off) on val macro-F1; halve the LR once when patience hits 2
    (simple, no scheduler dependency). Keep per-epoch + best checkpointing
    as-is.

1d. **Repo-ify the launcher + eval scripts** (they currently live only in the
    session scratchpad):
    - `python/ingest/scripts/train_drum_crnn.py` — argparse over TrainConfig
      (`--out`, `--corpus-root`, `--epochs`, `--batch`, `--workers`,
      `--train-limit`, `--val-limit`, `--device`, `--pos-weight`,
      `--init-checkpoint`, `--early-stop-patience`). Spawn-safe
      (`if __name__ == "__main__"`) for Windows dataloader workers.
    - Eval goes through the existing benchmark, no new script needed:
      `python -m aural_ingest.cli gt-benchmark --dataset egmd --algorithm
      drum_crnn --case-id-file ... --tolerance-ms 50` (the scratchpad
      `eval_drum_crnn.py` is only needed if you want the threshold sweep
      without reinstalling the modelpack each time — port it if useful).

1e. **Tests** (extend `python/ingest/tests/test_drum_crnn_training.py`):
    pos_weight resolution ("auto" produces per-class weights ≥ 1, capped;
    explicit tuple passthrough), init_checkpoint round-trip (weights actually
    load), early-stop triggers on a synthetic plateau. Run the full
    `test_drum_crnn_*` + `test_transcription_orchestration` suites.

## Step 2 — run 3 training (GPU, background; ~9–14 h)

**Primary attempt — fine-tune run-2 best with class weighting:**

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  python\ingest\scripts\train_drum_crnn.py `
  --out D:\drum_crnn_run3 `
  --corpus-root E:\AudioSourceOfTruthData\extracted\e_gmd `
  --init-checkpoint D:\drum_crnn_run2_full\checkpoint_best.pt `
  --epochs 15 --early-stop-patience 3 `
  --batch 16 --workers 16 --pos-weight auto --val-limit 800
```

Run in the background (~54 min/epoch full-corpus; early stop will likely end
it in 8–12 epochs). Watch `D:\drum_crnn_run3\history.json` — expect cymbals
val frame-F1 to move off ~0.01 within 2–3 epochs if the weighting works.

**Escalation (only if the fine-tune plateaus below run-2 +0.01):** fresh run,
same flags minus `--init-checkpoint`, `--epochs 30`. Budget ~a day of GPU.

**Optional perf side-quest (skip unless the fresh run is needed):** the
dataset `__getitem__` decodes the *whole* ~35 s WAV then crops 8 s
(`dataset.py:119-131`); a windowed read (soundfile `start/frames`) is a ~3–4×
epoch speedup. Only worth it for the 30-epoch path.

## Step 3 — eval + per-class threshold calibration (~2 h)

3a. **Build a calibration set that is NOT the report set.** Use the stratified
    sampler (`benchmarks/drums/gt_runs/stratified_egmd.py`) to select ~30
    cases from the **validation** split (add a `--split` arg if it is
    test-only — check first). Commit the manifest as
    `benchmarks/drums/gt_runs/stratified_sample_validation_30.json`.

3b. **Per-class threshold support in decode + adapter.**
    - `decode.py::decode_events`: accept `threshold: float | Mapping[str, float]`.
    - `algorithms/drum_crnn.py`: read per-class thresholds from (in priority
      order) env `AURAL_DRUM_CRNN_THRESHOLDS` (e.g.
      `"kick:0.2,snare:0.25,hi_hat:0.2,toms:0.2,cymbals:0.12"`), then a
      `decode_thresholds` block in `modelpack.json`, then the scalar default.
      **Calibration ships with the model** via modelpack.json.
    - Installer: `--thresholds` flag writes the block into the manifest.
    - Tests: mapping decode, env parse, manifest fallback.

3c. **Calibrate:** sweep each class independently (0.05–0.45, step 0.05) on
    the **validation-30**, pick per-class F1-max (cymbals will likely want a
    lower threshold than kick — the model under-triggers sparse classes).

3d. **Report:** run the frozen per-class thresholds on the **test stratified-30**
    via the real engine path (`gt-benchmark --algorithm drum_crnn
    --case-id-file benchmarks/drums/gt_runs/stratified_sample_test_30.json`).
    Compare against the success table above + run-2 + Magenta. Write
    `benchmarks/drums/drum_crnn_training_run3.md` (mirror run1/run2 reports)
    and commit it with the run JSONs (never the weights).

## Step 4 — ship modelpack 0.2.0 (~30 min)

```powershell
python python\ingest\scripts\install_drum_crnn_modelpack.py `
  --checkpoint D:\drum_crnn_run3\checkpoint_best.pt --version 0.2.0 `
  --thresholds "kick:...,snare:...,hi_hat:...,toms:...,cymbals:..."
```

(Add `--version` to the installer if absent — dir name must equal manifest
version or Studio's model manager marks it invalid.) Re-verify through the
real engine path (same gt-benchmark command; expect the step-3d number).
Portable repack picks the pack up automatically (PR #8).

## Step 5 — Phase D promotion (code ~2 h, then GATED on human review)

Only after step 3d meets the success criteria:

5a. Flip `DEFAULT_DRUM_ENGINE = "drum_crnn"` and put `drum_crnn` first in the
    `gameplay_default` profile's `drum_engines` (keep
    `beat_conditioned_multiband_decoder` next — the model-absent fall-through
    already works and is test-pinned).
5b. Update the pinned default assertions (they will fail loudly):
    `test_import_pipeline.py` (~1289–1294, 1346–1353),
    `test_quality_benchmark.py` (~68), `test_transcription_resilience.py`
    (~48–62), `test_transcription_orchestration.py` (~27–54, 271–272).
    Note commit `7255b74` already moved some of these to neural-first — read
    current line numbers, don't trust the old ones.
5c. Gameplay-metric regression check per the promotion policy
    (`benchmarks/quality/TRANSCRIPTION_QUALITY_EPIC.md`): density,
    duplicates/chatter, sync quarantine on the guard cases — reject if worse.
5d. **STOP for the human gate:** in-game / listening review on real Psalms
    (repo policy explicitly forbids flipping `gameplay_default` on F1 alone).
    Only commit the 5a/5b flip after that sign-off; keep it as its own commit
    so it can be reverted independently.
5e. Rebuild sidecar + repack portable; update
    `docs/deferred-work-2026-07-05.md` (tick items 1–3) and
    `docs/research-decision-gates.md` (Path 2 status).

## Risks / notes

- **pos_weight can tank precision** on dense classes (hi-hat). That is what
  per-class threshold calibration (step 3) is for — evaluate weighted models
  at calibrated thresholds, not at 0.2 flat.
- **Cymbals may stay < Magenta parity** even weighted (367-event support,
  hardest class). If overall F1 ≥ 0.57 and cymbals ≥ 0.53, ship anyway and
  note it; the DSP default scores 0.000–0.216 on cymbals, so it is still a
  massive upgrade.
- **Don't commit weights** (`*.pt`/`*.onnx` are gitignored under
  `assets/models/drum_crnn/` and the training dirs — keep it that way).
- The adapter's scalar `DEFAULT_THRESHOLD = 0.20` was tuned for run-2; after
  step 3 the modelpack manifest carries calibration, so the scalar only
  matters as a last-resort fallback.
- Two historic stale-default test failures were fixed by `7255b74` on main —
  the suite should be green before you start; verify with a baseline
  `pytest python/ingest/tests -k "drum or transcription"` run first.
