# AuralPrimer + AuralStudio - data-driven music learning suite

This repository contains a two-app desktop music-learning suite:

1. **AuralStudio (authoring app)**: import, transcription, and SongPack creation flows.
2. **AuralPrimer (game app)**: playback, practice, and gameplay from SongPacks.
3. **Python sidecar pipeline**: extraction/transcription tooling packaged as executables (no system Python required).
4. **Pluggable visualization engine**: instrument and theory visualizers powered by shared canonical events.

## Design principles

- **Test-driven development (TDD)**: tests first, implementation second, CI stays green.
- **SongPack-first runtime**: AuralPrimer consumes SongPacks as canonical game content.
  - AuralPrimer scans the songs folder for new/removed SongPacks.
  - AuralStudio importers convert external sources into SongPacks.
- **Deterministic imports**: import outputs are cacheable, reproducible, and versioned.
- **Plugin-first visualization**: visualizers remain decoupled from core runtime.
- **Local-first shipping**: required tooling is bundled in desktop artifacts.
  - ML model weights are not bundled in installers.
  - Model packs can be downloaded/imported post-install into `assets/models/`.

See `docs/testing-strategy.md` and `docs/local-dev-prereqs.md`.

## Docs (start here)

Top-level entry points:

- [`spec.md`](spec.md) - authoritative requirements (app boundaries, hard constraints, MIDI/audio rules)
- [`wip.md`](wip.md) - living implementation tracker (milestones, in-flight tasks, decisions)
- [`BUILDING.md`](BUILDING.md) - install/test/build/portable-package instructions

Architecture and contracts:

- [`docs/architecture.md`](docs/architecture.md) - system overview, module boundaries, runtime flows
- [`docs/songpack-spec.md`](docs/songpack-spec.md) - SongPack format, event model, versioning/migrations
- [`docs/songpack-deliverable.md`](docs/songpack-deliverable.md) - deterministic `.songpack` zip build contract
- [`docs/ingest-pipeline.md`](docs/ingest-pipeline.md) - Python pipeline DAG, stage plugins, caching, CLI contract
- [`docs/visualization-plugins.md`](docs/visualization-plugins.md) - visualization plugin API and loading model
- [`docs/audio-codec-policy.md`](docs/audio-codec-policy.md) - host playback uses Rust/Symphonia; FFmpeg stays in ingest sidecar
- [`docs/midi-keyboard-testing.md`](docs/midi-keyboard-testing.md) - hardware MIDI input verification path

Process, tooling, and decisions:

- [`docs/local-dev-prereqs.md`](docs/local-dev-prereqs.md) - OS-level prerequisites for local builds
- [`docs/testing-strategy.md`](docs/testing-strategy.md) - TDD layers, fixtures, golden tests
- [`docs/packaging-ci.md`](docs/packaging-ci.md) - bundling sidecars/decoders/models; CI build matrix
- [`docs/performance-baselines.md`](docs/performance-baselines.md) - hardware profiles backing benchmark thresholds
- [`docs/research-decision-gates.md`](docs/research-decision-gates.md) - locked production defaults (beat/tempo, separator, threshold policy)
- [`docs/research-deep-dive-adt-2026-05-07.md`](docs/research-deep-dive-adt-2026-05-07.md) - 2024–2025 ADT/transcription literature scan; revises 10 architectural assumptions and the paths-forward list
- [`docs/CLAUDE_CODE_RESUME_PLAN.md`](docs/CLAUDE_CODE_RESUME_PLAN.md) - resumable v0.2 task plan (synth corpus, ADTOF integration, Demucs gate, real Basic Pitch, multi-label CRNN, real-audio fixtures)
- [`docs/roadmap.md`](docs/roadmap.md) - milestones from MVP to v1
- [`docs/risk-register.md`](docs/risk-register.md) - technical risks and mitigations

Recovery context (preserved from the 2026-03-03 lost-tree recovery):

- [`PROJECT_ARCH_FROM_MEMORY.md`](PROJECT_ARCH_FROM_MEMORY.md)
- [`TRANSCRIPTION_RECOVERY_NOTES.md`](TRANSCRIPTION_RECOVERY_NOTES.md)
- [`DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md`](DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md)
- [`TRANSCRIPTION_REGRESSION_HISTORY.md`](TRANSCRIPTION_REGRESSION_HISTORY.md)

## Monorepo layout (current tree)

```text
/aural-primer
  /apps
    /game                   # AuralPrimer gameplay app (Tauri)
    /desktop                # AuralStudio authoring app (Tauri)
  /packages
    /core-music             # shared schema + utilities (TS + Rust)
    /viz-sdk                # visualization plugin SDK (TS)
    /songpack               # SongPack reader/writer/validator (TS + Rust)
  /python
    /ingest                 # Python extraction pipeline (built into sidecars)
  /visualizers
    /viz-beats              # beat/section grid (placeholder until host beats query lands)
    /viz-drum-highway       # drum lanes from host-provided MIDI events
    /viz-fretboard          # placeholder fretboard cursor (awaiting note query API)
    /viz-lyrics             # data-driven karaoke lyrics
    /viz-nashville          # chord-lane placeholder until host exposes chord/key data
  /benchmarks               # frontend/python/rust benches + thresholds.yml + reports
  /scripts                  # Node + PowerShell launchers for build/bench/portable
  /assets
    /models
    /test_fixtures
  /docs
```

## Packaging stance ("no external runtime dependencies")

At runtime, users should not need separate Python/FFmpeg/runtime installs.

We achieve this by:
- bundling ingest tools as sidecar executables
- bundling decoder binaries when needed
- supporting post-install model pack download/import into `assets/models/` (never bundled in installer)

See `docs/packaging-ci.md`.
