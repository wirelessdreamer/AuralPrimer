# Research — Drums: license resolution + the permissive path forward (2026-07-02)

Detail notes from the 2026-07-02 deep-research pass. Synthesis + ranked queue:
[research-transcription-novel-approaches-2026-07-02.md](research-transcription-novel-approaches-2026-07-02.md).

**This document resolves the open license gate in
`research-adt-drums-2026-06-22.md` and supersedes the ADTOF production
recommendation in `research-decision-gates.md` § "ADT Architecture Revision
(2026-05-07)" path 2.** The architectural conclusions there (neural CRNN over
DSP fusion, 5-class taxonomy, Demucs preprocessing, tatum decoding) still
stand; the *training-source* plan changes.

Provenance note: the deep-research verification panel's budget cutoff dropped
every drums claim before voting, so the four load-bearing claims below were
manually verified 2026-07-02 directly against primary artifacts (repo pages,
dataset pages, arXiv full texts). Numbers marked "fetch-extracted" came from
single-agent full-text reads and carry lower confidence.

## Empirical validation (2026-07-02) — the research is now confirmed with our own numbers

The literature-derived thesis of this document ("DSP is structurally capped;
a neural model trained on E-GMD is the permissive path") has since been
validated on our own ground-truth benchmark. Full artifacts:
[`benchmarks/drums/phase1_baseline_report.md`](../benchmarks/drums/phase1_baseline_report.md)
and [`benchmarks/drums/magenta_egmd_anchor.md`](../benchmarks/drums/magenta_egmd_anchor.md).
All numbers below were re-verified by recomputing from the raw per-case
`tp/fp/fn`, on a **stratified 30-case E-GMD test sample** (18 styles / 5
drummers / 19 BPMs), scored class-aware at 50 ms tolerance.

| | DSP (best of 6 engines) | Magenta E-GMD (neural) |
|---|:--:|:--:|
| Exact-class F1 | **0.284** (adaptive_beat_grid ≈ beat_conditioned) | **0.535** (+0.251, ≈1.88×) |
| — precision / recall | 0.37–0.39 / 0.22–0.23 | 0.581 / 0.496 |
| Onset-only F1 | 0.505 | 0.776 |
| **toms** F1 | **0.000** (all engines) | **0.836** |
| **cymbals** F1 | 0.000 (default) … 0.216 (spectral_flux) | **0.546** |

Three conclusions, now empirical rather than literature-inferred:

1. **DSP is structurally capped ~0.28** because it cannot classify toms
   (0.000 across every heuristic engine) and the default is blind to cymbals.
   No amount of heuristic tuning fixes a class the front-end can't represent.
2. **A model trained on E-GMD roughly doubles exact-class F1** and *decisively
   recovers toms + cymbals* — winning on both precision and recall, not trading
   one for the other. This generalises across genres, not just the funk slice
   the first anchor used (that biased slice under-reported the gap as +0.12).
3. **The verified floor for our in-house CRNN is F1 ≈ 0.535 exact / 0.776
   onset.** The Magenta checkpoint's weights are license-unstated (research
   only); E-GMD is CC BY 4.0, so a model we train ourselves on it is shippable.
   That training run is the active next step (harness at
   `python/ingest/src/aural_ingest/training/drum_crnn/`).

Caveat carried forward: the neural model's hi-hat still over-triggers
(precision 0.425, 782 FP on the stratified 30) — the same failure family as
the DSP stack, milder. A tatum/pattern-LM decode (Finding 7) remains the
indicated fix for that residual, on top of the neural base.

Independent aside surfaced during verification: **no single DSP engine wins
every class** (kick→adaptive, snare→librosa, hi-hat→default, toms→dsp_bandpass,
cymbals→spectral_flux), so a per-class DSP *ensemble* would beat any single
engine's 0.284 with zero training — a cheap interim, but it still cannot reach
the neural model's tom/cymbal recovery.

## Where we are

- Real-world drum F1 ~0.15–0.4 with the DSP classifier stack; the failure
  shape is a **precision collapse** (e.g. hi-hat recall 0.99 / precision
  0.19 — massive over-triggering).
- Planned path per the 2026-06-22 notes: ADTOF-style CRNN + tatum/pattern-LM
  decoding (RWC 3-class F 70.8 → 81.6, arXiv 2010.03749 / 2105.05791).
- Open gate at the time: "pretrained-weight license is UNRESOLVED."

## Finding 1 — ADTOF is license-dead for us, entirely (verified 2026-07-02)

The ADTOF repo README states verbatim: **"The content of this repository is
licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0
International License"** — and a LICENSE file backs it. That statement scopes
to the repository *content*: code, pretrained CRNN models, and the ADTOF
dataset builds.

Consequences, stronger than the 2026-06-22 doc anticipated:

1. Pretrained ADTOF weights: **unshippable** in a commercial product (NC).
2. **Retraining our own model on the ADTOF dataset is also blocked** — the
   dataset itself is NC-ShareAlike, and weights trained on it are at minimum
   legally contaminated, at maximum plainly NC. The fallback recorded in the
   2026-06-22 notes ("we may need to retrain the CRNN on the ADTOF dataset
   for shippable weights") does not survive this reading.
3. ADTOF remains useful as: architecture reference (the Vogl-style CRNN is
   described in the ISMIR 2021 paper, which is citable), and an *internal
   research-only* comparison point never shipped.

## Finding 2 — E-GMD is the permissive training corpus, and it's already on our drive (verified 2026-07-02)

The E-GMD dataset page states verbatim: **"The dataset is made available by
Google LLC under a Creative Commons Attribution 4.0 International (CC BY 4.0)
License."** Commercial use, including training a model for a commercial
product, is permitted with attribution.

- **444.5 hours** of audio, **43 drum kits** (electronic 808/909 through
  acoustic samples), **45,537 sequences** (1,059 unique), human-performed on
  e-kits with paired MIDI **including velocity** — the realism property
  Finding 5 says matters most.
- Already extracted locally at `E:\AudioSourceOfTruthData\extracted\e_gmd`
  and already wired into `gt-benchmark` via the `e_gmd` adapter (task #55/#57
  lineage).
- Known limitation (from the ISMIR 2025 "Performance Limitations" literature
  and the ADTOF paper's own comparisons): models trained on e-kit renders
  transfer imperfectly to real acoustic kits in dense mixes. Mitigation is
  Finding 5's realism levers plus our own rendered-mix corpus.

## Finding 3 — A pretrained E-GMD drum checkpoint is downloadable today (verified 2026-07-02)

The Magenta Onsets & Frames README links a drum checkpoint trained on E-GMD:

```
https://storage.googleapis.com/magentadata/models/onsets_frames_transcription/e-gmd_checkpoint.zip
```

run via `onsets_frames_transcription_transcribe --config="drums"`.

- **Code license:** the magenta repo is Apache-2.0.
- **Checkpoint license: unstated.** The README says nothing about the
  checkpoint's terms. Google's magentadata assets are *commonly treated* as
  following the repo license, but that is an inference, not a grant — same
  diligence class as the penn weights. Benchmarking with it internally is
  fine; **shipping it needs legal sign-off or a replacement we train
  ourselves (Finding 2 makes that possible).**
- **Operational caveats:** the magenta repository was **archived 2026-01-06
  (read-only)** — no fixes coming; the model is TF1-era (estimator/
  tf.contrib lineage), so expect a compatibility shim (run under
  `tf.compat.v1` with TF 2.14, which we already ship for basic-pitch) or a
  one-off conversion. Budget ~a day to get it running as a research engine
  behind our existing `KNOWN_MT3_DRUM_ENGINES`-style optional-adapter
  pattern.

**Why bother:** it's the fastest way to put a *real neural baseline* number
on our E-GMD adapter and the Psalms guard set — the decision anchor for how
much of Finding 4's training effort is justified.

## Finding 4 — Synthetic-only training reaches real-recording SOTA (the strongest strategic result of this pass)

**Source:** "Towards Realistic Synthetic Data for Automatic Drum
Transcription" (arXiv 2601.09520, Melucci/Merialdo/Akama; repo
`pier-maker92/ADT_STR`). Existence/architecture/data manually verified
2026-07-02 from the arXiv full text; F1 numbers fetch-extracted.

- Encoder-decoder Transformer over mel-spectrograms, decoder autoregressively
  emits MIDI tokens (tempo, instrument, velocity). 26-class internal
  vocabulary, evaluated after mapping to an 8-instrument taxonomy.
- Trained **exclusively on synthetic audio**: Lakh MIDI (LMD-matched, 45,129
  files) rendered with a curated one-shot drum sample library (8,495 samples:
  1,421 manually annotated seeds + 7,074 auto-classified from 12 public
  sample packs). Zero real paired data.
- Results on **real** test recordings: overall **F1 0.73 on ENST-Drums, 0.79
  on MDB-Drums** — reported as new state-of-the-art, beating fully-supervised
  prior work.
- **License:** repo is public (training + inference code, configs) but has
  **no LICENSE file** and **no released weights**. The code is unshippable
  as-is; the *recipe* is fully reproducible with assets we control.

**What it means for us:** this is the direct, published validation of the
Ableton-MCP drum strategy — MIDI + good one-shots + realistic rendering is
*sufficient* for strong real-world ADT, no crowdsourced NC dataset needed.
Where they used 12 public sample packs, we have Ableton drum racks, any
sample library we're licensed for, per-hit velocity control, and MIDI-exact
ground truth by construction.

## Finding 5 — The three realism levers that close the synthetic-to-real gap (fetch-extracted)

**Source:** arXiv 2407.19823 ("Towards Realistic Synthetic Drum Data" line of
work, 2024). Fetch-extracted; treat as design guidance, not verified numbers.

(a) **Human performances captured on e-kits** beat offline-annotated MIDI as
the event source (timing/velocity micro-structure matters) — E-GMD is
exactly this; also our `live_capture_midi` MCP tool can capture human takes.
(b) **Render full mixes** with up to four accompaniment instruments rather
than isolated drums — models trained on solo stems over-trigger on bleed,
which is *precisely our hi-hat precision failure on real music*.
(c) **Hundreds of synthesizer/kit presets** rather than a few — timbral
diversity is the drum-domain analogue of guitar amp-tone augmentation.

All three map one-to-one onto the Ableton render farm: E-GMD grooves +
captured takes → many drum racks → mixed with bass/keys/guitar accompaniment
stems (we have psalm stems and can render more) → paired audio/MIDI corpus.

## Finding 6 — Stem-separation-assisted class expansion (fetch-extracted, mixed result)

**Source:** "Enhanced Automatic Drum Transcription via Drum Stem Source
Separation" (arXiv 2509.24853, Riley & Dixon). Paper existence/topic manually
verified; the numbers are fetch-extracted.

- Post-processing ADTOF 5-class output with a 6-stem drum separator expands
  to 7 classes (adds crash/ride distinction) and estimates velocity.
- 8-class F: **0.72 → 0.84 MDB-Drums, 0.65 → 0.76 ENST-Drums, but 0.58 →
  0.56 on RBMA** (electronic drums regress).
- Licensing watch: prior research (2026-06-22 notes) found LarsNet weights
  CC BY-NC; this paper's tooling likely inherits similar constraints —
  verify before any adoption. Class expansion is a v2 concern anyway; the
  core 5-class precision fix comes first.

## Finding 7 — Post-filter evidence check (fetch-extracted)

- Hierarchical LM decoders on piano encoders: only **+0.01 / +0.022 F1**
  (arXiv 2501.03038 line) — weak.
- LLM chain-of-thought MIR post-processing: +1.0–2.8% absolute on chord
  recognition (arXiv 2509.18700) — modest, expensive.
- The **tatum/pattern-LM decoding** result from the 2026-06-22 notes (RWC
  3-class F 70.8 → 81.6) remains the strongest cited post-filter for our
  hi-hat over-triggering, and bolts onto `beat_conditioned_multiband_decoder`
  infrastructure we already have.

Net: prefer beat/tatum-grid decoding + per-class threshold calibration over
learned LM decoders — consistent with what worked for piano (analytical
supplement + consensus, not learned decoding).

## Recommended drum path (replaces the ADTOF plan)

1. ✅ **DONE — Baseline + neural anchor (queue #3).** Stratified E-GMD
   baseline locked (6 engines, per-class + onset-only) and the Magenta E-GMD
   checkpoint benchmarked as a research-only engine on the same 30 cases.
   Result: DSP ≈ 0.284 (0.000 toms), neural 0.535 (toms 0.836). See the
   Empirical validation section above and `benchmarks/drums/`.
2. **Render farm v1 (queue #5):** Ableton corpus with the three realism
   levers — E-GMD grooves through many kits, mixed with accompaniment stems,
   velocity preserved. Target a few hundred hours; MIDI-exact labels by
   construction. *(Optional augmentation on top of E-GMD, not a prerequisite.)*
3. 🔄 **IN PROGRESS — Train in-house (queue #6):** permissive-stack CRNN on
   E-GMD (CC BY 4.0, attributed). Harness landed at
   `python/ingest/src/aural_ingest/training/drum_crnn/` (compact CRNN, ONNX
   export). First real training run underway on a diversity-strided E-GMD
   subset; the trained model is decoded to events and scored on the stratified
   30 against the **0.535 floor** before any production wiring. Own code, own
   weights → modelpack under `assets/models/…`.
4. **Decode + calibrate:** tatum-grid decoding post-filter; per-class
   thresholds calibrated on the guard set (attack the residual hi-hat
   over-triggering directly — it survives into the neural model too).
5. ✅ **DONE — `research-decision-gates.md` corrected** (2026-07-02): the
   "ADT training-source correction" section replaces path 2's "CRNN trained
   on ADTOF or YourMT3+" with in-house E-GMD training (ADTOF NC-blocked,
   YourMT3+ GPL-blocked), with the verified 0.535 floor recorded.

Non-goals for now: 5→7-class expansion via stem separation (Finding 6 — v2,
license-watch), diffusion ADT (no permissive implementation surfaced this
pass), Omnizart (3-class MIDI output, poor fit, per 2026-06-22 notes).
