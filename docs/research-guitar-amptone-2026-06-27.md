# Electric/distorted guitar transcription — amp-tone augmentation research (2026-06-27)

Deep-research, adversarially verified (102 agents, 25 claims → 23 confirmed / 2 killed).
The remaining transcription frontier now that drums are solved (mr_mt3). This is a
**multi-week training effort**, not a drop-in — but the path below is concrete and
license-clean for the open-source/non-commercial AuralPrimer ↔ Feedback constraint.

## Bottom line
The dominant lever is **large-scale amp-tone augmentation**: re-render clean
direct-input (DI) electric-guitar recordings through many amp/cabinet/IR tones, and
pretrain a **tone-aware model** on that, then finetune on real data. There is **no
released permissively-licensed tone-robust electric-guitar checkpoint to adopt
directly** — you'd train. Realistic ceiling ~**0.79–0.84 onset F1** on amp-rendered
tone (confirms the prior internal estimate).

## Key verified findings
- **Architecture:** a tone-informed Transformer (**TIT** = hFT-Transformer + cross-attention
  tone embeddings) is best-reported: onset F1 **84.0 / 81.6 / 78.9** (low-gain/crunch/high-gain)
  on EGDB-PG, beating the no-tone-embedding hFT baseline (79.6/78.8/76.8). [arXiv 2504.07406]
- **The lever (biggest payoff):** scaling training from **1 tone → 256 amp presets** lifted
  *out-of-domain* low-gain onset F1 **22.8 → 84.8 (+62 pts)**. Single-tone/general models
  collapse on distorted tone. (Non-monotonic — 4 tones overfits at 65.) [arXiv 2504.07406]
- **Reamping works with FREE tones:** GOAT's amp-augmented model hit note-F1 **0.800 vs 0.714**
  (DI-only) using ~7000 **NAM amp profiles + cabinet IRs** (both freely available) to reamp real
  DI. The *method* is freely reproducible even though the GOAT *dataset* isn't. [arXiv 2509.22655]
- **Pretrain→finetune validated:** SynthTab synthetic-pretrain → real-finetune *unanimously*
  beats real-only and mitigates cross-dataset (tone-mismatch) overfitting. [arXiv 2309.09085]
- **MT3 family is NOT a shortcut:** YourMT3+ ~91.6 onset F1 on clean isolated GuitarSet but
  **<10% on non-main instruments (incl. electric guitar) in real pop mixes** (RWC-Pop). [arXiv 2407.04822]
- **Eval trap:** real-tone/effects augmentation is **invisible in in-distribution GuitarSet
  6-fold CV** (0.748 → 0.746) — the benefit ONLY shows on a held-out **out-of-domain distorted**
  benchmark. You MUST evaluate OOD. [DAFx-2024 paper 99]
- Cross-dataset generalization without this is severe: TabCNN IDMT→GuitarSet **15.3%** tab F1,
  EGDB→GuitarSet **37.1%**. [arXiv 2309.09085]

## Assets + licenses (what we can actually use)
| Asset | License | Use | Verdict |
|---|---|---|---|
| **robust-guitar-tabs/code** (DAFx-2024) | CC0 code | the augmentation+training recipe (GuitarProFX/GuitarSetFX/TabCNN) | ✅ adopt |
| **EGFxSet** | CC BY 4.0 | 8970 real-hardware clips incl. 3 real gain/distortion pedals — augmentation tones | ✅ use |
| **NAM amp profiles + cabinet IRs** | free | build the reamping pipeline locally | ✅ use |
| **SynthTab** | CC BY-NC 4.0 | content pretrain — BUT **DI-only, no amp/distortion** (GM labels, not amp-rendered) | ⚠️ partial |
| **GOAT dataset** | by-request, copyrighted covers | most relevant real distorted set | ❌ not redistributable (use the *method*) |
| **GuitarSet** | NOT MIT, **acoustic-only** | clean content aug only | ❌ not the distorted eval set |
| **YourMT3+ / MT3** | — | general multi-instrument | ❌ collapses on real distorted pop |

## Compute
Training the augmented pretrain (514–771 h of audio, hFT-Transformer) is **cloud-scale
(multi-GPU-day)** — not an overnight job on the 6 GB RTX 3060 Laptop. The local 3060 is fine
for **inference** + possibly light finetuning. (The research did not pin an exact $/hour or VRAM
figure for this specific model — budget a rented A100/4090 for the pretrain.)

## Recommended plan (ranked, effort↑)
1. **Build the amp-tone/IR reamping augmentation pipeline** — free NAM profiles + cabinet IRs +
   EGFxSet real distortion tones, on top of the DAFx CC0 recipe. This is the dominant lever.
2. **Pretrain a tone-aware model** (TIT / hFT-Transformer + tone embeddings) on the augmented data,
   then **finetune on real**. Cloud GPU for the pretrain; ship offline inference.
3. **Evaluate ONLY on a held-out OOD distorted benchmark** — in-distribution CV hides the gain.
4. Wire the trained model as the rhythm/electric-guitar transcriber (replacing the HPSS+onset DSP),
   keeping torchcrepe for monophonic lead.

## Killed claims (do not propagate)
- "GuitarSet is MIT-licensed" — **refuted 0-3** (it is not MIT; and it's acoustic-only).
- "Augmenting TabCNN with synthetic guitar pretraining [a specific over-broad gain claim]" — **refuted 0-3**.

Sources: arXiv 2504.07406 (TIT/amp-tone), 2509.22655 (GOAT), 2309.09085 (SynthTab),
2407.04822 (YourMT3+), DAFx-2024 paper 99, egfxset.github.io, github.com/marl/GuitarSet.
Full machine output: session task `wqeh9g2cb`.
