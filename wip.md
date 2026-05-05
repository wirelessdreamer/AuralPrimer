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
> Recovery notes (2026-03-03): `PROJECT_ARCH_FROM_MEMORY.md`, `TRANSCRIPTION_RECOVERY_NOTES.md`, `DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md`, `TRANSCRIPTION_REGRESSION_HISTORY.md`

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
- Portable folder builds may pre-stage modelpack zip artifacts (for offline install workflows), but runtime install location remains `assets/models/...`.

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

### Recovery context (from notes; not yet revalidated in current tree)
- [x] Capture architecture + regression history from memory in four recovery notes
- [x] Restore Studio app surface and portable build scripts (`build_sidecar.ps1`, `create_portable.ps1`)
  - [x] Restored portable scripts: `build_sidecar.ps1`, `create_portable.ps1` (hash/timestamp freshness guard)
  - [x] Split products into two separate app surfaces: `apps/game` (AuralPrimer gameplay) and `apps/desktop` as AuralStudio (content creation)
- [x] Restore advanced sidecar CLI surface (`import-dir`, `import-dtx`, `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter`)
- [~] Restore drum transcription algorithms + fallback ordering with `combined_filter` default
  - [x] Added deterministic algorithm modules under `python/ingest/src/aural_ingest/algorithms/*` for all planned IDs
  - [x] Wired `transcribe_drums` stage to emit `features/events.json` from selected/fallback algorithm
  - [x] Replaced deterministic pattern stubs with class-based waveform-driven DSP rebuild (`TranscriptionAlgorithm` contract, shared pre/post-processing, weighted fusion in `combined_filter`)
  - [~] Real-world sparse-source fidelity is still not recovered: the default `combined_filter` path is currently over-liberal on at least one reported real song (`Psalm 12`), with likely false-positive pressure coming from three-detector fusion, aggressive expanded-kit remaps, permissive refractory settings, and the dense synthetic fallback path used when candidate recovery fails
  - [ ] Reach full pre-loss quality parity and optional ML-backed drum inference path
- [~] Restore melodic transcription paths (Basic Pitch + pYIN) and frozen-runtime model-path fallback
  - [x] Added melodic orchestration for `auto`/`basic_pitch`/`pyin` with fallback + warning propagation
  - [x] Added model lookup fallback order (`onnx -> tflite -> savedmodel`) in sidecar transcription module
  - [~] Replaced deterministic melodic stubs with waveform-driven monophonic pitch tracking + dyad expansion baseline
  - [ ] Reach full Basic Pitch/pYIN model-quality parity in packaged runtime
- [x] Restore desktop drum parser strict/relaxed guard and King in Zion regression fixtures/tests
- [x] Revalidate packaging discipline so portable builds always include the newest sidecar (timestamp/hash check)
- [x] Portable build now stages `demucs_6` modelpack (`keys/drums/guitar/bass/vocals`) with manifest validation
- [x] Resolve note conflict for `import-dir` events export ordering (`sections` before `events.json`) and lock with tests

### Transcription quality program (2026-05-02)
- [~] Treat transcription improvement as a repo-wide quality program across drums, bass, guitar, keys/piano, and import sync instead of one-off method tweaks.
  - [x] Added transcription profiles: `gameplay_default`, `fidelity_midi`, `research_ab`.
  - [x] Added unified quality benchmark module/CLI for full-corpus runs with profile metadata, optional backend status, gameplay metrics, sync quarantine, reports, and heatmaps.
  - [x] Added gameplay metrics for density, duplicates/chatter, polyphony, piano hand distribution, drum lane coverage, drum overlaps, and start-offset quarantine.
  - [x] Recorded transcription profile in import metadata and stable song-id fingerprinting.
  - [x] Added role playability cleanup for bass, lead guitar, and rhythm guitar.
  - [x] Added fail-safe optional research wiring for MT3/YourMT3, Basic Pitch, Transkun, PTI, hFT, torchcrepe, BeatNet, and Omnizart availability reporting.
  - [x] Added `torchcrepe` as explicit/research monophonic method only; not part of legacy/default fallback.
  - [x] Added quality epic tracker: `benchmarks/quality/TRANSCRIPTION_QUALITY_EPIC.md`.
  - [~] Add standalone Piano MIDI Refinement Workbench for source MIDI + audio A/B review.
    - [x] Captured requirements in `benchmarks/piano/PIANO_REFINEMENT_WORKBENCH.md`.
    - [x] Implement `refine-piano` CLI, static dashboard, candidate MIDI artifacts, and reference/no-reference scoring.
    - [ ] Validate on real Suno piano MIDI plus matching audio/reference cases.
  - [x] Generate benchmark manifests from scanned SongPacks/split-stem folders so full-corpus A/B runs are repeatable.
  - [x] Add bounded guard-run filters (`--role`, `--case-filter`, `--max-cases`) for generated quality manifests.
  - [x] Add self-contained classifier performance explorer for full report coverage: role/method/risk filters, per-class metrics, confusions, pitch summaries, and TP/FP/FN timelines.
    - [~] Run the generated full-corpus quality manifest on local guard cases and save report artifacts.
      - [x] First bounded guard smoke: Psalm 130 keys / `piano_auto`, role-filtered reference MIDI, saved at `benchmarks/quality/runs/20260502_100131_psalm-130-keys-guard-role-filtered`.
      - [ ] Expand guard run to drums, bass, rhythm guitar, lead guitar, and at least one additional keys/piano case before treating it as a promotion gate.
  - [x] Add promotion-gate evaluation that labels benchmark winners without automatically changing `gameplay_default`.
  - [ ] Implement active BeatNet beat/downbeat-prior adapter only if benchmark setup shows it is worth testing.
  - [ ] Implement active Omnizart research comparator only if benchmark setup shows it is worth testing.
  - [ ] Add split-folder and single-file analysis import smoke gates to the final promotion checklist.
  - [ ] Build portable only after targeted tests, full benchmark, import smoke tests, model-absence tests, sidecar runtime check, and packaging checks pass.

### Audit refresh (2026-04-20 repo scan)
- [x] Current app split is real in the tree: `apps/game` is the primary playback/gameplay runtime, while `apps/desktop` is the authoring/import surface.
- [~] `apps/desktop` still carries a hidden `legacyPlaybackScaffold` for shared transport/plugin code paths, but it is not the active end-user playback shell.
- [~] Reference visualizers are mixed-fidelity right now:
  - [x] `viz-lyrics` consumes real `features/lyrics.json` timing data when present.
  - [x] `viz-drum-highway` consumes host-provided parsed MIDI note events.
  - [ ] `viz-beats` still renders a placeholder 1-beat-per-second grid instead of SongPack beat/section data.
  - [ ] `viz-nashville` still renders a placeholder harmony lane because the host does not yet provide chord/key song data to plugins.
  - [ ] `viz-fretboard` still uses a placeholder time-driven cursor instead of host note/chord queries.
- [~] Release automation is still partial:
  - [x] `.github/workflows/lint-test.yml` runs TS, Python, Rust, SongPack fixture validation, and Rust coverage.
  - [ ] `.github/workflows/build-release.yml` still leaves Python sidecar build/upload as placeholders and still invokes the legacy `@auralprimer/desktop` build target instead of the current game/studio package split.

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

- [~] (New) Native Rust audio engine (real-time DSP + instruments)
  - Goal: evolve from browser timebases into a single native engine suitable for low-latency playback, monitoring, FX, and instruments.
  - Phase 0 (scaffolding + tests)
    - [x] Define `AudioEngine` Rust module/service boundary (commands/events)
    - [x] Add unit tests for engine transport math (sample-accurate time, loop, seek) (pure Rust)
  - Phase 1 (playback-only)
    - [x] Implement native output playback backend (cpal/WASAPI on Windows; ALSA/Pulse/JACK on Linux via cpal)
    - [x] Wire host transport to native engine time instead of HTMLAudio/WebAudio
    - [~] Provide device selection + sample rate + buffer size settings (best-effort)
      - [x] Output device selection UI + persisted native-device identity (`name + channels + sample_rate`)
      - [x] Handle sample-rate mismatch by resampling decoded PCM into engine/device sample rate (linear interpolation, tested edge cases)
      - [ ] Manual sample-rate + buffer-size override controls (currently auto-selected best-effort)
    - [~] Emit metering + xruns/underruns to UI (debug)
      - [x] Added callback overrun/debug counters to native engine state (`callback_count`, `callback_overrun_count`, `output_buffer_frames`)
      - [ ] Emit richer meter/xrun event stream to UI
  - Phase 2 (FX graph)
    - [ ] Implement bus graph (master + song + metronome)
    - [ ] Add first FX: gain + simple distortion + simple compressor
    - [ ] Parameter automation path (lock-free)
  - Phase 3 (instruments / soundbanks)
    - [ ] Add sampler / soundbank playback driven by MIDI + events
  - Phase 4 (input monitoring)
    - [ ] Live input -> FX chain -> output
  - Phase 5 (ASIO / pro-audio) (stretch)
    - [~] Investigate ASIO feasibility/licensing; likely start with WASAPI exclusive as default
      - [x] Added optional ASIO build feature (`--features asio`) + runtime host selection UI (host + device)
      - [ ] Validate SDK/licensing/distribution policy and default-host strategy for production installer builds
- [~] Implement ingest sidecar MVP (decode + beats/tempo + sections + SongPack feature generation)
  - [x] `python/ingest` project scaffold (`pyproject.toml`, `pytest`, `ruff`)
  - [x] Sidecar CLI surface (current): `aural_ingest stages|info|validate|import|import-dir|import-dtx|runtime-check|benchmark-drums`
  - [x] JSONL progress event emitter (`aural_ingest.progress`)
  - [x] Real decode + analysis stages (wav inputs supported without ffmpeg; non-wav requires ffmpeg)
  - [x] Determinism tests (synthetic click-track wav fixture) for decode+tempo+beats+sections+SongPack feature outputs (`notes.mid` / `events.json`)
  - [x] Host import UI wiring to run sidecar + stream progress
    - [x] Configure panel ingest controls now call desktop `ingest_import` command end-to-end
    - [x] Stream per-stage JSONL progress events into Configure UI during import
  - [x] Runtime-check surface for dependency/modelpack health is wired through the sidecar and desktop UI
- [ ] (Recovery) Restore lost transcription stack + regression protections from 2026-03-03 notes (see Milestone 4A)
- [x] Strengthen automated test coverage gates (TS + Python) and expand Rust core unit coverage
  - [x] TS coverage reporting + thresholds (`vitest --coverage`)
  - [x] Python coverage reporting + fail-under gate (`pytest --cov`, fail-under 80 via `python/ingest/pyproject.toml`)
  - [x] Add high-value unit tests for untested Rust modules (`wav_mix`, `audio_decode`, `models`, `midi_clock_service`)
  - [x] Rust `cargo llvm-cov` CI step wired into `.github/workflows/lint-test.yml`

- [~] (New) GHWT-DE importer (audio import/stem mixdown working; chart parsing still pending)
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

- [~] (New) Create SongPack from WAV stems + MIDI (song creator / importer)
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
- [~] (New) Raw song folder importer (Suno/export folder -> canonical SongPack)
  - [x] Inspect raw folder contents (stems, MIDIs, lyrics) and surface detected-role + timing warnings
  - [x] Import folder into SongPack with `audio/mix.wav`, copied source MIDIs, normalized combined `features/notes.mid`, and optional lyrics carry-through
  - [x] Suno/raw-song import currently treats normalized source MIDI as the chart/timing authority when a playable mapping is found (`suno_source_midi_normalized`, `timing_authority = normalized_source`)
  - [x] Preserve Suno source MIDI drum note identities during raw-song import; safe start-time normalization still applies, but the importer no longer auto-canonicalizes drum pitches against the audio stem (this had been producing non-matching drum output relative to source MIDI)
  - [x] When a role-specific source MIDI/audio start delta is wildly unstable (>2s), raw-song import now falls back to the cross-track median normalization offset instead of dropping that role from the gameplay chart; this targets Psalm 10-style drum/audio sync failures while keeping source MIDI authoritative
  - [x] Raw-song import now derives non-drum gameplay roles from per-track MIDI names/channels when source filenames are generic, so bass/guitar/keys charts survive import and render instead of being silently dropped
  - [x] UI wiring exists in both `apps/game` and `apps/desktop`
  - [ ] Studio import UX should explicitly show which import engine/path was used and what the authoritative chart source is (for example: `raw_song_folder / Suno source MIDI` vs `sidecar ingest / combined_filter`)
  - [ ] Add richer authoring overrides / broader source heuristics and more fixture coverage
- [x] Implement model manager (download/import + versioned storage under `assets/models/`)
  - [x] Models UI section (preferred + local import)
  - [x] Tauri commands: list/install model packs
  - [x] Zip format: modelpack.json + files/**, extracted into app data dir
- [x] (New) Define songs-folder location policy + persistence (default per OS + user override) and add tests
- [ ] (New) Add optional file-watcher for live library updates (post-startup scan)
- [~] (New) Drum benchmark / reference shootout tooling
  - [x] Fixture corpus + manifest under `assets/test_fixtures/drum_benchmark_midis`
  - [x] Python CLI/scripts for `benchmark-drums` and manual reference shootouts
  - [ ] CI thresholds / published dashboards are not wired yet

- [~] (New) Realtime MIDI I/O + clock sync (bidirectional)
  - [x] Decide initial API boundary: Rust MIDI service + Tauri commands/events (no WebMIDI dependency)
  - [x] Implement MIDI device enumeration + port selection UI
    - [x] Windows builds use `midir` WinRT enumeration so modern MIDI endpoints are visible; macOS/Linux remain CoreMIDI/ALSA.
    - [x] Input port selection now persists and re-resolves by stable backend port id before falling back to name.
  - [~] Implement MIDI input routing (note on/off + key CCs) into gameplay input bus
    - [x] Native callback now emits structured `midi_input_message` events (note on/off, CC, pitch bend, program/pressure, realtime, SPP, optional SysEx)
    - [x] Host forwards MIDI input events onto a window-level app event (`auralprimer:midi-input`) for gameplay integration points
    - [x] Frontend input bus now maintains active keyboard notes, sustain-held state, and monitor output for hardware testing
    - [x] Piano-roll keyboard now highlights live MIDI input notes while a keys chart is loaded
    - [ ] Map `auralprimer:midi-input` into concrete gameplay scoring/hit-window evaluators
  - [x] Implement MIDI clock input -> transport sync (Start/Stop/Continue + Clock + SPP best-effort)
  - [x] Implement MIDI clock output from transport (supports tempo slowdown + loop)
  - [x] Implement tempo scaling when external clock drives transport (device tempo -> song tempo factor)
  - [x] SysEx support (opt-in per port) + safety controls
  - [~] Contract tests: determinism + jitter tolerance + loop/seek behavior under MIDI sync
    - [x] Added Rust unit coverage for inbound message parsing + outbound message validation/SysEx policy
    - [x] Added frontend unit coverage for active-note/sustain/all-notes-off tracking
    - [ ] Add deterministic fake-device integration tests for realtime jitter/loop behavior under sustained clock traffic

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
- [x] Contract-test scaffolding:
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
- [x] `packages/songpack` library:
  - [x] load directory SongPack
  - [x] load zip SongPack
  - [x] validate SongPack against JSON schemas
  - [x] canonical JSON serialization (stable key ordering)
  - [x] version/migration entry points (identity migration for v1 is implemented)

**TDD / testing deliverables**
- [x] Schema tests (fast, always-on): fixtures under `assets/test_fixtures/songpacks/...`
- [x] Round-trip tests: parse → normalize → serialize stability
- [x] Negative tests: missing files, invalid versions, out-of-range event times

**Exit criteria**
- [x] `packages/songpack` can validate at least one fixture SongPack.
- [x] CI fails if a schema breaks fixture validation.

**Dependencies / notes**
- This milestone unblocks host + plugin work.

---

### Milestone 2 — Desktop host skeleton (Tauri) + playback + plugin loader (2–4 weeks)
**Goal**: A minimal desktop app can load a SongPack and render a plugin synced to audio.

**Audit note (2026-04-20)**
- Active playback/runtime work now lives primarily in `apps/game`.
- `apps/desktop` focuses on authoring/import flows and retains a hidden `legacyPlaybackScaffold` for shared playback/plugin code.

**Deliverables**
- [x] `apps/game` + `apps/desktop` created (Tauri + TS UI; gameplay/runtime is centered in `apps/game`).
- [x] Song library view (minimal): list SongPacks found in a configured folder.
- [x] Scan songs folder on startup to discover new/removed SongPacks (directory + zip containers).
- [x] Load SongPack + show basic metadata.
- [x] Audio playback + transport clock (MVP):
  - load `audio/mix.wav`, `audio/mix.ogg`, or `audio/mix.mp3` from selected SongPack
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
- [~] Host + plugin contract tests:
  - [x] built-in/user plugin loading + discovery coverage exists
  - [ ] instantiate reference plugins and run lifecycle/render smoke frames end-to-end
- [~] Transport monotonicity tests
  - [x] transport/native-timebase unit coverage exists
  - [ ] add explicit monotonic/property-style transport clock assertions

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
  - [x] host-provided `charts`, `notesMidiBytes`, parsed `notes`, and optional `players` metadata
- [ ] `packages/viz-sdk` (next):
  - `SongHandle` query APIs with time-window queries
  - host services boundary (no direct filesystem access)
- [x] Reference plugins in `visualizers/` (initial):
  - [x] `viz-beats` (Canvas2D beat grid; lifecycle smoke target)
  - [x] `viz-lyrics` (Canvas2D karaoke-style lyrics highlighting)
  - [x] `viz-nashville` (chords lane; placeholder if chords missing)
  - [x] `viz-fretboard` (present in tree; currently placeholder cursor until richer note queries are exposed)
  - [x] `viz-drum-highway` (host-provided MIDI notes mapped to drum lanes)

**Current fidelity notes**
- `viz-lyrics` is data-driven when `features/lyrics.json` exists.
- `viz-drum-highway` is data-driven from host-provided parsed MIDI note events.
- `viz-beats`, `viz-nashville`, and `viz-fretboard` still contain placeholder logic and need richer host song-query APIs to become fully data-driven.

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
  - `aural_ingest import-dir <source-dir> --out ... --profile ...` (directory source picker)
  - `aural_ingest validate <songpack-dir>` (file presence checks)
  - `aural_ingest info <songpack-dir>`
  - `aural_ingest stages`
- [x] `aural_ingest runtime-check`
- [x] `aural_ingest benchmark-drums <stem> <reference>`
- [~] Pipeline stages (current repo pipeline has moved beyond the original `chart_generation` MVP and now centers on `notes.mid`/`events.json` outputs):
  - [x] `init_songpack`
  - [x] `decode_audio` (writes deterministic `audio/mix.wav`; non-wav decode requires ffmpeg)
  - [x] `beats_tempo` (deterministic BPM estimate + generated beat grid, plus optional `high_accuracy` `librosa.beat_track` mode with fallback metadata)
  - [x] `sections` (generated section blocks from duration + BPM)
  - [~] `separate_stems` (Demucs-backed when modelpack/runtime is present; still optional / best-effort)
    - [x] Added deterministic guitar stem split stage that emits `audio/stems/lead_guitar.wav` + `audio/stems/rhythm_guitar.wav` (uses `audio/stems/guitar.wav` when present, else mix fallback)
    - [ ] Integrate broader multi-stem outputs / production-quality separation beyond the current best-effort path
  - [x] Drum + melodic transcription emit `features/notes.mid` and `features/events.json`
  - [~] JSON chart generation remains limited; there is no standalone `chart_generation` Python stage in the current tree
- [x] Structured JSONL progress reporting.
- [x] Host import UI to run sidecar and show progress.

**TDD / testing deliverables**
- [ ] Stage fingerprint determinism tests.
- [ ] Cache invalidation tests.
- [ ] Golden tests on short fixtures (beats/tempo within tolerance).

**Exit criteria**
- [ ] User can import an mp3/ogg and play resulting SongPack.
- [ ] Golden test suite catches accidental extraction regressions.

---

### Milestone 4A — Transcription recovery from lost unpushed repo (notes-driven) (2–6 weeks)
**Goal**: Rebuild the previously working transcription stack and regression guards captured in the four recovery notes.

**Deliverables**
- [~] Recreate sidecar transcription modules (`python/ingest/src/aural_ingest/transcription.py`, `algorithms/*`)
  - [x] Orchestration scaffold added in `transcription.py` (fallback-chain + selector validation + result contract)
  - [x] Added concrete deterministic recovery stubs under `algorithms/*` for all target algorithm IDs
- [~] Recreate full DSP/ML algorithm implementations under `algorithms/*`
  - [x] Replaced deterministic drum pattern emitters with waveform-driven onset + event-classification logic
  - [x] Implemented documented drum recipes (shared preprocessing, adaptive peak-pick, class refractory/de-dup, velocity map, algorithm-specific fusion/classification)
  - [ ] Add ML-backed and/or higher-fidelity MIR implementations to match pre-loss quality expectations
- [x] Reintroduce CLI/import modes:
  - [x] `import-dir` (MVP directory audio source selection + forward to import pipeline)
  - [x] `import-dtx` (MVP: resolve DTX-referenced audio or chart-folder audio, then forward to import pipeline)
  - [x] `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter` (validated parsing + import pass-through)
  - [~] `--multi-filter` is currently parsed/persisted plumbing; a distinct multi-engine execution path is not yet evident in the current pipeline
- [x] Rebuild drum algorithm set: `combined_filter`, `dsp_bandpass_improved`, `dsp_spectral_flux`, `adaptive_beat_grid`, `dsp_bandpass`, `aural_onset`, `librosa_superflux`
- [~] Set default drum path to `combined_filter`; preserve documented fallback ordering; log unknown algorithm IDs instead of silent adaptive fallback
  - [x] Fallback-chain behavior and unknown-ID handling encoded in `python/ingest/src/aural_ingest/transcription.py`
  - [x] Fallback-selection results are now wired into ingest drum stage + persisted in manifest transcription metadata
- [~] Rebuild melodic path (`auto`/`basic_pitch`/`pyin`) including frozen-runtime model lookup fallback (`onnx -> tflite -> savedmodel`)
  - [x] `transcribe_melodic` orchestration + fallback chain restored in `python/ingest/src/aural_ingest/transcription.py`
  - [x] `basic_pitch` model path resolver restored with `onnx -> tflite -> savedmodel` preference
  - [x] Import pipeline now emits melodic notes in `features/events.json` and persists `melodic_method_used`
  - [~] Swap deterministic melodic stubs for real Basic Pitch/pYIN backend implementations
    - [x] Added waveform-driven monophonic tracking baseline (`pyin`) and dyad expansion path (`basic_pitch`) with model gate
    - [ ] Integrate true Basic Pitch/pYIN inference backends (model/runtime parity)
- [x] Restore Rust command forwarding of drum/melodic args from UI to sidecar
  - [x] Added Rust `ingest_import` Tauri command + sidecar CLI arg builder with explicit forwarding for drum/melodic flags
  - [x] Added app client wrappers (`apps/desktop/src/ingestClient.ts`, `apps/game/src/ingestClient.ts`) and tests to lock payload forwarding
  - [x] Wire Configure UI import controls to call `ingest_import` end-to-end
- [x] Restore chart parser strict/relaxed guard so sparse dedicated drum tracks are not dropped
  - [x] Added `apps/desktop/src/chartLoader.ts` strict/relaxed selection logic with dedicated drum-track guard
  - [x] Added desktop regression tests for strict-vs-relaxed and King in Zion sparse-drums behavior
  - [x] Integrated chart loader into active gameplay song selection path (`read_songpack_mid` + capability/instrument availability plumbing)
- [x] Rebuild portable packaging flow that always copies latest sidecar before ship
- [x] Verify/fix `import-dir` ordering around `sections` and `events.json`

**TDD / testing deliverables**
- [~] Python: `test_transcription_resilience.py` recovered with fallback and algorithm-diversity assertions
  - [~] Current assertions explicitly reward expanded note diversity for `combined_filter`; they do not yet guard against sparse-source false positives like the Psalm 12 report
  - [ ] Add a sparse-source regression fixture/assertion that fails when exported drum MIDI contains hallucinated/non-source drum hits
- [x] Python: `test_import_pipeline.py` includes `import-dir` events-export ordering regression coverage
- [x] Desktop: `chartLoader.test.ts` strict-vs-relaxed behavior and sparse-drum preservation cases
- [x] Desktop: `chartLoader.kingInZionRegression.test.ts` fixture regression test
- [x] End-to-end smoke: same stem A/B (`adaptive_beat_grid` vs `combined_filter`) confirms expanded-kit distribution in `combined_filter` (covered by ingest algorithm regression tests)
  - [~] This currently validates diversity more than fidelity; add a counterbalancing precision-oriented fixture before treating default import quality as recovered

**Exit criteria**
- [~] Default import path reproduces expanded drum-note diversity on known regression fixtures, but that is now known to be an incomplete/possibly wrong success metric for sparse real-world material
- [ ] Default import path preserves sparse-source fidelity without hallucinated expanded-kit hits (add Psalm 12-equivalent regression coverage before calling recovery complete)
- [x] Portable package contains sidecar matching just-built hash/timestamp
- [~] Regression suites above run in CI and prevent fallback/order regressions, but they do not yet protect against sparse-source false positives in real-world drum imports

---

### Milestone 5 — Pluggable importers (content adoption track) (ongoing)
**Goal**: Multiple import sources feed SongPack without constraining internal capabilities.

**Deliverables**
- [ ] Define importer interface (concept + CLI flags):
  - importer id
  - input discovery/validation
  - conversion into canonical SongPack
  - importer provenance surfaced in Studio UI and persisted in import results/metadata (`importer_id`, engine/path used, chart authority)
- [ ] Importers:
  - [~] `audio_only` (baseline sidecar import path exists; stable importer interface is not frozen)
  - [~] `midi` (stem+MIDI SongPack creation and raw-song folder import exist; formal importer interface is not frozen)
  - [x] `ghwt_de` (MVP: preview audio import into SongPacks)
  - [x] `raw_song_folder` / Suno-style export folder import (inspection + SongPack creation)
    - [x] Uses normalized Suno MIDI as source-of-truth gameplay timing when the MIDI is mappable
    - [ ] Make that source-of-truth choice obvious in Studio so users do not confuse it with heuristic sidecar transcription

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
- [~] Sidecar integration: stages declare required model id/version and resolve path.
  - [x] `stages` / `runtime-check` expose required model metadata for Demucs and MT3 drum engines
  - [x] melodic model resolution fallback (`onnx -> tflite -> savedmodel`) is restored in transcription code
  - [ ] fully gate all model-dependent stages from host UX when required packs are missing

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

## Implementation deep dive (research-backed, as of 2026-03-03)

### A) Native audio engine (Rust): concrete implementation plan
- [x] Keep `cpal` as the output I/O layer (current direction), but remove `Mutex` use from the audio callback path.
- [~] Introduce lock-free control/data channels:
  - [x] `rtrb` SPSC ring buffer for control commands (`play/pause/seek/loop/rate`) into the audio thread.
  - [ ] dedicated lock-free meter/event queue back to UI thread (xruns, level peaks, callback timing stats).
- [ ] Add thread priority promotion in native backend:
  - [ ] enable `cpal` `audio_thread_priority` feature where available
  - [ ] explicit fallback path if promotion fails (log + continue)
- [ ] Decode + resample pipeline split:
  - [ ] decode via `symphonia` on control thread
  - [ ] sample-rate conversion via `rubato::process_into_buffer()` with pre-allocated buffers only
  - [ ] callback consumes already prepared/interleaved blocks
- [ ] Transport clock source of truth:
  - [ ] maintain `sample_count_rendered` (`u64`) in callback
  - [ ] derive `t_sec = sample_count / sample_rate` for host sync
  - [ ] keep loop math sample-accurate (already scaffolded in `audio_engine.rs`)
- [ ] Deadline and xrun instrumentation:
  - [ ] use `OutputCallbackInfo.timestamp().callback/playback` to measure callback lead time/jitter
  - [ ] track callback runtime histogram (`p50/p95/p99`) and deadline misses
- [ ] Device model:
  - [x] enumerate supported output configs first; do not assume default cfg is valid for target format
  - [x] store persisted device selection as stable identity (`name + channels + sample_rate`) with re-resolution on startup

### B) MIDI I/O + clock sync: concrete implementation plan
- [ ] Keep `midir` as device I/O layer and retain `midly` for file-level MIDI parsing.
- [ ] Promote existing clock subsystem from "best-effort" to deterministic service:
  - [ ] output clock scheduler anchored to transport sample clock (not wall clock)
  - [ ] input clock via tempo PLL/smoother (EMA + outlier clamp + bounded drift correction)
  - [ ] SPP handling on seek and loop boundary transitions
- [ ] Add explicit clock ownership modes:
  - [ ] `internal_master` (AuralPrimer drives MIDI clock)
  - [ ] `external_slave` (incoming MIDI clock drives transport)
  - [ ] `hybrid_guarded` (external clock accepted only after lock + confidence threshold)
- [ ] SysEx safety:
  - [ ] opt-in per port
  - [ ] explicit max message size
  - [ ] rate limits + allow-list profile hooks

### C) Ingest/transcription recovery stack: concrete implementation plan
- [x] Restore sidecar CLI surface from recovery notes:
  - [x] `import-dir`, `import-dtx`
  - [x] `--drum-filter`, `--melodic-method`, `--shifts`, `--multi-filter`
- [x] Drum transcription architecture:
  - [x] restore algorithm modules (`combined_filter`, `dsp_bandpass_improved`, `dsp_spectral_flux`, `adaptive_beat_grid`, `dsp_bandpass`, `aural_onset`, `librosa_superflux`)
  - [x] enforce explicit fallback chain from notes
- [~] Melodic transcription architecture:
  - [x] `basic_pitch` requested path + `pyin` fallback chain restored in orchestration
  - [x] frozen-runtime model resolution order from notes (`onnx -> tflite -> savedmodel`)
  - [ ] reach full Basic Pitch/pYIN runtime-quality parity
- [~] Beat/tempo quality upgrade:
  - [x] keep deterministic MVP pipeline in place
  - [x] add optional higher-accuracy mode using `librosa.beat.beat_track` with fallback metadata when unavailable
  - [ ] evaluate whether Essentia-backed offline extraction is still needed
- [~] Stem separation provider model:
  - [x] keep separator pluggable (`none`, `demucs`, future providers)
  - [~] Demucs remains a best-effort, replaceable provider rather than a fully hardened default
  - [ ] note: upstream `facebookresearch/demucs` is archived; treat provider as "best-effort, replaceable"

### D) Packaging + sidecar reliability hardening
- [ ] Tauri sidecar contract hardening:
  - [ ] define `bundle.externalBin` entries for desktop + game sidecars
  - [ ] ensure target-triple suffixed binaries are generated/copy-validated pre-bundle
  - [ ] run sidecars via `shell().sidecar("<name>")` only
- [ ] PyInstaller reliability for model-based sidecar:
  - [ ] build with explicit `--collect-data basic_pitch` (or `--collect-all basic_pitch` when needed)
  - [ ] runtime checks for `sys.frozen` and `sys._MEIPASS` path resolution
  - [ ] store emitted `build_manifest.json` with sidecar hash + model asset hash
- [x] Portable build freshness guard:
  - [x] `create_portable.ps1` fails packaging if sidecar hash in portable root does not match the just-built sidecar artifact
  - [x] `create_portable.ps1` stages `modelpacks/demucs_6.zip` and validates `modelpack.json` id + required stem roles
  - [x] `build_sidecar.ps1` now rebuilds from Python source by default (unless `-SkipBuild`), preventing stale sidecar reuse

### E) Benchmarking and regression harness (mandatory)

#### E1) Rust performance benchmarks
- [ ] Add `criterion` benches for:
  - [ ] transport math (`seek`, `loop wrap`, large-step modulo)
  - [ ] mixing kernels (gain/distortion/compressor blocks)
  - [ ] decode + resample throughput
- [ ] Add `iai-callgrind` benches for deterministic instruction-level regressions in DSP hot paths.

#### E2) Audio real-time system benchmarks
- [ ] Add native soak benchmark runner (`--bench-audio`) that executes 10/30/60 minute sessions.
- [ ] Collect:
  - [ ] callback duration stats (`p50/p95/p99/max`)
  - [ ] deadline miss count/rate
  - [ ] output drift vs transport clock
- [ ] Target gates (phase-1):
  - [ ] deadline miss rate < 0.1% at 48kHz / 256 frames
  - [ ] callback `p99` runtime <= 40% of buffer period
  - [ ] no transport discontinuity > 1 audio block except explicit seek

#### E3) MIDI sync benchmarks
- [ ] Build synthetic clock fixture generator (controlled jitter, drift, missing ticks, start/stop bursts).
- [ ] Metrics:
  - [ ] tempo estimation error (mean absolute BPM error)
  - [ ] tick-to-transport phase error (ms)
  - [ ] lock acquisition/reacquisition time after dropout
- [ ] Target gates:
  - [ ] steady-state phase error p95 <= 2ms (internal master)
  - [ ] external clock lock within <= 2 beats after valid start

#### E4) Python ingest/transcription benchmarks
- [ ] Add `pytest-benchmark` suites for:
  - [ ] per-stage runtime (decode, beats, sections, chart, drums, melodic)
  - [ ] memory footprint and peak resident set size sampling
- [ ] Persist baselines and fail on regression using:
  - [ ] `--benchmark-save=<baseline>`
  - [ ] `--benchmark-compare=<baseline>`
  - [ ] `--benchmark-compare-fail=mean:<threshold>`

#### E5) Quality benchmarks (audio ML / MIR)
- [~] Separation quality:
  - [x] Add optional fail-safe `museval` SDR protocol adapter for local reference/estimate stem comparisons.
  - [x] Report MUSDB18/MUSDB18-HQ dataset roots as internal-only benchmark sources and explicitly prevent product-shipping assumptions.
  - [ ] Run MUSDB18/MUSDB18-HQ comparisons after local dataset roots are configured.
- [x] Transcription quality:
  - [x] Evaluate referenced melodic/piano note events with `mir_eval.transcription` (precision/recall/F1/overlap).
  - [x] Include both onset-only and onset+offset scoring modes in `summary.json` and `report.md`.
- [~] Drum-specific datasets for regression fixtures:
  - [x] Report ENST-Drums root/status as internal-only benchmark source.
  - [x] Report IDMT-SMT-Drums root/status as internal-only benchmark source.
  - [ ] Generate local drum benchmark manifests from ENST/IDMT once dataset roots are configured.

#### E6) Frontend/runtime performance benchmarks
- [~] Add `vitest bench` suites for parser/mapping hot paths and plugin update loops.
  - [x] Added `apps/game/benchmarks/frontend.bench.ts` covering MIDI parser, drum chart selection, melodic track selection, key-signature inference, and built-in visualizer update/render loops.
  - [x] Added `npm run bench:frontend` to write `benchmarks/frontend/vitest-bench.latest.json`.
  - [ ] Extend visualizer coverage to all built-ins after placeholder visualizers are backed by real data contracts.
- [~] Add bench artifact comparison in CI (`vitest bench --outputJson` + `--compare`).
  - [x] Added `npm run bench:frontend:compare` with optional baseline comparison against `benchmarks/frontend/vitest-bench.baseline.json`.
  - [x] Added CI benchmark artifact upload for `benchmarks/frontend/*.json`.
  - [ ] Freeze a versioned frontend benchmark baseline and threshold policy before making comparison failures PR-blocking.
- [ ] Add Playwright trace-based end-to-end perf captures for import/playback/plugin rendering paths.
  - [ ] Requires a deterministic app-runner fixture that can launch the game/studio shell and export traces in CI.

### F) CI enforcement upgrades
- [~] Add dedicated benchmark workflows:
  - [~] `bench-rust` artifact workflow added; current runner writes skip/summary artifacts until Criterion/IAI benchmark targets exist.
  - [x] `bench-python` pytest-benchmark workflow and JSON artifacts added for opt-in ingest runtime benchmarks.
  - [x] `bench-ts` vitest bench workflow and JSON artifacts added for frontend/parser/plugin hot paths.
- [~] Add quality-gate workflow:
  - [~] Transcription/separation quality summaries are checked when present; active CI fixture scoring still needs committed/synthetic promotion fixtures and configured private dataset roots.
  - [x] Added versioned threshold config at `benchmarks/thresholds.yml` plus dependency-free threshold checker.
  - [~] Threshold mode is `warn` by default; switch to strict only after baseline and threshold policy are frozen.
- [x] Publish benchmark dashboards as CI artifacts for PRs touching `apps/*/src-tauri`, `python/ingest`, parser/frontend benchmark paths, or quality benchmark paths.

### G) Research-driven decision gates to resolve before implementation lock
- [x] Choose realtime-safe queue strategy for audio callback (`rtrb` only vs dual-queue design for metrics).
- [x] Choose beat/tempo production default (`librosa`-first vs Essentia-first) for deterministic imports.
  - [x] Production default is `high_accuracy` / `librosa.beat_track` first, with `standard` energy-autocorrelation fallback.
  - [x] Essentia remains a research candidate, not a default, until adapter/benchmark/packaging evidence justifies it.
- [x] Choose separator support policy (ship Demucs provider as optional experimental vs fully supported path).
  - [x] Demucs is optional experimental under `auto`; absence must skip separation and continue with provided stems or mix fallback.
  - [x] GPU acceleration is supported when available, with CPU fallback/model-absence safety still required.
- [x] Freeze benchmark threshold policy for PR blocking (strict vs warn-only for first 2 weeks).
  - [x] Threshold config remains `warn` mode; strict PR blocking is disabled until representative baselines, role thresholds, hardware profile, and reviewed fixtures are frozen.
  - [x] Once those are frozen, keep warn-only for at least 14 days before enabling strict gates.

### H) Clarifications from project owner
- [x] Primary success metric priority is best transcription quality.
  - [x] Lowest-latency playback/runtime remains important, but it is not the top priority for transcription/import method selection.
  - [x] Fastest import throughput is secondary to recognizable, playable, high-quality transcription output.
- [x] Target hardware baseline for perf gates: anything remotely modern should be supported on Windows/Linux.
  - [x] Converted into concrete profiles in `benchmarks/thresholds.yml`: `minimum_modern` = 8 logical CPU threads / 16 GB RAM / x64-or-arm64 / no GPU required for default import; `recommended_model_workstation` = 12 logical CPU threads / 32 GB RAM / GPU recommended for model-backed A/B.
  - [x] Added `npm run bench:hardware` to capture `benchmarks/hardware/local-profile.latest.json` and upload it with benchmark CI artifacts.
- [x] Legal/product stance for research-only datasets: allowed for internal benchmarking and evaluation only.
  - [x] Do not ship research-only dataset content, derived in-game content, or dataset-dependent fixtures in the game/product.
  - [x] Keep distributable fixtures synthetic, owned, permissively licensed, or explicitly cleared.
- [~] Additional dedicated Studio-only surface beyond `apps/desktop` is deferred.
  - [~] No extra product-surface answer is needed right now; continue using `apps/desktop`/AuralStudio for current recovery work.
- [x] GPU acceleration is in scope and should be supported first class for sidecar/model-backed transcription.
  - [x] CPU fallback remains required for portability and model-absence safety.

### I) External references used for this deep-dive section
- CPAL docs: `https://docs.rs/cpal/latest/cpal/`
- CPAL feature flags (including `audio_thread_priority`): `https://docs.rs/crate/cpal/latest/features`
- Symphonia docs: `https://docs.rs/symphonia/latest/symphonia/`
- Rubato docs (`process_into_buffer`): `https://docs.rs/rubato/latest/rubato/`
- RTRB docs: `https://docs.rs/rtrb/latest/rtrb/`
- Tauri sidecar binaries (`externalBin`): `https://tauri.app/develop/sidecar/`
- Tauri shell sidecar execution: `https://v2.tauri.app/plugin/shell/#running-sidecars`
- PyInstaller manual: `https://pyinstaller.org/en/stable/index.html`
- PyInstaller operating mode (`onefile`, `sys._MEIPASS`): `https://pyinstaller.org/en/stable/operating-mode.html`
- PyInstaller usage (`--collect-data`, `--collect-all`): `https://pyinstaller.org/en/stable/usage.html`
- Spotify Basic Pitch repository/docs: `https://github.com/spotify/basic-pitch`
- Librosa beat tracking: `https://librosa.org/doc/latest/generated/librosa.beat.beat_track.html`
- Librosa pYIN: `https://librosa.org/doc/latest/generated/librosa.pyin.html`
- Essentia rhythm extractor: `https://essentia.upf.edu/reference/std_RhythmExtractor2013.html`
- Demucs repository status: `https://github.com/facebookresearch/demucs`
- MIR Eval transcription metrics: `https://mir-eval.readthedocs.io/en/latest/api/transcription.html`
- Museval + MUSDB18 references: `https://pypi.org/project/museval/`, `https://sigsep.github.io/datasets/musdb.html`
- ENST-Drums dataset: `https://perso.telecom-paristech.fr/essid/en/recherche/base.html`
- IDMT-SMT-Drums dataset: `https://www.idmt.fraunhofer.de/en/publications/datasets/smt/drums.html`
- Criterion benchmarking: `https://github.com/bheisler/criterion.rs`
- IAI-Callgrind benchmarking: `https://github.com/iai-callgrind/iai-callgrind`
- Pytest benchmark docs: `https://pytest-benchmark.readthedocs.io/en/latest/index.html`
- Vitest benchmark feature: `https://vitest.dev/guide/features#benchmarking-experimental`

---

## Blocked / Questions (need decisions)

### A) Audio codec layer implementation choice (host) - resolved
- [x] Chosen approach: bundled Rust/Symphonia decoder for host playback (`mix.ogg`, `mix.mp3`, `mix.wav`).
- [x] FFmpeg remains sidecar-only for ingest/non-WAV source conversion; it is not required for normal SongPack playback.
- [x] Documented in `docs/audio-codec-policy.md` and locked with host decode policy tests.

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
