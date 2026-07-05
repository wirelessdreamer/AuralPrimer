# Research — Bass: fixing the low-string recall choke (2026-07-02)

Detail notes from the 2026-07-02 deep-research pass. Synthesis + ranked queue:
[research-transcription-novel-approaches-2026-07-02.md](research-transcription-novel-approaches-2026-07-02.md).
Builds on `research-guitar-bass-2026-06-22.md` (which shipped torchcrepe) and
the 2026-07-01 fmax-clamp polish.

## Where we are (measured in-repo, 2026-07-01)

GuitarSet hex-debleeded low-string corpus, 60 cases, strict pitch, 50 ms
onset tolerance:

| Algorithm | F1 | P | R | Octave-slack* |
|---|---:|---:|---:|---:|
| melodic_torchcrepe (fmax 200 Hz clamp) | **0.214** | 0.452 | **0.140** | 20% |
| melodic_combined | 0.174 | 0.140 | 0.230 | 57% |
| melodic_pyin_bass_strict (prior default) | 0.144 | 0.183 | 0.118 | 10% |

*Octave-slack = 1 − (strict F1 / octave-forgiving F1).

**Recall (0.140) is the bottleneck** — six of seven real bass notes are
simply missed. Precision is comparatively healthy. Two distinct failure
layers: (a) the f0 tracker misses or mis-voices low fundamentals; (b) the
note-segmentation layer fails to turn voiced f0 streams into note events.

## Finding 1 — penn/FCNF0++: the strongest verified torchcrepe replacement

**Sources:** github.com/interactiveaudiolab/penn; arXiv 2301.12258
(ICASSP 2023). Panel-verified: 2-1 (range), 3-0 (accuracy), 3-0 (CPU speed),
3-0 ×3 (license/packaging).

**Range.** Default analysis range **fmin 31.0 Hz → fmax 1984 Hz** (1440
pitch bins × 5 cents = 6 octaves from FMIN=31, verified in both README and
`penn/config/defaults.py`). Covers 4-string low E (41.2 Hz) outright and
5-string low B (30.9 Hz) within ~7 cents of the floor — inside 50-cent note
tolerance. Our torchcrepe config clamps fmax at 200 Hz to suppress 2·f0
doubling; penn's decoder is trained with low fundamentals in-distribution
instead of being range-clamped after the fact.

> Refuted 0-3: "natively 30–1000 Hz". Use 31–1984 Hz only.

**Accuracy (frame-level, MDB-stem-synth + PTDB).**

| Metric | FCNF0++ | torchcrepe |
|---|---:|---:|
| Pitch error (cents) | **12.72** | 59.40 |
| Raw pitch accuracy | **.9825** | .9103 |
| Voicing-detection F1 | **.9816** | .9293 |

Same-author benchmark (Max Morrison wrote both penn AND torchcrepe), same
hardware — unusually fair comparison. **Disclosed confound:** FCNF0++
trained on both test domains while torchcrepe's CREPE weights lack PTDB
speech, so don't extrapolate the 4.7× cents-error gap raw to bass guitar
(out-of-domain for both). The voicing-F1 gap (.9816 vs .9293) is the number
most relevant to our recall problem — missed voicing = missed notes.

**CPU feasibility.** RTF 0.0861 on a 10-core i9-9820X (~11–12× real-time),
~7.5× faster than torchcrepe's 0.6435 on the same rig; 408× real-time on
GPU. Corroborated by independent TISMIR 2025 measurements.

**License + packaging (exactly matches our constraints).**
- Code: MIT — actual LICENSE file reads "MIT License / Copyright (c) 2022
  Interactive Audio Lab" (raw file fetched, not the badge).
- Wheel: penn-1.0.0, 63 KB, contents inspected — **zero weight files**.
- Weights: auto-download on first inference via
  `huggingface_hub.hf_hub_download('maxrmorrison/fcnf0-plus-plus', 'fcnf0++.pt')`;
  the HF repo is tagged `license: mit`. Fits "no bundled weights in
  installer, post-install modelpack download OK" — invoke the download as an
  explicit modelpack step, or pass a checkpoint path.
- **Diligence flag:** no weights-specific license file in-repo, and training
  data includes MDB-stem-synth (derived from CC BY-NC-SA MedleyDB audio).
  Dataset-to-weights license contamination is legally unsettled — re-verify
  before shipping; route to counsel if strict compliance required.

**Integration:** mirror `melodic_torchcrepe.py` as `melodic_penn.py`
(same MelodicNote interface, same silence gating, same instrument fmin/fmax
map — but let bass keep the full 31–1984 Hz native range and rely on penn's
voicing rather than an fmax clamp). Register in `KNOWN_MELODIC_METHODS`; A/B
via `gt-benchmark` on the same 60-case corpus. ~Half a day.

## Finding 2 — SwiftF0: cleanest license in the field, structurally blocked for low bass

**Sources:** github.com/lars76/swift-f0; arXiv 2508.18440;
github.com/lars76/pitch-benchmark. Panel-verified 3-0 ×4 (merged).

- **License:** MIT code AND weights — the ~96 K-parameter, 399 KB ONNX model
  ships *inside* the MIT pip wheel (`swift_f0-0.1.2`, METADATA
  `License-Expression: MIT`, dist-info/licenses/LICENSE inspected). Freely
  repackageable into a modelpack. The cleanest story of any candidate.
- **Speed/accuracy:** top-ranked across the author's 8-dataset benchmark
  (90.2% harmonic-mean accuracy vs PENN 84.8%, CREPE 85.3% — author's own
  benchmark, salt accordingly); 16.2 ms CPU per second of audio (~90× faster
  than CREPE). Fetch-extracted numbers, not panel-verified.
- **The blocker:** detection floor is **hard-limited to 46.875 Hz** — the
  STFT front-end discards bins below it (input hard-sliced to bins 3–134)
  and the 200-bin log-spaced classification head spans 46.875–2093.75 Hz.
  `fmin`/`fmax` params only filter voicing flags; they cannot extend range.
  Low E1 (41.2 Hz) and 5-string B0 (30.9 Hz) fundamentals are
  **unrepresentable**; expected failure mode is octave-up locking (2nd
  harmonics at 82.4/61.7 Hz ARE in-range). Training/eval corpora contain no
  bass-register data. No extended-range variant exists (checked 2026-07-02).
- **Possible workarounds** (untested anywhere): (a) resample input 2×
  (pitch-up an octave), track, then shift output down 12 semitones —
  costs high-end range we don't need for bass; (b) deliberate
  harmonic-tracking decode (+12 correction when envelope says low string).
  Only worth trying if penn disappoints, or as a consensus voter.

## Finding 3 — PESTO: technically attractive, LGPL-disqualified

**Source:** github.com/SonyCSLParis/pesto. Panel-verified 3-0 ×2.

- ~12× faster than real time on a laptop CPU (2 m51 s file in 13 s at 10 ms
  hop, i7-1185G7); 28.9 K parameters; best RTF of compared trackers in
  peer-reviewed TISMIR 2025 measurements.
- **LGPL-3.0** — verified from LICENSE.md via the GitHub license API and the
  PyPI `LGPLv3` classifier. In-repo mir-1k checkpoints inherit it. LGPL's
  relink/replacement obligation is specifically awkward for a PyInstaller
  one-file frozen sidecar. Excluded unless we accept LGPL compliance
  engineering or negotiate terms.

## Finding 4 — Voting at the f0 layer: externally validated

**Source:** "Voting-based Pitch Estimation with Temporal and Frequential
Alignment and Correlation Aware Selection" (arXiv 2602.01727, accepted
ICASSP 2026). Existence/topic manually verified 2026-07-02; numbers are
fetch-extracted (not panel-verified).

- Median-vote ensemble over 9 estimators (RAPT, SWIPE', pYIN, DIO, REAPER,
  Harvest, Praat, CREPE, FCNF0++) with pre-vote temporal/frequential
  alignment and greedy correlation-aware subset selection.
- Beats the best individual estimator in clean conditions on fine
  tolerances: **RPA5 29.01% vs CREPE's 13.07%; RPA25 66.89% vs 50.68%**
  (speech + MIR-1K singing + MDB-stem-synth).
- Two transferable design points: (1) align estimators before voting —
  systematic per-tracker bias otherwise poisons the median; (2) choose the
  voter subset by error *decorrelation*, not individual accuracy.

**What it means for us:** direct external validation of the consensus
instinct that got piano to F1 0.928. A cheap experiment: median-vote
penn + torchcrepe + librosa-pYIN f0 streams (aligned) before note
segmentation, keeping voiced frames where ≥2 of 3 agree within a semitone.
Attacks octave-slack and voicing false-negatives at once.

## Finding 5 — The remaining gap nobody's paper answers: f0 → note events

Every quantitative win above is frame-level (cents, RPA, voicing F1). Our
KPI is note-level F1 at 50 ms onset tolerance. Nothing published measures
penn or SwiftF0 on actual bass-guitar audio, and none of them emit notes.
The segmentation layer is ours to build and A/B:

1. **Envelope-gated segmentation** (current shape): note on when voiced f0
   stabilizes AND local RMS clears a floor. Cheap; likely part of today's
   recall loss (low-string RMS builds slowly — attack can precede stable f0
   by 30–60 ms).
2. **Onset-primed segmentation:** low-frequency-tuned onset detector
   (spectral flux weighted below ~350 Hz, longer windows) proposes onsets;
   f0 stream assigns pitch in a window after each onset. Decouples "when"
   from "what pitch" — directly targets the slow-attack recall failure.
3. **CQT joint decode:** joint onset+pitch decoding on a CQT front-end
   (research-grade; only if 1–2 plateau).

Recommended A/B: penn-f0 × {envelope-gated, onset-primed} on the 60-case
GuitarSet low-string corpus, strict + octave-forgiving, before any deeper
investment.

## Recommended bass sequence

1. `melodic_penn` adapter + benchmark vs the three baselines (queue #2).
2. Onset-primed segmentation layer A/B on penn f0 (queue #4).
3. If octave-slack persists: 3-tracker aligned median vote (queue #8).
4. SwiftF0 octave-up trick only as a cheap extra voter, never the primary.
