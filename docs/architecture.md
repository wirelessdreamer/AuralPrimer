# Architecture

## Executive summary
AuralPrimer is a **desktop-first** music-learning game. The runtime is a **small, shippable** desktop app that loads **SongPacks** (data packages) and renders one or more **visualization plugins** synced to audio.

All heavy extraction/transcription work happens **offline** in a Python-based ingest pipeline shipped as **embedded sidecar executables**.

## High-level modules

### A) Desktop game host (`apps/desktop`)
**Responsibilities**
- SongPack discovery/loading
  - scan a configured songs folder on startup for `*.songpack` (zip) and `*.songpack/` (directory) SongPacks
  - update local library index when new SongPacks appear
- Audio playback + transport clock (play/pause/seek/loop)
- Local audio codec layer (decode MP3/OGG as needed for playback)
- Latency compensation & sync
- Visualization plugin loading + lifecycle management
- Input routing (keyboard/controller; mic/MIDI later)
- Running ingestion sidecars and tracking progress

**Non-responsibilities**
- No offline ingestion/extraction/transcription inside the host.
- No heavy ML inference directly in the host process (prefer isolated sidecars/modules).

### B) SongPack format + schemas (`packages/songpack`, `packages/core-music`)
**Responsibilities**
- Canonical, versioned schema for:
  - beat/tempo grid
  - sections
  - note/onset events
  - chords/harmony (including Nashville / Roman)
  - difficulty/practice charts
- Validation and migrations
- Deterministic serialization

### C) Python ingest pipeline (`python/ingest`)
**Responsibilities**
- Importers and extraction pipeline that convert sources into SongPacks
  - audio-only import
  - MIDI import
  - ecosystem importers (e.g., GHWT DE)
- decoding → PCM (when needed)
- beat/tempo
- segmentation
- optional stem separation
- transcription (notes/onsets/pitch contours)
- harmony analysis (key/chords → Nashville)
- chart generation

**Deployment**
- shipped as OS-specific sidecar executables, invoked by the desktop host.

### D) Visualization plugins (`visualizers/*` + `packages/viz-sdk`)
**Responsibilities**
- Render visuals based on SongPack events + transport state
- Provide instrument-specific or theory-centric representations

**Constraints**
- Must be isolated from core; loaded dynamically.

---
## Runtime data flow

### 1) Import flow
1. User selects an import source (audio file, MIDI file, or external ecosystem folder) in the desktop host.
2. Host selects a **pluggable importer** and spawns sidecar: `aural_ingest import <source> --importer <id> --out <songpack-dir> --profile <...>`
3. Sidecar writes a SongPack folder (or zip) incrementally:
   - `manifest.json`
   - `audio/mix.wav`
   - `features/*.json`
   - `charts/*.json`
4. Host watches progress and surfaces logs.
5. Host validates SongPack, then adds to library.

### 2) Playback + render flow
1. Host opens SongPack and chooses a visualization plugin.
2. Host starts audio playback.
3. Each frame (~60fps):
   - Host computes `TransportState` from audio timebase.
   - Host calls `visualizer.update(dt, transportState)`
   - Host calls `visualizer.render(frameContext)`

---
## Key architectural choice: canonical event timeline
Visualizers must not depend on MIDI parsing or raw ML outputs.

---

## Realtime MIDI (runtime sync + I/O)

### Goals
- Allow external MIDI devices/controllers to provide **performance input** (note on/off, CC) into gameplay.
- Allow external MIDI clocks to **drive** AuralPrimer’s transport when desired.
- Allow AuralPrimer to **drive** downstream devices with MIDI clock so chained gear stays in time with:
  - the currently loaded SongPack
  - current practice slowdown factor (playbackRate)
  - loop/seek state (best-effort, device capabilities vary)

### Non-goals (MVP)
- Do not block MVP on implementing every MIDI message type.
- Do not hard-code vendor-specific SysEx logic in core; prefer opt-in profiles.

### Architecture placement
Treat MIDI as a first-class runtime subsystem alongside Audio and Transport:

- **Transport** remains the single source of truth for “song time” within the app.
- A **MIDI sync adapter** can be attached to the Transport to map between:
  - incoming MIDI clock ticks ↔ transport time progression
  - transport time progression ↔ outgoing MIDI clock ticks

Key concept: **Tempo scaling**
- The transport exposes a playbackRate (practice slowdown).
- MIDI clock output is derived from the effective tempo = song tempo × playbackRate.
- When the user selects “external clock input drives transport”, the app maps:
  incoming tempo to effective transport tempo with a user-defined scale.

### Implementation options (to be decided)
- WebMIDI in the renderer (if feasible in Tauri/WebView)
- Rust MIDI service (recommended for stable device I/O) + Tauri commands/events
- Sidecar-based MIDI bridge (least preferred for low-latency clock)

### Testing strategy notes
- Introduce a fake MIDI clock source/sink for deterministic unit tests.
- Contract tests should cover:
  - jitter tolerance and monotonicity under input clock
  - loop/seek behaviors while output clock is enabled
  - tempo scaling behavior (external tempo → song tempo) and slowdown (song tempo × playbackRate)

Instead, ingestion produces a **canonical event timeline** (see `docs/songpack-spec.md`) that is stable over time and supports many render paradigms:
- fretboard note targets
- drum lane grid
- vocal pitch lane
- Nashville chord blocks

---
## Plugin boundaries and stability

### Stable interfaces
1. **SongPack schema** (versioned) — what the game and visualizers consume.
2. **Viz SDK API** — lifecycle methods + rendering and event access.
3. **Ingest CLI contract** — host ↔ pipeline communication.

Each stable interface must have **contract tests** (TDD-first) that:
- lock in backwards-compatibility guarantees
- catch breaking changes immediately in CI

### Compatibility rules
- A visualizer declares `supported_schema_versions` in its manifest.
- Host can run migrations when loading older SongPacks.

---
## Technology choices (recommended)

### Desktop host
- **Tauri** (Rust backend + Web UI)
  - small distributables
  - good sidecar support
  - WebGL/Canvas rendering for visuals

### Python sidecars
- Python 3.11+ (in dev)
- packaging: **PyInstaller** or **Nuitka**
- ML dependencies pinned and vendored into sidecar builds

> Note: transcription accuracy is treated as modular and replaceable; early MVP can start with MIDI import and beat/section extraction.
