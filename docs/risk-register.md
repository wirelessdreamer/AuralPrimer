# Risk Register

This project contains a few known hard problems. This register is meant to keep execution realistic.

## R1 — Accurate MP3→MIDI for polyphonic instruments
**Risk**: general-purpose MP3→MIDI transcription (especially guitar chords) is not reliably accurate.

**Mitigations**
- make transcription stages optional and replaceable
- prioritize gameplay modes that don’t require full MIDI (beats, onsets, pitch contours, harmony blocks)
- allow **user-provided MIDI** for high-quality note targets

**Acceptance criteria for integration**
- adopt only when metrics beat baseline and produce usable practice targets.

---
## R2 — Shipping ML models increases installer size
**Risk**: stem separation/transcription models can add 200MB–2GB.

**Mitigations**
- “Lite vs Full Models” installers
- optional model packs (still offline, but separate downloads)
- MVP avoids heavy models

---
## R3 — Licensing (ffmpeg, models)
**Risk**: bundling ffmpeg and certain models can impose license constraints.

**Mitigations**
- track licenses from day 1 (`THIRD_PARTY_NOTICES.md`)
- select permissive models
- choose an ffmpeg build that aligns with distribution goals

---
## R4 — Audio/visual sync drift
**Risk**: drift or jitter leads to bad gameplay feel.

**Mitigations**
- use audio device timebase as master
- implement constant-latency scheduling and measured offsets
- add calibration tools (later)

---
## R5 — Plugin system instability (breaking changes)
**Risk**: rapid experimentation could break plugins.

**Mitigations**
- strict versioned plugin API
- schema compatibility checks
- keep a minimal stable core and move experiments to plugins

---
## R6 — Performance (rendering + large event sets)
**Risk**: heavy visualizers and large songs can exceed frame budget.

**Mitigations**
- time-window queries (avoid full scans)
- pre-index events and cache slices
- move heavy computations to workers

---
## R7 — Cross-platform build complexity
**Risk**: sidecar builds + tauri packaging differ per OS.

**Mitigations**
- early CI matrix
- deterministic build scripts
- keep sidecar interface stable
