# Testing Strategy

## Non-negotiable: Test-driven development (TDD)
All development is **TDD-first**:
- Write a failing test (or fixture) that describes the desired behavior.
- Implement the smallest change to make it pass.
- Refactor with tests ensuring behavior stays stable.

PR expectations:
- Each PR adds/updates tests for the behavior it introduces or changes.
- No “drive-by” feature work without corresponding tests.
- CI is treated as a hard gate (no merge if red).

## Goals
- Protect the **SongPack schema** from accidental breaking changes.
- Keep ingestion outputs **reproducible** and regression-tested.
- Ensure the visualization host can load any compatible plugin and render frames reliably.
- Catch cross-platform packaging issues early.

---
## Test layers

### 1) Schema tests (fast, always-on)
**Where**: `packages/songpack`, `packages/core-music`, `python/ingest`

**What to test**
- JSON schema validation for each file (`manifest.json`, `features/events.json`, etc.)
- migration tests:
  - given a SongPack produced by schema v0.x, migrate to v1.x
  - validate equality invariants (event ordering preserved, times preserved)
- round-trip tests:
  - parse → normalize → serialize yields stable output (canonical json)

**Failure signal**: any schema or migration change breaks CI.

---
### 2) Pipeline unit tests (fast)
**Where**: `python/ingest`

**What to test**
- stage fingerprint determinism
- cache invalidation rules
- progress JSONL emission format
- artifact metadata correctness

**Fixtures**
- tiny wav files (1–5 seconds)
- synthetic click tracks (known beats)

---
### 3) Pipeline golden tests (medium)
**Goal**: detect extraction regressions while tolerating small numeric drift.

**Approach**
- keep a small set of short audio fixtures (10–30s) under `assets/test_fixtures/`
- run the pipeline with pinned versions and configs
- compare produced artifacts against “golden” expected results

**Comparison rules**
- timestamps compared within tolerances (e.g., ±10ms for beats)
- sections compared by overlap metrics
- event lists compared for monotonic ordering and density constraints
- drum transcription should also run a lane-normalized benchmark:
  - compare against curated JSON or MIDI references
  - use one-to-one event matching within tolerance (default `60 ms`)
  - track per-lane precision / recall / F1 plus confusion pairs, especially for snare

---
### 4) Host + plugin contract tests (fast)
**Where**: `apps/game` and `packages/viz-sdk`

**What to test**
- plugin loader:
  - can discover built-in + user plugins
  - rejects incompatible schema versions
- lifecycle contract:
  - `init → resize → update → render → dispose`
- performance smoke:
  - render 300 frames and ensure average frame time under budget

---
### 5) End-to-end tests (slow, nightly)
**Flow**
1. Run ingest on fixture audio in AuralStudio -> produce SongPack.
2. Launch AuralPrimer in headless mode (or minimal UI harness).
3. Load SongPack.
4. Load a reference plugin.
5. Run playback for N seconds and verify:
   - no crashes
   - transport time is monotonic
   - plugin renders at least one frame

---
## Recommended tools

### TypeScript
- unit: `vitest`
- lint: `eslint`
- format: `prettier`

### Rust
- unit: `cargo test`
- lint: `cargo clippy`
- format: `cargo fmt`

### Python
- unit: `pytest`
- format: `ruff format` or `black`
- lint: `ruff`

---
## Release acceptance gates
A release candidate should pass:
- all unit tests
- schema validation suite
- golden tests on at least one OS
- packaging build on all target OSes

In addition, release work should be approached TDD-first:
- add tests/fixtures for any bug found during release hardening before fixing it
- prefer regression tests over manual checklists

---
## What “done” looks like (MVP)
- A SongPack fixture can be validated and loaded.
- At least one plugin passes contract tests.
- CI runs lint/test on PRs.
- Release workflow produces installers.
