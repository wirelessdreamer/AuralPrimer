# Magenta Onsets-and-Frames E-GMD drum checkpoint — neural baseline anchor

**Purpose.** Anchor how much F1 headroom a *pretrained neural* drum
transcriber buys over our DSP stack on E-GMD test, to decide whether an
in-house drum-model training investment is justified.

**Status: RESEARCH / BENCHMARK ONLY — not wired as a production engine.**
The Magenta *code* is Apache-2.0, but the *checkpoint's own license is
unstated* (see [License](#license)); the weights are **not** committed here.

**Bottom line (go/no-go).** It ran. On the **representative stratified 30-case
sample** (11 style families, 5 drummers, 3 BPM buckets — the sample the DSP
engines were re-benchmarked on, where they tie at F1 ≈ 0.284), the pretrained
Magenta E-GMD checkpoint scores **exact-class F1 0.535 vs DSP 0.284**
(**+0.251, ≈1.88×**) and **onset-only F1 0.776 vs 0.505** (+0.271), winning on
**both precision and recall** (P 0.58/0.37, R 0.50/0.23). It **recovers the two
classes every DSP engine drops entirely**: **toms 0.000 → 0.836** and **cymbals
0.000/0.061 → 0.546**. **This 0.535 exact / 0.776 onset is the real floor our
in-house CRNN must clear** — see [Stratified 30-case re-score](#stratified-30-case-re-score-authoritative)
below. (An earlier run on a *biased* funk/groove1-only 20-file slice gave a
narrower and misleading 0.269 vs 0.145 — kept below for provenance.)

> ⚠️ The original 20-file head-to-head that follows is **superseded** — it was a
> single-style/single-drummer slice. Use the stratified-30 numbers for any
> decision. The funk-20 section is retained only to show the sampling bias.

---

## Stratified 30-case re-score (AUTHORITATIVE)

The funk-20 slice below is one style / one drummer / one tempo — the DSP scores
0.145 there partly *because* funk/groove1 is DSP-hostile (ride-heavy). To make
the neural-vs-DSP comparison meaningful, both sides were re-run on the
**stratified 30-case sample** (`gt_runs/stratified_sample_test_30.json`:
deterministic round-robin over 30 distinct (style_family, drummer, bpm_bucket)
strata — 11 style families, 5 drummers, 3 BPM buckets, ≤45 s clips). All three
engines are scored by the **identical** `ground_truth_benchmark.score_drum_case`
path at 50 ms tolerance on the **same 30 case IDs** (verified set-equal to the
DSP baseline's case list).

### Overall (stratified 30, 50 ms tol)

| Metric            | DSP `adaptive_beat_grid` | DSP `beat_cond_multiband_dec` | **Neural Magenta E-GMD** | Δ (neural − best DSP) |
|-------------------|:------------------------:|:-----------------------------:|:------------------------:|:---------------------:|
| **Exact-class F1**| 0.284                    | 0.284                         | **0.535**                | **+0.251 (≈1.88×)**   |
| Exact-class P     | 0.367                    | 0.392                         | **0.581**                | +0.19                 |
| Exact-class R     | 0.232                    | 0.222                         | **0.496**                | +0.26 (≈2.1×)         |
| **Onset-only F1** | —                        | 0.505                         | **0.776**                | **+0.271**            |
| Onset-only P      | —                        | 0.699                         | **0.843**                | +0.14                 |
| Onset-only R      | —                        | 0.396                         | **0.719**                | +0.32 (≈1.8×)         |
| mean runtime/file | ~1.8 s                   | ~6.4 s                        | ~3.8 s (CPU)             |                       |

Unlike the funk slice (where neural traded precision for recall), on the
stratified sample **neural wins on both axes** — it is simultaneously more
precise *and* higher-recall than either DSP engine.

### Per-class exact F1 (5-class ADT taxonomy)

| Class    | DSP `adaptive_beat_grid` | DSP `beat_cond_mb_dec` | **Neural** | support |
|----------|:------------------------:|:----------------------:|:----------:|:-------:|
| kick     | 0.509                    | 0.400                  | **0.740**  | 751     |
| snare    | 0.310                    | 0.376                  | **0.624**  | 1083    |
| hi_hat   | 0.267                    | 0.305                  | **0.524**  | 851     |
| **toms** | **0.000**                | **0.000**              | **0.836**  | 190     |
| **cymbals** | **0.061**             | **0.000**              | **0.546**  | 367     |

**Toms and cymbals are recovered** — the headline finding. *Every* DSP engine
scores 0.000 on toms and ~0.000 on cymbals (they have no reliable way to
separate a tom from a snare, or a cymbal wash from a hat). The neural model
handles both cleanly (toms F1 0.836, cymbals 0.546), and is also materially
better on the three classes the DSP *can* do (kick +0.23, snare +0.25–0.31,
hi_hat +0.22–0.26). This generalises the funk-slice ride-recovery finding to
the whole cymbal/tom family across genres.

Machine-readable: `gt_runs/per_engine/egmd_stratified_30_magenta_egmd.json`
(overall + per-class, same shape as the DSP `per_engine/*.json`) and
`gt_runs/egmd_stratified_30_magenta_onset_vs_class.json` (exact vs onset-only).

### Revised headroom / floor
The pretrained E-GMD model clears the DSP tie (0.284) by **+0.25 exact-class
F1** and recovers two whole drum classes the DSP cannot touch. **The floor an
in-house drum model must beat is F1 ≈ 0.535 exact-class / 0.776 onset-only** on
this stratified sample (not the 0.269 the biased funk slice implied). Because
E-GMD is CC-BY (shippable) but this *checkpoint* is licence-blocked, the
in-house model should target **materially above 0.535 exact** to be worth
productionising — this is the number that justifies the training spend.

---

## Head-to-head (SUPERSEDED — biased funk/groove1-only 20-file slice, same scorer, 50 ms tol)

| Metric              | DSP `adaptive_beat_grid` | Neural Magenta E-GMD | Δ (neural − DSP) |
|---------------------|:------------------------:|:--------------------:|:----------------:|
| **Exact-class F1**  | 0.145                    | **0.269**            | **+0.124**       |
| Exact-class P       | 0.267                    | 0.298                | +0.031           |
| Exact-class R       | 0.100                    | **0.245**            | +0.145 (2.45×)   |
| **Onset-only F1**   | 0.377                    | **0.485**            | **+0.108**       |
| Onset-only P        | **0.691**                | 0.538                | −0.153           |
| Onset-only R        | 0.259                    | **0.442**            | +0.183 (1.71×)   |
| mean runtime / file | ~3.0 s (CPU)             | ~5.9 s (CPU)         |                  |

The DSP exact-class F1 of **0.1453 reproduces the stored baseline**
(`gt_runs/egmd_baseline_20.json`, `adaptive_beat_grid` = 0.1453) bit-for-bit,
confirming both engines are scored identically.

Interpretation: the DSP is **precise but deaf** (onset-only P 0.69, but only
sees ~26 % of hits); the neural model **hears far more** (recall ~1.7–2.5×
higher) at some precision cost, and wins F1 on both axes.

### Per-class, exact-class

| Class    | DSP F1 | Neural F1 | Notes |
|----------|:------:|:---------:|-------|
| kick     | 0.135  | 0.147     | both weak; neural mis-times kicks (~+20 ms) and mislabels some other hits as kick |
| snare    | 0.149  | 0.234     | neural ~1.6× |
| hi_hat   | 0.254  | 0.323     | neural better recall |
| ride     | **0.000** | **0.318** | **DSP detects zero of 2 540 ride hits** — the single biggest swing; ride is 31 % of all hits |
| crash    | 0.000  | 0.020     | both essentially fail (only 80 crash hits) |
| tom      | 0.000  | 0.000     | class-granularity mismatch, see caveats (only 20 tom hits) |
| oov (54) | 0.000  | 0.000     | tambourine (123 hits); neither can emit it, see caveats |

Full machine-readable results:
- `gt_runs/egmd_magenta_egmd_20.json` (neural, overall + per-class, exact + onset)
- `gt_runs/egmd_adaptive_beat_grid_onset_20.json` (DSP re-run, exact + onset + per-class)

---

## Sample

The exact 20 E-GMD **test**-split WAVs from `gt_runs/egmd_baseline_20.json`
(reused verbatim so the comparison is apples-to-apples). They are
`drummer1/eval_session`, style `funk/groove1`, 138 BPM, 4/4, ~28 s each, across
20 different drum kits (Acoustic, Classic Rock, Jazz Funk, Studio, …). They are
reproduced by `yield_cases(corpus_root, split="test", limit=20)` — set-equal to
the baseline case IDs. This is **one style / one drummer / one tempo**; it is a
kit-diversity slice, not a genre-diversity slice.

---

## How it ran (reproducible setup)

**Approach used: #2 — a separate pinned Python 3.10 venv.** Approach #1
(`tf.compat.v1` in the main ingest venv) is not viable: the main venv is
Python 3.13 with **no TensorFlow** (basic-pitch here uses `onnxruntime`, not
TF), and the checkpoint is a **TF1 estimator checkpoint** (`.meta` graph +
variables, not a frozen SavedModel), so it needs magenta's model-building code
to reconstruct the graph — you cannot just load the graph directly.

### Environment
- **Python 3.10.0** (`py -3.10`), throwaway venv in scratch (not committed).
- `tensorflow==2.9.3` (used via `tf.compat.v1`, `tf.disable_v2_behavior()`),
  `magenta==2.1.4`, `note-seq==0.0.3`.
- Support pins that have cp310 wheels (magenta pins ancient exact versions that
  don't build on 3.10; these newer ones are API-compatible for inference):
  `protobuf==3.19.6`, `numpy==1.23.5`, `scipy==1.9.3`, `llvmlite==0.39.1`,
  `numba==0.56.4`, `librosa==0.8.1`, `resampy==0.3.1`, `keras==2.9.0`,
  `tensorflow-probability==0.17.0`, `dm-tree`, `tf-slim==1.1.0`, `pretty_midi`,
  `mido==1.2.10`, `mir_eval==0.7`, `scikit-learn==1.1.3`, `pandas==1.5.3`,
  `sox`, `pydub`, `bokeh<3`.

### Dependency walls hit (and fixes)
1. **`magenta==2.1.4` bare install fails to build** `llvmlite` / `numba` /
   `python-rtmidi` from source on 3.10 (ancient `llvmlite` `setup.py` calls
   `spawn(dry_run=...)`, removed in modern setuptools; `python-rtmidi` needs a
   C++ toolchain). → Fix: install the compiled deps from **binary wheels
   first** (`--only-binary`), then `pip install --no-deps magenta note-seq`,
   then fill the pure-Python deps individually. `python-rtmidi` is a MIDI-I/O
   dep unused by transcription and is simply skipped.
2. **`magenta/__init__` eagerly imports** `tensorflow_probability` (via
   `magenta.common.nade`) and, in `configs.py`, `sox` (via `audio_transform`).
   → Fix: add `tensorflow-probability==0.17.0` and the `sox` Python package
   (the SoX *binary* is never invoked on the inference path).
3. **Windows DLL-load failure:** `ImportError: DLL load failed while importing
   _csr_polynomial_expansion: The filename or extension is too long.` This is
   **not** a magenta problem — the scratch venv path is deep enough to break
   native `.pyd` loading. → Fix: `subst N: <scratch>` to map a short drive and
   run everything through `N:\…`.

### Steps
1. Download + extract checkpoint (into scratch, **not** the repo):
   `curl -L -o e-gmd_checkpoint.zip https://storage.googleapis.com/magentadata/models/onsets_frames_transcription/e-gmd_checkpoint.zip`
   → `model.ckpt-569400.{data,index,meta}` + `checkpoint`.
2. Build the `drums` config estimator once and loop over the 20 WAVs, writing
   one `<stem>.midi` each. This mirrors magenta's own
   `onsets_frames_transcription_transcribe.run()` but times each file and
   reuses the loaded model. The `drums` config uses `sample_rate=44100`,
   `hop_length=441` (10 ms), `drum_data_map='8-hit'`.
3. Parse the MIDIs → `list[DrumEvent]` (`pretty_midi`, onset = `note.start`).
4. Score with `aural_ingest.ground_truth_benchmark.score_drum_case`
   (`tolerance_sec=0.05`, `pitch_aware=True` and `False`), overall + per-class,
   against `yield_cases(..., split="test", limit=20)`.

The shipped, gated adapter reproducing this is
`python/ingest/src/aural_ingest/algorithms/magenta_egmd_drums.py`. It never
imports TF/magenta at import time; `transcribe()` shells to the magenta venv
given by env vars and parses the output NoteSequence:

```
set AURAL_MAGENTA_PY=<py3.10 magenta venv>\Scripts\python.exe
set AURAL_MAGENTA_EGMD_CKPT=<extracted e-gmd checkpoint dir>
```

Output-vocabulary alignment: the 8-hit map's base pitches (36 kick, 38 snare,
48 tom, 46 hi-hat, 51/53 ride, 49 crash, 75 clave) map cleanly onto our
benchmark drum-class taxonomy (`_BENCHMARK_NOTE_TO_CLASS`), so exact-class
scoring is fair — verified end-to-end through the shipped adapter.

---

## License

**Checkpoint: license UNSTATED — treat as research-only, do not ship.**
- The `e-gmd_checkpoint.zip` (GCS, uploaded 2020-03-28, 25 MB) contains **only**
  `checkpoint` + `model.ckpt-569400.{data,index,meta}` — **no LICENSE, no
  NOTICE, no README**.
- A sibling `…/onsets_frames_transcription/LICENSE` in the bucket is **404**;
  the object is a bare GCS download with no accompanying terms page.
- The Magenta **code** (magenta 2.1.4, the transcribe script, configs) is
  Apache-2.0 (per-file headers), but that license covers the code, not the
  trained weights.
- Conclusion: fine to **benchmark** with; **do not** redistribute the weights
  or wire them into a shippable `KNOWN_DRUM_ENGINES` default.

**E-GMD dataset (the audio/MIDI we scored against): CC-BY 4.0** — the dataset's
`LICENSE` states Creative Commons Attribution 4.0. That's data, not the model,
and is only used here as a benchmark corpus.

---

## Caveats and honest reading
- **No offset correction was applied to either side.** The neural kicks show a
  systematic ~+20 ms skew and the kick channel is noisy (many hits of other
  classes mislabelled as kick), but there is **no global time offset to
  correct** (snare/hat/ride medians are within ±10 ms). Applying a per-class
  nudge would unfairly tune only the neural side — the DSP baseline got none.
- **Tom class-granularity mismatch:** GT toms here are pitch 43 (`tom3`); the
  8-hit model emits toms at base pitch 48 (`tom1`). Under exact-class these
  never match (0 tp). Only 20 tom hits exist, so overall impact is negligible,
  but a production drum-class reconciliation should collapse tom sub-classes.
- **Tambourine (pitch 54, 123 hits)** is out-of-vocab for our benchmark and the
  8-hit model folds it into the hi-hat class, so it is a guaranteed miss for
  both — a corpus/taxonomy artifact, not a model failure.
- **Scope:** this is 20 files of one style/drummer/tempo (kit-diverse only).
  The ~1.85× exact-class F1 gain is a strong signal but should be confirmed on a
  genre/drummer/tempo-stratified sample before it anchors a large training
  spend. The absolute neural number (F1 0.27 exact / 0.49 onset) is still
  modest — a *published pretrained* model, not a ceiling.

## Decision framing
On the **representative stratified 30-case sample** (the authoritative number),
a pretrained E-GMD neural model beats the tied DSP engines by **+0.25
exact-class F1 (0.284 → 0.535)** and **+0.27 onset-only F1 (0.505 → 0.776)**,
wins on both precision and recall, and **recovers the entire tom and cymbal
families** (toms 0.000 → 0.836, cymbals ~0.00 → 0.546) that the DSP stack
structurally cannot. There is large, unambiguous headroom above DSP.

The pretrained checkpoint is licensing-blocked for shipping (unstated weights
licence), so it can't be productionised — but E-GMD itself is CC-BY and
shippable. Conclusion:
- **Floor for the in-house CRNN: F1 ≈ 0.535 exact-class / 0.776 onset-only** on
  the stratified 30 (using this exact scorer). Anything below that is not worth
  productionising over the DSP-plus-nothing baseline.
- The gap between DSP (0.284) and this pretrained model (0.535) is the headroom
  that **justifies the in-house training investment** — the DSP genuinely
  cannot see toms/cymbals, and a trained model demonstrably can.

_(The earlier "F1 ≈ 0.27 exact" figure came from the biased funk-20 slice and
should not be used; the stratified 0.535 is the true anchor.)_
