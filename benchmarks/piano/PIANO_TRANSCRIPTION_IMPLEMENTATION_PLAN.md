# Piano Transcription Implementation Plan

This plan turns the piano transcription epic into an execution queue. When continuing piano work, take the first incomplete task in the queue, implement it, run the listed validation, then update this file and `PIANO_TRANSCRIPTION_EPIC.md`.

## Operating rule

- Work top-to-bottom through the execution queue.
- Do not change the default `piano_auto` ordering based only on synthetic tests; require real-song A/B evidence first.
- Every algorithm change should add or update at least one regression test that captures the failure mode.
- Every real-song benchmark run should leave notes in this file: source, method, observed failure, and whether the output is better, worse, or unchanged.
- Portable builds happen only after a user-visible quality improvement or app-flow fix is verified.

## Current next task

Test `D:\AuralPrimer\AuralPrimerPortable\AuralPrimer.exe` with a fresh piano-heavy import. Post-portable review showed `auto` on `keys` stems could still route through a monophonic melodic extractor, so `auto` for `keys` now prefers `piano_auto`/`piano_polyphonic_clean` before legacy fallback, and the portable package has been rebuilt.

## Execution Queue

| Status | Task | Acceptance Gate |
| --- | --- | --- |
| Done | Create real piano benchmark manifest | Manifest includes piano-only, piano-stem, bass-clef-heavy, repeated-note, sustain-heavy, and sparse/chordal cases. |
| Done | Run baseline A/B benchmark | Reports exist for `piano_auto`, `piano_polyphonic_clean`, `melodic_hpss_combined`, and `melodic_octave_fix`; worst passages are identified. |
| Done | Add benchmark observations | This file records concrete failures with timestamps or measure ranges where possible. |
| Partial | Tune low-register cleanup | Bass-clef octave doubling and muddy chord artifacts improve without deleting intentional octaves. |
| Partial | Tune broad pitch false positives | Piano-specific output should stop spanning 21-108 on ordinary excerpts unless the source audibly uses that range. |
| Partial | Tune sustain and note endings | Pedal-heavy passages sound less chopped or smeared, with regression coverage for long resonance tails. |
| Partial | Tune repeated-note handling | Attack-frame candidate generation is implemented and covered by regression tests; Psalm 2 remains weak because pitch selection favors upper harmonics. |
| Done | Tune velocity shaping | Pitch-aware/onset-aware linear velocity blend passes tests and improves mean Velocity MAE versus the frozen baseline. |
| Done | Integrate first research model candidate | Transkun, PTI, and hFT are wired as optional methods, map temp MIDI into the note schema, and fail safely when unavailable. |
| Done | Compare research model output | Final benchmark includes all clean research methods; local run records unavailable optional dependencies/checkpoints as explicit errors. |
| Done | Decide `piano_auto` default order | Keep `piano_auto` conservative internally, but route `auto` keys stems through the piano path first because game playback requires polyphony. |
| Done | Build updated portable package | Portable folder and zip were rebuilt; sidecar `runtime-check` passes from inside the portable root. |

## Finish Plan

Use this section as the remaining piano work contract. The goal is to stop tuning once the best available piano path is clearly labeled, benchmarked, listenable, and packaged, even if it is still not the default.

### Completion gates

- A final piano benchmark run exists with `piano_auto`, `piano_polyphonic_clean`, the best research candidate if available, `melodic_hpss_combined`, and `melodic_octave_fix`.
- The final run does not regress `piano_polyphonic_clean` below the latest stable baseline: mean F1 `0.047`, Psalm 130 keyboard F1 `0.050`, Psalm 10 keyboard F1 `0.094`, and Psalm 7 keyboard F1 `0.061`, unless the run is explicitly marked as an experimental rejection.
- The no-reference Psalm 6 piano-only case stays inside the 88-key range and does not return to the pre-cleanup dense output shape; target range should remain close to `31-96` with no duplicate-rate spike.
- At least one user-listenable MIDI artifact is exported for every benchmark case under the final run's `predictions` folder.
- `piano_auto` is promoted only if piano-specific output is better by benchmark and listening evidence; otherwise it stays experimental and the app should make A/B selection explicit.
- Portable build happens only after targeted tests, real benchmark, import smoke, and sidecar packaging checks pass.

### Remaining passes

1. Finish repeated-note candidate generation.
   - Implement onset-aligned pitch candidate generation in `piano_polyphonic`, focused on Psalm 2 keyboard.
   - Do not broaden post-cleanup same-pitch merging; that already regressed Psalm 130.
   - Gate: Psalm 2 `piano_polyphonic_clean` improves over F1 `0.024` without Psalm 130 keyboard dropping below F1 `0.050`.
   - Result 2026-05-01: implemented conservative attack-frame candidate injection. Final exact Psalm 2 F1 is `0.0241`, Psalm 130 keyboard is `0.0503`; improvement is too small to justify default promotion.

2. Finish velocity shaping.
   - Replace the current mostly energy-linear velocity blend with pitch-aware and onset-energy-aware scaling.
   - Keep accents, reduce mechanical sameness, and avoid over-loud bass artifacts.
   - Gate: targeted velocity tests pass, Velocity MAE does not worsen materially on referenced cases, and listening artifacts sound less flat.
   - Result 2026-05-01: implemented pitch-aware/onset-aware linear blend. `piano_polyphonic_clean` mean Velocity MAE improved from frozen baseline `38.26` to `34.76`.

3. Final sustain and low-register review.
   - Use listening review on Psalm 130 keyboard/synth and Psalm 6 piano-only before changing thresholds again.
   - Only tighten low notes if residual lows are clearly false; avoid hard cutoffs because real bass-clef notes exist in the suite.
   - Gate: no high/low pitch-range regression and no repeated-note smear caused by sustain extension.

4. Integrate one research candidate.
   - Try the most packageable Windows candidate first, behind an optional method such as `piano_research_auto` or the existing scaffolded model name.
   - The integration must fail safely when model files or dependencies are unavailable.
   - Gate: mocked parser/smoke test, optional availability reporting, real benchmark comparison, and no required dependency added to normal import.
   - Result 2026-05-01: integrated all scaffolded names instead of one new name: `piano_transkun`, `piano_pti`, and `piano_hft`, plus clean variants. All are optional and fail safely.

5. Decide final default and UI labels.
   - If a piano-specific path wins, set or keep `piano_auto` accordingly.
   - If legacy methods still win, keep piano methods available as explicit A/B choices and label them experimental.
   - Gate: the decision is recorded in this file with benchmark run path, listening notes, and the selected default order.
   - Result 2026-05-01: do not promote. Final run `20260501_150045_piano-finish-experimental-v2` has `melodic_octave_fix` mean F1 `0.079`, `melodic_hpss_combined` `0.072`, and `piano_polyphonic_clean` `0.047`.

6. Build and verify portable.
   - Run the import smoke path with an existing split-stems folder and one single-file analysis import.
   - Verify the visualizer still shows piano-roll output for keys and the sidecar path resolves in the portable app.
   - Gate: portable package runs on Windows without missing sidecar/modelpack errors.
   - Initial result 2026-05-01: import smoke subset passed, but sidecar packaging was blocked because `dist/sidecar-check/aural_ingest.exe runtime-check` exited nonzero with missing required `basic_pitch_model`.
   - Result 2026-05-01 later: installed `basic-pitch` without dependencies to supply the model asset, added package-root model discovery, explicitly bundled the model files in PyInstaller, rebuilt sidecar, and created `D:\AuralPrimer\AuralPrimerPortable.zip`. Portable sidecar `runtime-check` now passes.

## Phase 1: Real Piano Corpus

Build the manifest before more tuning. Synthetic tests caught basic regressions; the next quality gains require real audio failure cases.

Required case types:

- Piano-only song with clean melody and accompaniment.
- Piano stem from a full mix.
- Dense bass-clef passage with left-hand octaves or fifths.
- Repeated-note passage with audible re-attacks.
- Sustain-heavy passage with overlapping resonance.
- Sparse chordal passage where false ghost notes are easy to hear.

Validation:

```powershell
python benchmarks/piano/run_piano_regression.py --manifest benchmarks/piano/piano_suite_manifest.json
```

## Phase 2: Baseline A/B Report

Run all currently useful methods before making more cleanup changes.

Methods to compare:

- `piano_auto`
- `piano_polyphonic_clean`
- `melodic_hpss_combined`
- `melodic_octave_fix`

For each case, capture:

- Best method.
- Worst method.
- Audible doubling problems.
- Missing attacks.
- Bad note endings.
- Mechanical or incorrect velocity.
- Whether the issue is visible in piano roll, audible in playback, or both.

## Phase 3: Cleanup Passes

Implement cleanup improvements in this priority order because each pass depends on clearer failure labels from the benchmark:

1. Low-register false octave and chord-smear pruning.
2. Sustain-end cleanup and pedal proxy behavior.
3. Same-pitch repeated attack split/merge threshold tuning.
4. Velocity shaping and accent preservation.

Acceptance:

- Targeted unit tests pass.
- Benchmark reports do not regress cleaner songs.
- Listening notes show at least one real-case improvement.

## Phase 4: Research Model Integration

After the in-repo heuristic pipeline has a stable baseline, add one external piano model behind an optional method. The first candidate should be whichever can be packaged and invoked most reliably on Windows.

Integration requirements:

- Optional dependency or modelpack check; no hard failure when unavailable.
- CLI/import method name such as `piano_transkun` or `piano_research_auto`.
- Converts output into the existing melodic note schema.
- Clamps to 88-key piano range unless explicitly configured otherwise.
- Includes a tiny smoke test with mocked output parsing.
- Adds benchmark comparison against the real manifest.

## Phase 5: App And Import Flow

Expose enough metadata that A/B testing is not guesswork.

App/import requirements:

- Analysis import records selected piano method in songpack metadata.
- Review flow shows which method generated each MIDI/chart.
- If multiple candidates are generated, output names make comparison obvious.
- Portable package includes the working sidecar and does not point at missing dev-only executables.

## Validation Commands

Targeted tests:

```powershell
python -m pytest python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_polyphonic.py
```

Real-song benchmark:

```powershell
py -3 benchmarks/piano/run_piano_regression.py --manifest benchmarks/piano/piano_suite_manifest.json
```

Import smoke test:

```powershell
python -m pytest python/ingest/tests -k "piano or melodic"
```

## Benchmark Notes

Add dated notes here after each real-song run.

### 2026-04-30

- Plan created.
- Created `benchmarks/piano/piano_suite_manifest.json` with 12-second windows across Psalm 130 keyboard/synth, Psalm 10 keyboard, Psalm 2 keyboard, Psalm 7 keyboard, and a no-reference Psalm 6 piano-only listening case.
- Extended the benchmark runner to support no-reference listening cases and to emit prediction MIDI/JSON artifacts under each run's `predictions` folder.
- Fixed reference window trimming so pre-window MIDI events are excluded instead of clamped to time 0.
- Fixed benchmark runtime bottlenecks by disabling expensive HPSS in `piano_polyphonic` unless `AURAL_PIANO_POLYPHONIC_HPSS=1`, using RMS-only cleanup attacks by default, replacing per-note STFT fundamental checks with direct correlation, and adding dense harmonic-shadow cleanup.
- Previous density run: `benchmarks/piano/runs/20260430_161801_piano-density-cleanup-v1`.
- Previous density run aggregate: `piano_auto` mean F1 `0.035`, `piano_polyphonic_clean` mean F1 `0.034`, `melodic_hpss_combined` mean F1 `0.072`, `melodic_octave_fix` mean F1 `0.079`.
- Density improved, but pitch selection is still too broad. Example: Psalm 6 piano-only dropped from roughly 650 piano-specific notes to roughly 250 notes in 12 seconds, but still spans `21-108`.
- Current evidence says do not promote the piano-specific path as "better" than legacy melodic methods yet. Keep it as the experimental piano A/B path until pitch selection improves.
- Next action is pitch false-positive cleanup, especially high harmonic shadows and unsupported low-register notes that survive dense-cluster pruning.
- Added audio-aware unsupported extreme pruning and mixed-cluster extreme pitch spray pruning.
- Pitch-support run: `benchmarks/piano/runs/20260430_181958_piano-pitch-support-cleanup-v1`.
- Pitch-support aggregate: `piano_auto` mean F1 `0.043`, `piano_polyphonic_clean` mean F1 `0.041`, `melodic_hpss_combined` mean F1 `0.072`, `melodic_octave_fix` mean F1 `0.079`.
- Pitch range improved on the no-reference Psalm 6 piano-only excerpt from `21-108` before pitch cleanup to `29-96`; current suite has no `piano_polyphonic_clean` predictions above MIDI `96`.
- Note density also improved: Psalm 6 piano-only `piano_polyphonic_clean` is now `199` notes over 12 seconds versus roughly `253` after density cleanup and roughly `650` before cleanup.
- Added low-register support gating for MIDI `29-35`; this fixed a boundary bug where unsupported notes near the bass edge bypassed the stronger low-note evidence check.
- Added low harmonic-shadow pruning for fifth/twelfth/octave-related bass artifacts while preserving strong supported boundary notes around MIDI `34-35`.
- Low-register run: `benchmarks/piano/runs/20260430_210845_piano-low-register-cleanup-v1`.
- Low-register aggregate: `piano_auto` mean F1 `0.043`, `piano_polyphonic_clean` mean F1 `0.042`, `melodic_hpss_combined` mean F1 `0.072`, `melodic_octave_fix` mean F1 `0.079`.
- Low-register cleanup removed `piano_polyphonic_clean` notes below MIDI `36` from Psalm 10 keyboard and Psalm 7 keyboard, preserved referenced Psalm 2 low `35`, and narrowed the Psalm 6 no-reference piano-only range to `31-96`.
- Remaining risk: Psalm 130 keyboard/synth still contains low-register predictions below MIDI `36`. Some may be real bass-clef material, so review the latest MIDI artifacts before using a harder low cutoff.
- Added bounded audio-tail sustain cleanup using per-note pitch-band RMS. It extends note endings while the pitch band keeps decaying, caps before the next same-pitch attack, and keeps the static fallback when no stem audio is available.
- Audio-tail run: `benchmarks/piano/runs/20260430_212127_piano-audio-sustain-cleanup-v1`.
- Audio-tail aggregate: `piano_auto` mean F1 `0.043`, `piano_polyphonic_clean` mean F1 `0.042`, `melodic_hpss_combined` mean F1 `0.072`, `melodic_octave_fix` mean F1 `0.079`.
- Audio-tail sustain produced a small `piano_polyphonic_clean` offset improvement from `0.017` to `0.018` mean Offset F1; Psalm 10 keyboard improved from `0.062` to `0.067` Offset F1. The no-reference Psalm 6 piano-only mean duration increased from `0.30s` to `0.33s` without increasing note count or pitch range.
- Remaining risk: sustain still does not solve the main pitch/onset mismatch, and legacy melodic methods still score higher on the referenced suite. Do not promote the piano path yet.
- Added conservative per-pitch close-chatter merging so same-pitch micro-duplicates can merge even when interleaved with other chord tones, while audio attacks still block merges across real reattacks.
- Tested broader same-pitch chatter windows and rejected them because they improved Psalm 2 but regressed Psalm 130 keyboard/synth too heavily.
- Latest run: `benchmarks/piano/runs/20260430_220556_piano-close-chatter-merge-v1`.
- Aggregate latest run: `piano_auto` mean F1 `0.047`, `piano_polyphonic_clean` mean F1 `0.047`, `melodic_hpss_combined` mean F1 `0.072`, `melodic_octave_fix` mean F1 `0.079`.
- Close-chatter cleanup improved `piano_polyphonic_clean` Psalm 130 keyboard F1 from `0.044` to `0.050` and Psalm 7 keyboard F1 from `0.037` to `0.061`, while keeping Psalm 10 roughly stable at `0.094`.
- Remaining risk: Psalm 2 repeated-note quality is still poor (`piano_polyphonic_clean` F1 `0.024`). The next repeated-note pass needs better onset-aligned pitch candidate generation, not more aggressive post-cleanup merging.

### 2026-05-01

- Frozen stable baseline remains `benchmarks/piano/runs/20260430_220556_piano-close-chatter-merge-v1`: `piano_polyphonic_clean` mean F1 `0.04736`, Psalm 130 keyboard `0.050`, Psalm 10 `0.094`, Psalm 7 `0.061`, Psalm 2 `0.024`.
- Added shared MIDI decode support for external piano model output. It handles tempo changes, running status, note-on/off pairs, velocity preservation, instrument tagging, and 88-key clamping.
- Wired optional research adapters:
  - `piano_transkun` runs the documented Transkun-style CLI into temp MIDI, then decodes it.
  - `piano_pti` uses `PianoTranscription` and upstream `sample_rate`, writes temp MIDI, then decodes it. It requires an explicit checkpoint or downloader opt-in because Windows support is risky.
  - `piano_hft` is checkpoint/command backed via `AURAL_PIANO_HFT_CHECKPOINT` and `AURAL_PIANO_HFT_COMMAND`; unavailable configs fail safely.
- Added tests for MIDI decode, missing optional dependencies/checkpoints, clean research cleanup calls, repeated chord reattacks, and pitch-aware velocity shaping.
- Tightened `piano_polyphonic` attack-frame candidate injection to avoid flooding dense passages. Final exact Psalm 2 F1 is `0.0241`, which is only a minimal lift and not a meaningful quality win.
- Replaced square-root velocity scaling with pitch-aware/onset-aware linear blending. `piano_polyphonic_clean` mean Velocity MAE improved to `34.76` versus frozen baseline `38.26`.
- Final experimental run: `benchmarks/piano/runs/20260501_150045_piano-finish-experimental-v2`.
- Final aggregate: `piano_auto` mean F1 `0.04690`, `piano_polyphonic_clean` `0.04728`, `melodic_hpss_combined` `0.07210`, `melodic_octave_fix` `0.07874`.
- Final `piano_polyphonic_clean` guard cases: Psalm 130 keyboard `0.0503`, Psalm 10 keyboard `0.0939`, Psalm 2 keyboard `0.0241`, Psalm 7 keyboard `0.0606`, Psalm 6 no-reference `158` notes with pitch range `31-96` and duplicate rate `0.0%`.
- Research adapter benchmark result: local runtime lacks Transkun and PTI packages, and hFT lacks `AURAL_PIANO_HFT_CHECKPOINT`; all three report clear benchmark errors instead of breaking import.
- Decision: do not promote `piano_auto` as the preferred/default quality path. Keep piano methods available for explicit A/B testing and piano-roll use; legacy `melodic_octave_fix` remains the referenced benchmark winner.
- Validation passed: 58 focused piano/orchestration tests, four import smoke tests covering split-folder/configured-stem/directory import paths, and final benchmark artifact export.
- Portable build was not produced. `build_sidecar.ps1 -SkipBuild -OutDir dist/sidecar-check` runs, but `dist/sidecar-check/aural_ingest.exe runtime-check` exits nonzero because the required `basic_pitch_model` asset is missing.
- Follow-up portable build:
  - Installed `basic-pitch==0.4.0` with `--no-deps` for its bundled `nmp.onnx`/`nmp.tflite` model assets because normal install is incompatible with Python 3.13 TensorFlow pins.
  - Added Basic Pitch package-directory model discovery and explicit PyInstaller model-data inclusion.
  - Rebuilt fresh sidecar and app shells, then packaged `D:\AuralPrimer\AuralPrimerPortable` and `D:\AuralPrimer\AuralPrimerPortable.zip`.
  - Portable artifact hashes are recorded in `D:\AuralPrimer\AuralPrimerPortable\portable_manifest.json`; sidecar hash `d86302e9dafbaf124355a163ff0d10aed26ab031718fc9f0f3a596631a570ee7`.
  - Verified `D:\AuralPrimer\AuralPrimerPortable\sidecar\aural_ingest.exe runtime-check` exits `0`.
- Post-portable game review found the Psalm 130 Keys MIDI was monophonic because `auto` on `keys` preferred `melodic_adaptive`. Changed `melodic_fallback_chain("auto", instrument="keys")` to prefer `piano_auto` and `piano_polyphonic_clean` before legacy fallbacks, and added a regression test proving a three-note chord does not fall through to `melodic_adaptive`.
- Rebuilt portable again after the keys-routing fix. New sidecar hash: `03f48f964c78c8893f3b9da1a492dfc0a9f743fa5d069914c62371c49171ffe2`; portable `runtime-check` passes from `D:\AuralPrimer\AuralPrimerPortable`.
- Packaged smoke confirmed `--melodic-method auto` records `keys -> piano_auto` in `instrument_melodic_methods_used`. Direct transcription of the existing Psalm 130 keys stem with the fixed route returned `2013` notes with max overlap `26`, so the previous monophonic collapse is no longer the active path.
