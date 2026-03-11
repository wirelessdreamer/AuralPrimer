# AuralPrimer + AuralStudio - Project Specification (Requirements)

> This document is the **authoritative requirements spec** for the AuralPrimer/AuralStudio product suite.
> Architecture, file formats, and implementation details live in `/docs`, but any *must-have* behavior should be captured here.

## 1) Product vision
The product is split into **two separate desktop apps** with strict ownership boundaries:

- **AuralPrimer**: gameplay app (play/learn/practice/performance) that consumes SongPacks.
- **AuralStudio**: content app (import/transcription/song creation/preferences for authoring) that produces SongPacks.

Both apps are offline-first and exchange content through the SongPack format.

### 1.1 App-role boundaries (non-negotiable)
**AuralPrimer (game) must include:**
- SongPack library browsing/loading
- playback + visualization
- practice workflows (looping, slowdown, metronome)
- gameplay/performance input systems (MIDI/audio input for gameplay)

**AuralPrimer (game) must NOT include:**
- raw audio/chart import flows
- transcription/ingest execution UI
- SongPack creation wizards (e.g., stem+MIDI creator)
- ecosystem importers (e.g., GHWT importer)

**AuralStudio (authoring) must include:**
- import/transcription pipelines
- SongPack creation and conversion flows
- authoring-related preferences/settings
- model and tool setup needed for import/authoring

**AuralStudio (authoring) must NOT include:**
- gameplay/practice runtime modes as product features
- player-focused visualizer/gameplay surfaces

## 2) Hard constraints (non-negotiable)

### 2.0 Engineering process: TDD (non-negotiable)
- Development must follow **test-driven development**:
  - write/extend tests first
  - implement minimal code to pass
  - refactor with tests keeping behavior stable
- Every feature/bugfix PR must include:
  - unit tests and/or contract tests covering the change
  - updated golden fixtures when outputs intentionally change
- CI must run the test suite and block merges on failures.

### 2.1 Platform support
- **Windows**: supported
- **Linux**: supported
- macOS: **not a target**

### 2.2 Offline operation
- **AuralPrimer** must be fully usable offline for normal gameplay:
  - playback
  - visualization rendering
  - local library management
  - practice tools
- **AuralStudio** import/song creation workflows must also work offline from local files (except optional one-time model download/setup steps).
- No always-on services and no core product flow that requires constant network connectivity.

### 2.3 Model distribution
- The shipped installer **must not bundle ML model weights**.
- If/when ML models are needed, they are obtained **after install** via one of:
  1) **In-app model download** (one-time or on-demand) and then stored locally.
  2) **Manual offline import** of model packs (zip/folder) for users who never connect the app to the internet.

**Model storage location (consistent rule):**
- Models are stored under the app's `assets/models/<model-id>/<version>/...` directory.
- Model packs must be versioned and must not overwrite existing versions.

> Implication: "offline gameplay" is compatible with model downloads as a *setup step*; once downloaded/imported, the feature must function offline.

### 2.4 Local-only data and privacy
- User content (audio, SongPacks, derived features) stays on the local machine.
- No cloud upload is required to import or play songs.

## 3) Core system requirements

### 3.1 SongPack-first runtime
- **AuralPrimer runtime** consumes **SongPacks** as its canonical game content.
- External formats (audio+charts from other ecosystems) are supported via **pluggable importers** in **AuralStudio** that convert into SongPacks.
- The AuralPrimer runtime host must not perform ingestion/extraction/transcription on raw audio.
- Playback decoding (e.g., MP3/OGG) is provided by a **local codec layer** (bundled decoder/sidecar); this does not change the SongPack-first rule.
- All AuralPrimer runtime visuals are rendered from **SongPack artifacts** (canonical event timeline).
- Reference:
  - `docs/songpack-spec.md`
  - `docs/architecture.md`

### 3.2 Deterministic imports
- Given identical inputs + pipeline version + configuration, ingestion outputs must be reproducible.
- Imports must be cacheable and versioned.
- Deterministic import execution is owned by AuralStudio + ingest sidecar, not AuralPrimer runtime.
- Reference: `docs/ingest-pipeline.md`.

### 3.3 Plugin-first visualization
- Visualizers are separately packaged and loaded dynamically.
- Visualizers consume the canonical event model and transport state.
- Reference: `docs/visualization-plugins.md`.

### 3.4 Local SongPack library discovery
- Users must be able to download or copy **SongPacks** into a designated local "songs folder".
- On application start, AuralPrimer must **scan for new/removed SongPacks** and update the local library view.
- AuralPrimer must support **both** SongPack container forms:
  - directory SongPacks (`*.songpack/` folders)
  - zip SongPacks (`*.songpack` files)

### 3.5 Native audio engine (real-time DSP) (new direction)
The AuralPrimer runtime host must evolve from "web-audio playback" into a **native real-time audio engine**.

Motivation:
- enable **low-latency** playback and monitoring suitable for gameplay + practice + performance
- support **real-time effects** (e.g., compressor, distortion, EQ, reverb)
- support **instruments/soundbanks** (sampler / synth) driven by MIDI and/or gameplay events
- enable future **ASIO / pro-audio** device integration (Windows)

Requirements:
- The runtime must include a native audio engine with a **real-time audio thread**.
- The engine must support a graph/bus model:
  - master output bus
  - per-track buses (at minimum: SongPack playback bus, metronome bus; later: instrument buses)
  - per-bus effect chains
- The engine must expose time as a **sample-accurate transport clock** used for:
  - visualizer sync
  - metronome
  - MIDI clock output
- UI -> engine parameter updates must be designed for real-time constraints:
  - no blocking / no locks inside the audio callback
  - parameter updates via lock-free message passing or atomics

Phasing constraints:
- MVP may begin with native **playback-only** output (no input monitoring) and a minimal effect chain.
- The existing HTMLAudio/WebAudio timebases may remain for developer convenience, but the long-term goal is a single native engine.

ASIO note:
- ASIO support is a stretch goal and may start with WASAPI (shared/exclusive) as the default Windows backend.

## 4) Visual/UI requirements

### 4.1 Always show key + mode
During playback (and ideally during scrubbing/paused states), the UI must always display:
- current **key** (tonic)
- current **mode** (e.g., major/minor; additional modes later)

This is required even when a visualization plugin is showing an instrument-specific view.

### 4.2 Chord structure over notes
In note-centric visualizations, chord structure must be visually represented **above** (or otherwise hierarchically "over") individual notes.

- Chords should be time-ranged blocks/labels aligned to the transport timeline.
- Notes are rendered in relation to the chord context (e.g., scale degrees/Nashville/Roman optional overlays).

### 4.3 Core transport UX (MVP)
- play / pause
- seek
- loop region
- tempo slowdown
- metronome

## 5) Input requirements

### 5.1 MIDI input (runtime)
- Support receiving real-time input from MIDI devices.
- Latency calibration and timestamping must be compatible with the transport clock.

#### 5.1.1 MIDI realtime + sync (must-have)
The runtime must support **bidirectional MIDI integration** for practice and performance workflows.

**MIDI input (from external device into AuralPrimer)**
- Support incoming:
  - Note On / Note Off
  - Velocity
  - Channel messages (at least: pitch bend, mod wheel, sustain) where applicable to gameplay modes
- Support using external MIDI timing as a transport sync source:
  - MIDI Clock (24 PPQN)
  - Start/Stop/Continue (Transport)
  - Song Position Pointer (SPP) where available

**Default behavior: follow external clock**
- If a valid external MIDI clock source is selected, the default behavior is:
  - **follow external clock for transport timing**
  - provide an option to disable following external clock and fall back to audio-driven transport

**MIDI output (from AuralPrimer to external device)**
- Support emitting MIDI output synced to the current song/transport:
  - MIDI Clock + Start/Stop/Continue so external devices can follow the app
  - Optional: SPP (best-effort, per-device limitations)

**Tempo scaling / practice slowdown with sync**
- When the user applies a practice slowdown (e.g. 0.5x), the app must be able to:
  - keep internal transport, visualizers, and metronome aligned
  - emit a **scaled MIDI clock** so chained devices remain in time with the slowed song
- When an external device is driving clock (input), the app must support:
  - mapping incoming clock tempo to the song tempo with a user-controlled scale factor
    (e.g. device sends 120 BPM clock, song runs at 60 BPM for practice)

**SysEx support (where it makes sense)**
- The app must support receiving and sending SysEx messages for device integration where needed.
  - SysEx must be **opt-in** per device/port due to security and device-specific semantics.
  - The app must avoid hard-coding vendor SysEx behavior in core runtime; prefer pluggable device profiles.

**General MIDI scope**
- Support "full MIDI spec" *where it makes sense* for our use-cases:
  - prioritize clock/transport sync + note I/O
  - device-specific extensions (NRPN/RPN, SysEx) are supported as pass-through / profiles
  - do not block MVP on implementing every message type

**Non-regression constraint**
- MIDI sync must not break core transport behaviors (looping, seek, slowdown, metronome).

### 5.2 Audio signal input (runtime)
- Support audio input (microphone / line-in) as a gameplay input.

### 5.3 Realtime audio->MIDI conversion
The system must include a roadmap and architecture path to:
- take a **standard audio signal** (mic/line-in)
- convert it to **MIDI (notes/onsets) in realtime**
- feed that MIDI-like event stream into gameplay modes as input

Notes:
- This is distinct from offline ingestion (MP3->SongPack). It is **live**.
- The first implementation may target **monophonic** sources (voice/bass) before polyphonic guitar.

## 6) Content interoperability requirements

### 6.1 Interoperability via pluggable importers (GHWT DE is one source)
AuralPrimer uses **SongPack** as its native game content format.

To accelerate adoption without constraining internal capabilities, **AuralStudio** must support **pluggable importers** that convert external sources into SongPacks (e.g., audio-only, user MIDI, GHWT DE, and other ecosystems).

Minimum requirements:
- Define an ingestion/import path that can take **user-provided** GHWT DE assets and transform them into SongPacks.
- The pipeline must support generating canonical events and charts from those assets.

#### 6.1.2 Stem + MIDI song creator (MVP behavior)
**AuralStudio** must provide an importer/creator that can build a playable SongPack from:
- one or more **audio stem WAV files** (e.g. drums/bass/guitar/vocals), and
- a **standard MIDI file** (`.mid`) containing note events.

This enables users to:
- bring their own multi-track stems (from a DAW export, or other sources)
- pair them with a MIDI chart (hand-authored or exported)
- produce a SongPack that is then playable inside AuralPrimer.

**UI requirements**
- AuralStudio must provide a **Configure -> Create SongPack (stems + MIDI)** section.
- The user can:
  - select one or more stem WAV files (file picker)
  - optionally select a single already-mixed WAV file instead of multiple stems
  - select a MIDI file (`.mid`)
  - enter metadata (title, artist)
  - click **Create SongPack**

**Output requirements**
- Output is a **directory SongPack** created in the user's configured songs folder.
- Audio output must be `audio/mix.wav`:
  - If multiple stems are provided, the app must mix them down deterministically.
  - If a single WAV is provided, it may be copied as `audio/mix.wav`.
- MIDI must be stored in the SongPack as `features/notes.mid`.
- The importer should also emit a minimal canonical `features/events.json` containing at least:
  - a track entry (role/name can be generic)
  - note events derived from the MIDI file (`t_on`, `t_off`, `pitch`)

**Validation / constraints**
- The feature must work offline.
- WAV mixing must be deterministic.
- If stems have mismatched sample rate / channel count / duration, the importer must fail with a clear error.
- The app must not ship copyrighted music content in this repo.

#### 6.1.1 GHWT-DE import (MVP behavior)
- AuralStudio must provide a **Configure -> Import GHWT songs** section.
- The user can configure:
  - the GHWT-DE `DATA` root folder path
  - an optional explicit path to `vgmstream-cli` (otherwise it is resolved via PATH)
- AuralStudio must be able to:
  - scan GHWT-DE DLC content under:
    `DATA/MODS/Guitar Hero_ World Tour Downloadable Content/DLC*/song.ini`
  - display detected songs with basic metadata (title/artist/checksum)
  - import a selected song into the user's configured AuralPrimer songs folder as a directory SongPack:
    `ghwt_<checksum>.songpack/`

MVP importer scope / limitations:
- Audio source: `Content/MUSIC/<checksum>_preview.fsb.xen` (preview only)
- Output audio: `audio/mix.wav`
- Chart import from `*.pak.xen` is **out of scope** for MVP.

Audio compatibility:
- The runtime must support playing SongPacks containing `audio/mix.wav` (in addition to `mix.mp3` / `mix.ogg`).

Constraints / compliance:
- Do not ship copyrighted GHWT content in this repo.
- The feature must be designed around **user-owned** game files and a format translation layer.
- The SongPack schema must remain free to evolve beyond any source format's limitations.

### 6.2 Product boundary enforcement
- AuralPrimer must not expose raw import/song-creation controls in its UI.
- AuralStudio is the only app that may expose ingest/transcription/import/song-creation controls.
- The app handoff is via SongPack artifacts in the configured songs library.

## 7) Packaging & deployment requirements

### 7.1 Desktop hosts
- Ship two separate desktop executables (recommended stack: **Tauri**):
  - `AuralPrimer` (game runtime only)
  - `AuralStudio` (import/song-creation runtime only)
- Do not collapse these into a single mode-switched executable; separation is required for long-term maintainability.

### 7.2 Sidecar tooling
- Python ingest pipeline ships as OS-specific **sidecar executables**.
- No system Python required.

### 7.3 Decoding
- If MP3 decoding is required for ingest, bundle an internal decoder (e.g., ffmpeg sidecar) so import works offline.

### 7.4 Model management
- Models are stored under `assets/models/<model-id>/<version>/...`.
- The pipeline records model id/version fingerprints into the SongPack manifest.

## 8) Non-goals (for now)
- Online multiplayer / online leaderboards
- Always-online accounts
- Bundling ML model weights into installers

## 9) References
- `docs/architecture.md`
- `docs/songpack-spec.md`
- `docs/ingest-pipeline.md`
- `docs/packaging-ci.md`
- `docs/roadmap.md`

