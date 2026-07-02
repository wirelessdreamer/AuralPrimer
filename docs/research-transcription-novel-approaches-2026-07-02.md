# Research — Novel transcription approaches, synthesis + implementation queue (2026-07-02)

Deep, multi-source, adversarially-verified research pass over our three open
F1 problems (guitar precision, bass recall, drum precision). 105 research
agents across 5 search angles; 23 primary sources fetched; 115 falsifiable
claims extracted; 25 adversarially verified by 3-vote refutation panels
(22 confirmed, 3 refuted); drums/post-processing claims that lost the
verification-budget lottery were manually re-verified 2026-07-02 against
primary artifacts (repo LICENSE files, arXiv full texts, dataset pages).

Companion detail docs (read these for per-problem depth):

- [research-guitar-distortion-precision-2026-07-02.md](research-guitar-distortion-precision-2026-07-02.md)
- [research-bass-f0-upgrade-2026-07-02.md](research-bass-f0-upgrade-2026-07-02.md)
- [research-drums-license-resolution-2026-07-02.md](research-drums-license-resolution-2026-07-02.md)

Prior art in-repo this pass builds on (and partially supersedes):
`research-guitar-bass-2026-06-22.md`, `research-adt-drums-2026-06-22.md`,
`research-decision-gates.md` (ADT Architecture Revision section),
`benchmarks/guitar/GUITAR_TRANSCRIPTION_EPIC.md`.

## The one-line takeaway

**The strategies survived verification; the implementations didn't.** Every
high-value reference implementation found is license-blocked (GPL, LGPL,
CC-NC, or no license file at all), but every underlying *strategy* is
reproducible license-clean in-house — because we own a fully scriptable
Ableton Live render farm (MCP/OSC), the CC-BY-4.0 E-GMD dataset is already
on disk, and our benchmark harness already measures note-level F1 on real
ground truth. The unlock is treating Ableton as a labeled-data factory.

## License verdict table (all verified from actual LICENSE files / wheel metadata / model-card YAML, not README badges)

| Component | Code license | Weights license | Verdict for commercial pipeline |
|---|---|---|---|
| **penn (FCNF0++)** | MIT (LICENSE verified) | HF repo tagged MIT; no in-repo weights license; trained partly on MDB-stem-synth (MedleyDB is CC BY-NC-SA) | ✅ **Ship** — with a weights-provenance diligence flag |
| **SwiftF0** | MIT (LICENSE verified) | MIT — 399 KB ONNX ships inside the MIT pip wheel | ✅ Cleanest license story of any candidate; blocked for bass by 46.875 Hz floor |
| **E-GMD dataset** | — | CC BY 4.0 (Google LLC, verbatim statement) | ✅ **Commercial retraining allowed** with attribution; already at `E:\AudioSourceOfTruthData\extracted\e_gmd` |
| Magenta Onsets&Frames code | Apache-2.0 | **Checkpoint license unstated**; repo archived 2026-01-06 (read-only) | ⚠️ Usable as research baseline now; legal diligence before shipping the checkpoint |
| PESTO | LGPL-3.0 (LICENSE.md verified) | In-repo ckpts inherit LGPL | ❌ LGPL relink obligation is awkward in a PyInstaller one-file sidecar |
| MERT-v1-95M | Apache-2.0 (repo) | **CC BY-NC 4.0** (authors' own HF model card) | ❌ Foundation-model backbone route license-dead at its main entry |
| YourMT3 / YourMT3+ | GPL-3.0 (LICENSE verified) | Conservative default: GPL | ❌ Research reference / distillation teacher only |
| ADTOF (code + models + dataset) | CC BY-NC-SA 4.0 (README applies to entire repo content) | Same | ❌ **Planned drum path is dead** — even in-house retraining *on the ADTOF dataset* is NC-blocked |
| Open-Amp | **No license file** (GitHub API `license: null`, verified across both branches) | Proteus captures come from a GPL-3.0 GuitarML repo | ❌ Strategy reproducible via Ableton; implementation unshippable |
| almost_unsupervised_amt | **No license file**; no released checkpoints | — | ❌ Method paper only (reimplement to use) |
| ADT_STR (synthetic-drums SOTA) | **No license file**; no released weights | — | ❌ Recipe reproducible in-house; code unshippable |
| DAFx-2024 de-distortion | **No code released at all** | — | ❌ Architecture recipe to reimplement |

## Verified findings, ranked by expected F1 impact for us

1. **Bass: replace torchcrepe with penn/FCNF0++** (verified 3-0 ×6, two 2-1).
   31–1984 Hz default range covers low E (41.2 Hz) outright; 12.72 vs 59.40
   cents pitch error, voicing F1 .9816 vs .9293 vs torchcrepe on
   MDB-stem-synth+PTDB; ~11× real-time on CPU (7.5× faster than torchcrepe,
   same-author benchmark, same rig). Near-drop-in: mirrors our
   `melodic_torchcrepe.py` adapter shape. *Caveat: frame-level metrics —
   converting to note-level recall still needs the segmentation layer (see
   bass doc §5).*

2. **Drums: E-GMD is the permissive path, ADTOF is dead** (manually verified
   2026-07-02). ADTOF's CC BY-NC-SA covers code, pretrained CRNN models, and
   the dataset itself — the production plan recorded in
   `research-decision-gates.md` ("CRNN trained on ADTOF") cannot ship. E-GMD
   (CC BY 4.0, 444.5 h, 43 kits, human-performed with velocity) is the
   drop-in replacement training corpus and is already on our drive. A
   pretrained Magenta E-GMD checkpoint is downloadable today for immediate
   A/B baselining (license unstated — diligence before shipping).

3. **Guitar + drums: the Ableton-MCP synthetic-data factory is externally
   validated.** Three independent lines of verified evidence:
   - Synthetic-only drum transformer (Lakh MIDI + one-shot samples, zero
     real paired data) hits **F1 0.73 ENST / 0.79 MDB** — at or above
     supervised SOTA on real test sets (arXiv 2601.09520).
   - Procedural synthetic guitar pretrain → finetune on 1/5 of GuitarSet
     yields **note F1 77.45% vs 63.32%** from-scratch (+14.1 absolute,
     arXiv 2508.07987, AIMC 2025).
   - Amp-emulation augmentation amplifies 35 min of clean guitar into ~230 h
     (~394×) of labeled effected audio (Open-Amp, ICASSP 2025); YourMT3+
     stem-mixing ablations add +4.8/+5.5 onset/offset F1 (intra-stem) and
     +4.0/+7.2 more (cross-stem) on Slakh (3-0 verified).
   - Cycle-consistency result: even *unlabeled* guitar audio lifts guitar
     frame F1 +10.0 absolute with zero paired guitar labels; 1.6 h of paired
     data anchors 86.3% of fully-supervised performance (arXiv 2605.24193,
     medium confidence — recent preprint).

4. **Guitar: de-distortion front-end is feasible but unproven for
   transcription** (3-0 ×2). DAFx-2024 two-stage model recovers clean guitar
   from VST-distorted signal at SI-SDR 28.65 (baselines 2.98–6.21) — but
   nobody has measured whether transcribing the de-distorted audio raises
   note F1, and no code exists. We are unusually well-positioned to answer
   this: mass-render paired dry/wet through Ableton amp sims, train the
   front-end, A/B `guitar_basic_pitch_playable` on raw vs de-distorted
   Guitar-TECHS micamp.

5. **Cross-cutting: consensus voting validated at the f0 layer; LM decoding
   evidence is weak.** A 9-tracker voting ensemble with pre-vote alignment
   beats the best individual tracker (RPA5 29.01% vs CREPE's 13.07%;
   ICASSP 2026, fetch-verified). Meanwhile hierarchical LM decoders add only
   +0.01/+0.022 F1 on piano benchmarks and LLM chain-of-thought
   post-processing adds 1–2.8% on chords — both modest. This matches our
   in-house experience: piano hit F1 0.928 via consensus + analytical chord
   supplement, not via learned decoders. Keep investing in consensus and
   analytical supplements; deprioritize LM-decoder work.

## Refuted claims — do not cite these

| Claim | Vote | Why it matters |
|---|---|---|
| MusicFM-backboned MIROS "won the 2025 AMT Challenge with Slakh F 0.83" | 0-3 | No surviving affirmative evidence for foundation-model transcription backbones; combined with MERT's NC license, that route is closed on both evidence and licensing |
| FCNF0++ "natively covers 30–1000 Hz" | 0-3 | The real verified range is **31–1984 Hz** (1440 × 5-cent bins from FMIN=31); use those numbers |
| DAFx paper's 80 h BIAS-FX2 corpus "validates the commercial amp-sim data strategy" | 1-2 | Amp-sim-strategy validation rests on Open-Amp + YourMT3+ ablations + the unlabeled-adaptation preprint, not the de-distortion paper |

(Also previously refuted in the 2026-06-22 pass and still dead: the ~0.61
"clean-DI ceiling" for guitar, "augmentation beats tone-conditioning as the
single biggest factor", ADTOF weights "CC BY 4.0".)

## Implementation queue (recommended order)

| # | Work item | Problem | Expected lift | Effort | Blocked by |
|---|---|---|---|---|---|
| 1 | Benchmark composed `guitar_auto` (cleanup + chord supplement now co-located) on full Guitar-TECHS micamp + DI | Guitar | measure the ~2× smoke lift properly; DI now meaningful post-24-bit-fix | one `gt-benchmark` run | nothing |
| 2 | `melodic_penn` adapter (mirror `melodic_torchcrepe.py`) + A/B on GuitarSet low-strings | Bass | frame-level gains are 4.7× cents-error; note-level TBD | ~half day | nothing |
| 3 | Download Magenta E-GMD checkpoint, wire as research-only drum engine, A/B vs `combined_filter` on E-GMD + Psalms guard set | Drums | unknown → first neural baseline | ~1 day (TF1-era estimator stack) | checkpoint-license diligence before *shipping* (not before benchmarking) |
| 4 | Bass note-segmentation layer A/B (envelope-gated vs CQT joint decode) on top of penn f0 | Bass | converts f0 wins into recall — the actual KPI | ~1–2 days | #2 |
| 5 | Ableton render farm v1: drum corpus (MIDI grooves → drum racks → full mixes w/ accompaniment) per the three realism levers | Drums | enables #6 | ~2–3 days of MCP scripting | nothing |
| 6 | Train in-house permissive drum CRNN on E-GMD + rendered corpus (own code, own weights, CC-BY attribution) | Drums | targets the 0.7–0.9 strong-ADT band vs our real-world ~0.15–0.4 | the big one — ~1–2 weeks | #3 (baseline), #5 |
| 7 | Ableton render farm v2: paired dry/wet guitar corpus → train de-distortion front-end → A/B transcription F1 | Guitar | unmeasured in literature; we'd be first | ~1 week | #1 (baseline) |
| 8 | f0-layer voting ensemble (penn + torchcrepe + pYIN median vote w/ alignment) for bass/lead | Bass/Guitar | RPA5 2.2× in paper; cheap to try | ~1 day | #2 |

Items 1–3 are immediately actionable and independent — they can run in
parallel agents like the guitar-upgrade round did.

## Methodology + provenance note

Claims labeled "3-0"/"2-1" passed the adversarial refutation panel in the
deep-research workflow (each vote is an independent agent instructed to
refute). Claims labeled "manually verified 2026-07-02" were checked by the
integrating session directly against primary artifacts after the panel's
budget cutoff dropped them. Claims labeled "fetch-extracted" came from a
single full-text extraction agent and were *not* independently verified —
they are marked as such in the detail docs. All license determinations are
time-sensitive: re-verify immediately before shipping anything, and route
the penn weights-provenance question (MDB-stem-synth ← MedleyDB CC BY-NC-SA
training data; dataset-to-weights license contamination is legally
unsettled) to counsel if strict compliance is required.
