# Research Decision Gates

This document records the implementation-lock decisions for the current transcription recovery work. These are product defaults, not permanent research exclusions.

## Beat And Tempo Default

Decision: use `librosa` first for production-quality beat/tempo analysis.

- Default import mode is `high_accuracy`.
- `high_accuracy` uses `librosa.beat.beat_track` plus onset refinement.
- If `librosa` is unavailable or decoding fails, import degrades to `standard`.
- `standard` remains the deterministic fallback based on energy autocorrelation and a uniform beat grid.
- Essentia remains a research candidate only until it has a local adapter, benchmark data, and packaging evidence.

Rationale: transcription quality is the primary success metric. Beat timing quality affects downstream drum, melodic, and gameplay alignment more than the import speed saved by staying on the standard heuristic path.

## Stem Separation Policy

Decision: ship Demucs as an optional experimental provider, not a mandatory fully supported path.

- Default provider remains `auto`.
- `auto` uses Demucs only when the modelpack and runtime are present.
- If Demucs is absent, normal import must continue using provided stems or mix fallback.
- Demucs modelpack/runtime absence should be reported as capability metadata, not a portable import failure.
- GPU acceleration is first-class when available, but CPU fallback and model-absence safety remain required.

Rationale: Demucs is useful for A/B research and local quality work, but it is too heavy and operationally risky to make required for normal portable import.

## Benchmark Threshold Policy

Decision: CI threshold checks stay warn-only until representative baselines are frozen.

- `benchmarks/thresholds.yml` is the versioned threshold source.
- Strict PR blocking is disabled for now.
- Strict mode requires versioned baselines, role-specific quality thresholds, representative local hardware data, and reviewed synthetic/private quality fixtures.
- After those exist, keep warn-only active for at least 14 days before switching strict failure gates on.

Rationale: artifact collection should start now, but blocking PRs before baselines and fixtures are credible would create noise rather than quality pressure.

## ADT Architecture Revision (2026-05-07)

This section supersedes the older Stem Separation Policy gate above for the
production drum-transcription default once path 2 below lands. Until then,
Stem Separation Policy remains in effect.

Trigger: literature scan in [`docs/research-deep-dive-adt-2026-05-07.md`](research-deep-dive-adt-2026-05-07.md)
exposed seven stale or contradicted assumptions baked into the current
ingest pipeline (heuristic three-detector fusion as a viable production
path, 9-class taxonomy as the right output target, hand-tuned spectral-
centroid thresholds, source-weighted DSP fusion, Demucs as optional, that
cymbals are tractable, that overlapping hits are a refractory-window
problem). Recent work — Enhanced ADT via Drum Stem Source Separation
(Sept 2025), Noise-to-Notes (Sept 2025), YourMT3+ (2024), ISMIR 2025
"Performance Limitations in ADT" — points to a different production
shape.

Decisions:

1. **5-class taxonomy is the production-default output target.** Internal
   9-class detection is preserved as a research-only extended option
   (`--drum-taxonomy=9class`). The 5-class scaffolding (`STANDARD_5CLASS_DRUM_VOCABULARY`,
   `map_9class_drum_to_5class`, `map_midi_drum_to_5class_midi`) is in place
   in `python/ingest/src/aural_ingest/algorithms/_common.py` from
   2026-05-07. Production wiring (downstream consumers — events.json schema,
   gameplay metrics, charts, frontend mapping) is deferred until path 2
   below lands so the taxonomy + algorithm switch ship together.
2. **Production drum default migrates to a CRNN trained on ADTOF or to
   YourMT3+** once model-pack flow is validated for the larger weights.
   `combined_filter` remains shippable as `--drum-engine combined_filter`
   for research A/B but is no longer the production default. The 2026-05-07
   Phase 1 fix (low-band-energy guard + unanimous-detector boost) keeps
   the legacy path safe in the meantime — see test_quality_02 in
   `python/ingest/tests/test_ingest_quality_improvements.py`.
3. **Demucs preprocessing is required for the production drum path** once
   path 2 lands. The Stem Separation Policy section above keeps Demucs
   experimental for the current default; the goal under this revision is
   to flip it to required-with-degraded-warn-on-absence. The portable
   build already stages `demucs_6.zip`; the runtime-check already exposes
   it; the gating just needs to flip from "auto if present" to "required
   for production drums, with documented degraded fallback when missing."
4. **Hand-tuned spectral-centroid thresholds are removed from production
   classifiers.** Specifically the `kick + centroid > 520 → tom_floor`
   rule was dropped from `combined_filter` on 2026-05-07. New rules need
   to either be learned or framed as low/mid/high energy ratio guards
   that survive real-recording variability.
5. **Overlapping-hit handling is a model-level concern**, not a
   refractory-window concern. ISMIR 2025 "Performance Limitations" reports
   simultaneous-hit handling as the dominant ADT performance constraint;
   per-class refractory windows can only resolve same-class chatter.
   Multi-label per-frame outputs (CRNN/transformer) are the path; this
   revision flags refractory-only as inadequate for the drum-default but
   keeps it as a post-processing safety net.

Phase deferral: paths 1 and 3 ship together with path 2 (the algorithm
swap) as a coherent v0.2 of the drum transcription stack. Shipping
taxonomy or separator changes alone would break downstream consumers
without delivering the quality lift that justifies them.

## Implementation status (2026-05-07)

Phase 1 + 2 landings:

- **Path 1 (5-class taxonomy)** — opt-in production output now wired via
  `--drum-taxonomy=5class` and the `taxonomy` keyword arg on
  `transcribe_drums` / `transcribe_drums_dsp`. Default remains `9class`
  for backward compatibility; the production flip is held for the v0.2
  algorithm swap so downstream consumers (events.json, charts, gameplay
  metrics, frontend mapping) all migrate together.
- **Path 2 (production drum default)** — orchestration plumbing for MT3
  is in place via `transcribe_drums_with_profile()`, which walks the
  profile's `drum_engines` list and silently falls back from MT3 to DSP
  when MT3 weights/runtime are absent. Actual flip from `combined_filter`
  to ADTOF / YourMT3+ as the global production default still requires
  model weights and a model-pack-flow validation pass.
- **Path 3 (Demucs required-with-warn)** — `auto` separator provider
  selection in `cli.py` now appends a structured warning to the
  transcription warnings list when the `demucs_6` modelpack is absent,
  rather than silently degrading. The full required-with-failure flip
  (refuse import without Demucs) remains held with path 2.
- **Path 4 (drop centroid threshold rules)** — landed in Phase 1; the
  legacy `kick + centroid > 520 → tom_floor` rule in `combined_filter`
  is removed.
- **Path 5 (overlapping-hits at the model level)** — heuristic
  precursor landed in Phase 2 as the multi-label emitter in
  `combined_filter`. A real multi-label CRNN remains held with path 2.
