# Research — Automatic Drum Transcription improvements (2026-06-22)

Deep, multi-source, adversarially-verified research (clean re-run: 23 claims
confirmed, 2 killed). Goal: raise our drum F1 (real-world ~0.15–0.4, with a
**precision** collapse — e.g. hi-hat recall ~0.99 / precision ~0.19) toward the
0.7–0.9 strong-ADT range. Supersedes the 2026-05-07 ADT deep-dive.

## Recommendation

1. **Core fix — replace the DSP spectral/band classifier with a real-data-trained
   neural ADT CRNN (ADTOF-style).** ADTOF (ISMIR 2021, `github.com/MZehren/ADTOF`,
   arXiv 2111.11737) trains the Vogl-style CRNN directly on 359 h of crowdsourced
   real-music annotations and reaches **5-class F 0.89 (MDB-Drums) / 0.85
   (ENST-Drums)**, natively distinguishing kick/snare/hi-hat/toms/cymbals — a
   learned model fixes kick↔snare instead of spectral bands.
2. **Precision fix (permissive, layers on top) — beat/tatum-conditioned decoding
   + a drum-pattern language-model regularizer.** Raised 3-class F **70.8 → 81.6**
   on RWC (arXiv 2010.03749, 2105.05791) by snapping outputs to musically-natural
   tatum patterns; lifts recall with only a small precision drop. Bolts onto our
   existing `beat_conditioned_multiband_decoder` stage and directly attacks
   hi-hat over-triggering.
3. **Per-instrument drum-stem separation — optional add-on, not the core fix.**
   LarsNet/StemGMD/Inverse-Drum-Machine can expand 5→7 classes (crash/ride) and
   estimate velocity, faster-than-real-time on CPU. **LarsNet weights are CC BY-NC
   — unusable in a closed commercial app.** Inverse Drum Machine (IDM) is
   permissively reproducible (~100× fewer params) and worth watching.

## Open gates before adopting ADTOF

- **Pretrained-weight license is UNRESOLVED** (a "CC BY 4.0" claim was *refuted*).
  We may need to **retrain the CRNN on the ADTOF dataset** for shippable weights.
- ADTOF is **TensorFlow**-based. The current ingest runtime does not ship
  TensorFlow for Basic Pitch; Basic Pitch runs through its ONNX model plus
  `onnxruntime`. Keep ADTOF in a separate pinned runtime until **ONNX export**
  is proven for the PyInstaller one-file sidecar.

## Caveats

- 0.85–0.89 are on curated *acoustic* sets; drops to **0.63 on electronic-drum
  RBMA** — real Psalms material lands between, don't assume 0.89 transfers.
- Separation results are largely on *synthetic* StemGMD (flatters the method);
  the "5→7 separation improves 8-class F by ~10–12%" magnitude was **refuted**.
  nSDR (separation quality) ≠ F1 (transcription accuracy).
- Omnizart's drum model writes only 3 classes to MIDI — poor fit.

## Eval plan

MIREX-style per-class P/R/F at standard onset tolerance on E-GMD / MDB-Drums /
ENST-Drums, plus our Psalms real-world references. Compare combined_filter
(current) vs ADTOF-CRNN vs ADTOF-CRNN + LM post-filter.
