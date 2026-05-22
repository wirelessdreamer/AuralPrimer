# Research deep-dive: ADT/transcription assumptions vs. 2024–2025 literature

**Date:** 2026-05-07
**Trigger:** sparse-source kick-classification regression observed in
`combined_filter`; need to verify the current ingest architecture is not
operating off flawed premises before iterating.

This note tests each architectural assumption baked into
`python/ingest/src/aural_ingest/algorithms/` and `transcription.py` against
recent published work, then revises the paths-forward list.

---

## 1. Assumption survey

The current ingest pipeline embeds several architectural choices, mostly in
`combined_filter`, `_common.py` post-classification, the orchestration
fallback chain, and the melodic algorithm registry:

1. **A1 — Heuristic three-detector fusion is a viable production path** for
   drum classification (combine `dsp_bandpass_improved`, `dsp_spectral_flux`,
   `aural_onset` with fixed source weights `1.0 / 0.8 / 0.6`).
2. **A2 — A 9-class drum taxonomy** (kick, snare, hh_closed, hh_open, crash,
   ride, tom_high, tom_low, tom_floor) is the right output target.
3. **A3 — Hand-tuned spectral-centroid thresholds** (e.g. `centroid > 520 Hz
   → tom_floor`) are reliable enough to ship as default classification rules.
4. **A4 — Source-weighted DSP fusion can outperform the single best detector
   on hard cases.**
5. **A5 — Stem separation (Demucs) is best-effort optional**; absence falls
   back to mix audio and import continues.
6. **A6 — Cymbals/hi-hats are roughly as tractable as kick/snare.**
7. **A7 — Overlapping drum hits are a post-processing concern** handled by
   per-class refractory periods.
8. **A8 — pYIN is a sufficient melodic transcription baseline** for
   monophonic stems, with a deterministic dyad-expansion path covering
   light polyphony.
9. **A9 — DSP determinism is a meaningful product advantage over neural
   methods.**
10. **A10 — Real-time and offline transcription want the same algorithms**,
    so the ingest pipeline algorithm choices are also the gameplay pipeline
    choices.

---

## 2. What the literature actually says (2024–2025 snapshot)

### A1: heuristic three-detector fusion → CONTRADICTED

Modern ADT survey work (Wu, Dittmar, Southall, Vogl, Widmer, Hockman,
Müller, Lerch — 2018) already concluded that heuristic methods plateau and
that "reliable performance can be expected from state-of-the-art systems"
only on drum-only recordings. Every subsequent year (2019–2025) the
state-of-the-art entries on ENST-Drums, IDMT-SMT-Drums, MDB-Drums, RBMA,
and ADTOF have been neural — initially CRNN (ADTOF), then transformer
(YourMT3+, hFT-Transformer), and most recently diffusion (Noise-to-Notes,
Sept 2025, the first generative model to beat discriminative ADT). No
recent paper reports a heuristic three-detector fusion as competitive.

The mode of failure we observed is a known issue: when individual
detectors disagree on class for the same onset, fixed source weights
amplify the loudest (most-emitting) detector even when its precision is
worst. Modern systems either (a) train end-to-end so disagreement never
arises, or (b) use learned, score-level Bayesian fusion rather than fixed
weights.

### A2: 9-class drum taxonomy → NON-STANDARD

The dominant ADT taxonomies are:

- **5-class** (kick, snare, hi-hat, toms, cymbals): ADTOF, Enhanced-ADT
  via Stem Separation (2025), almost all production pipelines.
- **8-class** (extends with open/closed hh, ride/crash): MDB-Drums,
  IDMT-SMT-Drums.
- **18-class** (the established expanded benchmark): used by recent
  expanded-vocabulary papers and Few-Shot Drum Transcription (Wang &
  Salamon, 2020). A 26-class derivation exists from the GM percussion key
  map.

Our 9-class output (5 + open hh + ride + 2 toms) doesn't match any
benchmark. That makes it (a) harder to evaluate against published
metrics, (b) impossible to drop in pre-trained models without remapping,
and (c) less robust because we have less training-data-equivalent for the
extra classes. The 5-class and 18-class taxonomies are the two stable
points; 9 is in the unstable middle.

### A3: hand-tuned centroid thresholds → STALE TECHNIQUE

McDonald-style spectral-centroid features were introduced in the early
2000s and were considered SOTA into the mid-2000s. Modern systems learn
timbral representations end-to-end. The Towards Realistic Synthetic Data
for ADT paper (Jan 2026) explicitly identifies "controlled-recording-only
datasets" as the reason centroid-threshold systems break on real audio:
real recordings have mic distance, room ambience, kit variability, and
overlapping bleed that perturb centroid by hundreds of Hz, making any
hand-set 520 Hz threshold unreliable.

Our specific bug — an isolated 58 Hz sine kick whose attack transient
pushes centroid above 520 Hz, triggering kick→tom_floor demotion — is
exactly this failure mode in miniature.

### A4: weighted DSP fusion vs. single best detector → CONTRADICTED IN OUR DATA

Our own test exposed this: `aural_onset` alone scores 100% kick on the
kick-only synthetic. The three-detector fusion scores 0% kick on the
exact same input. Fusion is actively destroying signal because the two
detectors with higher weights also have higher false-positive crash
rates, and there is no learned veto. The Wu-Lerch survey notes the same
risk in the abstract (heuristic fusion can make the worst detector's
biases dominate); our code instantiates that risk.

### A5: Demucs as best-effort optional → SHOULD BE FIRST-CLASS

Recent ADT pipelines treat drum-stem isolation as a first-class
preprocessing step, not optional:

- Enhanced ADT via Drum Stem Source Separation (Sep 2025) reports +5–10%
  F1 from Demucs v4 preprocessing alone, before the transcription model
  changes.
- The Inverse Drum Machine (May 2025) integrates separation and
  transcription end-to-end via analysis-by-synthesis.
- Toward Deep Drum Source Separation / LarsNet provides 5-stem isolation
  inside the drum stem (kick / snare / hh / toms / cymbals), which is
  another lever we haven't exploited.

Our portable build already stages a Demucs 6-stem modelpack, but
`docs/research-decision-gates.md` keeps Demucs as "optional experimental
under `auto`." That decision gate is older than the 2025 papers and
should probably be revisited: separation should be required for the
production drum default, with a CPU-budget fallback only when Demucs is
genuinely missing.

### A6: cymbals-are-tractable → CONTRADICTED

ISMIR 2025 "Understanding Performance Limitations in ADT" (Fraunhofer)
reports across all evaluated methods that better F1 is achieved for kick
and snare than for hi-hat and cymbals — driven by the larger sound space
for hi-hats (open vs. closed) and the broadband, decaying spectra of
cymbals. The Towards Realistic Synthetic Data paper concurs.

Our specific failure (kick→crash, not crash→kick) is a known artefact:
cymbal classifiers tend to false-positive on broadband transients, and
weak training-data coverage for cymbals means the decision boundary leans
permissive. This is a structural problem in any classifier that doesn't
explicitly suppress crash on low-band-dominant onsets.

### A7: refractory periods solve overlapping hits → CONTRADICTED

ISMIR 2025 "Performance Limitations" paper isolates four bottlenecks and
concludes that **overlapping drum hits are the dominant performance
constraint**: when simultaneous onsets are removed from the test set,
ADT becomes "nearly error-free." Per-class refractory windows (our
current approach in `CLASS_REFRACTORY_SEC`) only resolve same-class
chatter, not the harder problem of two different drums hit at the same
millisecond. Modern systems address this with multi-label outputs at each
frame plus joint onset-class CRNN modeling.

### A8: pYIN as sufficient melodic baseline → BORDERLINE

pYIN remains a reasonable monophonic baseline (our quality dimensions 7
and 8 pass on a clean A3 sine). But on real material the gap to Basic
Pitch / CREPE is large:

- Spotify Basic Pitch (2022) was published showing it "substantially
  beats" a comparable baseline on multi-pitch + note + pitch-bend
  jointly. It's open-source, lightweight (≤20 MB ONNX), and CPU-friendly.
- CREPE outperforms competitor methods by >8 percentage points at
  10-cents pitch tolerance — relevant for in-game pitch-accuracy
  scoring, not just transcription.
- Multi-pitch CRNN architectures (2024) handle polyphonic stems where
  pYIN+dyad-expansion collapses to monophonic.

We already have `basic_pitch` in `KNOWN_MELODIC_METHODS`, but the recent
review (transcription quality program in wip.md) flags that the
deterministic stub does NOT actually run Basic Pitch inference. That is
the single biggest melodic-quality gap.

### A9: DSP determinism is a real advantage → MOSTLY MYTH

Pinned-weight neural models with fixed random seeds and deterministic
inference modes (PyTorch deterministic ops, ONNX runtime CPU) produce
bit-identical output across runs. ADTOF, Basic Pitch, MT3, YourMT3+,
hFT-Transformer all ship as pinned weights. Our `import_pipeline`
fingerprint test would still work. The only real determinism cost is on
GPU CUDA non-determinism, which we already document and gate.

The deterministic-DSP framing is therefore not a structural advantage
over modern ML; it just feels safer because the algorithms are easier
to read.

### A10: real-time and offline want the same algorithms → CONTRADICTED

The ingest pipeline runs offline as a sidecar, with no per-frame latency
budget. There's no reason it can't use heavier models (YourMT3+ at
~2-3× real-time, ADTOF CRNN ~10× real-time on CPU, hFT-Transformer
similar). Real-time constraints belong to the gameplay-input pipeline
(realtime audio→MIDI for live performance), which is a separate code
path with much smaller scope.

Conflating the two has been silently capping the ingest pipeline at
"DSP that runs in 100ms per stem" when we could comfortably spend
30s per stem on a heavy model.

---

## 3. Things we genuinely missed

Recent work that should sit on the radar even if we don't integrate it
this quarter:

- **Noise-to-Notes (N2N), Sept 2025.** Diffusion-based ADT, new SOTA. 5
  sampling steps already saturates E-GMD; 10 steps adds further headroom.
  First generative model to beat discriminative on this task.
- **Enhanced ADT via Drum Stem Source Separation, Sept 2025.**
  Demucs v4 → ADTOF, 5 classes, +5–10% F1 versus ADTOF alone. This is
  almost exactly the architecture we should consider as our drum
  default.
- **The Inverse Drum Machine, May 2025.** Joint separation + transcription
  via analysis-by-synthesis. Probably architecturally heavier than we
  need, but interesting for the longer roadmap.
- **STAR Drums dataset, 2024–2025.** Synthetic-but-realistic dataset
  designed for the drum-kit-variability problem. Could replace some of
  our hand-built test fixtures, with the caveat that synthetic data
  rarely transfers fully to real audio.
- **Real-time ADT with dynamic few-shot learning, Weber et al. 2024.**
  Relevant to the gameplay-input pipeline, not the ingest pipeline; lets
  users teach the system their own kit with a few examples.
- **LarsNet / 5-stem isolated drum separation, 2023+.** Inside the drum
  stem, isolate kick / snare / hh / toms / cymbals separately. If we ever
  want to ship a "drums-only" practice mode this is the path.
- **Few-Shot Drum Transcription in Polyphonic Music, Wang & Salamon
  2020.** Open-vocabulary scenario where the system must transcribe
  classes it has never seen during training. Relevant if we want users
  to add custom drum kits.

---

## 4. Revised top-10 paths forward

Reordered by leverage given the literature scan. Items 1–4 are the
highest-leverage moves; 5–7 are stop-gap fixes that buy time; 8–10 are
longer-horizon.

1. **Adopt a 5-class output as the production default**, with our
   existing 9-class as an internal/research extended option. Map current
   downstream consumers (gameplay metrics, charts, plugin contracts) to
   the 5-class version. Reverts our taxonomy onto the standard line and
   makes pre-trained models drop-in-able.

2. **Replace the production drum default with a small CRNN trained on
   ADTOF**, or directly integrate the ADTOF package. ~5 MB ONNX runtime,
   CPU-friendly, deterministic with pinned weights, and reports the
   strongest published F1 on real audio for 5-class. Keep
   `combined_filter` as `--drum-engine combined_filter` for research
   A/B, never as the default.

3. **Make Demucs preprocessing required for the production drum path,**
   with a documented exception when the modelpack is missing (degraded
   warning, falls back to mix). The portable build already stages
   `demucs_6.zip`; the runtime check already exposes it; the gating
   logic just needs to flip from optional to required-or-warn.

4. **Wire YourMT3+ (already in `KNOWN_MT3_DRUM_ENGINES`) into the
   orchestration as `--profile fidelity_midi` first-pass.** Drop-in
   model, reports +F1 over the original MT3, multi-instrument
   compatible, weights-available. CPU inference at ~2–3× real-time is
   acceptable for offline import.

5. **Drop the centroid > 520 Hz → tom_floor rule**, replace with a
   low-band-energy guard that prevents kick → crash classification when
   `low/total > 0.6`. Even if we keep `combined_filter` as a research
   filter, this stops the specific kick→crash hallucination that
   produced the Psalm 12 report.

6. **Re-weight three-detector fusion to favor unanimous detector
   votes.** Even a small change (when one detector is class-unanimous
   across all candidates, raise its weight to ≥1.5) would have flipped
   the kick fixture from 0% to 100% kick. Cheap, isolated, and shippable
   without architectural change.

7. **Build a real Psalm 12-equivalent fixture into the guard manifest
   (`benchmarks/quality/full_corpus_manifest.guard.template.json`
   `guard_drums_TBD`).** Synthetic guards are good for catching
   pathological regressions; only a real-audio guard can catch the
   broadband-transient false-positive class.

8. **Replace deterministic melodic stubs with real Basic Pitch / pYIN
   inference.** The current stubs produce one note per stable pitch
   region; Basic Pitch handles polyphonic stems and pitch bends, which
   matters most for guitar/keys. Already in `KNOWN_MELODIC_METHODS`,
   just not wired.

9. **Adopt overlapping-onsets handling at the model level rather than
   refractory periods.** This requires a model that emits multi-label
   per frame (CRNN, transformer); refractory windows can't separate two
   different drums fired at the same millisecond. ISMIR 2025 says this
   is THE dominant performance constraint.

10. **Add a sync-quarantine import gate** (already in our previous list).
    Most-likely impact still depends on real-fixture coverage.

---

## 5. Risks and counter-evidence

- **Model size/distribution.** ADTOF CRNN is small. YourMT3+ is heavier
  (≈100 MB). hFT-Transformer is also larger. We do not bundle model
  weights; they must come via the model manager. Confirm the model-pack
  flow handles the bigger weights.
- **CPU-only deployment.** Several of these models assume GPU is
  available for training but ship with CPU-friendly inference. Validate
  on the `minimum_modern` baseline (8 logical cores, 16 GB RAM, no GPU)
  before committing to defaults.
- **Datasets are research-only.** ENST-Drums, IDMT-SMT-Drums, MDB-Drums
  are not redistributable. Our quality benchmark fixtures must remain
  synthetic or owned/cleared. The good news is that ADTOF was trained on
  weakly-labeled data and the released model weights do not encode
  training audio, so the model itself is shippable.
- **Determinism.** All recommended models support pinned weights +
  deterministic inference; verify on a stem in CI. PyTorch deterministic
  ops are documented but cost ~10% throughput on some kernels.
- **The kick→crash bug may be partially in the synthetic fixture**,
  not just the algorithm. A 58 Hz pure sine has unusually narrow
  spectrum compared to real kicks (which have broadband attack noise +
  60–120 Hz body). Real-fixture validation (path 7) is needed before
  treating the synthetic result as a definitive classifier verdict.

---

## 6. Suggested next concrete change

Smallest action that buys the most signal:

1. Implement path 5 (drop centroid threshold, add low-band-energy
   guard) in `combined_filter`. Verify the test #2 xfail flips to xpass.
2. In parallel, scope path 2 (ADTOF integration). Estimate model size,
   inference time on the `minimum_modern` baseline, and how the
   model-pack flow needs to extend.
3. Defer paths 1, 3, 4 until path 2 lands so the taxonomy + separator +
   model-engine changes ship together as a coherent v0.2 of the drum
   transcription stack.
