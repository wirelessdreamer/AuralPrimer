# Research — Electric guitar: fixing the precision collapse on distorted chords (2026-07-02)

Detail notes from the 2026-07-02 deep-research pass. Synthesis + ranked queue:
[research-transcription-novel-approaches-2026-07-02.md](research-transcription-novel-approaches-2026-07-02.md).
Builds on `research-guitar-bass-2026-06-22.md` and
`benchmarks/guitar/GUITAR_TRANSCRIPTION_EPIC.md`; assumes the 2026-07-01
guitar-upgrade branch (`guitar_basic_pitch_playable`, `guitar_cleanup`,
`guitar_chord_supplement`, `guitar_auto`, 24-bit WAV fix) is merged.

## Where we are (measured in-repo, 2026-07-01)

- `guitar_basic_pitch_playable` on Guitar-TECHS micamp chords (limit 20):
  **P 0.250 / R 0.846 / F1 0.386**. Recall is essentially solved; precision
  is the whole problem — phantom octaves/fifths above heavy-gain chord tones.
- `guitar_auto` (split → torchcrepe lead + basic-pitch rhythm) partial smoke:
  **F1 0.396 vs 0.201** for `melodic_combined_guitar` — ~2× lift before the
  cleanup + chord-supplement passes were even co-located.
- DSP baseline: comp/rhythm phrases are **5.5× harder** than solo lead
  (GuitarSet F1 0.096 vs 0.528).
- Not yet measured: the *composed* `guitar_auto` (with
  `prune_distortion_overtone_shadows` + chord supplement firing). That
  benchmark is queue item #1 and gates everything below.

## Finding 1 — De-distortion front-end: high-fidelity proven, transcription benefit unmeasured, no code

**Source:** "Distortion Recovery: A Two-Stage Method for Guitar Effect Removal"
(arXiv 2407.16639, DAFx 2024). Panel-verified 3-0 (two merged claims).

- Two-stage architecture: Transformer Mel-spectrogram denoiser → HiFi-GAN
  vocoder resynthesis.
- Recovers clean guitar from commercial-VST (Positive Grid BIAS FX2)
  distorted signal at **SI-SDR 28.65 / FAD 0.080**, versus SI-SDR 2.98–6.21 /
  FAD 0.224–0.383 for Demucs V3, DCUnet, and a HiFi-GAN-denoiser baseline
  trained on the same data for the same 1.5 M steps. MOS from 26
  professionals corroborates (3.54 vs 1.66–2.67).
- **Counterweights:** loses on ESR (2.29 vs 0.87–1.22); evaluated in-domain
  on an 80 h proprietary BIAS-FX2-rendered set; authors concede real-world
  discrepancy — amp-mic'd (Guitar-TECHS-like) performance is unestablished.
- **The paper reports zero transcription metrics.** Feeding de-distorted
  audio to a transcriber is named only as future work. No code, no weights,
  only v1 of the paper exists.
- Refuted satellite claim (1-2): that the 80 h BIAS FX2 training corpus
  "validates the commercial amp-sim data strategy" — don't cite the
  de-distortion paper for that; the amp-sim strategy rests on Finding 2.

**What it means for us:** the highest-novelty guitar experiment available.
Nobody has published "does de-distortion raise note-level F1?" We can answer
it: mass-render paired dry/wet through Ableton amp sims (we control both
sides of the pair perfectly), train the two-stage model (or even start with
a simpler U-Net denoiser), then A/B `guitar_basic_pitch_playable` on raw vs
de-distorted Guitar-TECHS micamp. If the phantom octaves/fifths are created
by distortion harmonics, removing them *before* the neural transcriber
should attack precision at the source rather than post-hoc (our cleanup
passes prune after the fact; this prevents).

## Finding 2 — Amp-emulation augmentation: quantified ~394× data amplification; implementation license-blocked; strategy ours for free

**Source:** Open-Amp (arXiv 2411.14972, ICASSP 2025, IEEE 10888232).
Panel-verified 3-0 ×3 (merged).

- Wraps 160 crowdsourced neural amp/effect captures (59 amps + 101 pedals
  from GuitarML "Proteus Tone Packs"), rendered **online during PyTorch
  training** via multi-process dataloading.
- Turns 35 minutes of clean audio into ~230 hours of labeled effected audio
  (394 devices × 35 min — arithmetic checks out).
- Each capture is a single-layer LSTM, hidden size 40 — GuitarML runs these
  real-time on a Raspberry Pi, so CPU dataloader rendering is trivially
  feasible.
- **License:** repo has NO license file (GitHub API `license: null`, live
  verified 2026-07-02 across both branches, 737 files; no PyPI package;
  no license headers). Default all-rights-reserved. The Proteus captures
  ship from a GPL-3.0 GuitarML repo. Neither passes our permissive bar.

**What it means for us:** the *strategy* is verbatim reproducible with tools
we own — Ableton Live hosts any VST amp sim we're licensed for, and MCP
automation gives MIDI-accurate paired renders. Two consumption modes:
1. **Offline corpus:** render Guitar-TECHS DI / GuitarSet / our own DI takes
   through N amp-sim presets → tone-augmented eval + finetune corpora.
2. **Distillation:** if we ever finetune a transcriber, tone diversity at
   train time is the +31%-rel lever already documented in the 2026-06-22
   notes (arXiv 2405.14679) — Open-Amp just proves the amplification factor
   and CPU practicality at scale.

## Finding 3 — Unlabeled audio alone lifts guitar transcription (+10 frame F1)

**Source:** "Music Transcription with (Almost) No Supervision"
(arXiv 2605.24193, May 2026 preprint — Thickstun/Weinberger group).
Panel-verified 3-0 ×2 + 3-0 ×2 (license). Confidence: **medium**
(≈6-week-old unreviewed preprint; frame-level metric; clean acoustic domain).

- Cycle-consistent training: 1.6 h paired MAESTRO piano + *unlabeled*
  GuitarSet audio → GuitarSet frame F1 **54.81 → 64.81 (+10.0)** with ZERO
  paired guitar labels, beating a fully-supervised out-of-domain baseline
  (54.57 from 161 h paired piano).
- Sample-efficiency anchor: 1.6 h paired + 159.5 h unpaired = 75.45 frame F1
  on MAESTRO vs 87.43 fully supervised (161 h paired) — **86.3% of
  fully-supervised performance from ~1% of the labels.**
- **Blockers:** repo (`SaebyeolShin/almost_unsupervised_amt`) has no LICENSE
  file and no released checkpoints; training from scratch ≈4 days on an
  A6000 48 GB. arXiv CC BY 4.0 covers the paper text only.

**What it means for us:** medium-term. If we ever train in-house guitar
models, our unlabeled Suno/psalm guitar stems and Ableton renders are
directly usable *without alignment work*. Transfer from clean acoustic to
distorted electric is the untested extrapolation — pair it with Finding 2's
tone augmentation.

## Finding 4 — Stem-mixing augmentation numbers worth copying (from a GPL codebase)

**Source:** YourMT3+ (arXiv 2407.04822, IEEE MLSP 2024, QMUL).
Panel-verified 3-0 (ablations) + 2-1 (license).

- Slakh ablations (cumulative, not isolated A/B): intra-stem augmentation
  (per-stem keep probability 0.7) adds **+4.8 onset / +5.5 offset F1**;
  cross-dataset stem mixing adds a further **+4.0 / +7.2**. Drum gains were
  small (+0.6/+1.6). Base 64.8/41.7 → full 84.6/70.7.
- Gains measured on the synthetic Slakh test set; abstract concedes limits
  on real pop.
- **License:** GPL-3.0 (actual LICENSE file). The HF Space's `apache-2.0`
  metadata tag is too ambiguous to be a permissive grant for checkpoints
  bundled with GPL code — conservative GPL default stands. Research
  reference / distillation teacher only.

**What it means for us:** when building rendered corpora (Findings 1/2 and
the drum factory), bake in per-stem dropout and cross-song stem mixing at
render time — Ableton MCP makes both trivial (mute tracks per variation;
recombine stems across songs). Expect the same order of robustness gain the
ablations show.

## Finding 5 — Procedural synthetic pretrain → +14 note-level F1 with 1/5 of the real data

**Source:** "Exploring Procedural Data Generation for Automatic Acoustic
Guitar Fingerpicking Transcription" (arXiv 2508.07987, AIMC 2025, Murgul &
Heizmann). Existence/topic manually verified 2026-07-02; the specific
numbers are fetch-extracted (single-agent full-text read, not
panel-verified).

- Pipeline: knowledge-based tablature composition → MIDI performance
  rendering → extended Karplus-Strong physical modeling → audio
  augmentation. No DAW, no samples — fully procedural.
- Pretrain Onsets-and-Frames CRNN on the synthetic corpus, finetune on 1/5
  of GuitarSet's real recordings (60 recordings): **note-level F1 77.45% vs
  63.32%** training from scratch on the same reduced real data
  (fetch-extracted numbers).
- Domain: acoustic fingerpicking — the *far* end from distorted power
  chords, so treat as strategy validation, not a number that transfers.

**What it means for us:** third independent confirmation (with Open-Amp and
the drums synthetic-only result) that synthetic-pretrain-then-real-finetune
is the sample-efficient route. Our Ableton renders are higher-fidelity than
Karplus-Strong; our real anchor (GuitarSet + Guitar-TECHS) is bigger than
their 60 recordings.

## Finding 6 — Foundation-model backbone route: closed on both evidence and licensing

- **MERT-v1-95M weights are CC BY-NC 4.0** — the authors' own declaration in
  the official m-a-p HF model-card YAML (live-verified; lastModified
  2025-05-25). The Apache-2.0 GitHub repo covers code only. Panel 3-0.
- The one affirmative datapoint ("MusicFM-backboned MIROS won the 2025 AMT
  Challenge, Slakh F 0.83") was **refuted 0-3** in verification.
- Net: no surviving positive evidence that foundation-model backbones lead
  transcription, and the main permissively-coded entry point has NC weights.
  Drop this thread until the licensing or evidence changes.

## Recommended guitar sequence

1. **Measure first** (queue #1): full-corpus micamp + DI run of composed
   `guitar_auto`. DI is meaningful now that the 24-bit reader bug is fixed.
   If the distortion-shadow pruning pass gets precision to ~0.55 with recall
   ≥0.7, composed F1 lands ~0.6 and the cheap wins are banked.
2. **Tune the cleanup gates on corpus feedback** — thresholds in
   `guitar_cleanup.py` were set without data; the failing chord phrases from
   step 1 are the tuning set. Guard: acoustic GuitarSet must not regress.
3. **De-distortion experiment** (queue #7): render paired dry/wet via
   Ableton; train front-end; A/B note F1. First-mover territory.
4. **Tone-augmented finetune of the rhythm-path model** (Findings 2+4+5
   combined) — only if steps 1–3 plateau below target.
