# Piano MIDI Refinement Workbench

Status: MVP implemented; real-song Suno review artifacts exist for Psalm 7, Psalm 10, and Psalm 130, including the latest Psalm 10 dense-high staccato, Psalm 130 keyboard dense-low spectral, and Psalm 130 synth sparse-low spectral candidate reviews; source now also has a dense-low spectral upper-voice restore for the Psalm 130 keyboard failure shape, a dense-high staccato tail trim for the Psalm 10 ending failure shape, a default-fewer low-artifact selector for sparse full-stem Psalm 10/Psalm 2/Psalm 7 failure shapes, local sparse-low spectral windows plus a sparse-synth transient cull for the Psalm 130 synth full-stem failure shape, a sparse low-flood clamp and long sparse duration cull for the Psalm 2/Psalm 7 full-stem clutter shapes, and a long full-song velocity calibration for the Psalm 10 full-stem velocity failure. Broader Psalm 5 validation now includes 45-second and full-stem packaged smokes; the full-stem smoke keeps playable polyphony and velocity caps, while the 45-second excerpt-only smoke still needs high-velocity listening review. The synced sidecar includes the latest long sparse duration cull source; the portable root/zip still need a refresh if this build is promoted.

## Goal

Create a standalone tool for improving and reviewing piano MIDI when a source MIDI is already close but not trustworthy enough for learning or gameplay. The first target is Suno-style piano MIDI plus matching piano/keys audio.

The workbench must not replace normal import. It is a per-song refinement and A/B review flow that produces artifacts a user can inspect, listen to, and manually choose from.

## Primary User Story

Given:

- a piano/keys audio stem or piano-only audio file
- a source MIDI file, usually from Suno
- optionally, a hand-authored note-for-note reference MIDI

The user can run one command and get:

- normalized source MIDI baseline
- source MIDI after cleanup
- multiple audio-derived piano transcription candidates
- optional reference-backed scores
- no-reference diagnostics when truth MIDI is absent
- a static dashboard that works by opening a local HTML file directly
- a focused before/after playability visual report for measuring cleanup impact
- MIDI artifacts for every candidate

## Non-Goals

- Do not mutate the original Suno/source MIDI.
- Do not automatically change `gameplay_default`.
- Do not require optional research model packages for a successful run.
- Do not ship research-only datasets or derived in-game content.
- Do not force this through AuralSong import; AuralSong replacement can come later.

## Inputs

- Required `--audio`: piano/keys WAV or compatible audio path used by transcription candidates.
- Required `--source-midi`: source MIDI to refine and compare against.
- Optional `--reference-midi`: note-for-note truth MIDI for objective evaluation.
- Optional `--methods`: comma-separated or repeatable candidate methods.
- Optional `--label`: run label for artifact folder naming.
- Optional `--out-root`: output directory root.
- Optional `--tolerance-ms`, `--offset-tolerance-ms`, and `--velocity-tolerance`.

## Candidate Methods

Always available:

- `source_midi`: decoded source MIDI, normalized into the workbench format.
- `source_midi_clean`: source MIDI run through existing piano cleanup.
- `source_midi_playable`: source MIDI reduced for practical playability.
- `source_midi_clean_playable`: cleaned source MIDI reduced for practical playability.

Playable reduction follows this policy:

- target high-water mark is 5-7 simultaneous notes
- default hard cap is 7 simultaneous notes
- preserve the highest note in each attack cluster as the right-hand melody candidate
- preserve the lowest useful left-hand note as bass/support
- fill remaining room with strong right-hand and mid-register chord tones
- reject excess inner notes, muddy low-register notes, and sustained overlaps that push beyond the cap

Audio-derived candidates use the existing melodic/piano registry:

- `piano_auto`
- `piano_basic_pitch_playable`
- `piano_basic_pitch`
- `piano_basic_pitch_clean`
- `piano_polyphonic_clean`
- `piano_polyphonic`
- `piano_transkun_clean`
- `piano_pti_clean`
- `piano_hft_clean`
- legacy melodic comparators when explicitly requested

Missing optional model-backed candidates must record a clear error and allow the rest of the run to complete.

Normal AuralSong import also applies the same keys playability policy after transcription. For dense Basic Pitch output, `piano_basic_pitch_playable` first uses balanced piano thresholds, then applies density-triggered confidence pruning, capped sustain extension, guarded velocity calibration, a constrained default-threshold recall restore, and post-restore release-sustain passes for dense mid/high, sparse chordal, and dense low-register keys notes. It can lazily rerun one alternate Basic Pitch threshold profile when the balanced output's own density/cluster/register features match cases that benchmark better with a stricter profile, including a sparse low-register synth-like gate that tries the existing mid profile when the balanced playable output has only a few high notes. It can fall back to default Basic Pitch plus the same playable cleanup when no-reference plausibility shows the selected profile is over-pruning sparse material, when sparse low-artifact full-stem candidates tie on no-reference score but the default candidate has materially fewer notes, or when dense material only needs a small recall gain, and it can use a gated loose profile for sparse outputs where the loose playable result removes low-register artifacts without reopening dense-note clutter. For dense, short-decay, low-register keys outputs it can also apply a guarded audio-onset alignment pass that shifts attack clusters toward nearby audio onset peaks only when the max-polyphony cap remains intact; for sparse low-register outputs with overlong high tails it can trim and soften those tails, and for sparse low-register synth-like outputs with too few high notes it can add bounded octave-restored treble companions. For sparse low-register synth-like outputs where Basic Pitch anchors imply chord attacks but miss the upper voicing, it can replace the selected notes with a bounded spectral chord candidate that uses FFT-supported chord tones, preserves isolated high pickups, skips continuation clusters, and keeps the max-polyphony cap; on longer full-song outputs, the same spectral candidate can run over overlapping local windows and splice accepted candidate spans back into the full song. For long active sparse-synth outputs with short-note clutter, it can remove sub-0.8s transients after note selection when note count, span, density, mean duration, low-register reach, upper-voice reach, retained ratio, and max-polyphony gates match the Psalm 130 synth full-stem failure shape. For long low-density full-song outputs with a low-flood-normalized pitch floor, short median durations, and repeated-pitch clutter, it can drop short notes below a range-dependent duration floor while preserving enough retained notes and capped polyphony; this targets the Psalm 2/Psalm 7 full-stem sparse clutter shape. For dense low-register keyboard outputs where Basic Pitch keeps the anchors but under-recovers upper chord voices, it can add a tightly gated set of FFT-supported, interval-related upper tones while preserving the max-polyphony and attack-cluster caps. For dense high-register staccato-like outputs, it can trim over-sustained high-note tails without changing note count or the sparse/low-register guards. For long high-confidence full-song outputs with over-hot velocities, it can scale velocities down after final note selection when note count, span, density, median velocity, and minimum velocity gates match the Psalm 10 full-stem failure shape. That arranges `piano_auto` output toward the playable cap with less weak-note clutter and less over-loud model-confidence velocity, while recovering confident dense-passage notes, longer releases, sparse/dense chord voicings, dense staccato endings, local full-song synth voicings, sparse-synth transient control, long sparse duration control, and long full-song velocity behavior that still fit the max-polyphony and attack-cluster caps.

## Evaluation Modes

## Research Notes

This workbench treats playable reduction as arrangement, not transcription truth. The practical rule comes from common piano arranging guidance: keyboard-style accompaniments often use a bass line in the left hand with a few right-hand chord voices around the melody, left-hand accompaniment should stay simple enough not to cover the melody, and voicing commonly omits or redistributes chord tones instead of preserving every possible note.

### Reference Available

When `--reference-midi` is provided, candidate ranking prioritizes objective piano metrics:

- exact note F1
- onset-only F1
- note+offset F1
- pitch accuracy
- velocity MAE
- duplicate/chatter rate

The dashboard should still show source comparison so the user can see how much the candidate diverged from Suno.

### No Reference

When no truth MIDI is available, the tool ranks candidates only as a review hint. It must not claim truth.

Diagnostics include:

- agreement with source MIDI
- duplicate/chatter rate
- max polyphony
- whether max polyphony exceeds the practical 5-7-note playability range
- left-hand/right-hand balance
- low-register muddy-note pressure
- pitch range
- note density

The conservative default recommendation in no-reference mode should favor `source_midi_clean_playable` when present. If a playable candidate is not present, fall back to `source_midi_clean` unless another candidate has strong source agreement and better diagnostics.

## Required Artifacts

Each run writes under `benchmarks/piano/refinement_runs/<timestamp>_<label>` by default:

- `summary.json`: complete machine-readable run data
- `report.md`: concise review summary
- `refinement_dashboard.html`: static single-page review UI
- `playability_report.html`: focused before/after playability visual report
- `playability_metrics.svg`: before/after note count, duplicate, hand-balance, and polyphony metrics
- `playability_polyphony.svg`: before/after polyphony timeline with the 7-note playability cap
- `playability_roll.svg`: focused piano-roll diff around the densest before-cleanup window
- `playability_audition_before.wav`: synthesized MIDI preview of the focused before section
- `playability_audition_after.wav`: synthesized MIDI preview of the focused after section
- `playability_audition_ab.wav`: synthesized A/B preview that plays before, a short gap, then after
- `candidates/<method>.mid`: normalized MIDI for each successful candidate
- `candidates/<method>.notes.json`: decoded candidate notes
- `candidates/index.json`: candidate artifact index

## CLI

```powershell
py -3 -m aural_ingest.cli refine-piano `
  --audio D:\Songs\SongA\keys.wav `
  --source-midi D:\Songs\SongA\suno_keys.mid `
  --reference-midi D:\Songs\SongA\truth.mid `
  --method source_midi,source_midi_clean,source_midi_clean_playable,piano_polyphonic_clean,piano_auto `
  --label song-a-refine
```

Without a truth MIDI, omit `--reference-midi`. The recommendation then becomes a conservative review hint, not an objective winner.

## Dashboard Requirements

The dashboard must work from disk with no server and no external URLs.

It should show:

- candidate score table
- recommended candidate and recommendation basis
- source-vs-candidate metrics
- reference metrics when available
- diagnostics and risk flags
- piano-roll style overlay for selected candidate
- diff counts for missing notes, extra notes, and duplicate/chatter

The focused playability report should show the specific impact of the playable cleanup pass:

- max polyphony before and after
- total note count before and after
- duplicate/chatter change
- source MIDI F1 and source offset F1, so playability gains are visible alongside source-agreement tradeoffs
- left-hand and right-hand note balance
- a timeline showing whether the output stays under the 5-7 note practical playability range
- a piano-roll window around the densest source passage so removed/kept notes are visually obvious
- audio controls for synthesized before, after, and before-then-after focused section previews

## Acceptance Criteria

- Running with only source MIDI and audio produces all required artifacts.
- Running with reference MIDI produces objective scores and ranks candidates by reference F1.
- Missing optional methods do not fail the run.
- Original input MIDI is never modified.
- Every successful candidate writes playable MIDI.
- The feature is callable from the sidecar CLI as `refine-piano`.
- Tests cover source cleanup, reference scoring, missing candidate errors, artifact generation, and parser/CLI wiring.

## Validation

- [x] Focused tests: `py -3 -m pytest --no-cov python/ingest/tests/test_piano_refinement.py python/ingest/tests/test_cli_misc.py -q`
- [x] Relevant piano/transcription shard: `py -3 -m pytest --no-cov python/ingest/tests/test_piano_refinement.py python/ingest/tests/test_cli_misc.py python/ingest/tests/test_piano_benchmark.py python/ingest/tests/test_piano_cleanup.py python/ingest/tests/test_piano_research_adapters.py python/ingest/tests/test_transcription_orchestration.py -q`
- [x] CLI smoke with `source_midi` and `source_midi_clean` wrote the required artifacts under a temp run directory.
- [x] Playability pass validation: `source_midi_clean_playable` caps Psalm 5 keyboard cleanup output at max polyphony `7` and clears the `playability_polyphony` risk flag.
- [x] Before/after visual report generation: every workbench run writes `playability_report.html` plus static metric, polyphony, piano-roll SVGs, and focused A/B audition WAVs.
- [x] Studio Refine candidate decision writing typechecks after carrying the required accepted-candidate fields through `saveDecisions`, and the portable Studio GUI import smoke passes on the refreshed audio-align package.
- [x] Registry-backed methods ending in `_playable` now run through the actual melodic registry instead of the generic source-MIDI playable reducer; corrected Psalm 7 reference-backed review run `benchmarks/piano/refinement_runs/20260614_052857_psalm7-reference-backed-review-corrected-offset` shows `piano_basic_pitch_playable` as 27 notes / max polyphony `4` / reference F1 `0.533`, and its playability report compares `source_midi_clean_playable` before against `piano_basic_pitch_playable` after with the original source-audio excerpt included as `playability_audition_source.wav`.
- [x] Psalm 130 synth sparse-low spectral review run `benchmarks/piano/refinement_runs/20260614_100423_psalm130-synth-sparse-low-spectral-candidate-review` recommends `piano_basic_pitch_playable` with reference F1 `0.547`, Offset F1 `0.505`, source F1 `0.196`, `44` notes, duplicate rate `0.0%`, max polyphony `7`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
- [x] Psalm 10 dense-high staccato review run `benchmarks/piano/refinement_runs/20260614_113904_psalm10-keyboard-dense-high-staccato-review` recommends `piano_basic_pitch_playable` with reference F1 `0.597`, Offset F1 `0.465`, raw-source F1 `0.584`, `135` notes, duplicate rate `0.0%`, max polyphony `6`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
- [x] Psalm 130 keyboard dense-low spectral review run `benchmarks/piano/refinement_runs/20260614_113935_psalm130-keyboard-dense-low-spectral-review` recommends `piano_basic_pitch_playable` with reference F1 `0.531`, Offset F1 `0.117`, raw-source F1 `0.481`, `130` notes, duplicate rate `0.0%`, max polyphony `7`, source/before/after/AB audio previews, piano-roll visuals, and candidate MIDI.
- [x] Broader Psalm 5 45-second validation `benchmarks/piano/runs/20260614_114048_psalm5-45s-dense-high-staccato-validation` gives `piano_auto` / `piano_basic_pitch_playable` F1 `0.774`, onset F1 `0.817`, Offset F1 `0.258`, pitch accuracy `94.7%`, duplicate rate `0.0%`, and `160` notes versus raw Basic Pitch F1 `0.620`, onset F1 `0.637`, Offset F1 `0.101`, and `239` notes.
- [x] Packaged sidecar Psalm 5 45-second import smoke `tmp/psalm5-45s-packaged-sidecar-import-20260614_1141_dense_high_staccato.auralsong` validates with `keys -> piano_auto`, internal score `0.9292`, `160` keys notes, pitch range `37-94`, mean duration `1.004s`, velocity range `70-107`, max overlap `7`, max 55 ms attack cluster `4`, and skipped drums because the synthesized input-stem mix has no drum source.
- [x] Full-stem Psalm 5 validation `benchmarks/piano/runs/20260614_114716_psalm5-full-dense-high-staccato-validation` gives `piano_auto` / `piano_basic_pitch_playable` F1 `0.540`, onset F1 `0.674`, Offset F1 `0.139`, Off+Vel F1 `0.132`, Velocity MAE `8.0`, duplicate rate `0.0%`, and `1292` notes versus raw Basic Pitch F1 `0.494`, onset F1 `0.618`, Offset F1 `0.106`, Off+Vel F1 `0.002`, Velocity MAE `43.2`, and `2170` notes.
- [x] Packaged sidecar full-stem Psalm 5 import smoke `tmp/psalm5-full-packaged-sidecar-import-20260614_1149_dense_high_staccato.auralsong` validates with `keys -> piano_auto`, internal score `0.8834`, `1292` keys notes over `264.68s`, pitch range `27-94`, mean duration `0.890s`, velocity range `35-54`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the synthesized input-stem mix has no drum source.
- [x] Default-fewer low-artifact selector validation `benchmarks/piano/runs/20260614_120621_referenced-fullstem-default-fewer-low-artifact-current` improves referenced full-stem playable mean F1 from `0.296` to `0.316` versus the prior dense-high full-stem run, with Psalm 10/Psalm 2/Psalm 7 restored to raw/default-threshold parity while Psalm 130 keyboard is unchanged at F1 `0.614` and Psalm 130 synth remains weak at F1 `0.168`.
- [x] Default-fewer guard reruns `benchmarks/piano/runs/20260614_120712_piano-suite-default-fewer-low-artifact-current` and `benchmarks/piano/runs/20260614_120747_psalm5-default-fewer-low-artifact-current` keep the corrected 12-second suite and Psalm 5 guard unchanged: suite mean F1 `0.565`, Offset F1 `0.292`, Off+Vel F1 `0.274`, duplicate rate `0.0%`; Psalm 5 mean F1 `0.529`, onset F1 `0.682`, duplicate rate `0.0%`.
- [x] Default-fewer packaged sidecar smoke `tmp/psalm7-full-packaged-sidecar-import-20260614_1214_default_fewer.auralsong` validates with `keys -> piano_auto`, internal score `0.8667`, `204` keys notes over `198.12s`, pitch range `21-85`, mean duration `0.344s`, velocity range `60-101`, max overlap `7`, max 55 ms attack cluster `5`, and synced sidecar hash `8c87d98b0117335ec56f8242ee401f5de5b3ff03bd3399748a2932074b99c0e1`.
- [x] Local sparse-low spectral window validation `benchmarks/piano/runs/20260614_124617_referenced-fullstem-local-spectral-window-current` improves referenced full-stem playable mean F1 from `0.316` to `0.326`; Psalm 130 synth improves from F1 `0.168` to `0.220`, pitch accuracy `61.5%` to `70.7%`, Velocity MAE `20.0` to `11.4`, and notes `255` to `247`, while Psalm 130 keyboard, Psalm 10, Psalm 2, and Psalm 7 remain unchanged versus the default-fewer full-stem run.
- [x] Local-window guard reruns `benchmarks/piano/runs/20260614_124040_piano-suite-local-spectral-window-current` and `benchmarks/piano/runs/20260614_124115_psalm5-local-spectral-window-current` keep the corrected 12-second suite and Psalm 5 guard unchanged: suite mean F1 `0.565`, Offset F1 `0.292`, Off+Vel F1 `0.274`, duplicate rate `0.0%`; Psalm 5 mean F1 `0.529`, onset F1 `0.682`, duplicate rate `0.0%`.
- [x] Source CLI Psalm 130 synth full-stem import smoke `tmp/psalm130-synth-full-source-import-20260614_1253_local_spectral.auralsong` validates with `keys -> piano_auto`, internal score `0.8753`, `247` `keys_main` notes, pitch range `29-89`, mean duration `1.016s`, velocity range `24-54`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.

- [x] Local sparse-low spectral sidecar package synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `65c7ee9fb3751ae3f1353ff86de25744606e200bb8be395039221b2fe371f8df`; packaged sidecar smoke `tmp/psalm130-synth-full-packaged-sidecar-import-20260614_1306_local_spectral.auralsong` validates with `keys -> piano_auto`, internal score `0.8753`, `247` `keys_main` notes, pitch range `29-89`, mean duration `1.016s`, velocity range `24-54`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.

- [x] Sparse low-flood clamp validation `benchmarks/piano/runs/20260614_132619_referenced-fullstem-sparse-low-flood-clamp-current` improves referenced full-stem playable mean F1 from `0.326` to `0.386`; Psalm 2 improves F1 `0.148` to `0.249` with notes `357` to `197`, and Psalm 7 improves F1 `0.153` to `0.354` with notes `204` to `78`, while Psalm 130 keyboard, Psalm 130 synth, and Psalm 10 remain unchanged.

- [x] Sparse low-flood sidecar package synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `d79680de3c3a6d1e22d558c500609e8c060c6b1e9ff565bcec224837f4e408a9`; packaged sidecar smoke `tmp/psalm7-full-packaged-sidecar-import-20260614_1334_sparse_low_flood.auralsong` validates with `keys -> piano_auto`, internal score `0.9917`, `78` keys notes, pitch range `36-85`, mean duration `0.487s`, velocity range `62-101`, max overlap `7`, max 55 ms attack cluster `5`, and skipped drums because the configured input-stem mix has no drum source.

- [x] Long full-song velocity calibration validation `benchmarks/piano/runs/20260614_134526_referenced-fullstem-long-velocity-current` keeps referenced full-stem playable mean F1 at `0.386` while improving Psalm 10 Off+Vel F1 `0.000` to `0.192` and Velocity MAE `53.1` to `5.5`; Psalm 10 F1/onset/offset stay `0.496` / `0.619` / `0.194`, and Psalm 130 keyboard, Psalm 130 synth, Psalm 2, and Psalm 7 remain unchanged versus sparse low-flood.

- [x] Long-velocity sidecar package synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `a763a8a80b3d5e0a676f8eb8e8186bbeda0efa86b2cd4000459e4d543af242da`; packaged sidecar Psalm 10 full-stem smoke `tmp/psalm10-full-packaged-sidecar-import-20260614_1355_long_velocity.auralsong` validates with `keys -> piano_auto`, internal score `0.7667`, `645` keys notes, pitch range `21-97`, mean duration `0.599s`, velocity range `26-50`, mean velocity `36.77`, max overlap `7`, max 55 ms attack cluster `6`, and skipped drums because the configured input-stem mix has no drum source.

- [x] Sparse-synth transient cull validation `benchmarks/piano/runs/20260614_141406_referenced-fullstem-sparse-synth-transient-cull-current` improves referenced full-stem playable mean F1 `0.387` to `0.395`; Psalm 130 synth improves F1 `0.220` to `0.264`, Offset F1 `0.064` to `0.093`, Off+Vel F1 `0.054` to `0.077`, pitch accuracy `70.7%` to `73.9%`, Velocity MAE `11.4` to `9.5`, and notes `247` to `132`, while Psalm 130 keyboard, Psalm 10, Psalm 2, and Psalm 7 remain unchanged versus long velocity.

- [x] Sparse-synth transient cull sidecar package synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `1a5048db153afaa58accfd2085ff62327f55a40b5185cb753135382feab7ed55`; packaged sidecar Psalm 130 synth full-stem smoke `tmp/psalm130-synth-full-packaged-sidecar-import-20260614_1424_sparse_synth_cull.auralsong` validates with `keys -> piano_auto`, internal score `0.8626`, `132` keys notes, pitch range `29-89`, mean duration `1.493s`, velocity range `24-54`, mean velocity `35.95`, max overlap `7`, max 55 ms attack cluster `7`, and skipped drums because the configured input-stem mix has no drum source.

- [x] Long sparse duration cull validation `benchmarks/piano/runs/20260614_153026_referenced-fullstem-long-sparse-duration-cull-current` improves referenced full-stem playable mean F1 `0.395` to `0.505`; Psalm 2 improves F1 `0.249` to `0.629` with notes `197` to `34`, and Psalm 7 improves F1 `0.354` to `0.524` with notes `78` to `24`, while Psalm 130 keyboard, Psalm 130 synth, and Psalm 10 remain unchanged versus sparse-synth transient cull.

- [x] Long sparse duration sidecar package synced `dist/sidecar/aural_ingest.exe` and both desktop/game Tauri binaries to SHA-256 `4861a3cf9f45ec9277b0dddd3d9a7d9d72975dcdc62a35ca1a4783a73f47fbf5`; packaged sidecar Psalm 2 full-stem smoke `tmp/psalm2-full-packaged-sidecar-import-20260614_2108_long_sparse_duration.auralsong` validates with `keys -> piano_auto`, internal score `1.0`, `34` keys notes, pitch range `37-63`, mean duration `1.905s`, velocity range `66-92`, mean velocity `80.71`, max overlap `4`, max 55 ms attack cluster `3`, and skipped drums because the configured input-stem mix has no drum source.
- [ ] Real Suno piano MIDI plus matching audio/reference validation.
