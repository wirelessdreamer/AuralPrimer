# Piano Transcription Research Notes

See `PIANO_TRANSCRIPTION_EPIC.md` for the execution tracker and remaining work list.

## Immediate goal

Improve playable piano output from analyzed stems and piano-only songs, with special attention to:

- reduced doubled notes
- more realistic sustain
- cleaner repeated-note handling
- closer dynamic shape
- fewer octave/voicing errors in chord passages

## Phase 1 in repo

- `piano_auto` path in ingest transcription
- `piano_polyphonic_clean` heuristic for chord-aware piano extraction
- piano cleanup pass for dedupe, micro-gap merge, sustain extension, and velocity blending
- manifest-driven benchmark runner
- offset-aware and velocity-aware evaluation metrics

## Phase 2 candidates

- `piano_transkun_clean`
- `piano_pti_clean`
- stronger modelpack/runtime packaging for optional piano engines

## Evaluation priorities

1. exact-note F1
2. note-with-offset F1
3. note-with-offset-and-velocity F1
4. duplicate prediction rate
5. listening A/B on real songs

## Recommended case mix

- solo piano worship songs
- pre-split piano stems from full arrangements
- a few dense left-hand chord passages
- a few repeated-note arpeggio passages

## Current working rule

Do not replace the generic `auto` melodic path for every keyboard source. Keep piano-specific work in the `piano_*` family until it clearly wins on real listening tests.
