# SETUP — OaF-Drums (Magenta Onsets-and-Frames, E-GMD-retrained)

This document explains how to make the `drums_oaf` engine
(`python/ingest/src/aural_ingest/algorithms/drums_oaf.py`) go live. Until a
checkpoint is provided, the engine is **inert**: it returns `[]` and the drum
pipeline falls through to the next engine. Nothing here is required for the app
to run without the model.

Decision context and license reasoning:
[`docs/research-drum-data-2026-06-23.md`](../../../docs/research-drum-data-2026-06-23.md).

---

## Why this engine

OaF-Drums is the **drum analog of the piano PTI swap** that carried keys from
F1 0.706 → 0.928. It is the same Onsets-and-Frames CRNN family, retrained by
Magenta on **E-GMD**, and it outputs onsets + drum class + **velocity** for up
to 7 classes. The license story is the cleanest of any neural ADT model:

- **Code:** Magenta is **Apache 2.0**.
- **Training data:** E-GMD is **CC BY 4.0** (commercial OK).

So a checkpoint trained on E-GMD is shippable by both code and data license.
(Contrast: ADTOF is CC BY-NC-SA — its weights cannot ship.)

---

## The open gate: does a downloadable checkpoint exist?

**This is the single unresolved question and the reason the engine ships as a
scaffold.** Magenta documents the drums config and a Colab, but multiple GitHub
issues are people unable to find or reproduce the E-GMD-trained drum checkpoint:

- https://github.com/magenta/magenta/issues/1792
- https://github.com/magenta/magenta/issues/1876
- https://github.com/magenta/magenta/issues/1931

References:
- OaF-Drums page: https://magenta.withgoogle.com/oaf-drums
- E-GMD dataset: https://magenta.withgoogle.com/datasets/e-gmd
- Magenta O&F code (Apache 2.0):
  https://github.com/magenta/magenta/tree/main/magenta/models/onsets_frames_transcription

**Before committing to Lever B (adopt the pretrained checkpoint), verify a real
downloadable checkpoint exists.** If it does not, use the self-train fallback
below (Lever A) — legal because E-GMD is CC BY 4.0.

---

## Checkpoint resolution (how the engine finds the model)

`drums_oaf.transcribe()` resolves a checkpoint in the same order as `mr_mt3`
and `piano_d3rm`:

### 1. Environment variable (highest priority)

```
AURAL_OAF_DRUMS_CHECKPOINT=/abs/path/to/checkpoint
```

The value may point at:
- a **TF SavedModel directory**, or
- an exported **ONNX file** (`.onnx`), or
- a Lightning-style `.ckpt` bundle root / `.pth`.

If the path exists, it is used; if not, the engine stays inert (returns `[]`,
never raises).

### 2. Modelpack drop (mirrors the mr_mt3 modelpack layout)

Drop the model under a standard model search root as a versioned modelpack:

```
<model-root>/drums_oaf/<version>/
    modelpack.json
    files/checkpoints/drums_oaf/model            # SavedModel dir, or
    files/checkpoints/drums_oaf/model.onnx        # ONNX file, or
    files/checkpoints/drums_oaf/model.ckpt        # .ckpt / .pth
```

`<model-root>` is any of the roots the sidecar already searches for mr_mt3
modelpacks (e.g. `assets/models`, `data/assets/models`,
`AuralPrimerPortable/data/assets/models`, plus MEIPASS / exe-dir / cwd /
stem-relative roots). The engine reuses
`transcription._default_mt3_model_search_roots` and
`transcription._iter_installed_modelpack_dirs`, so it is found in exactly the
same places as an mr_mt3 modelpack — no separate search config.

`modelpack.json` must exist for the version dir to be picked up (that is the
mr_mt3 convention). A minimal manifest:

```json
{
  "modelpack_id": "drums_oaf",
  "version": "1.0.0",
  "checkpoints": [
    { "model": "drums_oaf", "path": "files/checkpoints/drums_oaf/model.onnx" }
  ]
}
```

The checkpoint filename stem is intentionally generic (`model`) because the O&F
drum checkpoint can be exported in several formats; the engine also probes the
common suffixes (`.onnx`, `.ckpt`, `.pth`, `.pb`) against that stem.

---

## Wiring the runtime (the one seam left to fill)

`drums_oaf.py::_run_inference()` is the only place that needs a real forward
pass. It is guarded so its heavy deps import only inside the function, and any
failure degrades to `[]`. Two supported runtime shapes:

### Option A — native Magenta (TensorFlow)

1. Install the magenta O&F stack (TF 2.x + magenta) in the sidecar env.
2. In `_run_inference`, restore the **E-GMD drums config** and the checkpoint,
   run `predict`, and read the resulting `pretty_midi` drum notes:
   `note.start` (time), `note.pitch` (GM drum pitch), `note.velocity`.
3. Return `(time, gm_pitch, velocity, duration)` tuples.

Caveat: the full magenta TF stack is heavy for a PyInstaller one-file sidecar.
Prefer Option B for packaging.

### Option B — exported ONNX (preferred for the one-file sidecar)

1. Export the O&F drum graph (onset + velocity heads) to ONNX once, offline.
2. In `_run_inference`, `onnxruntime.InferenceSession(checkpoint)`, build the
   mel/CQT front-end features from `audio` at 16 kHz, run the session, and
   decode the onset+velocity heads to `(time, gm_pitch, velocity 1..127,
   duration)`.

**Both paths MUST emit General-MIDI drum pitches** (35/36 kick, 38 snare,
42/44/46 hi-hat, toms, 49/51 cymbals, …). The engine maps those through the
shared `_BENCHMARK_NOTE_TO_CLASS` → `_CLASS_TO_CANONICAL_NOTE` taxonomy via
`transcription._normalize_midi_note_to_canonical`, so downstream (drum_tab,
cleanup lanes) is byte-for-byte identical to the mr_mt3 output. Do not invent a
new pitch mapping.

Model input rate is **16 kHz mono** (see `_MODEL_SAMPLE_RATE`), matching the
MT3 drum path's `librosa.load(..., sr=16000, mono=True)`.

---

## Self-train fallback (Lever A) — if no checkpoint is downloadable

If the published E-GMD checkpoint turns out to be unavailable, train one — this
is legal and shippable because E-GMD is **CC BY 4.0**.

1. **Data:** E-GMD (444 h, 43 kits, real TD-17 e-kit audio, onset + class +
   velocity, ~2 ms alignment). We already have an adapter:
   `dataset_adapters/egmd.py`. Optionally augment with Slakh2100 (CC BY 4.0,
   synthetic) or STAR Drums (per-file CC — audit before shipping weights).
2. **Train** the Magenta O&F drums config on E-GMD (multi-day GPU job; the
   environment's RTX 5090 makes this feasible).
3. **Export** the trained checkpoint (prefer ONNX per Option B) and drop it in
   as a `drums_oaf` modelpack (layout above).
4. **Benchmark** via the existing harness:
   `gt-benchmark --dataset egmd` against the four heuristic engines to get
   honest per-class P/R/F. Add MDB-Drums (CC BY 4.0) as a small real-audio
   second data point.

Expect ~0.55–0.75 F1 on real acoustic mixes (the e-kit→real-mix transfer gap),
versus the current heuristic 0.05–0.4 — a large jump regardless.

---

## Verifying it is live

Once a checkpoint is resolvable:

```
AURAL_OAF_DRUMS_CHECKPOINT=/path/to/model.onnx \
  gt-benchmark --dataset egmd --engines drums_oaf,combined_filter
```

If the engine still returns no events, check (in order): the checkpoint path
resolves (env var or modelpack `modelpack.json` present), the runtime deps are
installed, and `_run_inference` has been implemented for the checkpoint format
you shipped.
