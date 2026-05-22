# AuralPrimer — WIP / Implementation Tracker

This file is the living implementation tracker for AuralPrimer.

It is intentionally **engineering-oriented**: concrete milestones, technical tasks, dependencies, and **TDD-first** acceptance criteria.

> Authoritative requirements: `spec.md`
> Architecture: `docs/architecture.md`
> File format: `docs/songpack-spec.md`
> Ingest pipeline: `docs/ingest-pipeline.md`
> Plugin API: `docs/visualization-plugins.md`
> Testing: `docs/testing-strategy.md`
> Release/packaging: `docs/packaging-ci.md`
> Recovery notes (2026-03-03): `PROJECT_ARCH_FROM_MEMORY.md`, `TRANSCRIPTION_RECOVERY_NOTES.md`, `DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md`, `TRANSCRIPTION_REGRESSION_HISTORY.md`

---

## Global constraints (do not regress)

### Platforms
- Windows + Linux only.

### SongPack-first runtime
- Gameplay/runtime consumes SongPacks.
- External ecosystems are supported via **pluggable importers** that convert into SongPacks.

### Audio formats
- SongPacks may include compressed audio (`mix.mp3` / `mix.ogg`).
- Playback decoding is via a **local codec layer**.

### Models
- **Do not bundle model weights in installers.**
- Models are obtained post-install (in-app download or manual import) and stored under:
  - `assets/models/<model-id>/<version>/...`
- Portable folder builds may pre-stage modelpack zip artifacts (for offline install workflows), but runtime install location remains `assets/models/...`.

### Engineering process
- **All development is TDD-first**.
- Any milestone is not complete without:
  - unit tests and/or contract tests
  - updated golden fixtures when outputs intentionally change
  - CI green

---

## Status

### Status snapshot (2026-05-07)

Where things stand. Treat this as the "what changed since the 2026-04-20 audit" addendum; the per-milestone checkboxes below remain authoritative for fine-grained progress.

**Recently landed on `main` (since 2026-04-20)**
- MIDI input/clock hardening: stable port re-resolution by backend id, MIDI port list survives settings failures, frontend active-note + sustain monitor (`d9e0f1d`, `7581341`).
- Transcription quality program: new quality benchmark module/CLI, gameplay metrics, profile-aware import metadata, Piano MIDI Refinement Workbench scaffolded (`7581341`, `e9d9cab`).
- Model manager hardening (Milestone 6 first pass): preferred packs, local zip import, hash + path-traversal safety, Demucs/MT3 metadata exposed via `runtime-check` (`d855be9`).
- Transcription recovery regressions tightened, sidecar packaging contract hardened, portable build copies the just-built sidecar with hash/timestamp guard (`afbfcb9`, `fe67cf7`).
- Build ergonomics: WSL-safe Tauri/portable launchers; same `npm run game:build` / `studio:build` / `portable:build` work from native Windows or WSL worktrees (`555c69a`, `e7f92f3`).
- Studio import UX improvements + Suno MIDI normalization keeping source MIDI authoritative when mappable, with safe drum-pitch preservation (`0f55d19`).
- SongPack validator entry points re-hardened, including richer error contexts (`637c5df`).

**Landed in this session (uncommitted on working copy)**
1. `apps/game` no longer hosts import or content-creation flows — `spec.md §1.1` is now enforced in the gameplay app:
   - Removed the in-flight "Import" top-level route and the existing import sections in Configure (Suno stem+MIDI, GHWT, analysis import, advanced sidecar ingest).
   - Removed `generateLyricsForSelectedSongPack` from the visualizer-start path; the gameplay app now renders without lyrics if `features/lyrics.json` is missing and points users to AuralStudio.
   - Stubbed `apps/game/src/{ingestClient,ingestUi,lyricsGenerator}.ts` to empty modules and replaced their tests with `describe.skip` placeholders. Authoritative copies remain in `apps/desktop`.
   - Stripped orphaned import-only CSS classes (`.importLayout`, `.importStack`, `.importAudit*`) and added `.menuCard--info` for the "Import / Create lives in AuralStudio" home card.
2. New sparse-source drum precision regression: `python/ingest/tests/test_drum_sparse_source_precision.py` runs `combined_filter` against a synthetic 4-kick stem and asserts (a) the algorithm did NOT fall through to `fallback_events_from_classes` (the dense-grid Psalm 12 hallucination path) and (b) event count stays in `[1, 12]`. A second test pins the documented behavior on truly silent input. Smoke-verified that the test fails when `detect_candidates` is forced to return empty — i.e. the guard correctly catches the regression.
3. Multi-role quality guard manifest scaffold: `benchmarks/quality/full_corpus_manifest.guard.template.json` enumerates the five required guard cases (drums / bass / rhythm guitar / lead guitar / second keys-or-piano) with placeholder paths and per-case `intent` notes naming the algorithms each case should pin. Closing this manifest is the exit criterion for graduating the quality benchmark to a promotion gate.
4. `.github/workflows/build-release.yml` rewritten:
   - Builds both `@auralprimer/studio` and `@auralprimer/game` Tauri bundles (no more legacy `@auralprimer/desktop` target).
   - Replaces the echo-only Python sidecar placeholder with a real PyInstaller invocation (Linux uses `aural_ingest.spec` directly; Windows reuses `npm run portable:sidecar` with `-SyncTauriBinaries`). The sidecar step is `continue-on-error: true` because the spec collects heavy ML deps; the portable build remains the canonical end-to-end path with the hash/timestamp freshness guard.
   - Three artifact uploads: `auralstudio-<os>`, `auralprimer-<os>`, `aural-ingest-sidecar-<os>`.

**Top of mind / immediate next steps**
1. Validate the apps/game cleanup by running `npm test` and `npm -w @auralprimer/game run typecheck` once dependencies are installed locally; confirm no other sites still reference the removed identifiers.
2. Continue closing out Milestone 4A: replace the deterministic melodic stubs with real Basic Pitch / pYIN inference (the new `test_drum_sparse_source_precision.py` is the synthetic guard; a real Psalm 12-style fixture in the quality benchmark is still wanted).
3. Fill in the five guard cases in `full_corpus_manifest.guard.template.json` with real local stems and run the manifest end-to-end.
4. Freeze a versioned frontend benchmark baseline + threshold policy so `npm run bench:frontend:compare` can graduate from warn-only to a PR gate.
5. Validate `build-release.yml` end-to-end on `workflow_dispatch` once the next release tag goes out; harden the sidecar build deps if the heavy ML wheels make the Linux job impractically long.
6. Replace placeholder visualizers (`viz-beats`, `viz-nashville`, `viz-fretboard`) with data-driven implementations once `VizInitContext` exposes beats/sections, chord/key data, and note queries.

**Phase 2 ADT fixes landed (2026-05-07, working copy, second pass)**

Following Phase 1, a second pass landed all of the deep-dive paths that
don't require ML model weights or copyrighted audio. Listed by phase:

- **Phase A — 5-class taxonomy as opt-in production output.** Added
  `KNOWN_DRUM_TAXONOMIES`, `validate_drum_taxonomy()`, and
  `remap_drum_events_to_taxonomy()` in
  `python/ingest/src/aural_ingest/transcription.py`. New `taxonomy`
  keyword arg threaded through `transcribe_drums` /
  `transcribe_drums_dsp` (default None → `9class`, opt-in `5class`).
  The taxonomy is recorded in `DrumTranscriptionResult.meta["taxonomy"]`
  on every code path. Production default stays `9class` until path 2
  (model swap) lands, per `docs/research-decision-gates.md`.
- **Phase B — Multi-label overlapping-hit emitter** in
  `combined_filter`. When the runner-up class clears its class floor,
  scores ≥70% of the top class, has a real source-detector vote (not
  just feat-based boost), and belongs to a different physical drum
  group than the selected class, emit it as a SECOND event at the same
  time. Heuristic precursor to a true multi-label CRNN; addresses the
  ISMIR 2025 finding that overlapping hits are the dominant ADT
  performance constraint without needing a model.
- **Phase C — Demucs gate flipped to required-with-warn.** When the
  `auto` separator provider falls through to `none` because the
  `demucs_6` modelpack is absent, a structured warning is now appended
  to the transcription `warnings` list explaining that drum quality is
  reduced. The `auto` selection logic in `cli.py` was reworked to
  capture the modelpack-resolve error in the warning. Production
  imports without Demucs still succeed but no longer silently absorb
  the quality cost.
- **Phase D — MT3/YourMT3+ profile-driven orchestration.** New
  `transcribe_drums_with_profile()` in `transcription.py` walks the
  profile's `drum_engines` list in order and silently falls back from
  MT3 to DSP when MT3 weights/runtime are missing. The `fidelity_midi`
  profile's MT3-first preference now actually executes when the user
  opts in via profile; `gameplay_default` is unchanged because its
  list begins with `beat_conditioned_multiband_decoder`.
- **Phase E — Basic Pitch real-inference path.** Already wired in the
  existing `aural_ingest/algorithms/melodic_basic_pitch.py`: when the
  `basic_pitch` package is importable, real `predict()` runs; when not,
  the deterministic stub fires. Verified via code inspection. Model
  path resolution via `resolve_basic_pitch_model_path` looks under
  `assets/models/<model-id>/<version>/...` per the model-pack contract.
- **Phase F — Realistic synthetic Psalm-12-equivalent fixture.** New
  `python/ingest/tests/test_drum_psalm_12_equivalent.py` synthesizes a
  4-bar 96-BPM kick stem with realistic 65 Hz body + attack noise +
  decay tail + low-level pink-ish ambient noise, and asserts (a)
  kick fraction ≥ 60%, (b) crash+ride fraction ≤ 25%, (c) total event
  count in `[4, 64]` (guards against the dense fallback grid). Better
  triggers the broadband-transient → cymbal misclassification mode
  than the clean-sine fixture in test #2.

Pre-existing failures unrelated to this work (`spectral_flux_multiband`
hi-hat detection on the rebuild fixture) remain untouched — separate
algorithm, separate fix.

Genuinely deferred (require external resources we don't have here):

- **Real ADTOF / YourMT3+ model weights** — the orchestration plumbing
  is in place but the actual MT3 path needs the modelpack files.
- **Real Psalm-12 audio fixture** — copyrighted, can't ship; the
  synthetic equivalent above is the closest distributable substitute.
- **Multi-label CRNN model** — the heuristic emitter in Phase B is the
  shippable approximation; a learned model would do better.
- **Flip 5-class to default production output** — held until path 2
  (algorithm swap) per the deep-dive recommendation that taxonomy +
  separator + engine ship together as a coherent v0.2.

Sandbox verification note: the bash mount in this session sees stale
truncated content for some files (notably `algorithms/_common.py`)
because the Windows ↔ Linux mount lacks atomic propagation. The
Windows-side files (the actual repo) are verified correct via the file
tools. Tests run cleanly in any normal dev environment with a fresh
pycache.

---

**Phase 1 ADT fixes landed (2026-05-07, working copy)**

Following the deep-dive below, Phase 1 from the implementation plan landed:

- **`combined_filter` low-band-energy guard.** New rule: when the cluster's
  `low_dom > 0.55`, never assign `crash`/`ride`/`hh_open` — fall to `kick`
  if any kick votes exist, else to a tom. Blocks the kick→crash hallucination
  shape that produced the Psalm 12 report.
- **`combined_filter` stem-level unanimous-detector boost.** New helper
  `_stem_unanimous_classes()`: when one source detector emits the same
  drum class across ≥4 candidates in a stem (e.g. `aural_onset` emits only
  kicks on a kick-only stem), its weight is multiplied by 1.8× inside the
  cluster vote so the unanimous detector can overrule a louder but
  noisier disagreer.
- **Stale `kick + centroid > 520 → tom_floor` rule dropped.** The 2018
  Wu/Lerch ADT survey and the 2026 Towards-Realistic-Synthetic-Data paper
  both flag hand-tuned centroid thresholds as unreliable on real audio.
- **Test #2 in `test_ingest_quality_improvements.py` flipped from XFAIL
  to PASS.** Quality battery now: 9 pass / 1 XPASS / 1 skip / 0 fail.
  Previously regressing pure-kick fixture: 0% kick → 90% kick (9/10
  events correctly classified).
- **5-class taxonomy scaffolding added** to
  `python/ingest/src/aural_ingest/algorithms/_common.py`:
  `STANDARD_5CLASS_DRUM_VOCABULARY`, `DRUM_9CLASS_TO_5CLASS`,
  `DRUM_5CLASS_TO_MIDI`, `map_9class_drum_to_5class()`,
  `map_midi_drum_to_5class_midi()`. No production path uses this yet —
  forward scaffolding for path 2 (ADTOF/YourMT3+ integration). Tests:
  `python/ingest/tests/test_drum_5class_taxonomy.py`.
- **`docs/research-decision-gates.md` got an "ADT Architecture Revision
  (2026-05-07)" section** that supersedes the older Stem Separation Policy
  gate once the algorithm swap lands. Records the five concrete
  decisions (5-class default, ADTOF/YourMT3+ production default, Demucs
  required, no hand-tuned centroid thresholds, overlapping-hits is a
  model-level concern).

Pre-existing failures unrelated to this session (`spectral_flux_multiband`
hi-hat detection on the rebuild fixture) remain untouched — separate
algorithm, separate fix.

Deferred (require model weights or wide refactor — not in this session):
adopt 5-class as production default (touches schemas / charts / gameplay
metrics / frontend), replace drum default with ADTOF or YourMT3+ in the
orchestration, make Demucs required at runtime, real Basic Pitch
inference, real Psalm-12 fixture, multi-label CRNN for overlapping hits.

---

**Research deep-dive: ADT/transcription assumptions vs. 2024–2025 literature (2026-05-07)**

Before iterating further on `combined_filter` we ran a literature scan to
test the architectural assumptions baked into the current pipeline. The full
analysis lives in [`docs/research-deep-dive-adt-2026-05-07.md`](docs/research-deep-dive-adt-2026-05-07.md).
Headline findings:

- The three-detector heuristic fusion approach is not in any modern SOTA
  ADT system (2018 Wu/Lerch survey already concluded this; every leaderboard
  since has been neural). Our own test reproduces the failure mode the
  survey predicted: source-weighted fusion lets the loudest detector's
  biases dominate even when its precision is worst.
- The 9-class drum taxonomy in our code is non-standard. Modern benchmarks
  use 5-class (ADTOF) or 18-class (extended). 9 fragments evaluation and
  blocks pre-trained model drop-in.
- Cymbals and overlapping hits are the dominant performance constraints
  per ISMIR 2025 "Performance Limitations in ADT". Per-class refractory
  windows (our current strategy) cannot resolve same-time / different-class
  hits — only multi-label model outputs can.
- Demucs preprocessing is now treated as required (+5–10% F1), not
  best-effort optional. Our `docs/research-decision-gates.md` decision is
  older than the 2025 papers and should be revisited.
- Hand-tuned spectral-centroid thresholds (our `> 520 Hz → tom_floor` rule)
  are a stale technique known to break on real recordings.
- pYIN is a reasonable monophonic baseline but Basic Pitch / CREPE and
  multi-pitch CRNN architectures dominate on polyphonic content. We have
  `basic_pitch` declared in `KNOWN_MELODIC_METHODS` but the deterministic
  stub does not actually run model inference — biggest melodic gap.
- DSP determinism is not a real product advantage over modern pinned-weight
  ML inference; this framing has been silently capping the ingest pipeline
  at "DSP that runs in 100 ms per stem" when offline import can comfortably
  spend 30 s per stem on a heavy model.
- Newer work to track even if not integrated this quarter: Noise-to-Notes
  (diffusion ADT, new SOTA Sept 2025), Enhanced ADT via Drum Stem Source
  Separation (Sept 2025, almost exactly the architecture we should consider
  as the drum default), The Inverse Drum Machine (May 2025, joint
  separation + transcription), STAR Drums dataset, LarsNet 5-stem drum
  separation.

The deep-dive replaces the previous top-10 paths-forward list with a
revised order driven by the literature: adopt 5-class output, integrate
ADTOF or YourMT3+ as the production drum default, make Demucs required for
the production drum path, and only then layer in the smaller DSP fixes
(centroid rule, fusion re-weighting). The smallest concrete next change
remains: drop the centroid → tom_floor rule and add a low-band-energy
guard against kick→crash misclassification, in parallel with scoping
ADTOF integration.

**Top-10 ingest quality battery + iteration findings (2026-05-07)**

A new battery `python/ingest/tests/test_ingest_quality_improvements.py` exercises 10 measurable quality dimensions with synthetic fixtures. Run summary on the current implementation: **9 pass, 1 xfail (documented regression), 1 skip (optional dep)**.

| # | Dimension | Result | Note |
|---|---|---|---|
| 1 | Sparse-source snare precision (no fallback grid) | PASS | Companion to the earlier kick guard. |
| 2 | Pure kick fixture not classified as crash/ride | XFAIL | combined_filter routes 9/10 events to crash, 1 to tom_floor. Documented Psalm 12 root cause; details below. |
| 2b | Alt engines (gameplay_default profile) classify kick correctly | PASS | `beat_conditioned_multiband_decoder` and `spectral_flux_multiband` both score 100% kick on the same fixture. |
| 3 | Hi-hat-only stem dominated by hi-hat events | PASS | |
| 4 | Drum onset timing accuracy (±20 ms vs ground truth) | PASS | |
| 5 | Multi-class fixture recovers ≥2 of 3 core classes | PASS | |
| 6 | No chatter within per-class refractory windows | PASS | |
| 7 | Melodic pYIN tracks a sustained 220 Hz sine to A3 | PASS | |
| 8 | No blanket octave-error on a clean sine | PASS | |
| 9 | Beat tracking estimates 120 BPM ±5 | SKIP | Skips when `soundfile` (optional ingest dep) is unavailable. |
| 10 | Unknown filter id degrades to combined_filter with warning | PASS | |

**Iteration findings — combined_filter kick→crash misclassification (dimension 2)**

Per-detector probe on a 10-kick synthetic stem:

- `aural_onset` alone: 10 events, **100% kick** (perfect).
- `dsp_bandpass_improved`: 44 events, 23 kick + 17 crash + 4 tom_floor (over-emits on a sparse stem).
- `dsp_spectral_flux`: 21 events, 10 hh_closed + 7 crash + 3 ride + 1 kick (also over-emits, with the wrong classes).
- `combined_filter` (the three-detector fusion): 10 events, **0% kick** — classes flip to 9 crash + 1 tom_floor.

Source weights in `combined_filter._source_weight` are `dsp_bandpass_improved=1.0`, `dsp_spectral_flux=0.8`, `aural_onset=0.6`. Even though `aural_onset` is unanimously correct, the fusion's source weights let the over-emitting detectors win. A separate post-classifier rule (`elif selected_class == "kick" and feat["centroid"] > 520.0: selected_class = "tom_floor"`) further routes the surviving kick votes into tom_floor.

Whole-orchestration check: `transcribe_drums_dsp` only walks the fallback chain when an algorithm returns no events or raises. Because `combined_filter` returns 10 (wrong-class) events, the chain stops at the first algorithm and never tries `aural_onset` directly. So a real production import on a kick-heavy stem would emit hits classified as crash/tom_floor — exactly the Psalm 12 hallucination shape.

**Top-10 quality improvements for the ingest pipeline (paths forward)**

Grouped by tractability. Items 1–3 are testable today; items 4–7 are mostly tuning; items 8–10 require model integration.

1. **Promote a profile-aware default drum engine over `combined_filter`.** `gameplay_default`'s `beat_conditioned_multiband_decoder` and `spectral_flux_multiband` both score 100% kick on the same fixture where `combined_filter` scores 0%. The cheapest fix is to make the legacy `combined_filter` alias resolve to one of these in the default chain, leaving `combined_filter` available as an explicit research opt-in.

2. **Re-weight three-detector fusion to favor unanimous low-emitter votes.** When `aural_onset` is class-consistent (single class across all candidates) and the other detectors disagree, give it more weight in the cluster vote. This addresses the failure mode where `dsp_bandpass_improved`'s false-positive crash candidates outvote `aural_onset`'s correct kick votes.

3. **Tighten the kick→tom_floor centroid threshold in `combined_filter`.** The current rule triggers at `feat["centroid"] > 520.0`, which is too low for a 58 Hz sine kick whose attack transient has broadband energy. Raising to >800 Hz (or gating on `low/mid` ratio) would stop kicks from being silently demoted.

4. **Reduce `dsp_bandpass_improved`'s crash false-positive rate on sparse sources.** It emits 17 crash candidates on a clean 10-hit kick stem. The detector likely needs a higher minimum crash-energy threshold or a low-band sanity check before emitting crash.

5. **Fall through to the next algorithm when class distribution is suspiciously skewed.** Today the chain only fires on empty/exception. A skew-aware fallback (e.g., if 90%+ of events are crash for a stem with kick-band-dominant low energy, retry with the next algorithm) would catch this regression at the orchestration layer rather than the algorithm layer.

6. **Add per-class velocity floors and refractory tightening** (the chatter test passes today but is bound generously). Shorter refractory on hi-hat closed (currently 0.055 s) plus a velocity floor would make the algorithm survive denser hat patterns without false-positive chatter; a small benchmark on a 16th-note stream would set the right threshold.

7. **Build a real Psalm 12-equivalent fixture from a non-copyrighted source.** The current synthetic guard catches the dense-fallback path; a longer, more realistic stem with bleed and ambience would expose the noise-onset → crash misclassification under more realistic conditions. Drop it into `benchmarks/quality/full_corpus_manifest.guard.template.json` `guard_drums_TBD`.

8. **Replace the deterministic melodic stubs with real Basic Pitch / pYIN inference.** Today's pYIN baseline correctly tracks a clean sine (dimensions 7 + 8 pass) but produces only one note per stable pitch region. Real Basic Pitch would unlock chord/poly-melodic stems where the deterministic stubs collapse to monophonic.

9. **Wire MT3 / YourMT3 drum engines (already declared) into `transcribe_drums` orchestration on opt-in.** Both engines are listed in `KNOWN_MT3_DRUM_ENGINES` and have stage metadata in `runtime-check`, but the orchestration doesn't try them automatically. A profile-driven opt-in (`fidelity_midi` or `research_ab` enables MT3 first, with DSP fallback) would let users get model-quality drums where models are present without breaking the model-absent default.

10. **Add an audio→reference sync quarantine to the ingest pipeline.** Documented in wip.md as gameplay-metric "start-offset quarantine" but not yet enforced as a hard import gate. A short-list of import-time precision checks (drum count vs. mean inter-onset interval, melodic note density vs. tempo, lyrics-word alignment to vocal stem onsets) would surface bad imports before they hit the songs library.

**Suggested next concrete pull request (smallest unit of progress)**

- Apply path #3 (raise the kick→tom_floor centroid threshold to ≥800 Hz). Test #2 should flip from XFAIL to XPASS, which makes pytest report the regression as fixed and force the xfail decorator to be removed in the same change.
- If #3 alone is insufficient, layer in path #2 (re-weight `aural_onset` to ≥1.0 when its class distribution is unimodal). Re-run the battery; #2 should pass while #1, #3, #4, #5, #6 stay green (regression guards).
- Either way, this PR closes the documented Psalm 12 hallucination on synthetic input and unlocks tighter bounds on tests #2, #4, and #5 once a wider quality-benchmark run confirms the change is safe on real stems.

**Doc/scaffolding hygiene done earlier in this session**
- README docs index now points at every doc that lives under `docs/` (audio-codec-policy, midi-keyboard-testing, performance-baselines, research-decision-gates, songpack-deliverable, testing-strategy, local-dev-prereqs).
- README monorepo-layout block now matches the real visualizer names (`viz-beats`, `viz-drum-highway`, `viz-fretboard`, `viz-lyrics`, `viz-nashville`) and surfaces `benchmarks/` and `scripts/`.
- BUILDING.md picked up a "Working in this repo (quick orientation)" section so a returning contributor can find spec / wip / TDD rule / benchmarks / portable smoke / app boundaries in seven bullets.

---

### Done (docs / blueprint)
- [x] High-level architecture drafted (`docs/architecture.md`)
- [x] SongPack spec drafted (`docs/songpack-spec.md`)
- [x] Ingest pipeline draft (`docs/ingest-pipeline.md`)
- [x] Packaging/CI draft (`docs/packaging-ci.md`)
- [x] Roadmap draft (`docs/roadmap.md`)
- [x] Requirements consolidated (`spec.md`)
- [x] TDD mandated across documentation (`spec.md`, `docs/testing-strategy.md`, etc.)

### Recovery context (from notes; not yet revalidated in current tree)
- [x] Capture architecture + regression history from memory in four recovery notes
- [x] Restore Studio app surface and portable build scripts (`build_sidecar.ps1`, `create_portable.ps1`)
  - [x] Restored portable scripts: `build_sidecar.ps1`, `create_portable.ps1` (hash/timestamp freshness guard)
  - [x] Split products into two separate app surfaces: `apps/game` (AuralPrimer gameplay) and `apps/desktop` as AuralStudio (content creation)
- [x] Restore advanced sidecar CLI surface (`import-dir`, `import-dtx`, `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter`)
- [~] Restore drum transcription algorithms + fallback ordering with `combined_filter` default
  - [x] Added deterministic algorithm modules under `python/ingest/src/aural_ingest/algorithms/*` for all planned IDs
  - [x] Wired `transcribe_drums` stage to emit `features/events.json` from selected/fallback algorithm
  - [x] Replaced deterministic pattern stubs with class-based waveform-driven DSP rebuild (`TranscriptionAlgorithm` contract, shared pre/post-processing, weighted fusion in `combined_filter`)
  - [~] Real-world sparse-source fidelity is still not recovered: the default `combined_filter` path is currently over-liberal on at least one reported real song (`Psalm 12`), with likely false-positive pressure coming from three-detector fusion, aggressive expanded-kit remaps, permissive refractory settings, and the dense synthetic fallback path used when candidate recovery fails
  - [ ] Reach full pre-loss quality parity and optional ML-backed drum inference path
- [~] Restore melodic transcription paths (Basic Pitch + pYIN) and frozen-runtime model-path fallback
  - [x] Added melodic orchestration for `auto`/`basic_pitch`/`pyin` with fallback + warning propagation
  - [x] Added model lookup fallback order (`onnx -> tflite -> savedmodel`) in sidecar transcription module
  - [~] Replaced deterministic melodic stubs with waveform-driven monophonic pitch tracking + dyad expansion baseline
  - [ ] Reach full Basic Pitch/pYIN model-quality parity in packaged runtime
- [x] Restore desktop drum parser strict/relaxed guard and King in Zion regression fixtures/tests
- [x] Revalidate packaging discipline so portable builds always include the newest sidecar (timestamp/hash check)
- [x] Portable build now stages `demucs_6` modelpack (`keys/drums/guitar/bass/vocals`) with manifest validation
- [x] Resolve note conflict for `import-dir` events export ordering (`sections` before `events.json`) and lock with tests

### Transcription quality program (2026-05-02)
- [~] Treat transcription improvement as a repo-wide quality program across drums, bass, guitar, keys/piano, and import sync instead of one-off method tweaks.
  - [x] Added transcription profiles: `gameplay_default`, `fidelity_midi`, `research_ab`.
  - [x] Added unified quality benchmark module/CLI for full-corpus runs with profile metadata, optional backend status, gameplay metrics, sync quarantine, reports, and heatmaps.
  - [x] Added gameplay metrics for density, duplicates/chatter, polyphony, piano hand distribution, drum lane coverage, drum overlaps, and start-offset quarantine.
  - [x] Recorded transcription profile in import metadata and stable song-id fingerprinting.
  - [x] Added role playability cleanup for bass, lead guitar, and rhythm guitar.
  - [x] Added fail-safe optional research wiring for MT3/YourMT3, Basic Pitch, Transkun, PTI, hFT, torchcrepe, BeatNet, and Omnizart availability reporting.
  - [x] Added `torchcrepe` as explicit/research monophonic method only; not part of legacy/default fallback.
  - [x] Added quality epic tracker: `benchmarks/quality/TRANSCRIPTION_QUALITY_EPIC.md`.
  - [~] Add standalone Piano MIDI Refinement Workbench for source MIDI + audio A/B review.
    - [x] Captured requirements in `benchmarks/piano/PIANO_REFINEMENT_WORKBENCH.md`.
    - [x] Implement `refine-piano` CLI, static dashboard, candidate MIDI artifacts, and reference/no-reference scoring.
    - [ ] Validate on real Suno piano MIDI plus matching audio/reference cases.
  - [x] Generate benchmark manifests from scanned SongPacks/split-stem folders so full-corpus A/B runs are repeatable.
  - [x] Add bounded guard-run filters (`--role`, `--case-filter`, `--max-cases`) for generated quality manifests.
  - [x] Add self-contained classifier performance explorer for full report coverage: role/method/risk filters, per-class metrics, confusions, pitch summaries, and TP/FP/FN timelines.
    - [~] Run the generated full-corpus quality manifest on local guard cases and save report artifacts.
      - [x] First bounded guard smoke: Psalm 130 keys / `piano_auto`, role-filtered reference MIDI, saved at `benchmarks/quality/runs/20260502_100131_psalm-130-keys-guard-role-filtered`.
      - [x] Multi-role guard manifest scaffolded at `benchmarks/quality/full_corpus_manifest.guard.template.json` with placeholder cases for drums, bass, rhythm guitar, lead guitar, and a second keys/piano case (per-case `intent` notes name the algorithms each case should pin).
      - [ ] Fill in the five guard cases with real local stems and run end-to-end before treating the quality benchmark as a promotion gate.
  - [x] Add promotion-gate evaluation that labels benchmark winners without automatically changing `gameplay_default`.
  - [ ] Implement active BeatNet beat/downbeat-prior adapter only if benchmark setup shows it is worth testing.
  - [ ] Implement active Omnizart research comparator only if benchmark setup shows it is worth testing.
  - [ ] Add split-folder and single-file analysis import smoke gates to the final promotion checklist.
  - [ ] Build portable only after targeted tests, full benchmark, import smoke tests, model-absence tests, sidecar runtime check, and packaging checks pass.

### Audit refresh (2026-04-20 repo scan)
- [x] Current app split is real in the tree: `apps/game` is the primary playback/gameplay runtime, while `apps/desktop` is the authoring/import surface.
- [~] `apps/desktop` still carries a hidden `legacyPlaybackScaffold` for shared transport/plugin code paths, but it is not the active end-user playback shell.
- [x] `spec.md §1.1` is now actively enforced in `apps/game`: import / song-creation / lyrics generation flows have been removed from the gameplay app (route, markup, handlers, helper modules, related tests). Authoritative copies remain in `apps/desktop`.
- [~] Reference visualizers are mixed-fidelity right now:
  - [x] `viz-lyrics` consumes real `features/lyrics.json` timing data when present.
  - [x] `viz-drum-highway` consumes host-provided parsed MIDI note events.
  - [ ] `viz-beats` still renders a placeholder 1-beat-per-second grid instead of SongPack beat/section data.
  - [ ] `viz-nashville` still renders a placeholder harmony lane because the host does not yet provide chord/key song data to plugins.
  - [ ] `viz-fretboard` still uses a placeholder time-driven cursor instead of host note/chord queries.
- [~] Release automation is still partial:
  - [x] `.github/workflows/lint-test.yml` runs TS, Python, Rust, SongPack fixture validation, and Rust coverage.
  - [x] `.github/workflows/build-release.yml` now builds both `@auralprimer/studio` and `@auralprimer/game`, runs a real PyInstaller-based sidecar step (Linux uses `aural_ingest.spec`; Windows reuses `npm run portable:sidecar`), and uploads three artifact bundles. The sidecar step is `continue-on-error: true` because the spec collects heavy ML deps; the portable build remains the canonical end-to-end path.
  - [ ] Validate `build-release.yml` on `workflow_dispatch` once the next tag goes out; consider trimming the sidecar deps if the Linux job runs too long.

### Now (top priority)
- [x] Create the initial monorepo layout skeleton (apps/, packages/, python/, visualizers/, assets/, docs/)
- [x] Implement SongPack discovery + library indexing (scan folder; parse directory + zip `manifest.json`)
- [x] Implement SongPack schemas + validator (JSON schema + runtime validation) (manifest + core features + validateSongPack)
- [x] Implement host skeleton (Tauri) + startup library scan + SongPack details view
- [x] Implement plugin loader skeleton (viz-sdk + built-in plugin + lifecycle loop)
- [x] Implement audio playback/transport (MVP: HTMLAudio + SongPack audio load)
  - [x] Transport controller module (audio-backed clock + loop)
  - [x] UI controls: play/pause/stop/seek + loop
  - [x] Unit tests for transport behavior (jsdom)
- [x] Implement audio playback/transport (next: timebase abstraction + WebAudio backend)
  - [x] Transport timebase interface + refactor controller to depend on it
  - [x] HTMLAudio timebase implementation (keeps existing behavior)
  - [x] WebAudio timebase implementation (decoded AudioBuffer)
  - [x] UI backend toggle (HTMLAudio vs WebAudio)
  - [x] Tests updated to use fake timebase (no DOM audio dependency)

- [~] (New) Native Rust audio engine (real-time DSP + instruments)
  - Goal: evolve from browser timebases into a single native engine suitable for low-latency playback, monitoring, FX, and instruments.
  - Phase 0 (scaffolding + tests)
    - [x] Define `AudioEngine` Rust module/service boundary (commands/events)
    - [x] Add unit tests for engine transport math (sample-accurate time, loop, seek) (pure Rust)
  - Phase 1 (playback-only)
    - [x] Implement native output playback backend (cpal/WASAPI on Windows; ALSA/Pulse/JACK on Linux via cpal)
    - [x] Wire host transport to native engine time instead of HTMLAudio/WebAudio
    - [~] Provide device selection + sample rate + buffer size settings (best-effort)
      - [x] Output device selection UI + persisted native-device identity (`name + channels + sample_rate`)
      - [x] Handle sample-rate mismatch by resampling decoded PCM into engine/device sample rate (linear interpolation, tested edge cases)
      - [ ] Manual sample-rate + buffer-size override controls (currently auto-selected best-effort)
    - [~] Emit metering + xruns/underruns to UI (debug)
      - [x] Added callback overrun/debug counters to native engine state (`callback_count`, `callback_overrun_count`, `output_buffer_frames`)
      - [ ] Emit richer meter/xrun event stream to UI
  - Phase 2 (FX graph)
    - [ ] Implement bus graph (master + song + metronome)
    - [ ] Add first FX: gain + simple distortion + simple compressor
    - [ ] Parameter automation path (lock-free)
  - Phase 3 (instruments / soundbanks)
    - [ ] Add sampler / soundbank playback driven by MIDI + events
  - Phase 4 (input monitoring)
    - [ ] Live input -> FX chain -> output
  - Phase 5 (ASIO / pro-audio) (stretch)
    - [~] Investigate ASIO feasibility/licensing; likely start with WASAPI exclusive as default
      - [x] Added optional ASIO build feature (`--features asio`) + runtime host selection UI (host + device)
      - [ ] Validate SDK/licensing/distribution policy and default-host strategy for production installer builds
- [~] Implement ingest sidecar MVP (decode + beats/tempo + sections + SongPack feature generation)
  - [x] `python/ingest` project scaffold (`pyproject.toml`, `pytest`, `ruff`)
  - [x] Sidecar CLI surface (current): `aural_ingest stages|info|validate|import|import-dir|import-dtx|runtime-check|benchmark-drums`
  - [x] JSONL progress event emitter (`aural_ingest.progress`)
  - [x] Real decode + analysis stages (wav inputs supported without ffmpeg; non-wav requires ffmpeg)
  - [x] Determinism tests (synthetic click-track wav fixture) for decode+tempo+beats+sections+SongPack feature outputs (`notes.mid` / `events.json`)
  - [x] Host import UI wiring to run sidecar + stream progress
    - [x] Configure panel ingest controls now call desktop `ingest_import` command end-to-end
    - [x] Stream per-stage JSONL progress events into Configure UI during import
  - [x] Runtime-check surface for dependency/modelpack health is wired through the sidecar and desktop UI
- [ ] (Recovery) Restore lost transcription stack + regression protections from 2026-03-03 notes (see Milestone 4A)
- [x] Strengthen automated test coverage gates (TS + Python) and expand Rust core unit coverage
  - [x] TS coverage reporting + thresholds (`vitest --coverage`)
  - [x] Python coverage reporting + fail-under gate (`pytest --cov`, fail-under 80 via `python/ingest/pyproject.toml`)
  - [x] Add high-value unit tests for untested Rust modules (`wav_mix`, `audio_decode`, `models`, `midi_clock_service`)
  - [x] Rust `cargo llvm-cov` CI step wired into `.github/workflows/lint-test.yml`

- [~] (New) GHWT-DE importer (audio import/stem mixdown working; chart parsing still pending)
  - [x] Add Configure UI section to scan/import GHWT-DE DLC songs
  - [x] Add Tauri commands: configure GHWT paths + scan DLC + import preview audio
  - [x] Runtime: allow SongPacks with `audio/mix.wav` to load/play
  - [x] Rust tests for DLC scan + import using non-copyrighted fixtures
  - [x] Add native folder picker (Browse…) for GHWT DATA root
  - [ ] Follow-ups:
    - [x] Bulk import (import all scanned DLC songs)
    - [x] Better error UI: missing vgmstream / decode failures + preflight check
    - [x] Import full stems (DLC*_1..3) and mix down to `audio/mix.wav` (fallback to preview)
    - [ ] Parse GH charts from `*.pak.xen` into canonical SongPack charts/features

- [~] (New) Create SongPack from WAV stems + MIDI (song creator / importer)
  - [x] Spec: confirm SongPack output contract (manifest + audio + features)
  - [x] Desktop UI: Configure section with file pickers (stems WAVs + MIDI) + metadata (title/artist)
  - [x] Backend: create SongPack folder in songs directory
    - [x] validate stems (wav format, sample rate, channel count, duration)
    - [x] deterministic mixdown to `audio/mix.wav` (or copy if a single mix is provided)
    - [x] copy MIDI to `features/notes.mid`
    - [x] generate minimal `features/events.json` from MIDI notes
    - [x] generate `manifest.json` (duration_sec + stable song_id)
  - [x] Tests:
    - [x] Rust: fixture WAV+MIDI -> SongPack created; validates presence of artifacts
    - [ ] TS: library scan sees created SongPack and can load audio/mix.wav
- [~] (New) Raw song folder importer (Suno/export folder -> canonical SongPack)
  - [x] Inspect raw folder contents (stems, MIDIs, lyrics) and surface detected-role + timing warnings
  - [x] Import folder into SongPack with `audio/mix.wav`, copied source MIDIs, normalized combined `features/notes.mid`, and optional lyrics carry-through
  - [x] Suno/raw-song import currently treats normalized source MIDI as the chart/timing authority when a playable mapping is found (`suno_source_midi_normalized`, `timing_authority = normalized_source`)
  - [x] Preserve Suno source MIDI drum note identities during raw-song import; safe start-time normalization still applies, but the importer no longer auto-canonicalizes drum pitches against the audio stem (this had been producing non-matching drum output relative to source MIDI)
  - [x] When a role-specific source MIDI/audio start delta is wildly unstable (>2s), raw-song import now falls back to the cross-track median normalization offset instead of dropping that role from the gameplay chart; this targets Psalm 10-style drum/audio sync failures while keeping source MIDI authoritative
  - [x] Raw-song import now derives non-drum gameplay roles from per-track MIDI names/channels when source filenames are generic, so bass/guitar/keys charts survive import and render instead of being silently dropped
  - [x] UI wiring exists in both `apps/game` and `apps/desktop`
  - [ ] Studio import UX should explicitly show which import engine/path was used and what the authoritative chart source is (for example: `raw_song_folder / Suno source MIDI` vs `sidecar ingest / combined_filter`)
  - [ ] Add richer authoring overrides / broader source heuristics and more fixture coverage
- [x] Implement model manager (download/import + versioned storage under `assets/models/`)
  - [x] Models UI section (preferred + local import)
  - [x] Tauri commands: list/install model packs
  - [x] Zip format: modelpack.json + files/**, extracted into app data dir
- [x] (New) Define songs-folder location policy + persistence (default per OS + user override) and add tests
- [ ] (New) Add optional file-watcher for live library updates (post-startup scan)
- [~] (New) Drum benchmark / reference shootout tooling
  - [x] Fixture corpus + manifest under `assets/test_fixtures/drum_benchmark_midis`
  - [x] Python CLI/scripts for `benchmark-drums` and manual reference shootouts
  - [ ] CI thresholds / published dashboards are not wired yet

- [~] (New) Realtime MIDI I/O + clock sync (bidirectional)
  - [x] Decide initial API boundary: Rust MIDI service + Tauri commands/events (no WebMIDI dependency)
  - [x] Implement MIDI device enumeration + port selection UI
    - [x] Windows builds use `midir` WinRT enumeration so modern MIDI endpoints are visible; macOS/Linux remain CoreMIDI/ALSA.
    - [x] Input port selection now persists and re-resolves by stable backend port id before falling back to name.
  - [~] Implement MIDI input routing (note on/off + key CCs) into gameplay input bus
    - [x] Native callback now emits structured `midi_input_message` events (note on/off, CC, pitch bend, program/pressure, realtime, SPP, optional SysEx)
    - [x] Host forwards MIDI input events onto a window-level app event (`auralprimer:midi-input`) for gameplay integration points
    - [x] Frontend input bus now maintains active keyboard notes, sustain-held state, and monitor output for hardware testing
    - [x] Piano-roll keyboard now highlights live MIDI input notes while a keys chart is loaded
    - [ ] Map `auralprimer:midi-input` into concrete gameplay scoring/hit-window evaluators
  - [x] Implement MIDI clock input -> transport sync (Start/Stop/Continue + Clock + SPP best-effort)
  - [x] Implement MIDI clock output from transport (supports tempo slowdown + loop)
  - [x] Implement tempo scaling when external clock drives transport (device tempo -> song tempo factor)
  - [x] SysEx support (opt-in per port) + safety controls
  - [~] Contract tests: determinism + jitter tolerance + loop/seek behavior under MIDI sync
    - [x] Added Rust unit coverage for inbound message parsing + outbound message validation/SysEx policy
    - [x] Added frontend unit coverage for active-note/sustain/all-notes-off tracking
    - [ ] Add deterministic fake-device integration tests for realtime jitter/loop behavior under sustained clock traffic

- [x] Build instructions: `BUILDING.md`

---

## Milestones (implementation)

### Milestone 0 — Repo + CI foundations (1–2 weeks)
**Goal**: Establish the project structure, test harnesses, and CI gates so all future work can be TDD-first.

**Completed in repo so far**
- Monorepo tree created
- Root Node tooling installed (`vitest`, `typescript`)
- TS tests run via `npm test`
- `packages/songpack` includes tested SongPack discovery + basic library indexing

**Deliverables**
- [x] Monorepo folders created (matching README layout).
- [x] Node workspace tooling chosen + configured (**npm workspaces** in root `package.json`).
- [ ] TS tooling: eslint/prettier baseline.
- [x] TS tooling: typescript + vitest baseline (`npm test`).
- [x] Rust tooling baseline: crate present + CI runs `fmt/clippy/test` when `apps/desktop/src-tauri/Cargo.toml` exists.
- [x] Python tooling baseline: `pytest` + `ruff` configured under `python/ingest/`.
- [x] CI: add `ruff` lint step (Python now runs `ruff check` + `pytest` in `lint-test`).

**TDD / testing deliverables**
- [x] “Hello test” for each language target:
  - TS: `vitest` runs in CI (`npm test`)
  - Rust: `cargo test` is configured to run in CI (`apps/desktop/src-tauri/tests/smoke.rs`)
  - Python: `pytest` runs in CI (`python/ingest/tests/test_smoke.py`)
- [x] Contract-test scaffolding:
  - SongPack schema validation tests can run with at least one fixture.

**Exit criteria**
- [x] `lint-test` workflow *should be* green on PR with:
  - at least one TS test
  - at least one Rust test (when `apps/desktop/src-tauri` exists)
  - at least one Python test (when `python/ingest` exists)
  - (note: TS `lint` script is currently a placeholder; Python lint is real via `ruff check`)

**Dependencies / notes**
- Keep the “no directories → steps are skipped” behavior intact until code exists.

**Local dev note (Windows)**
- If `npm test` fails with a Rollup error about a missing optional dependency like `@rollup/rollup-win32-x64-msvc`, reinstall Node deps (e.g. `npm ci`). This is a known npm/optional-deps failure mode.

---

### Milestone 1 — SongPack core libraries + schemas (1–3 weeks)
**Goal**: Make SongPack real: validation, migrations (stub), and deterministic serialization.

**Progress**
- [x] `manifest.schema.json` created
- [x] TS manifest validation implemented with Ajv (`validateManifest`)
- [x] Feature schemas created: `beats.schema.json`, `tempo_map.schema.json`, `sections.schema.json`, `events.schema.json`
- [x] Feature schema created: `lyrics.schema.json` (karaoke timings)
- [x] TS validators added for features (`validateBeats`, `validateTempoMap`, `validateSections`, `validateEvents`)
- [x] TS validator added: `validateLyrics`
- [x] Minimal `chart.schema.json` created
- [x] `validateSongPack()` implemented for directory + zip SongPacks
- [x] Fixture updated: `minimal_valid.songpack` includes `features/lyrics.json`

**Deliverables**
- [x] JSON Schemas committed for:
  - `manifest.json`
  - `features/events.json`
  - `features/beats.json`
  - `features/tempo_map.json`
  - `features/sections.json`
  - `charts/*.json` (at least one chart schema)
- [x] `packages/songpack` library:
  - [x] load directory SongPack
  - [x] load zip SongPack
  - [x] validate SongPack against JSON schemas
  - [x] canonical JSON serialization (stable key ordering)
  - [x] version/migration entry points (identity migration for v1 is implemented)

**TDD / testing deliverables**
- [x] Schema tests (fast, always-on): fixtures under `assets/test_fixtures/songpacks/...`
- [x] Round-trip tests: parse → normalize → serialize stability
- [x] Negative tests: missing files, invalid versions, out-of-range event times

**Exit criteria**
- [x] `packages/songpack` can validate at least one fixture SongPack.
- [x] CI fails if a schema breaks fixture validation.

**Dependencies / notes**
- This milestone unblocks host + plugin work.

---

### Milestone 2 — Desktop host skeleton (Tauri) + playback + plugin loader (2–4 weeks)
**Goal**: A minimal desktop app can load a SongPack and render a plugin synced to audio.

**Audit note (2026-04-20)**
- Active playback/runtime work now lives primarily in `apps/game`.
- `apps/desktop` focuses on authoring/import flows and retains a hidden `legacyPlaybackScaffold` for shared playback/plugin code.

**Deliverables**
- [x] `apps/game` + `apps/desktop` created (Tauri + TS UI; gameplay/runtime is centered in `apps/game`).
- [x] Song library view (minimal): list SongPacks found in a configured folder.
- [x] Scan songs folder on startup to discover new/removed SongPacks (directory + zip containers).
- [x] Load SongPack + show basic metadata.
- [x] Audio playback + transport clock (MVP):
  - load `audio/mix.wav`, `audio/mix.ogg`, or `audio/mix.mp3` from selected SongPack
  - play/pause/stop/seek
  - drive `TransportState.t` from `audio.currentTime`
- [x] Audio playback + transport clock (next):
  - [x] loop region (t0..t1)
  - [x] transport timebase abstraction + WebAudio backend option
  - [x] tempo slowdown (playbackRate)
  - [x] metronome stub (WebAudio click)
- [x] Plugin loader skeleton (built-in plugin + lifecycle loop):
  - load ESM module (workspace plugin)
  - lifecycle: `init → resize → update → render → dispose`
- [x] Plugin loader (full):
  - [x] discover built-in plugins (bundled resources) and user plugins
  - [x] load ESM entrypoint (`dist/index.js`)
  - [x] lifecycle: `init → resize → update → render → dispose`
- [x] Host → visualizer song data (initial):
  - [x] Tauri command: `read_songpack_json(container_path, rel_path)` (restricted to `features/*.json`)
  - [x] Load `features/lyrics.json` best-effort when selecting a SongPack
  - [x] Pass into plugin init context: `VizInitContext.song.lyrics`

- [x] (New) Lyrics generation prompt (MVP):
  - [x] If user starts `viz-lyrics` and `features/lyrics.json` is missing, prompt to generate it
  - [x] Generation reads a user-selected `.txt` lyrics file and distributes lines uniformly across `manifest.duration_sec`
  - [x] Writes `features/lyrics.json` into **directory** SongPacks only (zip SongPacks are read-only for now)
- [x] Global HUD:
  - [x] always display **key + mode** (even if placeholder from fixture)

**TDD / testing deliverables**
- [~] Host + plugin contract tests:
  - [x] built-in/user plugin loading + discovery coverage exists
  - [ ] instantiate reference plugins and run lifecycle/render smoke frames end-to-end
- [~] Transport monotonicity tests
  - [x] transport/native-timebase unit coverage exists
  - [ ] add explicit monotonic/property-style transport clock assertions

**Exit criteria**
- [ ] A fixture SongPack plays with a minimal plugin rendering beats/sections.
- [ ] Plugin contract tests run in CI.

**Dependencies / notes**
- Codec layer decision: pick an initial implementation path (see “Open questions”).
- Linux note: local Tauri builds require system deps (`pkg-config`, WebKitGTK, GTK dev libs). See `docs/local-dev-prereqs.md`.
- Windows note: a local Windows bundle build produces installers under:
  - `apps/desktop/src-tauri/target/release/bundle/msi/*.msi`
  - `apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe`

- Dev environment note: on Windows, if `cargo` isn't found when running `tauri build`, ensure `%USERPROFILE%\.cargo\bin` is on `PATH`.

#### UI refresh (modern high-tech vibe)
- [x] Apply dark/neon "high-tech" theme (CSS variables, glass panels, neon controls)
- [x] Improve layout/markup: branded header, panel sections
- [x] Add favicon and reduce Vite dynamic-import warnings for plugin loading

---

### Milestone 3 — Viz SDK + reference plugins (2–6 weeks)
**Goal**: Stabilize the visualization contract so plugins can iterate independently.

**Deliverables**
- [x] `packages/viz-sdk` (initial):
  - `Visualizer` interface types
  - minimal `TransportState` and frame context
- [x] `packages/viz-sdk` (init context extension):
  - `VizInitContext.song.lyrics?: unknown` (host-provided)
  - [x] host-provided `charts`, `notesMidiBytes`, parsed `notes`, and optional `players` metadata
- [ ] `packages/viz-sdk` (next):
  - `SongHandle` query APIs with time-window queries
  - host services boundary (no direct filesystem access)
- [x] Reference plugins in `visualizers/` (initial):
  - [x] `viz-beats` (Canvas2D beat grid; lifecycle smoke target)
  - [x] `viz-lyrics` (Canvas2D karaoke-style lyrics highlighting)
  - [x] `viz-nashville` (chords lane; placeholder if chords missing)
  - [x] `viz-fretboard` (present in tree; currently placeholder cursor until richer note queries are exposed)
  - [x] `viz-drum-highway` (host-provided MIDI notes mapped to drum lanes)

**Current fidelity notes**
- `viz-lyrics` is data-driven when `features/lyrics.json` exists.
- `viz-drum-highway` is data-driven from host-provided parsed MIDI note events.
- `viz-beats`, `viz-nashville`, and `viz-fretboard` still contain placeholder logic and need richer host song-query APIs to become fully data-driven.

**TDD / testing deliverables**
- [ ] SDK contract tests for API stability.
- [ ] Plugin smoke tests against fixture SongPacks.

**Exit criteria**
- [ ] A new plugin can be added/updated without changing the host.

---

### Milestone 4 — Ingest sidecar MVP (2–4 weeks)
**Goal**: Import local audio into a playable SongPack deterministically.

**Deliverables**
- [x] `python/ingest` project structure.
- [x] Sidecar CLI:
  - `aural_ingest import <source> --out ... --profile ...` (basic flags)
  - `aural_ingest import-dir <source-dir> --out ... --profile ...` (directory source picker)
  - `aural_ingest validate <songpack-dir>` (file presence checks)
  - `aural_ingest info <songpack-dir>`
  - `aural_ingest stages`
- [x] `aural_ingest runtime-check`
- [x] `aural_ingest benchmark-drums <stem> <reference>`
- [~] Pipeline stages (current repo pipeline has moved beyond the original `chart_generation` MVP and now centers on `notes.mid`/`events.json` outputs):
  - [x] `init_songpack`
  - [x] `decode_audio` (writes deterministic `audio/mix.wav`; non-wav decode requires ffmpeg)
  - [x] `beats_tempo` (deterministic BPM estimate + generated beat grid, plus optional `high_accuracy` `librosa.beat_track` mode with fallback metadata)
  - [x] `sections` (generated section blocks from duration + BPM)
  - [~] `separate_stems` (Demucs-backed when modelpack/runtime is present; still optional / best-effort)
    - [x] Added deterministic guitar stem split stage that emits `audio/stems/lead_guitar.wav` + `audio/stems/rhythm_guitar.wav` (uses `audio/stems/guitar.wav` when present, else mix fallback)
    - [ ] Integrate broader multi-stem outputs / production-quality separation beyond the current best-effort path
  - [x] Drum + melodic transcription emit `features/notes.mid` and `features/events.json`
  - [~] JSON chart generation remains limited; there is no standalone `chart_generation` Python stage in the current tree
- [x] Structured JSONL progress reporting.
- [x] Host import UI to run sidecar and show progress.

**TDD / testing deliverables**
- [ ] Stage fingerprint determinism tests.
- [ ] Cache invalidation tests.
- [ ] Golden tests on short fixtures (beats/tempo within tolerance).

**Exit criteria**
- [ ] User can import an mp3/ogg and play resulting SongPack.
- [ ] Golden test suite catches accidental extraction regressions.

---

### Milestone 4A — Transcription recovery from lost unpushed repo (notes-driven) (2–6 weeks)
**Goal**: Rebuild the previously working transcription stack and regression guards captured in the four recovery notes.

**Deliverables**
- [~] Recreate sidecar transcription modules (`python/ingest/src/aural_ingest/transcription.py`, `algorithms/*`)
  - [x] Orchestration scaffold added in `transcription.py` (fallback-chain + selector validation + result contract)
  - [x] Added concrete deterministic recovery stubs under `algorithms/*` for all target algorithm IDs
- [~] Recreate full DSP/ML algorithm implementations under `algorithms/*`
  - [x] Replaced deterministic drum pattern emitters with waveform-driven onset + event-classification logic
  - [x] Implemented documented drum recipes (shared preprocessing, adaptive peak-pick, class refractory/de-dup, velocity map, algorithm-specific fusion/classification)
  - [ ] Add ML-backed and/or higher-fidelity MIR implementations to match pre-loss quality expectations
- [x] Reintroduce CLI/import modes:
  - [x] `import-dir` (MVP directory audio source selection + forward to import pipeline)
  - [x] `import-dtx` (MVP: resolve DTX-referenced audio or chart-folder audio, then forward to import pipeline)
  - [x] `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter` (validated parsing + import pass-through)
  - [~] `--multi-filter` is currently parsed/persisted plumbing; a distinct multi-engine execution path is not yet evident in the current pipeline
- [x] Rebuild drum algorithm set: `combined_filter`, `dsp_bandpass_improved`, `dsp_spectral_flux`, `adaptive_beat_grid`, `dsp_bandpass`, `aural_onset`, `librosa_superflux`
- [~] Set default drum path to `combined_filter`; preserve documented fallback ordering; log unknown algorithm IDs instead of silent adaptive fallback
  - [x] Fallback-chain behavior and unknown-ID handling encoded in `python/ingest/src/aural_ingest/transcription.py`
  - [x] Fallback-selection results are now wired into ingest drum stage + persisted in manifest transcription metadata
- [~] Rebuild melodic path (`auto`/`basic_pitch`/`pyin`) including frozen-runtime model lookup fallback (`onnx -> tflite -> savedmodel`)
  - [x] `transcribe_melodic` orchestration + fallback chain restored in `python/ingest/src/aural_ingest/transcription.py`
  - [x] `basic_pitch` model path resolver restored with `onnx -> tflite -> savedmodel` preference
  - [x] Import pipeline now emits melodic notes in `features/events.json` and persists `melodic_method_used`
  - [~] Swap deterministic melodic stubs for real Basic Pitch/pYIN backend implementations
    - [x] Added waveform-driven monophonic tracking baseline (`pyin`) and dyad expansion path (`basic_pitch`) with model gate
    - [ ] Integrate true Basic Pitch/pYIN inference backends (model/runtime parity)
- [x] Restore Rust command forwarding of drum/melodic args from UI to sidecar
  - [x] Added Rust `ingest_import` Tauri command + sidecar CLI arg builder with explicit forwarding for drum/melodic flags
  - [x] Added app client wrappers (`apps/desktop/src/ingestClient.ts`, `apps/game/src/ingestClient.ts`) and tests to lock payload forwarding
  - [x] Wire Configure UI import controls to call `ingest_import` end-to-end
- [x] Restore chart parser strict/relaxed guard so sparse dedicated drum tracks are not dropped
  - [x] Added `apps/desktop/src/chartLoader.ts` strict/relaxed selection logic with dedicated drum-track guard
  - [x] Added desktop regression tests for strict-vs-relaxed and King in Zion sparse-drums behavior
  - [x] Integrated chart loader into active gameplay song selection path (`read_songpack_mid` + capability/instrument availability plumbing)
- [x] Rebuild portable packaging flow that always copies latest sidecar before ship
- [x] Verify/fix `import-dir` ordering around `sections` and `events.json`

**TDD / testing deliverables**
- [~] Python: `test_transcription_resilience.py` recovered with fallback and algorithm-diversity assertions
  - [~] Current assertions explicitly reward expanded note diversity for `combined_filter`; they do not yet guard against sparse-source false positives like the Psalm 12 report
  - [x] Added a sparse-source regression test (`python/ingest/tests/test_drum_sparse_source_precision.py`) that runs `combined_filter` on a synthetic kick-only stem and fails if the algorithm falls through to `fallback_events_from_classes` (the dense-grid Psalm 12 hallucination path) or emits an out-of-bounds event count. Negative-test verified the guard fires when `detect_candidates` is forced empty.
  - [ ] Wire a real Psalm 12-style fixture into the quality benchmark (`full_corpus_manifest.guard.template.json` -> `guard_drums_TBD`) for end-to-end coverage on real audio.
- [x] Python: `test_import_pipeline.py` includes `import-dir` events-export ordering regression coverage
- [x] Desktop: `chartLoader.test.ts` strict-vs-relaxed behavior and sparse-drum preservation cases
- [x] Desktop: `chartLoader.kingInZionRegression.test.ts` fixture regression test
- [x] End-to-end smoke: same stem A/B (`adaptive_beat_grid` vs `combined_filter`) confirms expanded-kit distribution in `combined_filter` (covered by ingest algorithm regression tests)
  - [~] This currently validates diversity more than fidelity; add a counterbalancing precision-oriented fixture before treating default import quality as recovered

**Exit criteria**
- [~] Default import path reproduces expanded drum-note diversity on known regression fixtures, but that is now known to be an incomplete/possibly wrong success metric for sparse real-world material
- [ ] Default import path preserves sparse-source fidelity without hallucinated expanded-kit hits (add Psalm 12-equivalent regression coverage before calling recovery complete)
- [x] Portable package contains sidecar matching just-built hash/timestamp
- [~] Regression suites above run in CI and prevent fallback/order regressions, but they do not yet protect against sparse-source false positives in real-world drum imports

---

### Milestone 5 — Pluggable importers (content adoption track) (ongoing)
**Goal**: Multiple import sources feed SongPack without constraining internal capabilities.

**Deliverables**
- [ ] Define importer interface (concept + CLI flags):
  - importer id
  - input discovery/validation
  - conversion into canonical SongPack
  - importer provenance surfaced in Studio UI and persisted in import results/metadata (`importer_id`, engine/path used, chart authority)
- [ ] Importers:
  - [~] `audio_only` (baseline sidecar import path exists; stable importer interface is not frozen)
  - [~] `midi` (stem+MIDI SongPack creation and raw-song folder import exist; formal importer interface is not frozen)
  - [x] `ghwt_de` (MVP: preview audio import into SongPacks)
  - [x] `raw_song_folder` / Suno-style export folder import (inspection + SongPack creation)
    - [x] Uses normalized Suno MIDI as source-of-truth gameplay timing when the MIDI is mappable
    - [ ] Make that source-of-truth choice obvious in Studio so users do not confuse it with heuristic sidecar transcription

**TDD / testing deliverables**
- [ ] Importer contract tests per importer (fixtures in `assets/test_fixtures/import_sources/...`).

**Exit criteria**
- [ ] Adding a new importer does not require modifying existing importers.

---

### Milestone 6 — Model manager + post-install model packs (1–3 weeks)
**Goal**: Provide a first-class, deterministic model management story without bundling weights.

**Deliverables**
- [x] Host UI: “Models” screen (basic)
  - [x] list installed model packs
  - [x] download model pack (when online) (renderer fetch + Rust zip install; URL config per pack)
  - [x] import model pack from local zip path (offline)
  - [ ] show license info (not implemented yet)
- [x] Model pack format (implemented)
  - `modelpack.json` at zip root contains `{id, version, ...}`
  - `files/**` extracted under `assets/models/<id>/<version>/...` in app data dir
  - sha256 verification supported when downloading (optional expected hash)
  - safety: no overwrite + path traversal prevention
- [~] Sidecar integration: stages declare required model id/version and resolve path.
  - [x] `stages` / `runtime-check` expose required model metadata for Demucs and MT3 drum engines
  - [x] melodic model resolution fallback (`onnx -> tflite -> savedmodel`) is restored in transcription code
  - [ ] fully gate all model-dependent stages from host UX when required packs are missing

**TDD / testing deliverables**
- [ ] Model pack verification tests (hash checking, version non-overwrite).
- [ ] Offline behavior tests (feature disabled until models present).

**Exit criteria**
- [ ] A model-dependent stage can be enabled once the model pack is installed.

---

### Milestone 7 — Realtime audio→MIDI / realtime identification (parallel track) (4–8+ weeks)
**Goal**: Local realtime audio processing feeds gameplay input without putting heavy ML in the host.

**Deliverables**
- [ ] Define runtime sidecar protocol:
  - host streams audio frames
  - sidecar outputs MIDI-like events with timestamps
  - latency calibration hooks
- [ ] First target: monophonic (voice/bass)

**TDD / testing deliverables**
- [ ] Deterministic simulation tests with recorded input buffers.
- [ ] Latency/jitter characterization harness.

**Exit criteria**
- [ ] Realtime events can drive a simple gameplay mechanic with acceptable latency.

---

## Critical path / dependency notes
- Milestone 1 (SongPack libs/schemas) unblocks Milestone 2 (host) and Milestone 3 (plugins).
- Milestone 0 is required before anything else (CI + tests).
- Milestone 4 (ingest MVP) is required for real content, but Milestone 2/3 can progress with fixtures.
- Model manager (Milestone 6) is required before any model-dependent stages can ship.

---

## Implementation deep dive (research-backed, as of 2026-03-03)

### A) Native audio engine (Rust): concrete implementation plan
- [x] Keep `cpal` as the output I/O layer (current direction), but remove `Mutex` use from the audio callback path.
- [~] Introduce lock-free control/data channels:
  - [x] `rtrb` SPSC ring buffer for control commands (`play/pause/seek/loop/rate`) into the audio thread.
  - [ ] dedicated lock-free meter/event queue back to UI thread (xruns, level peaks, callback timing stats).
- [ ] Add thread priority promotion in native backend:
  - [ ] enable `cpal` `audio_thread_priority` feature where available
  - [ ] explicit fallback path if promotion fails (log + continue)
- [ ] Decode + resample pipeline split:
  - [ ] decode via `symphonia` on control thread
  - [ ] sample-rate conversion via `rubato::process_into_buffer()` with pre-allocated buffers only
  - [ ] callback consumes already prepared/interleaved blocks
- [ ] Transport clock source of truth:
  - [ ] maintain `sample_count_rendered` (`u64`) in callback
  - [ ] derive `t_sec = sample_count / sample_rate` for host sync
  - [ ] keep loop math sample-accurate (already scaffolded in `audio_engine.rs`)
- [ ] Deadline and xrun instrumentation:
  - [ ] use `OutputCallbackInfo.timestamp().callback/playback` to measure callback lead time/jitter
  - [ ] track callback runtime histogram (`p50/p95/p99`) and deadline misses
- [ ] Device model:
  - [x] enumerate supported output configs first; do not assume default cfg is valid for target format
  - [x] store persisted device selection as stable identity (`name + channels + sample_rate`) with re-resolution on startup

### B) MIDI I/O + clock sync: concrete implementation plan
- [ ] Keep `midir` as device I/O layer and retain `midly` for file-level MIDI parsing.
- [ ] Promote existing clock subsystem from "best-effort" to deterministic service:
  - [ ] output clock scheduler anchored to transport sample clock (not wall clock)
  - [ ] input clock via tempo PLL/smoother (EMA + outlier clamp + bounded drift correction)
  - [ ] SPP handling on seek and loop boundary transitions
- [ ] Add explicit clock ownership modes:
  - [ ] `internal_master` (AuralPrimer drives MIDI clock)
  - [ ] `external_slave` (incoming MIDI clock drives transport)
  - [ ] `hybrid_guarded` (external clock accepted only after lock + confidence threshold)
- [ ] SysEx safety:
  - [ ] opt-in per port
  - [ ] explicit max message size
  - [ ] rate limits + allow-list profile hooks

### C) Ingest/transcription recovery stack: concrete implementation plan
- [x] Restore sidecar CLI surface from recovery notes:
  - [x] `import-dir`, `import-dtx`
  - [x] `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter`
- [x] Drum transcription architecture:
  - [x] restore algorithm modules (`combined_filter`, `dsp_bandpass_improved`, `dsp_spectral_flux`, `adaptive_beat_grid`, `dsp_bandpass`, `aural_onset`, `librosa_superflux`)
  - [x] enforce explicit fallback chain from notes
- [~] Melodic transcription architecture:
  - [x] `basic_pitch` requested path + `pyin` fallback chain restored in orchestration
  - [x] frozen-runtime model resolution order from notes (`onnx -> tflite -> savedmodel`)
  - [ ] reach full Basic Pitch/pYIN runtime-quality parity
- [~] Beat/tempo quality upgrade:
  - [x] keep deterministic MVP pipeline in place
  - [x] add optional higher-accuracy mode using `librosa.beat.beat_track` with fallback metadata when unavailable
  - [ ] evaluate whether Essentia-backed offline extraction is still needed
- [~] Stem separation provider model:
  - [x] keep separator pluggable (`none`, `demucs`, future providers)
  - [~] Demucs remains a best-effort, replaceable provider rather than a fully hardened default
  - [ ] note: upstream `facebookresearch/demucs` is archived; treat provider as "best-effort, replaceable"

### D) Packaging + sidecar reliability hardening
- [ ] Tauri sidecar contract hardening:
  - [ ] define `bundle.externalBin` entries for desktop + game sidecars
  - [ ] ensure target-triple suffixed binaries are generated/copy-validated pre-bundle
  - [ ] run sidecars via `shell().sidecar("<name>")` only
- [ ] PyInstaller reliability for model-based sidecar:
  - [ ] build with explicit `--collect-data basic_pitch` (or `--collect-all basic_pitch` when needed)
  - [ ] runtime checks for `sys.frozen` and `sys._MEIPASS` path resolution
  - [ ] store emitted `build_manifest.json` with sidecar hash + model asset hash
- [x] Portable build freshness guard:
  - [x] `create_portable.ps1` fails packaging if sidecar hash in portable root does not match the just-built sidecar artifact
  - [x] `create_portable.ps1` stages `modelpacks/demucs_6.zip` and validates `modelpack.json` id + required stem roles
  - [x] `build_sidecar.ps1` now rebuilds from Python source by default (unless `-SkipBuild`), preventing stale sidecar reuse

### E) Benchmarking and regression harness (mandatory)

#### E1) Rust performance benchmarks
- [ ] Add `criterion` benches for:
  - [ ] transport math (`seek`, `loop wrap`, large-step modulo)
  - [ ] mixing kernels (gain/distortion/compressor blocks)
  - [ ] decode + resample throughput
- [ ] Add `iai-callgrind` benches for deterministic instruction-level regressions in DSP hot paths.

#### E2) Audio real-time system benchmarks
- [ ] Add native soak benchmark runner (`--bench-audio`) that executes 10/30/60 minute sessions.
- [ ] Collect:
  - [ ] callback duration stats (`p50/p95/p99/max`)
  - [ ] deadline miss count/rate
  - [ ] output drift vs transport clock
- [ ] Target gates (phase-1):
  - [ ] deadline miss rate < 0.1% at 48kHz / 256 frames
  - [ ] callback `p99` runtime <= 40% of buffer period
  - [ ] no transport discontinuity > 1 audio block except explicit seek

#### E3) MIDI sync benchmarks
- [ ] Build synthetic clock fixture generator (controlled jitter, drift, missing ticks, start/stop bursts).
- [ ] Metrics:
  - [ ] tempo estimation error (mean absolute BPM error)
  - [ ] tick-to-transport phase error (ms)
  - [ ] lock acquisition/reacquisition time after dropout
- [ ] Target gates:
  - [ ] steady-state phase error p95 <= 2ms (internal master)
  - [ ] external clock lock within <= 2 beats after valid start

#### E4) Python ingest/transcription benchmarks
- [ ] Add `pytest-benchmark` suites for:
  - [ ] per-stage runtime (decode, beats, sections, chart, drums, melodic)
  - [ ] memory footprint and peak resident set size sampling
- [ ] Persist baselines and fail on regression using:
  - [ ] `--benchmark-save=<baseline>`
  - [ ] `--benchmark-compare=<baseline>`
  - [ ] `--benchmark-compare-fail=mean:<threshold>`

#### E5) Quality benchmarks (audio ML / MIR)
- [~] Separation quality:
  - [x] Add optional fail-safe `museval` SDR protocol adapter for local reference/estimate stem comparisons.
  - [x] Report MUSDB18/MUSDB18-HQ dataset roots as internal-only benchmark sources and explicitly prevent product-shipping assumptions.
  - [ ] Run MUSDB18/MUSDB18-HQ comparisons after local dataset roots are configured.
- [x] Transcription quality:
  - [x] Evaluate referenced melodic/piano note events with `mir_eval.transcription` (precision/recall/F1/overlap).
  - [x] Include both onset-only and onset+offset scoring modes in `summary.json` and `report.md`.
- [~] Drum-specific datasets for regression fixtures:
  - [x] Report ENST-Drums root/status as internal-only benchmark source.
  - [x] Report IDMT-SMT-Drums root/status as internal-only benchmark source.
  - [ ] Generate local drum benchmark manifests from ENST/IDMT once dataset roots are configured.

#### E6) Frontend/runtime performance benchmarks
- [~] Add `vitest bench` suites for parser/mapping hot paths and plugin update loops.
  - [x] Added `apps/game/benchmarks/frontend.bench.ts` covering MIDI parser, drum chart selection, melodic track selection, key-signature inference, and built-in visualizer update/render loops.
  - [x] Added `npm run bench:frontend` to write `benchmarks/frontend/vitest-bench.latest.json`.
  - [ ] Extend visualizer coverage to all built-ins after placeholder visualizers are backed by real data contracts.
- [~] Add bench artifact comparison in CI (`vitest bench --outputJson` + `--compare`).
  - [x] Added `npm run bench:frontend:compare` with optional baseline comparison against `benchmarks/frontend/vitest-bench.baseline.json`.
  - [x] Added CI benchmark artifact upload for `benchmarks/frontend/*.json`.
  - [ ] Freeze a versioned frontend benchmark baseline and threshold policy before making comparison failures PR-blocking.
- [ ] Add Playwright trace-based end-to-end perf captures for import/playback/plugin rendering paths.
  - [ ] Requires a deterministic app-runner fixture that can launch the game/studio shell and export traces in CI.

### F) CI enforcement upgrades
- [~] Add dedicated benchmark workflows:
  - [~] `bench-rust` artifact workflow added; current runner writes skip/summary artifacts until Criterion/IAI benchmark targets exist.
  - [x] `bench-python` pytest-benchmark workflow and JSON artifacts added for opt-in ingest runtime benchmarks.
  - [x] `bench-ts` vitest bench workflow and JSON artifacts added for frontend/parser/plugin hot paths.
- [~] Add quality-gate workflow:
  - [~] Transcription/separation quality summaries are checked when present; active CI fixture scoring still needs committed/synthetic promotion fixtures and configured private dataset roots.
  - [x] Added versioned threshold config at `benchmarks/thresholds.yml` plus dependency-free threshold checker.
  - [~] Threshold mode is `warn` by default; switch to strict only after baseline and threshold policy are frozen.
- [x] Publish benchmark dashboards as CI artifacts for PRs touching `apps/*/src-tauri`, `python/ingest`, parser/frontend benchmark paths, or quality benchmark paths.

### G) Research-driven decision gates to resolve before implementation lock
- [x] Choose realtime-safe queue strategy for audio callback (`rtrb` only vs dual-queue design for metrics).
- [x] Choose beat/tempo production default (`librosa`-first vs Essentia-first) for deterministic imports.
  - [x] Production default is `high_accuracy` / `librosa.beat_track` first, with `standard` energy-autocorrelation fallback.
  - [x] Essentia remains a research candidate, not a default, until adapter/benchmark/packaging evidence justifies it.
- [x] Choose separator support policy (ship Demucs provider as optional experimental vs fully supported path).
  - [x] Demucs is optional experimental under `auto`; absence must skip separation and continue with provided stems or mix fallback.
  - [x] GPU acceleration is supported when available, with CPU fallback/model-absence safety still required.
- [x] Freeze benchmark threshold policy for PR blocking (strict vs warn-only for first 2 weeks).
  - [x] Threshold config remains `warn` mode; strict PR blocking is disabled until representative baselines, role thresholds, hardware profile, and reviewed fixtures are frozen.
  - [x] Once those are frozen, keep warn-only for at least 14 days before enabling strict gates.

### H) Clarifications from project owner
- [x] Primary success metric priority is best transcription quality.
  - [x] Lowest-latency playback/runtime remains important, but it is not the top priority for transcription/import method selection.
  - [x] Fastest import throughput is secondary to recognizable, playable, high-quality transcription output.
- [x] Target hardware baseline for perf gates: anything remotely modern should be supported on Windows/Linux.
  - [x] Converted into concrete profiles in `benchmarks/thresholds.yml`: `minimum_modern` = 8 logical CPU threads / 16 GB RAM / x64-or-arm64 / no GPU required for default import; `recommended_model_workstation` = 12 logical CPU threads / 32 GB RAM / GPU recommended for model-backed A/B.
  - [x] Added `npm run bench:hardware` to capture `benchmarks/hardware/local-profile.latest.json` and upload it with benchmark CI artifacts.
- [x] Legal/product stance for research-only datasets: allowed for internal benchmarking and evaluation only.
  - [x] Do not ship research-only dataset content, derived in-game content, or dataset-dependent fixtures in the game/product.
  - [x] Keep distributable fixtures synthetic, owned, permissively licensed, or explicitly cleared.
- [~] Additional dedicated Studio-only surface beyond `apps/desktop` is deferred.
  - [~] No extra product-surface answer is needed right now; continue using `apps/desktop`/AuralStudio for current recovery work.
- [x] GPU acceleration is in scope and should be supported first class for sidecar/model-backed transcription.
  - [x] CPU fallback remains required for portability and model-absence safety.

### I) External references used for this deep-dive section
- CPAL docs: `https://docs.rs/cpal/latest/cpal/`
- CPAL feature flags (including `audio_thread_priority`): `https://docs.rs/crate/cpal/latest/features`
- Symphonia docs: `https://docs.rs/symphonia/latest/symphonia/`
- Rubato docs (`process_into_buffer`): `https://docs.rs/rubato/latest/rubato/`
- RTRB docs: `https://docs.rs/rtrb/latest/rtrb/`
- Tauri sidecar binaries (`externalBin`): `https://tauri.app/develop/sidecar/`
- Tauri shell sidecar execution: `https://v2.tauri.app/plugin/shell/#running-sidecars`
- PyInstaller manual: `https://pyinstaller.org/en/stable/index.html`
- PyInstaller operating mode (`onefile`, `sys._MEIPASS`): `https://pyinstaller.org/en/stable/operating-mode.html`
- PyInstaller usage (`--collect-data`, `--collect-all`): `https://pyinstaller.org/en/stable/usage.html`
- Spotify Basic Pitch repository/docs: `https://github.com/spotify/basic-pitch`
- Librosa beat tracking: `https://librosa.org/doc/latest/generated/librosa.beat.beat_track.html`
- Librosa pYIN: `https://librosa.org/doc/latest/generated/librosa.pyin.html`
- Essentia rhythm extractor: `https://essentia.upf.edu/reference/std_RhythmExtractor2013.html`
- Demucs repository status: `https://github.com/facebookresearch/demucs`
- MIR Eval transcription metrics: `https://mir-eval.readthedocs.io/en/latest/api/transcription.html`
- Museval + MUSDB18 references: `https://pypi.org/project/museval/`, `https://sigsep.github.io/datasets/musdb.html`
- ENST-Drums dataset: `https://perso.telecom-paristech.fr/essid/en/recherche/base.html`
- IDMT-SMT-Drums dataset: `https://www.idmt.fraunhofer.de/en/publications/datasets/smt/drums.html`
- Criterion benchmarking: `https://github.com/bheisler/criterion.rs`
- IAI-Callgrind benchmarking: `https://github.com/iai-callgrind/iai-callgrind`
- Pytest benchmark docs: `https://pytest-benchmark.readthedocs.io/en/latest/index.html`
- Vitest benchmark feature: `https://vitest.dev/guide/features#benchmarking-experimental`

---

## Blocked / Questions (need decisions)

### A) Audio codec layer implementation choice (host) - resolved
- [x] Chosen approach: bundled Rust/Symphonia decoder for host playback (`mix.ogg`, `mix.mp3`, `mix.wav`).
- [x] FFmpeg remains sidecar-only for ingest/non-WAV source conversion; it is not required for normal SongPack playback.
- [x] Documented in `docs/audio-codec-policy.md` and locked with host decode policy tests.

### B) Importer boundaries
- What is the minimum initial importer interface surface (CLI + filesystem contract) we want to freeze in v0.1?

### C) GHWT DE importer scope
- Exactly which files are in scope first (song metadata, charts, stems, etc.)?
- Confirm legal posture/documentation requirements.

### D) Model pack trust
- Should we require:
  - hash-only verification, or
  - signed manifests?

---

## Notes
- **Always update this file (`wip.md`) after completing any implementation task** (add/remove checkboxes, update status, and keep milestones consistent).
- This tracker is intentionally concrete; as implementation starts, break each milestone into issues with owners.
