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
- [~] Implement ingest sidecar MVP (decode + beats/tempo + sections + chart gen)
  - [x] `python/ingest` project scaffold (`pyproject.toml`, `pytest`, `ruff`)
  - [x] Sidecar CLI surface (current): `aural_ingest stages|info|validate|import|import-dir|import-dtx`
  - [x] JSONL progress event emitter (`aural_ingest.progress`)
  - [x] Real decode + analysis stages (wav inputs supported without ffmpeg; non-wav requires ffmpeg)
  - [x] Determinism tests (synthetic click-track wav fixture) for decode+tempo+beats+sections+chart
  - [x] Host import UI wiring to run sidecar + stream progress
    - [x] Configure panel ingest controls now call desktop `ingest_import` command end-to-end
    - [x] Stream per-stage JSONL progress events into Configure UI during import
- [ ] (Recovery) Restore lost transcription stack + regression protections from 2026-03-03 notes (see Milestone 4A)
- [x] Strengthen automated test coverage gates (TS + Python) and expand Rust core unit coverage
  - [x] TS coverage reporting + thresholds (`vitest --coverage`)
  - [x] Python coverage reporting + fail-under gate (`pytest-cov`, fail-under 85)
  - [x] Add high-value unit tests for untested Rust modules (`wav_mix`, `audio_decode`, `models`, `midi_clock_service`)
  - [ ] Validate new Rust `cargo llvm-cov` CI step in GitHub runner environment

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

- [~] (New) Realtime MIDI I/O + clock sync (bidirectional)
  - [x] Decide initial API boundary: Rust MIDI service + Tauri commands/events (no WebMIDI dependency)
  - [x] Implement MIDI device enumeration + port selection UI
  - [~] Implement MIDI input routing (note on/off + key CCs) into gameplay input bus
    - [x] Native callback now emits structured `midi_input_message` events (note on/off, CC, pitch bend, program/pressure, realtime, SPP, optional SysEx)
    - [x] Host forwards MIDI input events onto a window-level app event (`auralprimer:midi-input`) for gameplay integration points
    - [ ] Map `auralprimer:midi-input` into concrete gameplay scoring/hit-window evaluators
  - [x] Implement MIDI clock input -> transport sync (Start/Stop/Continue + Clock + SPP best-effort)
  - [x] Implement MIDI clock output from transport (supports tempo slowdown + loop)
  - [x] Implement tempo scaling when external clock drives transport (device tempo -> song tempo factor)
  - [x] SysEx support (opt-in per port) + safety controls
  - [~] Contract tests: determinism + jitter tolerance + loop/seek behavior under MIDI sync
    - [x] Added Rust unit coverage for inbound message parsing + outbound message validation/SysEx policy
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
  - `aural_ingest import-dir <source-dir> --out ... --profile ...` (directory source picker)
  - `aural_ingest validate <songpack-dir>` (file presence checks)
  - `aural_ingest info <songpack-dir>`
  - `aural_ingest stages`
- [~] Pipeline stages (MVP deterministic analysis is implemented; advanced transcription is not yet restored):
  - [x] `init_songpack`
  - [x] `decode_audio` (writes deterministic `audio/mix.wav`; non-wav decode requires ffmpeg)
  - [x] `beats_tempo` (deterministic BPM estimate + generated beat grid)
  - [x] `sections` (generated section blocks from duration + BPM)
  - [x] `chart_generation` (deterministic beats-only easy chart)
  - [~] Restore higher-fidelity transcription outputs (`audio/stems/*.wav`, `charts/notes.mid`, richer feature extraction)
    - [x] Added deterministic guitar stem split stage that emits `audio/stems/lead_guitar.wav` + `audio/stems/rhythm_guitar.wav` (uses `audio/stems/guitar.wav` when present, else mix fallback)
    - [ ] Integrate true Demucs separation stage and richer multi-stem outputs from modelpacks
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
  - [x] Added desktop client wrapper (`apps/desktop/src/ingestClient.ts`) and tests to lock payload forwarding
  - [x] Wire Configure UI import controls to call `ingest_import` end-to-end
- [x] Restore chart parser strict/relaxed guard so sparse dedicated drum tracks are not dropped
  - [x] Added `apps/desktop/src/chartLoader.ts` strict/relaxed selection logic with dedicated drum-track guard
  - [x] Added desktop regression tests for strict-vs-relaxed and King in Zion sparse-drums behavior
  - [x] Integrated chart loader into active gameplay song selection path (`read_songpack_mid` + capability/instrument availability plumbing)
- [x] Rebuild portable packaging flow that always copies latest sidecar before ship
- [x] Verify/fix `import-dir` ordering around `sections` and `events.json`

**TDD / testing deliverables**
- [ ] Python: `test_transcription_resilience.py` recovered with fallback and algorithm-diversity assertions
- [x] Python: `test_import_pipeline.py` includes `import-dir` events-export ordering regression coverage
- [x] Desktop: `chartLoader.test.ts` strict-vs-relaxed behavior and sparse-drum preservation cases
- [x] Desktop: `chartLoader.kingInZionRegression.test.ts` fixture regression test
- [x] End-to-end smoke: same stem A/B (`adaptive_beat_grid` vs `combined_filter`) confirms expanded-kit distribution in `combined_filter` (covered by ingest algorithm regression tests)

**Exit criteria**
- [ ] Default import path reproduces expanded drum-note diversity on known regression fixtures
- [ ] Portable package contains sidecar matching just-built hash/timestamp
- [ ] Regression suites above run in CI and prevent fallback/order regressions

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
  - [x] set default to `combined_filter`
  - [x] unknown algorithm IDs must log warning + return explicit fallback id used
- [ ] Melodic transcription architecture:
  - [ ] `basic_pitch` path first for polyphonic targets (guitar/keys), `pyin` fallback for monophonic safety path
  - [ ] frozen-runtime model resolution order from notes (`onnx -> tflite -> savedmodel`)
- [ ] Beat/tempo/sections quality upgrade:
  - [ ] keep deterministic MVP pipeline in place
  - [ ] add optional higher-accuracy mode using `librosa.beat.beat_track` and/or Essentia `RhythmExtractor2013` for offline imports
- [ ] Stem separation provider model:
  - [ ] keep separator pluggable (`none`, `demucs`, future providers)
  - [ ] note: upstream `facebookresearch/demucs` is archived; treat provider as "best-effort, replaceable"

### D) Packaging + sidecar reliability hardening
- [ ] Tauri sidecar contract hardening:
  - [ ] define `bundle.externalBin` entries for desktop + studio sidecars
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
- [ ] Separation quality:
  - [ ] evaluate stems with `museval` SDR protocol
  - [ ] use MUSDB18/MUSDB18-HQ for controlled comparisons
- [ ] Transcription quality:
  - [ ] evaluate note events with `mir_eval.transcription` (precision/recall/F1/overlap)
  - [ ] include both onset-only and onset+offset scoring modes
- [ ] Drum-specific datasets for regression fixtures:
  - [ ] ENST-Drums
  - [ ] IDMT-SMT-Drums

#### E6) Frontend/runtime performance benchmarks
- [ ] Add `vitest bench` suites for parser/mapping hot paths and plugin update loops.
- [ ] Add bench artifact comparison in CI (`vitest bench --outputJson` + `--compare`).
- [ ] Add Playwright trace-based end-to-end perf captures for import/playback/plugin rendering paths.

### F) CI enforcement upgrades
- [ ] Add dedicated benchmark workflows:
  - [ ] `bench-rust` (criterion + iai-callgrind artifacts)
  - [ ] `bench-python` (pytest-benchmark JSON artifacts)
  - [ ] `bench-ts` (vitest bench JSON artifacts)
- [ ] Add quality-gate workflow:
  - [ ] transcription/separation fixture scoring
  - [ ] fail on delta thresholds defined in versioned config (`benchmarks/thresholds.yml`)
- [ ] Publish benchmark dashboards as CI artifacts for every PR touching `apps/desktop/src-tauri`, `python/ingest`, or parser code.

### G) Research-driven decision gates to resolve before implementation lock
- [x] Choose realtime-safe queue strategy for audio callback (`rtrb` only vs dual-queue design for metrics).
- [ ] Choose beat/tempo production default (`librosa`-first vs Essentia-first) for deterministic imports.
- [ ] Choose separator support policy (ship Demucs provider as optional experimental vs fully supported path).
- [ ] Freeze benchmark threshold policy for PR blocking (strict vs warn-only for first 2 weeks).

### H) Clarifications needed from project owner (to finalize this plan)
- [ ] Confirm primary success metric priority:
  - [ ] lowest-latency playback/runtime
  - [ ] best transcription quality
  - [ ] fastest import throughput
- [ ] Confirm target hardware baseline for perf gates (minimum Windows/Linux CPU class and default buffer size).
- [ ] Confirm legal/product stance for research-only datasets in CI (MUSDB18, ENST, IDMT) vs internal/private fixtures only.
- [ ] Confirm whether `apps/studio` restoration is required in this recovery phase or deferred after sidecar recovery.
- [ ] Confirm whether GPU acceleration is in scope for sidecar in v1 recovery, or CPU-only deterministic path first.

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
