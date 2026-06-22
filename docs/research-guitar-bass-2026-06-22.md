# Research — Guitar (electric) & Bass transcription (2026-06-22)

Deep, adversarially-verified research (clean re-run: 21 confirmed, 4 killed).
Goal: raise electric-guitar F1 (our hardest instrument, ~0.24–0.36) and fix
bass octave errors (best is a YIN-octave + HPS-pitch hybrid, ~0.52 F1).

## Bass — clearest shippable win

**Fix octave errors with `torchcrepe`** (neural monophonic pitch tracker,
`github.com/maxrmorrison/torchcrepe). It is **MIT-licensed, pip-installable, a
pure-Python wheel, CPU-runnable** — i.e. drops straight into our Python-3.11 /
PyInstaller closed-commercial sidecar, and pairs naturally with our existing
octave/onset post-processing. Highest-confidence, lowest-friction upgrade.

## Electric guitar — ceiling is model-dependent

- **Onset-F1 ceiling ~0.78–0.84 on amp-rendered/clean electric with a tone-aware
  model** (Tone-informed Transformer, arXiv 2504.07406). (The earlier ~0.61
  "clean-DI ceiling" was *refuted 0-3* — do not cite it.) General models collapse
  on **real distorted commercial pop** (YourMT3+ <10% on non-main instruments,
  arXiv 2407.04822) — real-world will be materially below the rendered ceiling.
- **Highest-leverage lever: amp-tone / multi-timbre training augmentation.**
  Re-amping/effect-augmenting training audio lifts held-out electric tablature F1
  **0.45 → 0.59** (+31% rel) and unseen-tone onset **0.534 → 0.592**, while barely
  moving in-domain acoustic — it buys tone robustness, not capacity (arXiv
  2405.14679, 2202.09907). (The claim that augmentation *beats* tone-conditioning
  architecture as the single biggest factor was *refuted* — treat as
  complementary.)
- **SynthTab synthetic-pretrain → real fine-tune** beats real-only training and
  can mass-produce labeled electric data with expression techniques (arXiv
  2309.09085).
- TabCNN / FretNet jointly do polyphony + string/fret (FretNet adds continuous
  pitch for bends/vibrato) — but all validated on *acoustic* GuitarSet only.

## Offline / license fit

Only **`torchcrepe` and `basic-pitch`** were confirmed permissive + offline-
friendly. MT3/MR-MT3/YourMT3+/Omnizart were not confirmed (research provenance,
synthetic-data weakness). `trimplexx/music-transcription` is an MIT-licensed
GuitarSet CRNN (acoustic) worth a look for the polyphonic path.

## Pragmatic path (matches our split pipeline)

Hybrid: keep DSP onset/beat; add **torchcrepe** for monophonic **lead/bass** with
octave correction; add a polyphonic step (basic-pitch already wired, or a
TabCNN/FretNet-class model) for **rhythm** chords. **Specializing lead (mono) vs
rhythm (poly) is evidence-supported.**

## Eval plan

Finally benchmark **Guitar-TECHS** (electric, never benchmarked) and **EGDB** at
note-level / note-with-offset F1, ±50 cents pitch tolerance. See
`benchmarks/guitar/GUITAR_TRANSCRIPTION_EPIC.md`.
