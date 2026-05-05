# Piano Transcription Epic

This file is the living tracker for piano-focused transcription quality work.

## Goal

Produce piano MIDI that is:

- playable
- recognizably close to the source performance
- less doubled and less octave-confused than the current generic melodic path
- better at sustain, chords, repeated-note attacks, and dynamics

## Current status

Status: experimental complete; portable build ready for user testing

Primary explicit piano path today:

- `piano_auto`

Best in-repo heuristic path today:

- `piano_polyphonic_clean`

Benchmark winner on the referenced suite:

- `melodic_octave_fix`

Execution plan:

- `benchmarks/piano/PIANO_TRANSCRIPTION_IMPLEMENTATION_PLAN.md`

Finish contract:

- Follow the `Finish Plan` section in `benchmarks/piano/PIANO_TRANSCRIPTION_IMPLEMENTATION_PLAN.md`.
- Stop tuning when the final piano path is benchmarked, labeled as default or experimental, and packaged if the quality/build gates pass.

Current next task:

- Test `D:\AuralPrimer\AuralPrimerPortable\AuralPrimer.exe` with a piano-heavy import, then compare `piano_auto`, `piano_polyphonic_clean`, and `melodic_octave_fix` in game.

Research-model paths integrated as optional fail-safe adapters:

- `piano_transkun`
- `piano_transkun_clean`
- `piano_pti`
- `piano_pti_clean`
- `piano_hft`
- `piano_hft_clean`

## Done

- [x] Added a dedicated `piano_*` method family in ingest orchestration.
- [x] Kept generic `auto` separate from piano-specific work.
- [x] Added piano cleanup pass:
  - [x] same-pitch dedupe
  - [x] same-pitch micro-gap merge
  - [x] audio-aware same-pitch reattack splitting
  - [x] audio-aware merge blocking across true reattacks
  - [x] low-register false octave shadow pruning
  - [x] ghost-note suppression
  - [x] 88-key range clamp
  - [x] audio-informed velocity blending
  - [x] sustain extension
- [x] Added a dedicated piano benchmark/eval path.
- [x] Added note-with-offset and note-with-offset-and-velocity metrics.
- [x] Added manifest-driven piano regression runner and benchmark docs.
- [x] Added real-case piano benchmark manifest with referenced stems and a no-reference piano-only listening case.
- [x] Added no-reference piano benchmark support with exported prediction MIDI/JSON artifacts.
- [x] Exposed piano methods in both desktop and game import UIs.
- [x] Added synthetic test coverage for:
  - [x] cleanup behavior
  - [x] benchmark metrics
  - [x] registry/fallback behavior
  - [x] simple major-triad detection
  - [x] repeated same-pitch retrigger detection
- [x] Implemented `piano_polyphonic` heuristic:
  - [x] harmonic salience across the 88-key range
  - [x] multi-note concurrent activation
  - [x] octave-shadow suppression
  - [x] repeated-note retrigger handling
  - [x] bounded polyphony selection
- [x] Made the piano benchmark loop interactive enough for real excerpts:
  - [x] windowed WAV excerpts
  - [x] fixed reference MIDI window trimming
  - [x] disabled expensive piano HPSS by default
  - [x] replaced slow cleanup spectral checks with bounded audio checks
  - [x] dense high-harmonic shadow pruning
- [x] Added audio-aware pitch false-positive cleanup:
  - [x] unsupported extreme low/high pruning
  - [x] mixed-cluster high-spray pruning
  - [x] synthetic tests for unsupported and audio-supported extreme notes
- [x] Added low-register cleanup refinement:
  - [x] support gating for MIDI `29-35`
  - [x] fifth/twelfth/octave low-shadow pruning
  - [x] preservation tests for strongly supported MIDI `34-35` boundary notes
- [x] Added audio-tail sustain cleanup:
  - [x] pitch-band RMS tail extension
  - [x] same-pitch retrigger cap
  - [x] static fallback for no-audio cleanup
  - [x] synthetic tests for sustain extension and no same-pitch smearing
- [x] Added conservative repeated-note cleanup:
  - [x] per-pitch same-pitch microgap merging across interleaved chord tones
  - [x] audio-aware merge blocking across true reattacks
  - [x] regression tests for interleaved same-pitch chatter and same-pitch audio reattack preservation
- [x] Added shared external MIDI decode support for optional research adapters:
  - [x] MIDI note-on/off decoding
  - [x] tempo-map handling
  - [x] running-status handling
  - [x] 88-key clamp
  - [x] velocity preservation
- [x] Integrated optional research adapters:
  - [x] `piano_transkun` via temp MIDI CLI adapter
  - [x] `piano_pti` via `PianoTranscription` temp MIDI adapter
  - [x] `piano_hft` via checkpoint-backed command adapter
  - [x] clean variants call `piano_cleanup.cleanup_notes`
- [x] Tightened onset-aligned attack candidate injection in `piano_polyphonic`.
- [x] Added pitch-aware/onset-aware velocity shaping with improved benchmark Velocity MAE.

## In progress

- [x] Run first real-song A/B comparison on piano-only songs and piano stems.
- [x] Decide whether `piano_auto` should be promoted: do not promote; legacy methods still win the referenced suite.
- [ ] Tune remaining low-register false positives only after listening review; Psalm 130 and Psalm 6 remain guard cases.
- [ ] Tune sustain and note endings only after listening review; current audio-tail pass is stable but not a final pedal model.
- [ ] Improve Psalm 2 chord-pitch recovery; attack candidate generation is present, but the remaining issue is upper-harmonic pitch selection.
- [x] Resolve portable packaging gate: Basic Pitch model asset is now bundled into the sidecar and portable `runtime-check` passes.

## Autonomous execution rule

When continuing this epic, take the first pending task in `PIANO_TRANSCRIPTION_IMPLEMENTATION_PLAN.md`, implement it, run the relevant validation command, then update both docs with the result. Do not re-plan unless a benchmark result invalidates the current order.

## Remaining implementation work

### Near term

- [x] Add a real-case piano benchmark manifest with actual songs instead of the template placeholder.
- [x] Run the benchmark suite on your piano test corpus.
- [ ] Review visual outputs and listening results side by side.
- [ ] Tune `piano_polyphonic` thresholds from real songs:
  - [ ] onset sensitivity
  - [ ] sustain hysteresis
  - [ ] octave-shadow suppression
  - [ ] max polyphony per frame
- [x] Tune broad pitch false positives from harmonic shadows.
- [ ] Improve low-register left-hand handling on dense passages.
- [ ] Improve note endings when sustain pedal behavior is strong.
- [ ] Add regression cases specifically for bass-clef doubling and muddy left-hand chords.

### Research model integration

- [x] Integrate `piano_transkun` end to end as an optional adapter.
- [x] Integrate `piano_transcription_inference` end to end as an optional adapter.
- [x] Integrate `piano_hft` as an optional checkpoint/command adapter.
- [x] Decide packaging/runtime strategy for optional piano research models: keep them optional and fail-safe; do not require them in normal import.
- [x] Compare:
  - [x] `piano_polyphonic_clean`
  - [x] `piano_transkun_clean`
  - [x] `piano_pti_clean`
  - [x] `piano_hft_clean`
  - [x] legacy melodic methods

### Possible later work

- [x] Add standalone Piano MIDI Refinement Workbench requirements for per-song Suno/source-MIDI A/B review.
- [x] Implement `refine-piano` workbench MVP: source MIDI baselines, selected audio candidates, candidate MIDI artifacts, JSON/Markdown reports, and static dashboard.
- [ ] Validate `refine-piano` workbench runs on real Suno piano MIDI plus matching audio.
- [ ] Explicit sustain-pedal event support instead of note-length approximation only.
- [ ] Hand/voice separation for readability and playability.
- [ ] Chord labeling / harmonic context overlays for review tooling.
- [ ] Separate presets for solo piano vs piano stem inside a full mix.

## Validation completed so far

- [x] Python targeted tests for orchestration, cleanup, benchmark metrics, and polyphonic heuristic fixtures.
- [x] `py -3 -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_benchmark.py python/ingest/tests/test_piano_polyphonic.py -q --no-cov` on 2026-04-30.
- [x] Windowed real-song benchmark run: `benchmarks/piano/runs/20260430_161801_piano-density-cleanup-v1`.
- [x] Pitch-support cleanup benchmark run: `benchmarks/piano/runs/20260430_181958_piano-pitch-support-cleanup-v1`.
- [x] Low-register cleanup benchmark run: `benchmarks/piano/runs/20260430_210845_piano-low-register-cleanup-v1`.
- [x] `py -3 -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_polyphonic.py python/ingest/tests/test_piano_benchmark.py -q --no-cov` on 2026-04-30, 20 passed.
- [x] Audio-tail sustain cleanup benchmark run: `benchmarks/piano/runs/20260430_212127_piano-audio-sustain-cleanup-v1`.
- [x] `py -3 -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_polyphonic.py python/ingest/tests/test_piano_benchmark.py -q --no-cov` on 2026-04-30, 22 passed.
- [x] Close same-pitch chatter merge benchmark run: `benchmarks/piano/runs/20260430_220556_piano-close-chatter-merge-v1`.
- [x] `py -3 -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_polyphonic.py python/ingest/tests/test_piano_benchmark.py -q --no-cov` on 2026-04-30, 24 passed.
- [x] Frontend selector/typecheck coverage in both apps.
- [x] Frontend production builds in both apps.
- [x] `py -3 -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_polyphonic.py python/ingest/tests/test_piano_benchmark.py python/ingest/tests/test_piano_research_adapters.py python/ingest/tests/test_transcription_orchestration.py -q --no-cov` on 2026-05-01, 58 passed.
- [x] Import smoke subset on 2026-05-01:
  - [x] split-folder analysis import path
  - [x] configured input stems reuse
  - [x] transcription option persistence
  - [x] directory import path
- [x] Final experimental benchmark run: `benchmarks/piano/runs/20260501_150045_piano-finish-experimental-v2`.
- [x] Sidecar packaging check attempted on 2026-05-01 with `build_sidecar.ps1 -SkipBuild -OutDir dist/sidecar-check`.
- [x] Portable build created on 2026-05-01:
  - [x] `D:\AuralPrimer\AuralPrimerPortable`
  - [x] `D:\AuralPrimer\AuralPrimerPortable.zip`
  - [x] portable sidecar `runtime-check` passes from inside the portable root.
- [x] Post-portable game review found `auto` on `keys` could still use a monophonic melodic extractor, producing a single-note piano track. Changed `auto` for `keys` stems to prefer `piano_auto`/`piano_polyphonic_clean` first, with legacy methods as fallback.
- [x] Rebuilt portable after the keys-routing fix on 2026-05-02. New sidecar hash: `03f48f964c78c8893f3b9da1a492dfc0a9f743fa5d069914c62371c49171ffe2`.
- [x] Added Piano MIDI Refinement Workbench MVP on 2026-05-02:
  - [x] requirements: `benchmarks/piano/PIANO_REFINEMENT_WORKBENCH.md`
  - [x] CLI: `refine-piano`
  - [x] artifacts: candidate MIDIs/notes, `summary.json`, `report.md`, `refinement_dashboard.html`
  - [x] validation shard: 86 passed, 3 warnings
- [x] Added playable piano refinement pass on 2026-05-03:
  - [x] `source_midi_clean_playable` candidate
  - [x] melody/top-note and useful left-hand anchor priority
  - [x] default max polyphony cap: 7 simultaneous notes
  - [x] Psalm 5 keyboard cleanup max polyphony reduced from 15-16 to 7

## What to use right now

If the source has a `keys`/piano stem, `auto` now routes that stem through the piano-roll-oriented polyphonic path first.

If you want the explicit piano-roll-oriented output, use:

- `piano_auto`

If you want the current best explicit in-repo heuristic for A/B:

- `piano_polyphonic_clean`

If you want the current benchmark winner or a legacy comparison for A/B:

- `melodic_octave_fix`
- `melodic_hpss_combined`
- `melodic_combined`

## Command checklist

Benchmark:

```powershell
py -3 benchmarks/piano/run_piano_regression.py --manifest benchmarks/piano/piano_suite_manifest.json --label baseline
```

Targeted piano tests:

```powershell
py -3 -m pytest --no-cov python/ingest/tests/test_transcription_orchestration.py python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_benchmark.py python/ingest/tests/test_piano_polyphonic.py -q
```

## Next recommended step

1. Listen to `benchmarks/piano/runs/20260501_150045_piano-finish-experimental-v2/predictions`.
2. If external piano-model A/B is needed, install/configure Transkun, PTI, or hFT dependencies/checkpoints and rerun the same benchmark.
3. Test the portable build in game with a piano-heavy import.
4. Test the rebuilt portable after the `auto`/`keys` routing fix with a fresh Psalm 130 import.
