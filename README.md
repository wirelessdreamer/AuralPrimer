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

- [`docs/architecture.md`](docs/architecture.md) - system overview, module boundaries, runtime flows
- [`docs/songpack-spec.md`](docs/songpack-spec.md) - SongPack format, event model, versioning/migrations
- [`docs/ingest-pipeline.md`](docs/ingest-pipeline.md) - Python pipeline DAG, stage plugins, caching, CLI contract
- [`docs/visualization-plugins.md`](docs/visualization-plugins.md) - visualization plugin API and loading model
- [`docs/packaging-ci.md`](docs/packaging-ci.md) - bundling sidecars/decoders/models; CI build matrix
- [`docs/roadmap.md`](docs/roadmap.md) - milestones from MVP to v1
- [`docs/risk-register.md`](docs/risk-register.md) - technical risks and mitigations

## Monorepo layout (current direction)

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
    /viz-fretboard
    /viz-drum-grid
    /viz-vocal-lane
    /viz-nashville
  /assets
    /models
    /demo_songpacks
  /docs
```

## Packaging stance ("no external runtime dependencies")

At runtime, users should not need separate Python/FFmpeg/runtime installs.

We achieve this by:
- bundling ingest tools as sidecar executables
- bundling decoder binaries when needed
- supporting post-install model pack download/import into `assets/models/` (never bundled in installer)

See `docs/packaging-ci.md`.
