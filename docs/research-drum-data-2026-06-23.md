# Research — Open annotated drum datasets for ADT (2026-06-23)

Can we improve AuralPrimer's drum transcription the way piano was improved —
by leveraging open sources of annotated audio data? This doc answers that,
grounded in our own codebase, and **extends** (does not repeat)
[`research-adt-drums-2026-06-22.md`](research-adt-drums-2026-06-22.md).

The headline up front, because it reframes the whole question:

> **The piano F1 climb (0.706 → 0.928) was NOT a "train on annotated data" win.
> It was "swap to a better pretrained model + gated DSP post-processing."** The
> annotated corpus was used *only to benchmark*, never to train. So the honest
> parallel for drums is: **adopt a strong pretrained model and benchmark/auto-tune
> it on annotated drum data — do not (yet) train our own weights.**

---

## 1. How piano was actually improved here (ground truth, not assumption)

Read the four-step climb in
[`research-piano-cleanup-deep-dive-2026-06-20.md`](research-piano-cleanup-deep-dive-2026-06-20.md)
and the engines in `python/ingest/src/aural_ingest/algorithms/piano_*.py`. The
mechanism, categorized against the task's (a)/(b)/(c)/(d) framing:

| Lever | Used for piano? | Evidence |
|-------|-----------------|----------|
| (a) Train/fine-tune on annotated data | **NO** | No training code exists. `piano_pti.py` loads a *third-party* checkpoint (`high_resolution_MAESTRO_augmentations.pth`, Kong/Edwards-robust). The synthetic corpus has 117 notes — far too small to train on, and the doc explicitly says all 4 cases were used *to tune gates*, not to train. |
| (b) Swap to a better pretrained model | **YES — the core win** | Production keys moved to `piano_transcription_inference` (PTI = Kong's Onsets-and-Frames-derived CRNN). That single swap is where the strong baseline (F1≈0.706, real-piano "within 4% of reference on Psalm 5") comes from. |
| (c) Ensembling | **NO — explicitly rejected** | `piano_ensemble.py` (naive PTI ∪ basic_pitch union) *regressed* F1 to 0.674. The doc calls naive ensembles "the single most important lesson … lose to either parent." |
| (d) Post-processing / cleanup | **YES — the polish** | Steps 1-4 (`piano_pti_clean` → `_dedup` → `_dedup_pyin` → `piano_chord_supplement`) are all gated DSP post-filters layered on PTI. Each is a *no-op where it can't help* ("supplement, never replace"). |

**The annotated data's only role was benchmarking.** `gt-benchmark`
(`cli.py::cmd_gt_benchmark` → `ground_truth_benchmark.run_sweep`) scores engines
against prepared corpora via `dataset_adapters/`. Piano even lacked a real
annotated corpus (MAESTRO wasn't approved this round), so they *authored a
synthetic one* (`dataset_adapters/piano_synthetic.py`) purely to measure the lift.

**Implication for drums:** the lever that actually moved piano was **(b) + (d)**,
backed by **benchmarking on annotated data**. The drum question "can annotated
data help?" therefore splits cleanly:

- **Lever A — TRAIN weights on annotated drum data.** This is a *new* capability
  we never used for piano. High effort, real license landmines (below).
- **Lever B — use annotated drum data to BENCHMARK/auto-tune** a pretrained model
  + gated post-filters. **This is the literal piano playbook.** Lower effort,
  no new license exposure beyond the dataset's own terms.

---

## 2. Current drum stack (what we're starting from)

From `transcription.py` and `algorithms/`:

- **Heuristic engines** (`KNOWN_HEURISTIC_DRUM_FILTERS`): `combined_filter`
  (default), `beat_conditioned_multiband_decoder`, `spectral_flux_multiband`,
  `adaptive_beat_grid`, + ~12 others. All DSP band/onset/template classifiers.
- **MT3 engines** (`KNOWN_MT3_DRUM_ENGINES`): `mr_mt3_drums` (176 MB),
  `yourmt3_drums` (536 MB) — research candidates, gated behind modelpacks, slow
  (15-57× realtime), and `fidelity_midi`-profile only.
- **9-class taxonomy** mapped via `_BENCHMARK_NOTE_TO_CLASS` (kick/snare/hi_hat/
  toms/crash/ride).
- **gt-benchmark already wired to E-GMD** (`dataset_adapters/egmd.py`), plus
  GuitarSet / Guitar-TECHS / piano_synthetic. The E-GMD adapter is complete:
  parses TD-17 MIDI, maps to `DrumEvent`, buckets by kit/style/split.

### Measured current baseline (this is the problem we're solving)

`benchmarks/drums/gt_runs/egmd_baseline_20.json` — E-GMD test, 20 performances ×
4 kits, pitch-aware, 50 ms tolerance:

| Engine | Precision | Recall | F1 |
|--------|----------:|-------:|----:|
| `combined_filter` (default) | 0.215 | **0.029** | **0.051** |
| aggregate (4 heuristic engines) | 0.250 | 0.072 | 0.112 |

This is a **recall collapse** on real recorded drums — the heuristics miss
~97% of hits. (Note: the 2026-06-22 doc described the *opposite* failure — a
hi-hat *precision* collapse — on the Psalms real-world mix references. Both are
true; they're different corpora. On clean E-GMD solo drums the engines under-fire;
on dense full-mix Psalms material the hi-hat band over-fires. Either way the
heuristic ceiling is far below the 0.7-0.9 strong-ADT range.)

A learned model is the fix the literature has demanded since 2018 (see
`research-deep-dive-adt-2026-05-07.md` §A1: heuristic fusion CONTRADICTED).

---

## 3. Open annotated drum datasets — survey

Sorted by usefulness to *us*. "Shippable-trained?" = if we trained weights on it,
could those weights ship in a closed commercial app?

| Dataset | Size | Audio | Annotation | Align | License | Shippable-trained? |
|---------|------|-------|-----------|-------|---------|--------------------|
| **E-GMD** | **444 h, 43 kits** | **Real** (Roland TD-17 e-kit, re-recorded) | onset + class + **velocity** | ~2 ms | **CC BY 4.0** | **YES** |
| **STAR Drums** (ISMIR/TISMIR 2025) | large; FLAC 48 kHz stereo | **Synth drums + real melodic/vocal beds**; drum & non-drum stems separate | onset + many classes; high temporal accuracy | high | **CC (per-file, redistributable)** — chosen specifically to be fully shareable | **Likely YES** (verify per-file CC variants — heterogeneous) |
| **Slakh2100** | 145 h (2100 tracks) | **Synthesized** (sample VSTs from Lakh MIDI) | full MIDI incl. drums; stems | exact (rendered) | **CC BY 4.0** | YES (but synthetic timbre → transfer gap) |
| **MDB-Drums** | 20m 42s (23 tracks) | **Real** (MedleyDB subset) | onset, 21 fine / 6 coarse classes | manual | **CC BY 4.0** | YES — but tiny; **benchmark-grade, not train-grade** |
| **ENST-Drums** | ~1 h eval (64 tracks) | **Real** (3 drummers, multi-kit) | 20 classes; "wet/dry" mixes | manual | **Research/academic** (request access; not a clear commercial grant) | **NO (assume not)** — benchmark only |
| **IDMT-SMT-Drums** | 2h 10m (608 WAV) | **Real** drum loops | onset, **3 classes only** (kick/snare/hihat) | manual | Zenodo (CC; verify) | benchmark only (3-class too coarse) |
| **ADTOF** | **359 h** non-synthetic | Real (crowdsourced charts → aligned) | onset + 5 class | aligned | **CC BY-NC-SA 4.0** | **NO — non-commercial** |
| TMIDT / MUSDB-derived | varies | mixed | varies | varies | MUSDB18 is **non-commercial** | **NO** |

### Per-dataset notes

- **E-GMD** is the standout: it is the *drum analog of MAESTRO* — large, real
  audio, velocity-annotated, **CC BY 4.0 (commercial OK)**, and **we already have
  an adapter for it**. The catch: the audio is a Roland TD-17 **electronic kit**,
  so models trained purely on it carry an e-kit→acoustic/real-mix transfer gap
  (the 2026-06-22 doc's "0.85-0.89 acoustic → 0.63 electronic RBMA" caveat runs in
  this direction too). Good for benchmarking *our* heuristics; usable for training
  but not a silver bullet on real mixes alone.
- **STAR Drums (2025)** is the most important *new* find since the last doc. It
  was designed to beat the "MIDI-rendered audio" transfer gap: real musician-played
  melodic/vocal beds with synthesized-but-realistic drum stems, **and the authors
  deliberately picked licenses that permit raw-audio redistribution.** The paper
  reports training on STAR Drums *beats* training on MIDI-rendered-only data. This
  is the strongest open *training* corpus if (and only if) the per-file CC variants
  check out as commercial-compatible — they are heterogeneous, so this needs a
  per-file audit before any shipped-weights claim.
- **Slakh2100** (CC BY 4.0) is large and clean but fully synthetic — same transfer-
  gap risk as rendering our own MIDI. Useful as *augmentation*, not as the sole
  training set.
- **MDB-Drums / ENST-Drums / IDMT-SMT-Drums** are the classic *evaluation* triad.
  Small, real, well-annotated — perfect for benchmarking, too small (and ENST/IDMT
  too license-restricted or too coarse) to train shippable weights on.
- **ADTOF** (the 2026-06-22 doc's recommendation) remains **CC BY-NC-SA 4.0 — non-
  commercial**, confirmed again here on both the GitHub repo and the dataset terms
  ("free of charge for internal non-commercial use," no redistribution). Its
  pretrained weights inherit the NC restriction. **Not shippable.** This is the
  single biggest correction to the prior doc's plan: ADTOF's "retrain for shippable
  weights" escape hatch only works if you retrain on a *different* (CC-BY) corpus —
  i.e. E-GMD/STAR — at which point you're not really using ADTOF anymore.

---

## 4. State of the art in open ADT models (+ shippability)

| Model | Year | Classes | Framework | Trained on | Weights license | Shippable? |
|-------|------|---------|-----------|-----------|-----------------|-----------|
| **OaF-Drums** (Magenta Onsets-and-Frames, E-GMD-retrained) | 2020 | **up to 7 + velocity** | TensorFlow | **E-GMD (CC BY 4.0)** | **Apache 2.0** (code) | **YES** ✅ |
| **ADTOF CRNN** (Vogl-style) | 2021 | 5 | TensorFlow | ADTOF (NC) | **CC BY-NC-SA** | **NO** ❌ |
| **ADTLib** (Southall bi-RNN/CNN) | 2016-17 | **3 only** (k/s/hh) | TF + madmom | private | **BSD-2-Clause** | YES ✅ but 3-class + stale deps |
| **YourMT3+ / MR-MT3** | 2024 | multi-instr. | PyTorch | mixed | varies (check) | partial — already gated in our tree |
| **Noise-to-Notes** (diffusion) | 2025 | 7/5/3 | PyTorch | E-GMD | **no public weights/code found** | unknown — research only |
| **Enhanced ADT via drum-stem sep.** | 2025 | 5→**7** + velocity | — (post-proc) | builds on ADTOF | inherits ADTOF NC | NO (as-is) ❌ |
| omnizart drums | 2021 | 3 | TF | — | code MIT; drum checkpoints buggy | weak (3-class, known bugs) |

### The standout: OaF-Drums is the drum PTI

This is the near-exact parallel to the piano win:

- **PTI for piano** = Onsets-and-Frames CRNN, third-party checkpoint, swapped in
  as a strong pretrained baseline → 0.706 → polished to 0.928 with gated DSP.
- **OaF-Drums** = the *same* Onsets-and-Frames architecture, **retrained by Magenta
  on E-GMD**, outputs onsets + drum-class + **velocity** (up to 7 classes). Code is
  **Apache 2.0**; training data is **CC BY 4.0**. So a checkpoint trained on E-GMD
  is shippable *both* by code license and by data license — the cleanest license
  story of any neural ADT model.

**The one real risk on OaF-Drums:** the *published E-GMD checkpoint's* download
availability is murky. The Magenta repo documents the drums config and a Colab,
but multiple GitHub issues (magenta #1792, #1876, #1931) are people struggling to
find/reproduce the E-GMD-trained drum checkpoint. **Must verify a downloadable
checkpoint exists before committing** — if it doesn't, we'd have to *train it
ourselves on E-GMD* (which is Lever A, and legal since E-GMD is CC BY, but is a
multi-day GPU job and crosses into "train our own weights" territory). The RTX
5090 in this environment makes self-training feasible if needed.

---

## 5. The two levers, decided

### Lever A — TRAIN/fine-tune our own drum weights

- **Datasets:** E-GMD (CC BY, real, 444 h) as the spine; optionally augment with
  STAR Drums (pending per-file license audit) and Slakh2100 (CC BY, synthetic).
- **Shippability of the result:** weights trained *only* on CC-BY corpora (E-GMD /
  Slakh / verified-CC STAR) are **shippable**. Weights touching ADTOF / MUSDB /
  ENST are **not**.
- **Expected gain:** E-GMD-trained Onsets-and-Frames-class models report strong
  in-domain F (the OaF paper's whole point), and 5-class CRNNs hit **F≈0.85-0.89
  on MDB/ENST**. Realistically on *our* real Psalms mixes, expect **0.55-0.75**
  (the e-kit→real-mix transfer gap; STAR-style training narrows it). Versus the
  current **0.05-0.4**, that is a large jump regardless.
- **Effort:** high. Data prep + training harness + ONNX export for the PyInstaller
  one-file sidecar + a new `algorithms/drums_oaf.py` engine + checkpoint hosting.
  Estimate **1-2 weeks**, most of it the train/export/package loop, not the model.

### Lever B — BENCHMARK/auto-tune a pretrained model + gated post-filters (the piano playbook)

- **Datasets:** E-GMD (already wired) as the primary benchmark; MDB-Drums and
  IDMT-SMT-Drums as small real-audio sanity corpora. No training, so no new license
  exposure beyond each dataset's own (all CC BY for the benchmark set).
- **Approach (mirrors piano exactly):**
  1. Adopt **OaF-Drums** as a new `drums_oaf` engine (the "swap to better pretrained
     model" = piano's PTI step). It's already TF-shaped like our basic-pitch path.
  2. Sweep it through `gt-benchmark --dataset egmd` against the four heuristic
     engines to get honest per-class P/R/F.
  3. Layer **gated DSP post-filters** the way piano did — e.g. the beat/tatum-
     conditioned + pattern-LM precision fix from the 2026-06-22 doc (70.8→81.6 on
     RWC) bolted onto `beat_conditioned_multiband_decoder`, and a hi-hat over-fire
     suppressor for the Psalms precision-collapse case. Each gated "no-op where it
     can't help."
  4. Optionally add the **2025 drum-stem-separation post-step** (5→7 classes +
     velocity) — but only on a *shippable* base model (OaF, not ADTOF), and it
     reuses the **Demucs v4 path we already ship** for stem separation.
- **Shippability:** clean — OaF weights are Apache/CC-BY, post-filters are our own
  DSP.
- **Effort:** medium. New engine wrapper + reuse existing `gt-benchmark` plumbing +
  port two post-filters. Estimate **3-5 days** to a benchmarked, gated, shippable
  engine.

---

## 6. Recommendation

**Do Lever B first; fall into Lever A only if the OaF checkpoint isn't downloadable.**

1. **Adopt OaF-Drums (Magenta Onsets-and-Frames, E-GMD-trained) as the new default
   neural drum engine.** It is the drum analog of the PTI swap that carried piano,
   and it is the *only* neural ADT model with a fully clean shippable license story
   (Apache code + CC-BY E-GMD training data + velocity output).
2. **Verify the published E-GMD drum checkpoint is downloadable** (resolve the
   magenta #1792/#1876/#1931 ambiguity). If yes → pure Lever B, ~3-5 days. If no →
   **train it ourselves on E-GMD** (Lever A, legal under CC BY, ~1-2 weeks on the
   5090) and ship our own checkpoint.
3. **Benchmark via the existing `gt-benchmark --dataset egmd`** harness — no new
   eval infra needed. Add MDB-Drums as a small real-audio adapter for a second
   data point.
4. **Layer gated post-filters** (beat/tatum + pattern-LM for the E-GMD recall
   collapse; hi-hat suppression for the Psalms precision collapse), each gated
   no-op, exactly as piano's steps 1-4 were gated.
5. **Optional:** STAR Drums for a future training/augmentation round *after* a
   per-file CC-license audit; it's the best open corpus for closing the
   synth→real gap if its licenses clear.

### Top datasets, with licenses
- **E-GMD** — 444 h real e-kit, velocity, **CC BY 4.0 (commercial OK)**, already
  adapted. *Primary for both training and benchmarking.*
- **STAR Drums (2025)** — real beds + realistic drum stems, **per-file CC
  (redistributable by design)**. *Best future training corpus, pending license
  audit.*
- **MDB-Drums** — 23 real tracks, **CC BY 4.0**. *Small real-audio benchmark.*

### Train-vs-benchmark verdict
**Benchmark, not train — at least first.** The piano playbook never trained on
data; it swapped to a strong pretrained model (OaF-Drums is ours) and benchmarked
on annotated data. Training our own weights is a viable *fallback* (E-GMD is
CC-BY, so it's legal and shippable), but it's only forced if the OaF checkpoint
turns out to be unavailable.

### Honest caveats
- **The piano playbook was "better pretrained model + gated post-proc," NOT
  "train on data."** Anyone pitching this as "let's train on drum datasets like we
  did for piano" has the history wrong — we never trained for piano. The cheapest
  faithful parallel is adopting OaF-Drums and benchmarking it.
- **ADTOF (the prior doc's pick) is non-commercial** — re-confirmed. Its weights
  can't ship; its "retrain for shippable weights" path only helps if you retrain on
  E-GMD/STAR, i.e. you've switched corpora.
- **E-GMD is a Roland e-kit.** Training/benchmarking purely on it understates the
  difficulty of real acoustic mixes (Psalms). Expect 0.55-0.75 on real mixes, not
  the 0.85-0.89 quoted on curated acoustic eval sets.
- **OaF checkpoint availability is unverified** — the central open gate. If it's
  gone, effort jumps from medium (B) to high (A).
- **Naive ensembles lose** (piano proved this empirically). Any multi-engine
  combination must be *gated*, not unioned.
- **Newest models (Noise-to-Notes 2025) have no released weights/code** found — not
  adoptable today, watch-list only.

---

## Sources
- Piano playbook (internal): `docs/research-piano-cleanup-deep-dive-2026-06-20.md`;
  `python/ingest/src/aural_ingest/algorithms/piano_pti.py`, `piano_ensemble.py`.
- Prior drum research (internal): `docs/research-adt-drums-2026-06-22.md`,
  `docs/research-deep-dive-adt-2026-05-07.md`.
- Current baseline (internal): `benchmarks/drums/gt_runs/egmd_baseline_20.json`;
  `python/ingest/src/aural_ingest/transcription.py`,
  `dataset_adapters/egmd.py`, `ground_truth_benchmark.py`.
- E-GMD: https://magenta.withgoogle.com/datasets/e-gmd ;
  https://magenta.withgoogle.com/oaf-drums ; https://arxiv.org/abs/2004.00188
- OaF-Drums / Magenta code (Apache 2.0):
  https://github.com/magenta/magenta/tree/main/magenta/models/onsets_frames_transcription ;
  checkpoint-availability issues:
  https://github.com/magenta/magenta/issues/1792 ,
  https://github.com/magenta/magenta/issues/1876 ,
  https://github.com/magenta/magenta/issues/1931
- ADTOF (CC BY-NC-SA): https://github.com/MZehren/ADTOF ;
  https://arxiv.org/abs/2111.11737 ; https://zenodo.org/doi/10.5281/zenodo.10084510
- ADTLib (BSD-2-Clause, 3-class): https://github.com/CarlSouthall/ADTLib ;
  https://archives.ismir.net/ismir2016/paper/000217.pdf
- STAR Drums (2025): https://transactions.ismir.net/articles/10.5334/tismir.244 ;
  https://zenodo.org/records/15690078
- Slakh2100 (CC BY 4.0): http://www.slakh.com/ ; https://zenodo.org/records/4599666
- MDB-Drums (CC BY 4.0):
  https://www.researchgate.net/publication/321168375 ;
  https://musicinformatics.gatech.edu/wp-content_nondefault/uploads/2017/10/Wu-et-al_2017_MDB-Drums-An-Annotated-Subset-of-MedleyDB-for-Automatic-Drum-Transcription.pdf
- IDMT-SMT-Drums: https://zenodo.org/records/7544164
- ENST-Drums: Gillet & Richard 2005 (academic-access dataset)
- Enhanced ADT via drum-stem separation (2025):
  https://arxiv.org/abs/2509.24853
- Noise-to-Notes (2025): https://arxiv.org/abs/2509.21739
- 2025 AMT Challenge: https://arxiv.org/abs/2603.27528
- omnizart: https://github.com/Music-and-Culture-Technology-Lab/omnizart
