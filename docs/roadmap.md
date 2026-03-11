# Roadmap

This roadmap is structured to deliver value early without getting blocked by the hardest R&D problem (accurate polyphonic MP3->MIDI transcription).

Role split for this roadmap:
- AuralPrimer = gameplay runtime.
- AuralStudio = import/song-creation runtime.

## Milestone 0 - Project foundations (1-2 weeks)
**Deliverables**
- SongPack schema v1 + JSON schema definitions.
- SongPack reader/writer + validator library.
- AuralPrimer host skeleton:
  - library view
  - scan songs folder on startup (discover new/removed SongPacks)
  - SongPack loader
  - audio playback
  - plugin loader
- AuralStudio shell:
  - import/creation workspace
  - shared songs-folder configuration
- 1 minimal visualizer that renders beats/sections.
- **Test harness + CI gates in place**:
  - schema validation tests
  - host/plugin lifecycle smoke tests
  - pipeline unit tests scaffold

**Exit criteria**
- A demo SongPack can be played with synced visuals.

## Milestone 1 - Import MVP (2-4 weeks)
**Deliverables**
- Python sidecar `aural_ingest` with:
  - decode stage
  - beat + tempo stage
  - simple sections stage
  - chart generation stage (at least one mode)
- **TDD requirement**: each stage ships with unit tests (fingerprints, caching rules, artifact validation) and at least one golden fixture.
- AuralStudio import UI to run sidecar + show progress.
- Packaging proof: Windows + Linux.

**Exit criteria**
- User imports an mp3 in AuralStudio and gets a SongPack playable in AuralPrimer.
- CI includes regression coverage for the import path (unit + golden tests).

## Milestone 2 - Visualization experimentation (2-6 weeks)
**Deliverables**
- Viz SDK stabilized.
- Reference plugins:
  - Nashville chord lane (if chords available; otherwise placeholder)
  - Drum grid (from onset targets)
  - Fretboard (from MIDI import first)
- Plugin discovery from user folder.

**Exit criteria**
- Plugins can be added/updated without modifying AuralPrimer.

**UI requirement checkpoint (from `spec.md`)**
- The AuralPrimer UI always shows **current key + mode** during playback.
- Note-centric views represent **chord structure over notes**.

## Milestone 3 - Instrument extraction (4-8 weeks)
**Deliverables (incremental)**
- Optional stem separation stage.
- Drums: onset detection + basic classification.
- Vocals: pitch contour extraction.
- Bass: monophonic pitch extraction.

**Exit criteria**
- At least one instrument profile produces usable targets from MP3.

## Milestone 4 - Gameplay loops (4-8 weeks)
**Deliverables**
- Game modes:
  - rhythm timing scoring (drums)
  - pitch target matching (vocals/bass)
  - theory quiz overlays (Nashville)
- Practice controls:
  - loop sections
  - slowdown
  - metronome

**Exit criteria**
- A complete learn/practice loop exists for at least one instrument.

## Milestone 5 - v1 release hardening (ongoing)
- onboarding, UX polish
- performance profiling
- cross-platform build automation and code signing
- licensing documentation

---
## Parallel tracks (post-MVP)

### A) Pluggable importers (content adoption)
- Define a stable importer interface and implement multiple importers (e.g., audio-only, MIDI, GHWT DE).
- Ensure importers convert into SongPacks without constraining SongPack capabilities.

### B) Realtime audio->MIDI / realtime identification
- Implement a local, realtime sidecar/module that converts mic/line-in audio into MIDI-like events.
- Integrate into gameplay input pipeline with calibration.

### C) "True MP3->MIDI" (R&D)
Treat polyphonic transcription as a replaceable stage and run it as an R&D line:
- evaluate transcription models per instrument
- quantify accuracy metrics
- integrate only when it beats a baseline
