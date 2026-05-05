# Piano MIDI Refinement Workbench

Status: MVP implemented; real-song Suno validation pending.

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
- Do not force this through SongPack import; SongPack replacement can come later.

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
- `piano_polyphonic_clean`
- `piano_polyphonic`
- `piano_transkun_clean`
- `piano_pti_clean`
- `piano_hft_clean`
- `basic_pitch`
- legacy melodic comparators when explicitly requested

Missing optional model-backed candidates must record a clear error and allow the rest of the run to complete.

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
- [ ] Real Suno piano MIDI plus matching audio/reference validation.
