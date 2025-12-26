# AuralPrimer — data-driven music learning game

This repository is the project blueprint (and eventual implementation) for a **data-driven music-learning game** with:

1. **Song import engine**: MP3/audio → extraction pipeline → **SongPack** (optionally includes MIDI transcription).
2. **Python extraction**: all extraction/transcription tasks run in Python and are shipped as self-contained executables (no system Python required).
3. **Pluggable visualization engine**: multiple instrument visualizations (bass/guitar/drums/vocals) and theory-centric views (e.g., Nashville numbers).

## Design principles

- **Test-driven development (TDD)**: new behavior is introduced by writing tests first; PRs are expected to include tests and CI must stay green.
- **Fully data-driven gameplay**: the runtime consumes **SongPacks** (canonical game content).
  - Users can download/unzip SongPacks into a local songs folder; the app scans for new songs on startup.
  - Importers convert from other sources into SongPacks.
- **Deterministic + reproducible imports**: imports are cached and versioned.
- **Plugin-first visualization**: visualizers are independent packages that render from the same canonical event timeline.
- **Local-first + shippable**: the desktop app bundles Python tools and decoders as needed.
  - **ML model weights are not bundled** into installers.
  - When required, models are downloaded/imported post-install into the app’s `assets/models/` directory (see `spec.md` + `docs/packaging-ci.md`).

See `docs/testing-strategy.md`.
See `docs/local-dev-prereqs.md`.

## Docs (start here)

- [`docs/architecture.md`](docs/architecture.md) — system overview, module boundaries, runtime flows
- [`docs/songpack-spec.md`](docs/songpack-spec.md) — SongPack format, event model, versioning/migrations
- [`docs/ingest-pipeline.md`](docs/ingest-pipeline.md) — Python pipeline DAG, stage plugins, caching, CLI contract
- [`docs/visualization-plugins.md`](docs/visualization-plugins.md) — visualization plugin API and loading model
- [`docs/packaging-ci.md`](docs/packaging-ci.md) — bundling Python/FFmpeg/models; CI build matrix
- [`docs/roadmap.md`](docs/roadmap.md) — milestones from MVP → v1
- [`docs/risk-register.md`](docs/risk-register.md) — technical risks + mitigations

## Proposed monorepo layout (initial)

```
/aural-primer
  /apps
    /desktop                # Tauri desktop app (Rust + TS)
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

## Packaging stance (“no external dependencies”)

At runtime, the shipped application should require **no separate installs**.

We achieve this by:
- bundling Python pipeline tools as **sidecar executables** (PyInstaller/Nuitka)
- bundling any decoder binaries (e.g., **ffmpeg**) when needed
- downloading/importing ML model weights post-install into `assets/models/` (never bundled in the installer)

See `docs/packaging-ci.md`.
