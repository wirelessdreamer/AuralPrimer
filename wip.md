# AuralPrimer — WIP / Implementation Tracker

This file is the living implementation tracker for AuralPrimer.

It is intentionally **engineering-oriented**: concrete milestones, technical tasks, dependencies, and **TDD-first** acceptance criteria.

> Authoritative requirements: `spec.md`
> Architecture: `docs/architecture.md`
> File format: `docs/songpack-spec.md`
> Ingest pipeline: `docs/ingest-pipeline.md`
> Plugin API: `docs/visualization-plugins.md`
> Testing: `docs/testing-strategy.md`
> Release/packaging: `docs/packaging-ci.md`

---

## Global constraints (do not regress)

### Platforms
- Windows + Linux only.

### SongPack-first runtime
- Gameplay/runtime consumes SongPacks.
- External ecosystems are supported via **pluggable importers** that convert into SongPacks.

### Audio formats
- SongPacks may include compressed audio (`mix.mp3` / `mix.ogg`).
- Playback decoding is via a **local codec layer**.

### Models
- **Do not bundle model weights in installers.**
- Models are obtained post-install (in-app download or manual import) and stored under:
  - `assets/models/<model-id>/<version>/...`

### Engineering process
- **All development is TDD-first**.
- Any milestone is not complete without:
  - unit tests and/or contract tests
  - updated golden fixtures when outputs intentionally change
  - CI green

---

## Status

### Done (docs / blueprint)
- [x] High-level architecture drafted (`docs/architecture.md`)
- [x] SongPack spec drafted (`docs/songpack-spec.md`)
- [x] Ingest pipeline draft (`docs/ingest-pipeline.md`)
- [x] Packaging/CI draft (`docs/packaging-ci.md`)
- [x] Roadmap draft (`docs/roadmap.md`)
- [x] Requirements consolidated (`spec.md`)
- [x] TDD mandated across documentation (`spec.md`, `docs/testing-strategy.md`, etc.)

### Now (top priority)
- [x] Create the initial monorepo layout skeleton (apps/, packages/, python/, visualizers/, assets/, docs/)
- [x] Implement SongPack discovery + library indexing (scan folder; parse directory + zip `manifest.json`)
- [x] Implement SongPack schemas + validator (JSON schema + runtime validation) (manifest + core features + validateSongPack)
- [x] Implement host skeleton (Tauri) + startup library scan + SongPack details view
- [x] Implement plugin loader skeleton (viz-sdk + built-in plugin + lifecycle loop)
- [x] Implement audio playback/transport (MVP: HTMLAudio + SongPack audio load)
  - [x] Transport controller module (audio-backed clock + loop)
  - [x] UI controls: play/pause/stop/seek + loop
  - [x] Unit tests for transport behavior (jsdom)
- [x] Implement audio playback/transport (next: timebase abstraction + WebAudio backend)
  - [x] Transport timebase interface + refactor controller to depend on it
  - [x] HTMLAudio timebase implementation (keeps existing behavior)
  - [x] WebAudio timebase implementation (decoded AudioBuffer)
  - [x] UI backend toggle (HTMLAudio vs WebAudio)
  - [x] Tests updated to use fake timebase (no DOM audio dependency)

- [ ] (New) Native Rust audio engine (real-time DSP + instruments)
  - Goal: evolve from browser timebases into a single native engine suitable for low-latency playback, monitoring, FX, and instruments.
  - Phase 0 (scaffolding + tests)
    - [ ] Define `AudioEngine` Rust module/service boundary (commands/events)
    - [ ] Add unit tests for engine transport math (sample-accurate time, loop, seek) (pure Rust)
  - Phase 1 (playback-only)
    - [ ] Implement native output playback backend (cpal/WASAPI on Windows; ALSA/Pulse/JACK on Linux via cpal)
    - [ ] Wire host transport to native engine time instead of HTMLAudio/WebAudio
    - [ ] Provide device selection + sample rate + buffer size settings (best-effort)
    - [ ] Emit metering + xruns/underruns to UI (debug)
  - Phase 2 (FX graph)
    - [ ] Implement bus graph (master + song + metronome)
    - [ ] Add first FX: gain + simple distortion + simple compressor
    - [ ] Parameter automation path (lock-free)
  - Phase 3 (instruments / soundbanks)
    - [ ] Add sampler / soundbank playback driven by MIDI + events
  - Phase 4 (input monitoring)
    - [ ] Live input -> FX chain -> output
  - Phase 5 (ASIO / pro-audio) (stretch)
    - [ ] Investigate ASIO feasibility/licensing; likely start with WASAPI exclusive as default
- [ ] Implement ingest sidecar MVP (decode + beats/tempo + sections + chart gen)
  - [x] `python/ingest` project scaffold (`pyproject.toml`, `pytest`, `ruff`)
  - [x] Sidecar CLI skeleton (`aural_ingest stages|info|validate|import`)
  - [x] JSONL progress event emitter (`aural_ingest.progress`)
  - [x] Real decode + analysis stages (wav inputs supported without ffmpeg; non-wav requires ffmpeg)
  - [x] Determinism tests (synthetic click-track wav fixture) for decode+tempo+beats+sections+chart
  - [ ] Host import UI wiring to run sidecar + stream progress

- [ ] (New) GHWT-DE importer (MVP: preview audio)
  - [x] Add Configure UI section to scan/import GHWT-DE DLC songs
  - [x] Add Tauri commands: configure GHWT paths + scan DLC + import preview audio
  - [x] Runtime: allow SongPacks with `audio/mix.wav` to load/play
  - [x] Rust tests for DLC scan + import using non-copyrighted fixtures
  - [x] Add native folder picker (Browse…) for GHWT DATA root
  - [ ] Follow-ups:
    - [x] Bulk import (import all scanned DLC songs)
    - [x] Better error UI: missing vgmstream / decode failures + preflight check
    - [x] Import full stems (DLC*_1..3) and mix down to `audio/mix.wav` (fallback to preview)
    - [ ] Parse GH charts from `*.pak.xen` into canonical SongPack charts/features

- [ ] (New) Create SongPack from WAV stems + MIDI (song creator / importer)
  - [x] Spec: confirm SongPack output contract (manifest + audio + features)
  - [x] Desktop UI: Configure section with file pickers (stems WAVs + MIDI) + metadata (title/artist)
  - [x] Backend: create SongPack folder in songs directory
    - [x] validate stems (wav format, sample rate, channel count, duration)
    - [x] deterministic mixdown to `audio/mix.wav` (or copy if a single mix is provided)
    - [x] copy MIDI to `features/notes.mid`
    - [x] generate minimal `features/events.json` from MIDI notes
    - [x] generate `manifest.json` (duration_sec + stable song_id)
  - [x] Tests:
    - [x] Rust: fixture WAV+MIDI -> SongPack created; validates presence of artifacts
    - [ ] TS: library scan sees created SongPack and can load audio/mix.wav
- [x] Implement model manager (download/import + versioned storage under `assets/models/`)
  - [x] Models UI section (preferred + local import)
  - [x] Tauri commands: list/install model packs
  - [x] Zip format: modelpack.json + files/**, extracted into app data dir
- [x] (New) Define songs-folder location policy + persistence (default per OS + user override) and add tests
- [ ] (New) Add optional file-watcher for live library updates (post-startup scan)

- [ ] (New) Realtime MIDI I/O + clock sync (bidirectional)
  - [ ] Decide initial API boundary: host-direct (WebMIDI/tauri plugin) vs Rust service vs sidecar
  - [ ] Implement MIDI device enumeration + port selection UI
  - [ ] Implement MIDI input routing (note on/off + key CCs) into gameplay input bus
  - [ ] Implement MIDI clock input → transport sync (Start/Stop/Continue + Clock + SPP best-effort)
  - [ ] Implement MIDI clock output from transport (supports tempo slowdown + loop)
  - [ ] Implement tempo scaling when external clock drives transport (device tempo → song tempo factor)
  - [ ] SysEx support (opt-in per port) + safety controls
  - [ ] Contract tests: determinism + jitter tolerance + loop/seek behavior under MIDI sync

- [x] Build instructions: `BUILDING.md`

---

## Milestones (implementation)

### Milestone 0 — Repo + CI foundations (1–2 weeks)
**Goal**: Establish the project structure, test harnesses, and CI gates so all future work can be TDD-first.

**Completed in repo so far**
- Monorepo tree created
- Root Node tooling installed (`vitest`, `typescript`)
- TS tests run via `npm test`
- `packages/songpack` includes tested SongPack discovery + basic library indexing

**Deliverables**
- [x] Monorepo folders created (matching README layout).
- [x] Node workspace tooling chosen + configured (**npm workspaces** in root `package.json`).
- [ ] TS tooling: eslint/prettier baseline.
- [x] TS tooling: typescript + vitest baseline (`npm test`).
- [x] Rust tooling baseline: crate present + CI runs `fmt/clippy/test` when `apps/desktop/src-tauri/Cargo.toml` exists.
- [x] Python tooling baseline: `pytest` + `ruff` configured under `python/ingest/`.
- [x] CI: add `ruff` lint step (Python now runs `ruff check` + `pytest` in `lint-test`).

**TDD / testing deliverables**
- [x] “Hello test” for each language target:
  - TS: `vitest` runs in CI (`npm test`)
  - Rust: `cargo test` is configured to run in CI (`apps/desktop/src-tauri/tests/smoke.rs`)
  - Python: `pytest` runs in CI (`python/ingest/tests/test_smoke.py`)
- [ ] Contract-test scaffolding:
  - SongPack schema validation tests can run with at least one fixture.

**Exit criteria**
- [x] `lint-test` workflow *should be* green on PR with:
  - at least one TS test
  - at least one Rust test (when `apps/desktop/src-tauri` exists)
  - at least one Python test (when `python/ingest` exists)
  - (note: TS `lint` script is currently a placeholder; Python lint is real via `ruff check`)

**Dependencies / notes**
- Keep the “no directories → steps are skipped” behavior intact until code exists.

**Local dev note (Windows)**
- If `npm test` fails with a Rollup error about a missing optional dependency like `@rollup/rollup-win32-x64-msvc`, reinstall Node deps (e.g. `npm ci`). This is a known npm/optional-deps failure mode.

---

### Milestone 1 — SongPack core libraries + schemas (1–3 weeks)
**Goal**: Make SongPack real: validation, migrations (stub), and deterministic serialization.

**Progress**
- [x] `manifest.schema.json` created
- [x] TS manifest validation implemented with Ajv (`validateManifest`)
- [x] Feature schemas created: `beats.schema.json`, `tempo_map.schema.json`, `sections.schema.json`, `events.schema.json`
- [x] Feature schema created: `lyrics.schema.json` (karaoke timings)
- [x] TS validators added for features (`validateBeats`, `validateTempoMap`, `validateSections`, `validateEvents`)
- [x] TS validator added: `validateLyrics`
- [x] Minimal `chart.schema.json` created
- [x] `validateSongPack()` implemented for directory + zip SongPacks
- [x] Fixture updated: `minimal_valid.songpack` includes `features/lyrics.json`

**Deliverables**
- [x] JSON Schemas committed for:
  - `manifest.json`
  - `features/events.json`
  - `features/beats.json`
  - `features/tempo_map.json`
  - `features/sections.json`
  - `charts/*.json` (at least one chart schema)
- [ ] `packages/songpack` library:
  - [x] load directory SongPack
  - [x] load zip SongPack
  - [x] validate SongPack against JSON schemas
  - [x] canonical JSON serialization (stable key ordering)
  - [ ] version/migration entry points (even if only identity migration for v1)

**TDD / testing deliverables**
- [x] Schema tests (fast, always-on): fixtures under `assets/test_fixtures/songpacks/...`
- [x] Round-trip tests: parse → normalize → serialize stability
- [ ] Negative tests: missing files, invalid versions, out-of-range event times

**Exit criteria**
- [ ] `packages/songpack` can validate at least one fixture SongPack.
- [ ] CI fails if a schema breaks fixture validation.

**Dependencies / notes**
- This milestone unblocks host + plugin work.

---

### Milestone 2 — Desktop host skeleton (Tauri) + playback + plugin loader (2–4 weeks)
**Goal**: A minimal desktop app can load a SongPack and render a plugin synced to audio.

**Deliverables**
- [x] `apps/desktop` created (Tauri + TS UI).
- [x] Song library view (minimal): list SongPacks found in a configured folder.
- [x] Scan songs folder on startup to discover new/removed SongPacks (directory + zip containers).
- [x] Load SongPack + show basic metadata.
- [x] Audio playback + transport clock (MVP):
  - load `audio/mix.ogg` or `audio/mix.mp3` from selected SongPack
  - play/pause/stop/seek
  - drive `TransportState.t` from `audio.currentTime`
- [x] Audio playback + transport clock (next):
  - [x] loop region (t0..t1)
  - [x] transport timebase abstraction + WebAudio backend option
  - [x] tempo slowdown (playbackRate)
  - [x] metronome stub (WebAudio click)
- [x] Plugin loader skeleton (built-in plugin + lifecycle loop):
  - load ESM module (workspace plugin)
  - lifecycle: `init → resize → update → render → dispose`
- [x] Plugin loader (full):
  - [x] discover built-in plugins (bundled resources) and user plugins
  - [x] load ESM entrypoint (`dist/index.js`)
  - [x] lifecycle: `init → resize → update → render → dispose`
- [x] Host → visualizer song data (initial):
  - [x] Tauri command: `read_songpack_json(container_path, rel_path)` (restricted to `features/*.json`)
  - [x] Load `features/lyrics.json` best-effort when selecting a SongPack
  - [x] Pass into plugin init context: `VizInitContext.song.lyrics`

- [x] (New) Lyrics generation prompt (MVP):
  - [x] If user starts `viz-lyrics` and `features/lyrics.json` is missing, prompt to generate it
  - [x] Generation reads a user-selected `.txt` lyrics file and distributes lines uniformly across `manifest.duration_sec`
  - [x] Writes `features/lyrics.json` into **directory** SongPacks only (zip SongPacks are read-only for now)
- [x] Global HUD:
  - [x] always display **key + mode** (even if placeholder from fixture)

**TDD / testing deliverables**
- [ ] Host + plugin contract tests:
  - load reference plugin
  - run lifecycle smoke test
  - render N frames without crash
- [ ] Transport monotonicity tests

**Exit criteria**
- [ ] A fixture SongPack plays with a minimal plugin rendering beats/sections.
- [ ] Plugin contract tests run in CI.

**Dependencies / notes**
- Codec layer decision: pick an initial implementation path (see “Open questions”).
- Linux note: local Tauri builds require system deps (`pkg-config`, WebKitGTK, GTK dev libs). See `docs/local-dev-prereqs.md`.
- Windows note: a local Windows bundle build produces installers under:
  - `apps/desktop/src-tauri/target/release/bundle/msi/*.msi`
  - `apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe`

- Dev environment note: on Windows, if `cargo` isn't found when running `tauri build`, ensure `%USERPROFILE%\.cargo\bin` is on `PATH`.

#### UI refresh (modern high-tech vibe)
- [x] Apply dark/neon "high-tech" theme (CSS variables, glass panels, neon controls)
- [x] Improve layout/markup: branded header, panel sections
- [x] Add favicon and reduce Vite dynamic-import warnings for plugin loading

---

### Milestone 3 — Viz SDK + reference plugins (2–6 weeks)
**Goal**: Stabilize the visualization contract so plugins can iterate independently.

**Deliverables**
- [x] `packages/viz-sdk` (initial):
  - `Visualizer` interface types
  - minimal `TransportState` and frame context
- [x] `packages/viz-sdk` (init context extension):
  - `VizInitContext.song.lyrics?: unknown` (host-provided)
- [ ] `packages/viz-sdk` (next):
  - `SongHandle` query APIs with time-window queries
  - host services boundary (no direct filesystem access)
- [x] Reference plugins in `visualizers/` (initial):
  - [x] `viz-beats` (Canvas2D beat grid; lifecycle smoke target)
  - [x] `viz-lyrics` (Canvas2D karaoke-style lyrics highlighting)
  - [x] `viz-nashville` (chords lane; placeholder if chords missing)
  - [x] `viz-fretboard` (from MIDI notes)

**TDD / testing deliverables**
- [ ] SDK contract tests for API stability.
- [ ] Plugin smoke tests against fixture SongPacks.

**Exit criteria**
- [ ] A new plugin can be added/updated without changing the host.

---

### Milestone 4 — Ingest sidecar MVP (2–4 weeks)
**Goal**: Import local audio into a playable SongPack deterministically.

**Deliverables**
- [x] `python/ingest` project structure.
- [x] Sidecar CLI:
  - `aural_ingest import <source> --out ... --profile ...` (basic flags)
  - `aural_ingest validate <songpack-dir>` (file presence checks)
  - `aural_ingest info <songpack-dir>`
  - `aural_ingest stages`
- [ ] Pipeline stages (currently present but **placeholder outputs**, not real analysis):
  - [x] `init_songpack`
  - [~] `decode_audio` (currently copies input to `audio/mix.mp3`; no transcoding)
  - [~] `beats_tempo` (placeholder)
  - [~] `sections` (placeholder)
  - [~] `chart_generation` (placeholder)
- [x] Structured JSONL progress reporting.
- [ ] Host import UI to run sidecar and show progress.

**TDD / testing deliverables**
- [ ] Stage fingerprint determinism tests.
- [ ] Cache invalidation tests.
- [ ] Golden tests on short fixtures (beats/tempo within tolerance).

**Exit criteria**
- [ ] User can import an mp3/ogg and play resulting SongPack.
- [ ] Golden test suite catches accidental extraction regressions.

---

### Milestone 5 — Pluggable importers (content adoption track) (ongoing)
**Goal**: Multiple import sources feed SongPack without constraining internal capabilities.

**Deliverables**
- [ ] Define importer interface (concept + CLI flags):
  - importer id
  - input discovery/validation
  - conversion into canonical SongPack
- [ ] Importers:
  - [ ] `audio_only` (baseline)
  - [ ] `midi` (user-provided MIDI + optional audio)
  - [x] `ghwt_de` (MVP: preview audio import into SongPacks)

**TDD / testing deliverables**
- [ ] Importer contract tests per importer (fixtures in `assets/test_fixtures/import_sources/...`).

**Exit criteria**
- [ ] Adding a new importer does not require modifying existing importers.

---

### Milestone 6 — Model manager + post-install model packs (1–3 weeks)
**Goal**: Provide a first-class, deterministic model management story without bundling weights.

**Deliverables**
- [x] Host UI: “Models” screen (basic)
  - [x] list installed model packs
  - [x] download model pack (when online) (renderer fetch + Rust zip install; URL config per pack)
  - [x] import model pack from local zip path (offline)
  - [ ] show license info (not implemented yet)
- [x] Model pack format (implemented)
  - `modelpack.json` at zip root contains `{id, version, ...}`
  - `files/**` extracted under `assets/models/<id>/<version>/...` in app data dir
  - sha256 verification supported when downloading (optional expected hash)
  - safety: no overwrite + path traversal prevention
- [ ] Sidecar integration: stages declare required model id/version and resolve path.

**TDD / testing deliverables**
- [ ] Model pack verification tests (hash checking, version non-overwrite).
- [ ] Offline behavior tests (feature disabled until models present).

**Exit criteria**
- [ ] A model-dependent stage can be enabled once the model pack is installed.

---

### Milestone 7 — Realtime audio→MIDI / realtime identification (parallel track) (4–8+ weeks)
**Goal**: Local realtime audio processing feeds gameplay input without putting heavy ML in the host.

**Deliverables**
- [ ] Define runtime sidecar protocol:
  - host streams audio frames
  - sidecar outputs MIDI-like events with timestamps
  - latency calibration hooks
- [ ] First target: monophonic (voice/bass)

**TDD / testing deliverables**
- [ ] Deterministic simulation tests with recorded input buffers.
- [ ] Latency/jitter characterization harness.

**Exit criteria**
- [ ] Realtime events can drive a simple gameplay mechanic with acceptable latency.

---

## Critical path / dependency notes
- Milestone 1 (SongPack libs/schemas) unblocks Milestone 2 (host) and Milestone 3 (plugins).
- Milestone 0 is required before anything else (CI + tests).
- Milestone 4 (ingest MVP) is required for real content, but Milestone 2/3 can progress with fixtures.
- Model manager (Milestone 6) is required before any model-dependent stages can ship.

---

## Blocked / Questions (need decisions)

### A) Audio codec layer implementation choice (host)
Pick one initial approach:
- Option 1: bundle `ffmpeg` sidecar for decode to PCM/wav for playback
- Option 2: use platform audio stack decode where available + fallback sidecar
- Option 3: ship a small decoder library for mp3/ogg (license implications)

### B) Importer boundaries
- What is the minimum initial importer interface surface (CLI + filesystem contract) we want to freeze in v0.1?

### C) GHWT DE importer scope
- Exactly which files are in scope first (song metadata, charts, stems, etc.)?
- Confirm legal posture/documentation requirements.

### D) Model pack trust
- Should we require:
  - hash-only verification, or
  - signed manifests?

---

## Notes
- **Always update this file (`wip.md`) after completing any implementation task** (add/remove checkboxes, update status, and keep milestones consistent).
- This tracker is intentionally concrete; as implementation starts, break each milestone into issues with owners.
