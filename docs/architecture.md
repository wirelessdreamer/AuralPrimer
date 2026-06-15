# Architecture

## Executive summary
AuralPrimer/AuralStudio is a **two-app desktop suite**:
- **AuralPrimer** is the gameplay runtime.
- **AuralStudio** is the import/authoring runtime.

Both apps exchange content through **AuralSongs**.
All heavy extraction/transcription work happens **offline** in a Python-based ingest pipeline shipped as **embedded sidecar executables**.

## High-level modules

### A) AuralPrimer game host (`apps/game`)
**Responsibilities**
- AuralSong discovery/loading
  - scan a configured songs folder on startup for `*.auralsong` (zip) and `*.auralsong/` (directory) AuralSongs
  - update local library index when new AuralSongs appear
- Audio playback + transport clock (play/pause/seek/loop)
- Local audio codec layer (Rust/Symphonia decode for AuralSong playback)
- Latency compensation & sync
- Visualization plugin loading + lifecycle management
- Input routing (keyboard/controller; mic/MIDI later)

**Non-responsibilities**
- No offline ingestion/extraction/transcription inside AuralPrimer.
- No heavy ML inference directly in the host process (prefer isolated sidecars/modules).
- No import/song-creation UI flows.

### B) AuralStudio authoring host (`apps/desktop`)
**Responsibilities**
- Import/song-creation UX
  - raw audio import
  - user-provided stems, MIDI, and authored chart import
  - stem+MIDI AuralSong creation
- Running ingestion sidecars and tracking progress
- Authoring-related preferences and tool configuration
- Writing validated AuralSong outputs to the shared songs library

**Non-responsibilities**
- No gameplay/practice runtime modes as product features.
- No player-focused visualizer/gameplay surfaces.

### C) AuralSong format + schemas (`packages/auralsong`, `packages/core-music`)
**Responsibilities**
- Canonical, versioned schema for:
  - beat/tempo grid
  - sections
  - note/onset events
  - chords/harmony (including Nashville / Roman)
  - difficulty/practice charts
- Validation and migrations
- Deterministic serialization

### D) Python ingest pipeline (`python/ingest`)
**Responsibilities**
- Importers and extraction pipeline that convert sources into AuralSongs
  - audio-only import
  - MIDI import
  - user-provided stem and chart import
- decoding → PCM (when needed)
- beat/tempo
- segmentation
- optional stem separation
- transcription (notes/onsets/pitch contours)
- harmony analysis (key/chords → Nashville)
- chart generation

**Deployment**
- shipped as OS-specific sidecar executables, invoked by AuralStudio.

### E) Visualization plugins (`visualizers/*` + `packages/viz-sdk`)
**Responsibilities**
- Render visuals based on AuralSong events + transport state
- Provide instrument-specific or theory-centric representations

**Constraints**
- Must be isolated from core; loaded dynamically.

---
## Runtime data flow

### 1) Import flow
1. User selects an import source (audio file, stem folder, MIDI file, or authored chart file) in AuralStudio.
2. AuralStudio selects a **pluggable importer** and spawns sidecar: `aural_ingest import <source> --importer <id> --out <auralsong-dir> --profile <...>`
3. Sidecar writes an AuralSong folder (or zip) incrementally:
   - `manifest.json`
   - `audio/mix.wav`
   - `features/*.json`
   - `charts/*.json`
4. AuralStudio watches progress and surfaces logs.
5. AuralStudio validates AuralSong, then writes output to the shared songs library.

### 2) Playback + render flow
1. AuralPrimer opens AuralSong and chooses a visualization plugin.
2. AuralPrimer starts audio playback.
3. Each frame (~60fps):
   - AuralPrimer computes `TransportState` from audio timebase.
   - AuralPrimer calls `visualizer.update(dt, transportState)`
   - AuralPrimer calls `visualizer.render(frameContext)`

---
## Key architectural choice: canonical event timeline
Visualizers must not depend on MIDI parsing or raw ML outputs.

---

## Realtime MIDI (runtime sync + I/O)

### Goals
- Allow external MIDI devices/controllers to provide **performance input** (note on/off, CC) into gameplay.
- Allow external MIDI clocks to **drive** AuralPrimer’s transport when desired.
- Allow AuralPrimer to **drive** downstream devices with MIDI clock so chained gear stays in time with:
  - the currently loaded AuralSong
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
- A **MIDI input bus** normalizes native keyboard/controller messages into structured frontend events and active-note state for gameplay integration.
  See `docs/midi-keyboard-testing.md` for the current hardware verification path.

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

Instead, ingestion produces a **canonical event timeline** (see `docs/auralsong-spec.md`) that is stable over time and supports many render paradigms:
- fretboard note targets
- drum lane grid
- vocal pitch lane
- Nashville chord blocks

---
## Plugin boundaries and stability

### Stable interfaces
1. **AuralSong schema** (versioned) — what AuralPrimer and visualizers consume.
2. **Viz SDK API** — lifecycle methods + rendering and event access.
3. **Ingest CLI contract** — AuralStudio ↔ pipeline communication.

Each stable interface must have **contract tests** (TDD-first) that:
- lock in backwards-compatibility guarantees
- catch breaking changes immediately in CI

### Compatibility rules
- A visualizer declares `supported_schema_versions` in its manifest.
- Host can run migrations when loading older AuralSongs.

---
## Technology choices (recommended)

### Desktop hosts
- **Tauri** (Rust backend + Web UI)
  - small distributables
  - good sidecar support
  - WebGL/Canvas rendering for visuals

### Python sidecars
- Python 3.11+ (in dev)
- packaging: **PyInstaller** or **Nuitka**
- ML dependencies pinned and vendored into sidecar builds

> Note: transcription accuracy is treated as modular and replaceable; early MVP can start with MIDI import and beat/section extraction.

### Host audio codecs
- Playback hosts decode AuralSong `mix.ogg`, `mix.mp3`, and `mix.wav` in-process with Rust/Symphonia.
- FFmpeg is not part of the normal playback path; it is reserved for ingest-sidecar source conversion.
- See `docs/audio-codec-policy.md`.
