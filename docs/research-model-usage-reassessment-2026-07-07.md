# Research — Model-usage reassessment under the GPLv3 / non-commercial policy (2026-07-07)

The project relicensed to **GPL-3.0-or-later** with no commercial aspirations
(commit `d99ce17`), replacing the former commercial gate (permissive-only, no
NC weights, no GPL deps). This pass re-ranks model options per pipeline area
under the new gate: **code deps must be GPLv3-compatible; weights/datasets
need an explicit redistributable license (NC acceptable); unstated-license
weights remain excluded.**

Method: 13-agent workflow — 2 repo sweeps (36 license-gated rejections
catalogued; full production map), 10 web license verifications against
primary sources (repo LICENSE files, HF model-card YAML, Wayback snapshots
for deleted accounts), 1 completeness critique. Verification date for all
license claims: 2026-07-07.

## Headline finding: YourMT3+ was mis-gated and is fully usable

The old verdict ("GPL-3.0 — code and, by conservative default, checkpoints;
research reference only") is half-stale and half-wrong:

- Code GPL-3.0 — **now fine**, we are GPLv3.
- Weights — **explicitly Apache-2.0**: `license: apache-2.0` in the YAML
  front matter of huggingface.co/mimbres/YourMT3, verified via raw fetch.
  All checkpoints in that repo are covered; no per-checkpoint split.

The `yourmt3_drums` engine is **already registered** in
`transcription.py` (`KNOWN_MT3_DRUM_ENGINES`, modelpack-gated, ~536 MB) —
weights were simply never shipped. Paper numbers (arXiv 2407.04822,
YPTF.MoE+Multi vs MT3): **ENST-Drums drum onset F1 88.79** (MT3: 77.82),
Slakh multi-onset 84.14, MAESTRO piano 96.98, GuitarSet 88.92. Standing
caveat from our own docs: collapses on non-main instruments in real
distorted pop — must be validated on our psalm imports, not just corpora.

## Ranked plan

### Tier 1 — cheap, high-confidence wins (do first)

1. **Beats: flip Beat This! `dbn=True`** (madmom DBN post-processing). We run
   `dbn=False` *specifically* because madmom's models are CC BY-NC-SA — that
   was the only reason. Cheapest change on this list; improves the beat grid
   under every chart. madmom itself (BSD-with-NC-clause + NC models) is now
   fully admissible. Watch: madmom installs from git with Cython pinning
   pain on Windows.
2. **Drums: benchmark YourMT3+ drums** through the existing engine on
   stratified test-30 + the psalm real-music set, head-to-head against
   drum_crnn run-4 (0.576) and mr_mt3. If the ENST numbers hold anywhere
   near 88, it leapfrogs everything in-house. Ship as modelpack (Apache
   weights, no restriction).
3. **Drums: evaluate ADTOF CRNN** — the actual SOTA for real-music ADT
   (5-class F 0.89 MDB / 0.85 ENST / 0.85 ADTOF-YT; QMUL 2025 independently
   calls it current SOTA). Weights CC BY-NC-SA via the **official
   MZehren/ADTOF repo only** — the community PyTorch port
   (xavriley/ADTOF-pytorch) has NO license on its code and is not a legal
   distribution channel for us. Integration friction is real (TF >=2.13 +
   madmom-from-git; ~5.4 MB model, CPU-practical). Evaluate first; shipping
   route decision after (official TF checkpoint vs in-house weight
   conversion under SA terms).

### Tier 2 — new capabilities unlocked

4. **Drum-stem separation → 7/8-class drums + velocity** (Riley & Dixon,
   arXiv 2509.24853): DrumSep MDX23C checkpoint (CC BY-NC-SA — confirmed via
   Wayback snapshot of the deleted jarredou/models statement; ignore the
   MIT-blanket mirrors, they are not authoritative) + ADTOF. 8-class F 0.84
   MDB / 0.76 ENST. Gameplay value: real crash/ride lanes + velocity.
   Electronic drums regress (0.58→0.56 RBMA) — gate on style.
5. **Vocals — an entire missing modality.** Nothing is transcribed today.
   RMVPE (official code Apache-2.0; checkpoint source/license audit still
   required) for a karaoke pitch lane; SOME / ROSVOT (openvpi ecosystem,
   weights likely CC BY-NC-SA — verify) for note-level MIDI a chart actually
   needs.
6. **Stem separation upgrade + finally measurable.** htdemucs_6s (2022) is
   no longer SOTA; BS/Mel-RoFormer community checkpoints (ZFTurbo's MIT
   Music-Source-Separation-Training ecosystem; per-checkpoint license
   verify) reach vocal SDR ~11 vs ~9. Quick win inside the shipped demucs
   package: `htdemucs_ft` for the drums stem. And MUSDB18-HQ (NC) is now
   usable → unblocks the standing "museval OK=False" gap so in-house SDR
   numbers can exist at all.

### Tier 3 — instrument-specific follow-ups

7. **Guitar (our weakest area — best F1 0.261).** High-Resolution Guitar
   Transcription via domain adaptation (QMUL, arXiv 2402.15258) — SOTA
   GuitarSet, NC-expected. Fretting-Transformer (arXiv 2506.14223) for
   string/fret tab lanes. GAPS (14 h, NC) + IDMT-SMT-Guitar as newly-legal
   corpora. YourMT3 GuitarSet 88.92 onset is also worth a benchmark pass
   given #2 ships the runtime anyway.
8. **Chords & key — absent area.** madmom CNN chord/key (NC, zero new deps
   beyond #1), BTC (ISMIR19), Essentia models (AGPL/CC BY-NC-SA — both now
   tolerable). New lane type for a music-learning game.
9. **Piano — already strong (0.928 F1 supplement path).** No action; the
   real blocker there is the torch-2.11 PTI incompatibility, not model
   availability. Mobile-AMT (onset F1 96.7 MAESTRO) on the watch list.

### Still blocked regardless of policy (unstated license = no grant)

- **Magenta OaF E-GMD checkpoint** — no license anywhere (bucket LICENSE
  URL 404s); benchmark-anchor use only. Also TF1-era in an archived repo.
- **ADT_STR** — public code, no LICENSE file, no released weights; recipe
  already reimplemented in-house (that's the drum_crnn line).
- **Noise-to-Notes** — nothing released; watch list.
- **xavriley/ADTOF-pytorch** — port code unlicensed (weights inside it
  inherit CC BY-NC-SA, but the code isn't distributable).

## Cross-cutting notes

- **ShareAlike propagation:** derivatives of CC BY-NC-SA weights (fine-tunes,
  conversions) must stay CC BY-NC-SA. Keep NC-derived model assets in
  clearly-labeled modelpacks with their own license files; the GPLv3 codebase
  is unaffected (weights are data, not linked code).
- **Pin checkpoint revisions** (HF commit hashes) — mirrors blanket-relabel
  licenses (observed twice this pass: MIT-tagged mirrors of NC weights);
  only upstream statements are authoritative.
- **The in-house drum_crnn line keeps its purpose**: it is the only path
  producing weights *we* control end-to-end (retrainable, recalibratable,
  relicensable), and it validates the Ableton-render-farm strategy. It is no
  longer required to win the quality race — Tier 1 items 2–3 exist for that.
- Promotion gates unchanged: no default flips on corpus F1 alone; human
  in-game review required (repo policy).

## Artifacts

- Full structured results (36 rejections, production map, 10 license
  verifications, completeness critique): workflow run `wf_76329f29-10a`,
  2026-07-07. Key verification evidence inline above.
- Supersedes the *license verdicts* (not the architecture/quality analysis)
  in: research-drums-license-resolution-2026-07-02.md (Findings 1, 6),
  research-transcription-novel-approaches-2026-07-02.md (YourMT3 row),
  research-adt-drums-2026-06-22.md (LarsNet), README license-gate note
  (madmom).
