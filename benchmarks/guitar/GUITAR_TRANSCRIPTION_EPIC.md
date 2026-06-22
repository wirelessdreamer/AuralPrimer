# Electric Guitar Transcription Epic

Living tracker for electric-guitar transcription quality work. Mirrors
`benchmarks/piano/PIANO_TRANSCRIPTION_EPIC.md` and reuses the same
ground-truth benchmark harness and "neural base + gated cleanup" playbook
that took piano from F1 ~0.28 to ~0.93 on the synthetic corpus.

## Goal

Produce electric-guitar MIDI that is:

- playable and recognizably close to the source performance
- correct on **chords/rhythm** (polyphonic) as well as **lead/single-note** lines
- robust to **distortion/overdrive** inharmonic overtones
- sane on expressive technique (bends, slides, vibrato, palm mutes, ghost notes)
- routed correctly through the existing lead/rhythm stem split

## Why this is the open frontier

- Guitar is the weakest instrument in the suite. Best **acoustic** GuitarSet
  result is production `melodic_combined` at **F1 0.261** (precision 0.309,
  recall 0.226 — three-quarters of real notes missed). See
  `docs/research-ground-truth-benchmarks-2026-06-14.md` (Guitar section).
- The one tuned attempt, `melodic_combined_guitar`, traded +7% recall for
  −17% precision and netted **−5% F1**. Shipped alongside the default but
  not promoted.
- **Electric guitar has never been benchmarked.** `gt_runs/` has
  `guitarset_*` (acoustic) but no `guitar_techs` runs.
- **The piano playbook has not been applied to guitar at all.** Guitar still
  runs the old DSP melodic algorithms (`melodic_combined` / YIN family).
  Piano's gains came from a neural polyphonic base (Basic Pitch) plus a long
  stack of narrowly gated cleanup passes and an analytical chord supplement —
  none of which guitar uses yet.

## Ground truth available (already wired into `gt-benchmark`)

| Dataset | Adapter | Content | Use |
|---|---|---|---|
| Guitar-TECHS v1 | `guitar_techs` | **104 electric phrases**, `micamp` (amp-mic) + `directinput` (DI), per-string MIDI, categories chords/scales/singlenotes/techniques/music | **Primary electric GT** |
| GuitarSet v1.1.0 | `guitarset` | 360 acoustic phrases, JAMS per-string | Acoustic control / regression guard |

> Datasets live under `E:\AudioSourceOfTruthData\extracted\{guitar_techs,guitarset}`
> and must be reachable on the machine running the benchmark. Not present on
> every clone — confirm the drive is mounted before running.

Run shape (mirrors the piano/drum GT runs):

```
aural_ingest gt-benchmark --dataset guitar_techs ...   # electric
aural_ingest gt-benchmark --dataset guitarset ...      # acoustic guard
```

## The playbook (ported from piano)

1. **Ground truth + baseline.** Lock `guitar_techs` (micamp = electric) and
   `guitarset` (acoustic) baselines for the current production path before
   changing anything. Break results down by `signal` (micamp vs directinput)
   and by `category` (chords vs singlenotes vs techniques).
2. **Neural polyphonic base.** Wrap Spotify Basic Pitch as
   `guitar_basic_pitch_playable`, mirroring `piano_basic_pitch_playable`.
   Basic Pitch is polyphonic — directly addresses chord/rhythm guitar that
   the monophonic melodic path can't represent.
3. **Guitar-specific gated cleanup passes** (the piano-cleanup analogues,
   each narrowly gated to a detected output shape so it can't regress others):
   - octave/harmonic-shadow pruning tuned for distortion overtone stacks
   - bend/slide/vibrato pitch-glide merging (guitar's "sustain" problem)
   - palm-mute / ghost-note suppression
   - per-string range gating via the lead/rhythm split
4. **Chord supplement for rhythm guitar** (the piano 0.82→0.93 move):
   detect strum onsets, add FFT-supported chord tones the neural model missed.
   Highest-leverage step because rhythm guitar is polyphonic.
5. **Lead vs rhythm specialization + ship.** Lead → monophonic hybrid;
   rhythm → chord-supplement path; route via `split_lead_rhythm_guitar_stem`.
   Guard reruns, register `guitar_auto`, repackage sidecar.

## Discipline (same rules as piano)

- Every change = one benchmark run + guard reruns; no regression allowed on
  the dominant metric or on the acoustic guard set.
- Log every run verbatim in this file with the run-dir path and numbers.
- Static output review required; console numbers alone are not sufficient.
- Keep guitar-specific work in a `guitar_*` family; do not replace the
  generic `auto` melodic path until it clearly wins.

## Status

- [x] Synced clone to the piano-infrastructure lineage (`main` @ origin/main).
- [x] Confirmed Guitar-TECHS (electric) + GuitarSet (acoustic) adapters are
      wired into `gt-benchmark`.
- [ ] **BLOCKED:** mount/point to `E:\AudioSourceOfTruthData\extracted\…`
      (datasets not on this machine).
- [ ] Phase 1: lock electric + acoustic baselines.
- [ ] Phase 2: `guitar_basic_pitch_playable` neural base.
- [ ] Phase 3: guitar-specific gated cleanup passes.
- [ ] Phase 4: rhythm chord supplement.
- [ ] Phase 5: lead/rhythm specialization, `guitar_auto`, repackage.

## Open questions

- Benchmark against `micamp` (amp-mic, realistic) or `directinput` (DI,
  cleaner) first? micamp is the truer electric target; DI is the easier win.
- Does Basic Pitch's training (acoustic-leaning) hold up on heavy distortion,
  or do we need a distortion-robust front end / synthetic distorted corpus?
