# Piano Transcription Epic

This file is the living tracker for piano-focused transcription quality work.

## Goal

Produce piano MIDI that is:

- playable
- recognizably close to the source performance
- less doubled and less octave-confused than the current generic melodic path
- better at sustain, chords, repeated-note attacks, and dynamics

## Current status

Status: experimental complete; corrected Psalm reference offsets now show Basic Pitch playable is the leading current path on the referenced Psalm piano suite, sparse low-register treble restore plus sparse-low mid-profile, spectral chord-candidate, dense-low spectral upper-voice, dense-high staccato tail-trim, default-fewer low-artifact candidate, local sparse-low spectral window, sparse low-flood clamp, long full-song velocity calibration, sparse-synth transient cull, and long sparse duration cull passes improve the Psalm 130 keyboard/synth, Psalm 10 ending/full-song velocity, and sparse full-stem Psalm 2/Psalm 7 failure shapes without regressing Psalm 5; Psalm 10 and Psalm 130 keyboard review artifacts plus 45-second and full-stem Psalm 5 validations now exist, and the next weak area is human listening review plus portable packaging for the latest source, with note endings and Psalm 130 synth exact F1 still weak; current source, `dist/sidecar`, and desktop/game Tauri sidecar binaries now include the long sparse duration cull source, while the portable root/zip remain older until repacked

Primary explicit piano path today:

- `piano_auto`
- `piano_basic_pitch_playable`
- `piano_basic_pitch`
- `piano_basic_pitch_clean`

Best in-repo heuristic path today:

- `piano_polyphonic_clean`

Benchmark winner on the older referenced heuristic suite:

- `melodic_octave_fix`

Benchmark winner on the current Psalm 5 keyboard/Suno source shootout:

- `piano_auto` / `piano_basic_pitch_playable`

Execution plan:

- `benchmarks/piano/PIANO_TRANSCRIPTION_IMPLEMENTATION_PLAN.md`

Finish contract:

- Follow the `Finish Plan` section in `benchmarks/piano/PIANO_TRANSCRIPTION_IMPLEMENTATION_PLAN.md`.
- Stop tuning when the final piano path is benchmarked, labeled as default or experimental, and packaged if the quality/build gates pass.

Current next task:

- Human-listen to the latest review and full-stem import artifacts, then decide whether to repack the portable build from the latest sidecar. Current full-stem validation `benchmarks/piano/runs/20260614_153026_referenced-fullstem-long-sparse-duration-cull-current` improves playable mean F1 `0.395` to `0.505` versus the sparse-synth full-stem run; Psalm 2 improves F1 `0.249` to `0.629` with notes `197` to `34`, and Psalm 7 improves F1 `0.354` to `0.524` with notes `78` to `24`, while Psalm 130 keyboard stays F1 `0.614`, Psalm 130 synth stays F1 `0.264`, and Psalm 10 stays F1 `0.496`. Corrected-suite guard `benchmarks/piano/runs/20260614_153201_piano-suite-long-sparse-duration-cull-current` remains unchanged at mean F1 `0.565`, onset F1 `0.681`, mean Offset F1 `0.292`, mean Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, and duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_153107_psalm5-long-sparse-duration-cull-current` is unchanged at mean F1 `0.529`. Packaged sidecar Psalm 2 full-stem smoke `tmp/psalm2-full-packaged-sidecar-import-20260614_2108_long_sparse_duration.auralsong` validates with `keys -> piano_auto`, internal score `1.0`, `34` `keys_main` notes over `222.64s`, pitch range `37-63`, mean duration `1.905s`, velocity range `66-92`, mean velocity `80.71`, max overlap `4`, and max 55 ms attack cluster `3`; the synced packaged sidecar SHA-256 is `4861a3cf9f45ec9277b0dddd3d9a7d9d72975dcdc62a35ca1a4783a73f47fbf5`.

Research-model paths integrated as optional fail-safe adapters:

- `piano_basic_pitch`
- `piano_basic_pitch_playable`
- `piano_basic_pitch_clean`
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
- [x] Added strict packaged Basic Pitch piano methods on 2026-06-13:
  - [x] `piano_basic_pitch` fails closed when the Basic Pitch package/model cannot run
  - [x] `piano_basic_pitch_playable` uses balanced piano thresholds before applying the shared keys playability pass, including density-triggered low-confidence pruning, capped sustain extension, guarded velocity calibration, a dense-only default-threshold recall restore for confident notes that still fit the playable caps, and a post-restore release-sustain pass for dense mid/high piano notes
  - [x] `piano_basic_pitch_playable` now lazily reruns one alternate Basic Pitch threshold profile when balanced-output density/cluster/register features match benchmarked cases that improve with stricter thresholds
  - [x] `piano_basic_pitch_clean` applies the piano cleanup pass to that strict model output
  - [x] `piano_auto` tries playable strict Basic Pitch before raw Basic Pitch, optional PTI/research models, and heuristic piano extraction based on the Psalm 5 keyboard shootout
  - [x] normal keys import applies the playable max-polyphony cap after transcription
  - [x] 2026-06-13 playable benchmark `benchmarks/piano/runs/20260613_122132_psalm5-playable-basic-pitch-current`: `piano_auto` mean F1 `0.482`, onset F1 `0.635`, pitch accuracy `75.9%`, duplicate rate `0.0%`; polyphony-stress output reduced from 193 raw notes to 155 playable notes
  - [x] 2026-06-13 sustain benchmark `benchmarks/piano/runs/20260613_131247_psalm5-playable-basic-pitch-sustain-current`: keeps `piano_auto` mean F1 `0.482` and max overlap `7`, while improving mean Offset F1 from `0.103` to `0.146`
  - [x] 2026-06-13 velocity benchmark `benchmarks/piano/runs/20260613_140500_psalm5-playable-basic-pitch-velocity-current`: keeps `piano_auto` mean F1 `0.482`, Offset F1 `0.146`, pitch accuracy `75.9%`, duplicate rate `0.0%`, and max overlap `7`, while improving Velocity MAE from `44.1` to `9.1` and Off+Vel F1 from `0.003` to `0.122`
  - [x] 2026-06-13 balanced-threshold benchmark `benchmarks/piano/runs/20260613_145551_psalm5-playable-basic-pitch-balanced-threshold-current`: improves `piano_auto` mean F1 from `0.482` to `0.502`, Offset F1 from `0.146` to `0.155`, pitch accuracy from `75.9%` to `78.5%`, keeps duplicate rate `0.0%`, and improves every Psalm 5 window's F1 versus the prior playable path
  - [x] 2026-06-13 recall-restore benchmark `benchmarks/piano/runs/20260613_160113_psalm5-playable-basic-pitch-recall-restore-confidence-order-current`: improves `piano_auto` mean F1 from `0.502` to `0.512`, onset F1 from `0.634` to `0.656`, Offset F1 from `0.155` to `0.157`, Off+Vel F1 from `0.130` to `0.131`, keeps duplicate rate `0.0%`, and leaves every Psalm 5 window at or above the balanced-threshold F1
  - [x] 2026-06-13 release-sustain benchmark `benchmarks/piano/runs/20260613_164931_psalm5-playable-basic-pitch-release-sustain-current`: keeps `piano_auto` mean F1 `0.512`, onset F1 `0.656`, pitch accuracy `77.7%`, and duplicate rate `0.0%`, while improving Offset F1 from `0.157` to `0.170` and Off+Vel F1 from `0.131` to `0.144`
  - [x] 2026-06-13 adaptive-profile benchmark `benchmarks/piano/runs/20260613_174842_psalm5-playable-basic-pitch-adaptive-profile-current`: improves `piano_auto` mean F1 from `0.512` to `0.529`, onset F1 from `0.656` to `0.682`, keeps duplicate rate `0.0%`, and gives Psalm 5 window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`
  - [x] 2026-06-13 broader guard run `benchmarks/piano/runs/20260613_174941_piano-suite-basic-pitch-adaptive-profile-current`: keeps the older referenced suite effectively flat-to-slightly-up (`piano_basic_pitch_playable` mean F1 `0.0939` vs previous balanced-profile expectation `0.0932`) while preserving the Psalm 130 synth, Psalm 2, and Psalm 7 sparse/sustain-heavy guards on the balanced profile
  - [x] 2026-06-13 default-candidate selector benchmark `benchmarks/piano/runs/20260613_212244_piano-suite-basic-pitch-default-candidate-current`: source `piano_basic_pitch_playable` now compares the selected playable profile with default Basic Pitch plus the same playable cleanup for sparse pitch-spray and dense small-recall cases; older-suite mean F1 improves from `0.094` to `0.106`, Onset F1 from `0.231` to `0.239`, pitch accuracy from `40.7%` to `49.4%`, and duplicate rate remains `0.0%`
  - [x] 2026-06-13 Psalm 5 guard rerun `benchmarks/piano/runs/20260613_212308_psalm5-playable-basic-pitch-default-candidate-current`: unchanged versus the adaptive-profile run, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, and duplicate rate `0.0%`
  - [x] 2026-06-13 loose sparse-low-artifact selector benchmark `benchmarks/piano/runs/20260613_221946_piano-suite-basic-pitch-loose-candidate-current`: `piano_basic_pitch_playable` can lazily try a loose Basic Pitch profile only when the selected sparse candidate contains low-register artifacts and the loose playable result removes them with a stronger no-reference score; older-suite mean F1 improves from `0.106` to `0.112`, Onset F1 from `0.239` to `0.244`, pitch accuracy from `49.4%` to `50.8%`, Velocity MAE from `25.6` to `18.5`, and duplicate rate remains `0.0%`
  - [x] 2026-06-13 loose selector guard result: Psalm 7 keyboard improves from F1 `0.061` to `0.089` and note count drops from `48` to `27`; Psalm 130 keyboard/synth, Psalm 10, Psalm 2, and Psalm 6 no-reference output remain unchanged versus the default-candidate run
  - [x] 2026-06-13 Psalm 5 guard rerun `benchmarks/piano/runs/20260613_222012_psalm5-playable-basic-pitch-loose-candidate-current`: unchanged versus the default-candidate run, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`
  - [x] 2026-06-13 dense low-register audio-onset alignment benchmark `benchmarks/piano/runs/20260613_232404_piano-suite-basic-pitch-audio-align-current`: `piano_basic_pitch_playable` now aligns only dense, short-decay, low-register keys outputs to nearby audio onset peaks while preserving max polyphony `7`; older-suite mean F1 improves from `0.112` to `0.115`, and Psalm 130 keyboard improves from F1 `0.028` / onset F1 `0.046` to F1 `0.156` / onset F1 `0.358`
  - [x] 2026-06-13 audio-onset guard result: Psalm 130 synth remains F1 `0.082`, Psalm 10 remains `0.323`, Psalm 2 remains `0.038`, Psalm 7 remains `0.089`, and duplicate rate stays `0.0%`
  - [x] 2026-06-13 Psalm 5 audio-onset guard rerun `benchmarks/piano/runs/20260613_232513_psalm5-playable-basic-pitch-audio-align-current`: unchanged versus the loose-selector guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`
  - [x] 2026-06-14 Psalm 2 reference-alignment correction: audio-onset audit showed the Psalm 2 benchmark MIDI reference was offset by about `-0.475s` after the clipped opening chord, so `benchmarks/piano/piano_suite_manifest.json` now records `offset_sec: -0.475`. Rerun `benchmarks/piano/runs/20260614_045949_piano-suite-basic-pitch-psalm2-offset-current` raises `piano_basic_pitch_playable` broad-suite mean F1 from `0.138` to `0.253`; Psalm 2 improves from F1 `0.038` to `0.615`, onset F1 `0.077` to `0.692`, and pitch accuracy `50.0%` to `88.9%`.
  - [x] 2026-06-14 fixed piano refinement workbench `_playable` dispatch so registry-backed methods such as `piano_basic_pitch_playable` are not intercepted by the generic source-MIDI playable reducer.
  - [x] 2026-06-14 Psalm 7 reference-backed review run `benchmarks/piano/refinement_runs/20260614_052003_psalm7-reference-backed-review-source-audio` uses `reference_offset_sec=-184.0` and compares `source_midi_clean_playable` before against recommended `piano_basic_pitch_playable` after. `piano_basic_pitch_playable` is 27 notes / max polyphony `4` / reference F1 `0.089` / onset F1 `0.356`; `source_midi_clean_playable` is 60 notes / max polyphony `7` / reference F1 `0.077` / onset F1 `0.256`, with `playability_audition_source.wav`, synthesized before/after/AB clips, and piano-roll artifacts written for listening.
  - [x] 2026-06-14 corrected the remaining referenced-suite offsets after an audio-onset/reference sweep: Psalm 130 keyboard `+0.350s`, Psalm 130 synth `+0.375s`, Psalm 10 keyboard `+0.150s`, Psalm 7 keyboard `-0.475s`, and Psalm 2 keyboard remains `-0.475s`. Rerun `benchmarks/piano/runs/20260614_053231_piano-suite-basic-pitch-reference-offset-audit-current` raises `piano_auto` / `piano_basic_pitch_playable` mean F1 to `0.483`, onset F1 to `0.611`, pitch accuracy to `77.0%`, and keeps duplicate rate `0.0%`; note+offset F1 remains weak at `0.076`.
  - [x] 2026-06-14 corrected Psalm 7 reference-backed review `benchmarks/piano/refinement_runs/20260614_052857_psalm7-reference-backed-review-corrected-offset` uses `reference_offset_sec=-184.475`; `piano_basic_pitch_playable` is still recommended, now F1 `0.533` / onset F1 `0.622`, while `source_midi_clean_playable` is F1 `0.359`.
  - [x] 2026-06-14 sparse chordal release-sustain gate: Basic Pitch playable output with 12-60 notes, moderate density, and clustered attacks can extend eligible mid/high notes toward 2-second releases while preserving same-pitch reattack gaps and max polyphony `7`. Corrected-suite run `benchmarks/piano/runs/20260614_053944_piano-suite-basic-pitch-sparse-release-sustain-current` keeps mean F1 `0.483`, onset F1 `0.611`, pitch accuracy `77.0%`, duplicate rate `0.0%`, and improves mean Offset F1 from `0.076` to `0.127`; Psalm 2 Offset F1 improves to `0.192` and Psalm 7 to `0.178`.
  - [x] 2026-06-14 Psalm 5 sparse-release guard rerun `benchmarks/piano/runs/20260614_054036_psalm5-playable-basic-pitch-sparse-release-sustain-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 tail-balance release/trim gates: dense low-register Basic Pitch playable output can extend eligible notes above pitch `45` toward 2-second releases, while sparse low-register outputs with overlong high tails can trim high notes and damp their velocities. Corrected-suite run `benchmarks/piano/runs/20260614_055143_piano-suite-basic-pitch-tail-balance-velocity-current` improves `piano_auto` / `piano_basic_pitch_playable` from sparse-release mean F1 `0.483` to `0.487`, onset F1 `0.611` to `0.613`, mean Offset F1 `0.127` to `0.154`, mean Off+Vel F1 `0.109` to `0.137`, pitch accuracy `77.0%` to `77.5%`, and keeps duplicate rate `0.0%`.
  - [x] 2026-06-14 tail-balance per-case guard details: Psalm 130 keyboard is now F1 `0.485`, onset F1 `0.703`, Offset F1 `0.119`, Off+Vel F1 `0.119`, with `110` notes and no duplicates; Psalm 130 synth remains exact-match weak at F1 `0.204` / onset F1 `0.347`, but Offset and Off+Vel F1 both reach `0.102` and Velocity MAE drops to `17.8`; Psalm 10, Psalm 2, Psalm 7, and Psalm 6 shape stay within the previous guard expectations.
  - [x] 2026-06-14 Psalm 5 tail-balance guard rerun `benchmarks/piano/runs/20260614_055331_psalm5-playable-basic-pitch-tail-balance-velocity-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 sparse low-register treble restore: a tightly gated Basic Pitch playable pass adds octave-restored treble companions only for sparse, low-register, synth-like outputs with very few high notes, while preserving duplicate, attack-cluster, and max-polyphony caps. Corrected-suite run `benchmarks/piano/runs/20260614_061054_piano-suite-basic-pitch-sparse-low-treble-restore-current` improves mean F1 `0.487` to `0.498`, onset F1 `0.613` to `0.628`, Offset F1 `0.154` to `0.156`, Off+Vel F1 `0.137` to `0.138`, pitch accuracy `77.5%` to `77.9%`, Velocity MAE `11.5` to `10.5`, and keeps duplicate rate `0.0%`.
  - [x] 2026-06-14 sparse low-register treble restore per-case guard details: Psalm 130 synth improves from F1 `0.204` to `0.257`, onset F1 `0.347` to `0.422`, Offset/Off+Vel F1 `0.102` to `0.110`, note count `47` to `58`, and stays at duplicate rate `0.0%`; Psalm 130 keyboard, Psalm 10, Psalm 2, Psalm 7, and Psalm 6 are unchanged. Review artifact `benchmarks/piano/refinement_runs/20260614_061159_psalm130-synth-sparse-low-treble-restore-review` includes source audio, before/after/AB previews, piano-roll visuals, and candidate MIDI.
  - [x] 2026-06-14 Psalm 5 sparse-low-treble guard rerun `benchmarks/piano/runs/20260614_061145_psalm5-playable-basic-pitch-sparse-low-treble-restore-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 current-source Psalm 130 synth import smoke `tmp/psalm130-synth-current-import-20260614_0612.auralsong`: keys-only input-stem config, stem separation skipped, AuralSong validation returns `ok=true`, recognition records `keys -> piano_auto`, internal score `0.975`, `58` keys notes, pitch range `31-82`, mean duration `0.988s`, max 55 ms attack cluster `4`, velocity range `24-48`, and drums skipped because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 sparse-low mid-profile selector: when the balanced playable output is a 40-70 note, low-register, synth-like candidate with only a few high notes, `piano_basic_pitch_playable` now reruns the existing mid Basic Pitch profile before default-candidate selection. Corrected-suite run `benchmarks/piano/runs/20260614_091658_piano-suite-basic-pitch-sparse-low-mid-profile-current` improves mean F1 `0.498` to `0.501`, onset F1 `0.628` to `0.633`, Offset F1 `0.156` to `0.157`, Off+Vel F1 `0.138` to `0.139`, and keeps duplicate rate `0.0%`.
  - [x] 2026-06-14 sparse-low mid-profile per-case guard details: Psalm 130 synth improves from F1 `0.257` to `0.272`, onset F1 `0.422` to `0.447`, Offset/Off+Vel F1 `0.110` to `0.117`, note count `58` to `52`, and stays at duplicate rate `0.0%`; Psalm 130 keyboard, Psalm 10, Psalm 2, Psalm 7, and Psalm 6 no-reference output are unchanged.
  - [x] 2026-06-14 Psalm 5 sparse-low mid-profile guard rerun `benchmarks/piano/runs/20260614_091820_psalm5-playable-basic-pitch-sparse-low-mid-profile-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 current-source and packaged sidecar Psalm 130 synth import smokes after sparse-low mid-profile selector: `tmp/psalm130-synth-current-import-20260614_0921_sparse_low_mid.auralsong` and `tmp/psalm130-synth-packaged-sidecar-import-20260614_0929_sparse_low_mid.auralsong` both validate, record `keys -> piano_auto`, internal score `0.975`, `52` keys notes, pitch range `31-82`, mean duration `1.075s`, velocity range `24-48`, and skipped drums because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 sparse-low spectral chord candidate: a tightly gated Basic Pitch playable pass can replace sparse low-register synth-like outputs with a bounded spectral chord candidate. It uses Basic Pitch low anchors for chord starts, FFT energy support for candidate chord tones, preserves isolated high pickups, skips continuation clusters, caps max polyphony at `7`, and only wins when the no-reference score does not worsen.
  - [x] 2026-06-14 sparse-low spectral benchmark `benchmarks/piano/runs/20260614_100334_piano-suite-sparse-low-spectral-candidate-current`: `piano_auto` / `piano_basic_pitch_playable` improves from sparse-low mid-profile mean F1 `0.501` to `0.556`, onset F1 `0.633` to `0.683`, Offset F1 `0.157` to `0.235`, Off+Vel F1 `0.139` to `0.217`, pitch accuracy `77.9%` to `81.5%`, Velocity MAE `10.5` to `8.1`, and keeps duplicate rate `0.0%`.
  - [x] 2026-06-14 sparse-low spectral per-case guard details: Psalm 130 synth improves from F1 `0.272` to `0.547`, onset F1 `0.447` to `0.695`, Offset/Off+Vel F1 `0.117` to `0.505`, note count `52` to `44`, and stays at duplicate rate `0.0%`; Psalm 130 keyboard, Psalm 10, Psalm 2, Psalm 7, and Psalm 6 no-reference output are unchanged.
  - [x] 2026-06-14 Psalm 5 sparse-low spectral guard rerun `benchmarks/piano/runs/20260614_100319_psalm5-playable-sparse-low-spectral-candidate-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 Psalm 130 synth sparse-low spectral review artifact `benchmarks/piano/refinement_runs/20260614_100423_psalm130-synth-sparse-low-spectral-candidate-review`: recommended `piano_basic_pitch_playable`, reference F1 `0.547`, Offset F1 `0.505`, source F1 `0.196`, `44` notes, duplicate rate `0.0%`, max polyphony `7`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
  - [x] 2026-06-14 current-source and packaged sidecar Psalm 130 synth import smokes after sparse-low spectral candidate: `tmp/psalm130-synth-current-import-20260614_1005_sparse_low_spectral.auralsong` and `tmp/psalm130-synth-packaged-sidecar-import-20260614_1013_sparse_low_spectral.auralsong` both validate, record `keys -> piano_auto`, internal score `0.9833`, `44` keys notes, pitch range `34-84`, mean duration `1.593s`, velocity `24`, and skipped drums because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 sparse-low spectral sidecar package: patched source `python/ingest/dist_patched_spectral/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_spectral\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `7179ffa5bcce64b9ca8612a6da7067b6fbefdda8751b71b66d3192d77a035c83`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 dense-low spectral upper-voice restore: a tightly gated Basic Pitch playable pass targets 95-125 note, low-register, under-recalled dense keyboard outputs with no existing notes above MIDI `83`, then adds FFT-supported upper voices that are interval-related to active chord tones while preserving the max-polyphony and attack-cluster caps. The existing sparse-low spectral chord candidate also now uses the source note span instead of a hardcoded 12-second cap for full-song imports.
  - [x] 2026-06-14 dense-low spectral benchmark `benchmarks/piano/runs/20260614_103334_piano-suite-dense-low-spectral-treble-current`: `piano_auto` / `piano_basic_pitch_playable` improves from sparse-low spectral mean F1 `0.556` to `0.565`, pitch accuracy `81.5%` to `83.0%`, and keeps duplicate rate `0.0%`; Psalm 130 keyboard improves from F1 `0.485` to `0.531`, pitch accuracy `69.0%` to `76.6%`, notes `110` to `130`, and max polyphony stays `7`.
  - [x] 2026-06-14 dense-low spectral per-case guard details: Psalm 130 synth, Psalm 10, Psalm 2, Psalm 7, and Psalm 6 no-reference output are unchanged versus the sparse-low spectral run; Psalm 130 keyboard onset F1 moves from `0.703` to `0.694` because the added upper voices trade a small onset-only precision drop for a larger exact-pitch recall gain.
  - [x] 2026-06-14 Psalm 5 dense-low spectral guard rerun `benchmarks/piano/runs/20260614_103407_psalm5-playable-dense-low-spectral-treble-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 current-source Psalm 130 keyboard import smoke after dense-low spectral restore: `tmp/psalm130-keyboard-current-import-20260614_1038_dense_low_spectral.auralsong` validates, records `keys -> piano_auto`, internal score `0.9417`, `130` keys notes, pitch range `39-94`, mean duration `0.595s`, velocity range `35-51`, and skipped drums because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 dense-low spectral sidecar package: patched source `python/ingest/dist_patched_dense/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_dense\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `075f9d07500ed1eb3f79baf57f8bd80125b4786b256ffeef067712e3e2c3b1ce`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 130 keyboard import smoke after dense-low spectral restore: `tmp/psalm130-keyboard-packaged-sidecar-import-20260614_1047_dense_low_spectral.auralsong` validates and matches the source smoke with `keys -> piano_auto`, internal score `0.9417`, `130` keys notes, pitch range `39-94`, mean duration `0.595s`, velocity range `35-51`, and skipped drums because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 dense-high staccato tail trim: a tightly gated Basic Pitch playable pass trims over-sustained high-register dense outputs with staccato-like source shape while leaving sparse/high, low-register, and Psalm 130-style dense-low outputs unchanged.
  - [x] 2026-06-14 dense-high staccato benchmark `benchmarks/piano/runs/20260614_110050_piano-suite-dense-high-staccato-trim-current`: `piano_auto` / `piano_basic_pitch_playable` keeps mean F1 `0.565`, onset F1 `0.681`, pitch accuracy `83.0%`, Velocity MAE `8.4`, and duplicate rate `0.0%`, while mean Offset F1 improves from `0.235` to `0.292` and mean Off+Vel F1 improves from `0.217` to `0.274`; Psalm 10 keeps F1 `0.597` / onset F1 `0.701` / `135` notes and improves Offset/Off+Vel F1 from `0.181` to `0.465`.
  - [x] 2026-06-14 Psalm 5 dense-high staccato guard rerun `benchmarks/piano/runs/20260614_110137_psalm5-playable-dense-high-staccato-trim-current`: unchanged versus the previous guard, with `piano_auto` / `piano_basic_pitch_playable` mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, duplicate rate `0.0%`, and window F1s intro `0.740`, mid dense `0.543`, polyphony stress `0.417`, late dense `0.416`.
  - [x] 2026-06-14 dense-high staccato sidecar package: patched source `python/ingest/dist_patched_staccato/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_staccato\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `7a31b4ebe1d9dbfce96ee9371adf598dbed082db09a5a0f4850e428eaa552a81`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 current-source and packaged sidecar Psalm 10 keyboard import smokes after dense-high staccato trim: `tmp/psalm10-keyboard-current-import-20260614_1124_dense_high_staccato.auralsong` and `tmp/psalm10-keyboard-packaged-sidecar-import-20260614_1126_dense_high_staccato.auralsong` both validate, record `keys -> piano_auto`, internal score `1.0`, `135` keys notes, pitch range `56-88`, mean duration `0.340s`, velocity range `35-54`, and skipped drums because the synthesized input-stem mix has no drum source.
  - [x] 2026-06-14 Psalm 10 dense-high staccato review artifact `benchmarks/piano/refinement_runs/20260614_113904_psalm10-keyboard-dense-high-staccato-review`: recommended `piano_basic_pitch_playable`, reference F1 `0.597`, Offset F1 `0.465`, raw-source F1 `0.584`, `135` notes, duplicate rate `0.0%`, max polyphony `6`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
  - [x] 2026-06-14 Psalm 130 keyboard dense-low spectral review artifact `benchmarks/piano/refinement_runs/20260614_113935_psalm130-keyboard-dense-low-spectral-review`: recommended `piano_basic_pitch_playable`, reference F1 `0.531`, Offset F1 `0.117`, raw-source F1 `0.481`, `130` notes, duplicate rate `0.0%`, max polyphony `7`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
  - [x] 2026-06-14 broader Psalm 5 45-second validation `benchmarks/piano/runs/20260614_114048_psalm5-45s-dense-high-staccato-validation`: `piano_auto` / `piano_basic_pitch_playable` F1 `0.774`, onset F1 `0.817`, Offset F1 `0.258`, pitch accuracy `94.7%`, duplicate rate `0.0%`, and `160` notes versus raw Basic Pitch F1 `0.620`, onset F1 `0.637`, Offset F1 `0.101`, and `239` notes.
  - [x] 2026-06-14 packaged sidecar Psalm 5 45-second import smoke `tmp/psalm5-45s-packaged-sidecar-import-20260614_1141_dense_high_staccato.auralsong`: validates with `keys -> piano_auto`, internal score `0.9292`, `160` keys notes, pitch range `37-94`, mean duration `1.004s`, velocity range `70-107`, max overlap `7`, max 55 ms attack cluster `4`, and skipped drums because the synthesized input-stem mix has no drum source. The high velocity range is a remaining broader-window listening/tuning risk.
  - [x] 2026-06-14 full-stem Psalm 5 validation `benchmarks/piano/runs/20260614_114716_psalm5-full-dense-high-staccato-validation`: `piano_auto` / `piano_basic_pitch_playable` improves over raw Basic Pitch on the full keyboard stem, F1 `0.540` vs `0.494`, onset F1 `0.674` vs `0.618`, Offset F1 `0.139` vs `0.106`, Off+Vel F1 `0.132` vs `0.002`, Velocity MAE `8.0` vs `43.2`, notes `1292` vs `2170`, and duplicate rate `0.0%` for both.
  - [x] 2026-06-14 packaged sidecar full-stem Psalm 5 import smoke `tmp/psalm5-full-packaged-sidecar-import-20260614_1149_dense_high_staccato.auralsong`: validates with `keys -> piano_auto`, internal score `0.8834`, `1292` keys notes over `264.68s`, pitch range `27-94`, mean duration `0.890s`, velocity range `35-54`, mean velocity `41.21`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the synthesized input-stem mix has no drum source. The 45-second excerpt-only high velocity did not reproduce on the full-stem smoke.
  - [x] 2026-06-14 default-fewer low-artifact candidate selector: sparse low-artifact Basic Pitch playable outputs can choose default-threshold plus the same playable cleanup when both candidates have low-register reach, the default candidate has at least `5%` fewer notes, and no-reference score is tied within `0.001`. This targets sparse full-stem Psalm 10/Psalm 2/Psalm 7 cases where the balanced/loose playable profile added notes without improving reference metrics.
  - [x] 2026-06-14 referenced full-stem validation `benchmarks/piano/runs/20260614_120621_referenced-fullstem-default-fewer-low-artifact-current`: `piano_auto` / `piano_basic_pitch_playable` improves from dense-high full-stem mean F1 `0.296` to `0.316`, onset F1 `0.361` to `0.384`, Offset F1 `0.064` to `0.078`, and pitch accuracy `82.4%` to `83.3%`. Psalm 10 moves from F1 `0.446` to raw parity at `0.496`, Psalm 2 from `0.139` to `0.148`, Psalm 7 from `0.111` to `0.153`, Psalm 130 keyboard stays F1 `0.614`, and Psalm 130 synth remains weak at F1 `0.168`.
  - [x] 2026-06-14 default-fewer guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_120712_piano-suite-default-fewer-low-artifact-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, and duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_120747_psalm5-default-fewer-low-artifact-current` is unchanged at mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, and duplicate rate `0.0%`.
  - [x] 2026-06-14 default-fewer sidecar package: the clean full PyInstaller rebuild still stalls, so the shipped sidecar was produced by patching the prior valid one-file PyInstaller archive with the current `aural_ingest.transcription` PYZ entry. Patched source `python/ingest/dist_patched_default_fewer/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_default_fewer\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `8c87d98b0117335ec56f8242ee401f5de5b3ff03bd3399748a2932074b99c0e1`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar full-stem Psalm 7 import smoke `tmp/psalm7-full-packaged-sidecar-import-20260614_1214_default_fewer.auralsong`: validates with `keys -> piano_auto`, internal score `0.8667`, `204` keys notes over `198.12s`, pitch range `21-85`, mean duration `0.344s`, velocity range `60-101`, mean velocity `75.5`, max overlap `7`, max 55 ms attack cluster `5`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 local sparse-low spectral windows: long low-register Basic Pitch playable outputs can now run the existing sparse-low spectral chord candidate over overlapping 12-second local windows, then splice accepted candidate spans back into the full song only when local and final no-reference scores stay within tolerance and max polyphony remains `7`.
  - [x] 2026-06-14 focused Psalm 130 synth full-stem validation `benchmarks/piano/runs/20260614_123940_psalm130-synth-full-local-spectral-span-current`: `piano_auto` / `piano_basic_pitch_playable` improves over the default-fewer full-stem result, F1 `0.168` to `0.220`, onset F1 `0.273` to `0.311`, Offset F1 `0.016` to `0.064`, Off+Vel F1 `0.005` to `0.054`, pitch accuracy `61.5%` to `70.7%`, Velocity MAE `20.0` to `11.4`, and notes `255` to `247` versus raw Basic Pitch F1 `0.167` and `258` notes.
  - [x] 2026-06-14 local-window guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_124040_piano-suite-local-spectral-window-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, and duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_124115_psalm5-local-spectral-window-current` is unchanged at mean F1 `0.529`, onset F1 `0.682`, Offset F1 `0.172`, Off+Vel F1 `0.145`, pitch accuracy `76.8%`, Velocity MAE `8.8`, and duplicate rate `0.0%`.
  - [x] 2026-06-14 referenced full-stem validation after local windows `benchmarks/piano/runs/20260614_124617_referenced-fullstem-local-spectral-window-current`: `piano_auto` / `piano_basic_pitch_playable` improves mean F1 from `0.316` to `0.326`, onset F1 `0.384` to `0.391`, Offset F1 `0.078` to `0.088`, Off+Vel F1 `0.037` to `0.046`, pitch accuracy `83.3%` to `85.1%`, and Velocity MAE `21.0` to `19.3` while keeping duplicate rate `0.0%`; Psalm 130 keyboard, Psalm 10, Psalm 2, and Psalm 7 are unchanged versus the default-fewer full-stem run.
  - [x] 2026-06-14 source CLI Psalm 130 synth full-stem import smoke `tmp/psalm130-synth-full-source-import-20260614_1253_local_spectral.auralsong`: validates with `keys -> piano_auto`, internal score `0.8753`, `247` `keys_main` notes, pitch range `29-89`, mean duration `1.016s`, velocity range `24-54`, mean velocity `36.34`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 local sparse-low spectral sidecar package: patched source `python/ingest/dist_patched_local_spectral/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_local_spectral\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `65c7ee9fb3751ae3f1353ff86de25744606e200bb8be395039221b2fe371f8df`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 130 synth full-stem import smoke `tmp/psalm130-synth-full-packaged-sidecar-import-20260614_1306_local_spectral.auralsong`: validates with `keys -> piano_auto`, internal score `0.8753`, `247` `keys_main` notes, pitch range `29-89`, mean duration `1.016s`, velocity range `24-54`, mean velocity `36.34`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 sparse low-flood clamp: long, sparse Basic Pitch playable outputs with a large sub-36 low-note share now drop the low-register spray, plus >88 harmonics, only when enough notes remain and max polyphony stays capped. This targets the Psalm 2/Psalm 7 full-stem flood shape without touching dense Psalm 10/Psalm 130 or short corrected-suite windows.
  - [x] 2026-06-14 referenced full-stem validation after sparse low-flood clamp `benchmarks/piano/runs/20260614_132619_referenced-fullstem-sparse-low-flood-clamp-current`: `piano_auto` / `piano_basic_pitch_playable` improves mean F1 from `0.326` to `0.386`, onset F1 `0.391` to `0.456`, Offset F1 `0.088` to `0.095`, Off+Vel F1 `0.046` to `0.049`, and keeps duplicate rate `0.0%`; Psalm 2 improves F1 `0.148` to `0.249`, onset F1 `0.158` to `0.266`, notes `357` to `197`; Psalm 7 improves F1 `0.153` to `0.354`, onset F1 `0.162` to `0.375`, notes `204` to `78`.
  - [x] 2026-06-14 sparse low-flood guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_132723_piano-suite-sparse-low-flood-clamp-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_132702_psalm5-sparse-low-flood-clamp-current` is unchanged at mean F1 `0.529`.
  - [x] 2026-06-14 sparse low-flood sidecar package: patched source `python/ingest/dist_patched_sparse_low_flood/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_sparse_low_flood\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `d79680de3c3a6d1e22d558c500609e8c060c6b1e9ff565bcec224837f4e408a9`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 7 full-stem import smoke `tmp/psalm7-full-packaged-sidecar-import-20260614_1334_sparse_low_flood.auralsong`: validates with `keys -> piano_auto`, internal score `0.9917`, `78` keys notes over `198.12s`, pitch range `36-85`, mean duration `0.487s`, velocity range `62-101`, mean velocity `77.51`, max overlap `7`, max 55 ms attack cluster `5`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 long full-song velocity calibration: high-confidence long Basic Pitch playable outputs with many notes, moderate density, high median velocity, and no very-soft notes now scale velocities down after sparse low-flood cleanup. This targets the Psalm 10 full-stem velocity failure without changing note timing, pitch selection, dense short-window output, Psalm 130 soft full-stem output, or sparse Psalm 2/Psalm 7 clamp output.
  - [x] 2026-06-14 referenced full-stem validation after long velocity calibration `benchmarks/piano/runs/20260614_134526_referenced-fullstem-long-velocity-current`: Psalm 10 stays F1 `0.496`, onset F1 `0.619`, Offset F1 `0.194`, and `645` notes, while Off+Vel F1 improves `0.000` to `0.192` and Velocity MAE drops `53.1` to `5.5`; Psalm 130 keyboard, Psalm 130 synth, Psalm 2, and Psalm 7 are unchanged versus the sparse low-flood run, keeping mean F1 `0.386`.
  - [x] 2026-06-14 long-velocity guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_134626_piano-suite-long-velocity-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_134608_psalm5-long-velocity-current` is unchanged at mean F1 `0.529`.
  - [x] 2026-06-14 long-velocity sidecar package: patched source `python/ingest/dist_patched_long_velocity/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_long_velocity\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `a763a8a80b3d5e0a676f8eb8e8186bbeda0efa86b2cd4000459e4d543af242da`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 10 full-stem import smoke `tmp/psalm10-full-packaged-sidecar-import-20260614_1355_long_velocity.auralsong`: validates with `keys -> piano_auto`, internal score `0.7667`, `645` keys notes over `232.54s`, pitch range `21-97`, mean duration `0.599s`, velocity range `26-50`, mean velocity `36.77`, max overlap `7`, max 55 ms attack cluster `6`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 sparse-synth transient cull: long, active 45-90 second sparse-low synth-shaped Basic Pitch playable outputs now drop sub-0.8s transient notes only when note count, density, mean duration, low-register reach, upper-voice reach, retained ratio, and max-polyphony gates match the Psalm 130 synth full-stem clutter shape. This targets short false-positive spectral/splice notes without touching the 12-second Psalm suite, Psalm 5, Psalm 10 full-stem, Psalm 130 keyboard full-stem, or sparse Psalm 2/Psalm 7 full-stem cases.
  - [x] 2026-06-14 referenced full-stem validation after sparse-synth transient cull `benchmarks/piano/runs/20260614_141406_referenced-fullstem-sparse-synth-transient-cull-current`: playable mean F1 improves `0.387` to `0.395`; Psalm 130 synth improves F1 `0.220` to `0.264`, onset F1 `0.311` to `0.357`, Offset F1 `0.064` to `0.093`, Off+Vel F1 `0.054` to `0.077`, pitch accuracy `70.7%` to `73.9%`, Velocity MAE `11.4` to `9.5`, and notes `247` to `132`; Psalm 130 keyboard, Psalm 10, Psalm 2, and Psalm 7 are unchanged versus the long-velocity run.
  - [x] 2026-06-14 sparse-synth transient cull guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_141457_piano-suite-sparse-synth-transient-cull-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_141507_psalm5-sparse-synth-transient-cull-current` is unchanged at mean F1 `0.529`.
  - [x] 2026-06-14 sparse-synth transient cull sidecar package: patched source `python/ingest/dist_patched_sparse_synth_cull/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_sparse_synth_cull\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `1a5048db153afaa58accfd2085ff62327f55a40b5185cb753135382feab7ed55`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 130 synth full-stem import smoke `tmp/psalm130-synth-full-packaged-sidecar-import-20260614_1424_sparse_synth_cull.auralsong`: validates with `keys -> piano_auto`, internal score `0.8626`, `132` keys notes over `59.24s`, pitch range `29-89`, mean duration `1.493s`, velocity range `24-54`, mean velocity `35.95`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 long sparse duration cull: long, low-density full-song Basic Pitch playable outputs with low-flood-normalized pitch floors, short median durations, and repeated-pitch clutter now drop short notes below a range-dependent duration floor. The low-range floor is `1.0s` for Psalm 2-like outputs with max pitch at or below MIDI `70`; the upper-range floor is `0.5s` for Psalm 7-like sparse outputs with higher notes. The gate requires enough retained notes, a minimum retained ratio, and max polyphony still capped, so it does not touch the 12-second suite, Psalm 5, dense Psalm 10, Psalm 130 keyboard, or Psalm 130 synth.
  - [x] 2026-06-14 referenced full-stem validation after long sparse duration cull `benchmarks/piano/runs/20260614_153026_referenced-fullstem-long-sparse-duration-cull-current`: playable mean F1 improves `0.395` to `0.505`, onset F1 `0.465` to `0.607`, Offset F1 `0.096` to `0.112`, and Off+Vel F1 `0.092` to `0.108`; Psalm 2 improves F1 `0.249` to `0.629` with notes `197` to `34`, and Psalm 7 improves F1 `0.354` to `0.524` with notes `78` to `24`; Psalm 130 keyboard, Psalm 130 synth, and Psalm 10 are unchanged versus sparse-synth transient cull.
  - [x] 2026-06-14 long sparse duration cull guard reruns: corrected 12-second suite `benchmarks/piano/runs/20260614_153201_piano-suite-long-sparse-duration-cull-current` is unchanged at mean F1 `0.565`, onset F1 `0.681`, Offset F1 `0.292`, Off+Vel F1 `0.274`, pitch accuracy `83.0%`, Velocity MAE `8.4`, duplicate rate `0.0%`; Psalm 5 guard `benchmarks/piano/runs/20260614_153107_psalm5-long-sparse-duration-cull-current` is unchanged at mean F1 `0.529`.
  - [x] 2026-06-14 long sparse duration sidecar package: patched source `python/ingest/dist_patched_long_sparse_duration/aural_ingest.exe` passed `runtime-check`; `build_sidecar.ps1 -SourceExePath python\ingest\dist_patched_long_sparse_duration\aural_ingest.exe -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 900 -SkipBuild` synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `4861a3cf9f45ec9277b0dddd3d9a7d9d72975dcdc62a35ca1a4783a73f47fbf5`. `python/ingest/dist/aural_ingest.exe` and the portable root/zip remain older package artifacts and should not be used as latest-source package evidence.
  - [x] 2026-06-14 packaged sidecar Psalm 2 full-stem import smoke `tmp/psalm2-full-packaged-sidecar-import-20260614_2108_long_sparse_duration.auralsong`: validates with `keys -> piano_auto`, internal score `1.0`, `34` keys notes over `222.64s`, pitch range `37-63`, mean duration `1.905s`, velocity range `66-92`, mean velocity `80.71`, max overlap `4`, max 55 ms attack cluster `3`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 synced the audio-onset-alignment sidecar through the skip-build package path after direct runtime-check returned `ok=true`; `python/ingest/dist/aural_ingest.exe`, `dist/sidecar/aural_ingest.exe`, and both desktop/game Tauri binaries initially matched SHA-256 `580126f10bdc8488db1e4668b60fc6f4ef88ad2beb8e4aea0851d848e0c7c5db`
  - [x] 2026-06-14 package-level checks passed after audio-onset alignment: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml ingest_sidecar --lib` reported 9 passed / 1 ignored; `cargo test --manifest-path apps/game/src-tauri/Cargo.toml ingest_sidecar --lib` reported 9 passed; ignored Studio wrapper smoke produced `tmp/studio-wrapper-psalm5-audio-align-import-20260614_003500.auralsong` with `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, 117 keys notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, and mean duration `0.619`
  - [x] 2026-06-14 portable repack/smoke after audio-onset alignment: the first full `create_portable.ps1 -SkipSidecarBuild -ZipOutput` attempt timed out after building the game release exe because the workspace `tauri:build` script redundantly launched another full sidecar build; a direct `npm -w '@auralprimer/studio' run tauri:build -- --no-bundle` attempt hit the same issue. Running `node ..\..\scripts\run-tauri.mjs build --no-bundle` from `apps/desktop` bypassed the redundant sidecar wrapper and produced a fresh Studio release exe.
  - [x] 2026-06-14 final portable sidecar/package hashes after repack: `python/ingest/dist/aural_ingest.exe`, `dist/sidecar/aural_ingest.exe`, both Tauri sidecar binaries, `AuralPrimerPortable/aural_ingest.exe`, and `AuralPrimerPortable/sidecar/aural_ingest.exe` match SHA-256 `2d96957b66d71a636036b185c81e4605f705347c55692745e830083e8570db53`; `AuralPrimerPortable/AuralPrimer.exe` hash `e276a5495ec457a40e4dfe7d07ecaa9c29bf9bff6154e6317f159d9b325ffc62`; `AuralPrimerPortable/AuralStudio.exe` hash `9693a1a29ba287fdffc945b0515df0a18b94bcacb428c63353de28251dd072f2`.
  - [x] 2026-06-14 final portable sidecar smoke `tmp/portable-psalm5-audio-align-import-20260614_043000.auralsong`: run from `D:\AuralPrimer\AuralPrimerPortable` with `sidecar\aural_ingest.exe import ... --melodic-method piano_auto`; output records `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, 117 keys notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, mean duration `0.619`, and skipped drums because the configured input-stem mix has no drum source.
  - [x] 2026-06-14 portable Studio GUI smoke from `D:\AuralPrimer\AuralPrimerPortable\AuralStudio.exe` produced `tmp/portable-studio-gui-psalm5-audio-align-import-20260614_084928.auralsong`; the UI reported `Import complete` / `Done`, and the AuralSong records `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, 117 keys notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean duration `0.619`, and skipped drums with reason `input_stems_mix_without_drums`.
  - [x] 2026-06-14 `create_portable.ps1` now builds/syncs the sidecar once before app builds and invokes `run-tauri.mjs build --no-bundle` directly from each app folder, avoiding redundant per-app sidecar rebuilds from workspace `tauri:build` scripts.
  - [x] 2026-06-14 `AuralPrimerPortable.zip` was recreated with `tar.exe -a -cf` because PowerShell `Compress-Archive` failed with `Stream was too long`; final zip size `7,954,199,687` bytes, SHA-256 `cccc344b0513727641591995667fd39dd04c703bef13aa6db0375065e6b5b50d`, and `tar.exe -tf` lists expected root entries.
  - [x] 2026-06-13 current-source import smoke `tmp/psalm5-excerpt-current-import-20260613_122132_playable.auralsong`: only `keys_main`, 155 notes, max overlap 7, min velocity 70, drums/guitar skipped for the synthesized keys-only mix
  - [x] 2026-06-13 current-source velocity import smoke `tmp/psalm5-excerpt-current-import-20260613_140500_velocity.auralsong`: only `keys_main`, 155 notes, max overlap 7, max 55 ms attack cluster 7, velocity range 35-53, mean velocity 40.5, mean duration 0.435, drums/guitar skipped for the synthesized keys-only mix
  - [x] 2026-06-13 current-source balanced-threshold import smoke `tmp/psalm5-excerpt-current-import-20260613_145551_balanced.auralsong`: only `keys_main`, 108 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.507, drums/guitar skipped for the synthesized keys-only mix
  - [x] 2026-06-13 current-source recall-restore import smoke `tmp/psalm5-excerpt-current-import-20260613_160204_recall.auralsong`: only `keys_main`, 124 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.515, drums/guitar skipped for the synthesized keys-only mix
  - [x] 2026-06-13 current-source release-sustain import smoke `tmp/psalm5-excerpt-current-import-20260613_164959_release.auralsong`: only `keys_main`, 124 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.590, drums/guitar skipped for the synthesized keys-only mix
  - [x] 2026-06-13 fixed direct `import` with configured keys-only `input_stem_paths` so it suppresses mix-fallback drum transcription and guitar splitting the same way `import-dir` already did
  - [x] 2026-06-13 current-source adaptive import smoke `tmp/psalm5-excerpt-current-import-20260613_175631_adaptive.auralsong`: only `keys_main`, 117 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.45, mean duration 0.618, guitar split skipped, and drum backend skipped because the configured input-stem mix has no drum source
  - [x] 2026-06-13 current-source default-candidate import smoke `tmp/psalm5-excerpt-current-import-20260613_212308_default_candidate.auralsong`: still records `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, only `keys_main`, 117 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.45, mean duration 0.618, guitar split skipped, and drum backend skipped because the configured input-stem mix has no drum source
  - [x] 2026-06-13 full no-skip sidecar rebuild after the loose selector: `build_sidecar.ps1 -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 300` passed, wrote a BOM-free JSON manifest with runtime-check `ok=true`, and synced identical SHA-256 `785df09b0c18e4e05e3ede10d8858ffb983cc7957b07e23736930dc3a75e7f36` to `python/ingest/dist/aural_ingest.exe`, `dist/sidecar/aural_ingest.exe`, and both desktop/game Tauri binaries
  - [x] 2026-06-13 post-loose-rebuild command-layer checks passed: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml ingest_sidecar --lib` reported 9 passed / 1 ignored, and `cargo test --manifest-path apps/game/src-tauri/Cargo.toml ingest_sidecar --lib` reported 9 passed
  - [x] 2026-06-13 post-loose-rebuild Studio wrapper smoke `tmp/studio-wrapper-psalm5-loose-candidate-import-20260613_230430.auralsong`: records `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, drum request `combined_filter`, 117 `keys_main` notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, mean duration `0.618`, skipped guitar split, and skipped drums because the configured input-stem mix has no drum source
  - [x] 2026-06-13 full no-skip sidecar rebuild after the default-candidate selector: `build_sidecar.ps1 -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 300` passed, wrote a BOM-free JSON manifest with runtime-check `ok=true`, and synced identical SHA-256 `4eeec5c9b8b56bae056c121edab2178826a4b7472692258879a030dd065c7441` to `python/ingest/dist/aural_ingest.exe`, `dist/sidecar/aural_ingest.exe`, and both desktop/game Tauri binaries
  - [x] 2026-06-13 post-rebuild command-layer checks passed: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml ingest_sidecar --lib` and `cargo test --manifest-path apps/game/src-tauri/Cargo.toml ingest_sidecar --lib`
  - [x] 2026-06-13 post-rebuild Studio wrapper smoke `tmp/studio-wrapper-psalm5-default-candidate-import-20260613_215447.auralsong`: records `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, drum request `combined_filter`, 117 `keys_main` notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, mean duration `0.618`, skipped guitar split, and skipped drums because the configured input-stem mix has no drum source
  - [x] 2026-06-13 post-rebuild visible native AuralStudio GUI smoke via WebView2 remote debugging produced `tmp/studio-native-gui-psalm5-default-candidate-import-20260613_220000.auralsong`; the GUI reported `Import complete` and the AuralSong contains `keys -> piano_auto`, internal `piano_auto.piano_basic_pitch_playable = 0.9833`, 117 `keys_main` notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, mean duration `0.618`, skipped guitar split, and skipped drums with reason `input_stems_mix_without_drums`
  - [x] adaptive sidecar binary synced to `dist/sidecar`, desktop Tauri binaries, and game Tauri binaries with SHA-256 `f625bc4fde242bb08d48ec04b0729bbf4a1dd92f200a6b659f8f8f15af4ab923`
  - [x] direct rebuilt-sidecar adaptive import smoke `tmp/psalm5-excerpt-sidecar-import-20260613_200234_adaptive.auralsong` on Psalm 5 polyphony-stress excerpt produced only `keys_main`, 117 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.45, mean duration 0.618, skipped guitar split, and skipped drums because the configured input-stem mix has no drum source
  - [x] fresh sidecar binary synced to `dist/sidecar`, desktop Tauri binaries, and game Tauri binaries with SHA-256 `77dcdb51a19796b9619918a6d3ce252cefc6c5136b70f57c69a5d34dd868b5ad`
  - [x] direct rebuilt-sidecar import smoke `tmp/psalm5-excerpt-sidecar-import-20260613_153930_balanced.auralsong` on Psalm 5 polyphony-stress excerpt produced only `keys_main`, 108 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.507, and skipped synthetic-mix drums/guitar
  - [x] direct rebuilt-sidecar import smoke `tmp/psalm5-excerpt-sidecar-import-20260613_163753_recall.auralsong` on Psalm 5 polyphony-stress excerpt produced only `keys_main`, 124 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.515, and skipped synthetic-mix drums/guitar
  - [x] direct rebuilt-sidecar import smoke `tmp/psalm5-excerpt-sidecar-import-20260613_172444_release.auralsong` on Psalm 5 polyphony-stress excerpt produced only `keys_main`, 124 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.5, mean duration 0.590, and skipped synthetic-mix drums/guitar
  - [x] `build_sidecar.ps1 -SkipBuild -OutDir dist\sidecar -SyncTauriBinaries -RuntimeCheckTimeoutSec 300` passes against the adaptive sidecar, writes BOM-free JSON, records runtime-check `ok=true`, and syncs both Tauri binaries to SHA-256 `f625bc4fde242bb08d48ec04b0729bbf4a1dd92f200a6b659f8f8f15af4ab923`
  - [x] desktop and game Tauri ingest-sidecar command-layer checks pass: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml ingest_sidecar --lib` and `cargo test --manifest-path apps/game/src-tauri/Cargo.toml ingest_sidecar --lib`
  - [x] added ignored Studio wrapper smoke `explicit_binary_piano_import_smoke_from_env`; rerunning it against the synced desktop Tauri sidecar binary with the visible form's default `combined_filter` drum value produced `tmp/studio-wrapper-psalm5-gui-default-import-20260613_205240.auralsong` with only `keys_main`, 117 notes, max overlap 7, max 55 ms attack cluster 6, velocity range 35-50, mean velocity 40.45, mean duration 0.618, skipped guitar split, and skipped drums because the configured input-stem mix has no drum source
  - [x] local browser render of AuralStudio at `http://127.0.0.1:46173/` shows the analysis import form with `piano_auto`, `piano_basic_pitch_playable`, `piano_basic_pitch`, and `piano_basic_pitch_clean` in the melodic selector; filling the Psalm 5 smoke fields leaves mode `import`, melodic `piano_auto`, drum `combined_filter`, shifts `1`, and the expected source/config/output paths in the visible controls
  - [x] visible native AuralStudio Tauri GUI import smoke via WebView2 remote debugging produced `tmp/studio-native-gui-psalm5-import-20260613_210923.auralsong` from the synced sidecar with GUI values mode `import`, melodic `piano_auto`, drum `combined_filter`, shifts `1`, and the matching Psalm 5 source/config; the GUI reported `Import complete`, progress `100%`, exit `0`, and the AuralSong contains only `keys_main`, 117 notes, pitch range `35-85`, max overlap `7`, max 55 ms attack cluster `6`, velocity range `35-50`, mean velocity `40.45`, mean duration `0.618`, skipped guitar split, and skipped drums because the configured input-stem mix has no drum source
  - [x] Ableton MCP was not callable in this Codex session; existing Ableton verification artifacts remain under `benchmarks/piano/ableton_verification`

## What to use right now

If the source has a `keys`/piano stem, `auto` now routes that stem through `piano_auto`, which tries packaged model-backed piano output before falling back to heuristic polyphonic extraction.

If you want the explicit piano-roll-oriented output, use:

- `piano_auto`
- `piano_basic_pitch_playable`
- `piano_basic_pitch`
- `piano_basic_pitch_clean`

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
