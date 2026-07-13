# Packaging & CI (no external runtime dependencies)

## Interpretation of “no external dependencies”
At runtime, end users should not need to install:
- Python
- FFmpeg
- ML runtimes

**Model weights are not auto-fetched by packaging.**
- Reviewed local modelpacks/checkpoints can be staged into portable/release
  artifacts when they are present, pinned, and recorded in the build manifest.
- Otherwise, models are obtained **post-install** via in-app download or manual
  offline import.
- Models are stored under `assets/models/<model-id>/<version>/...`.

Instead, everything else needed for local processing is **bundled** into the shipped app.

---
## Packaging overview

### Desktop hosts: Tauri
Use Tauri packaging per OS for both apps:
- Windows: MSI / NSIS
- Linux: AppImage + (optional) deb/rpm

Host roles:
- `AuralPrimer`: gameplay runtime
- `AuralStudio`: import/song-creation runtime

### Python tools: sidecar executables
Build OS-specific executables from `python/ingest`.

**Options**
- PyInstaller (fastest path, common)
- Nuitka (often smaller/faster but more build complexity)

Bundling requirements:
- include Python runtime
- include native deps (numpy, torch, etc. if used)
- include only reviewed/pinned modelpacks when explicitly staged; record their
  id/version/hash/license metadata in packaging manifests
- install the mirrored `python/ingest/requirements-runtime.txt` dependencies
  before PyInstaller runs; clean sidecar builds must not rely on an already
  hydrated developer venv. `basic-pitch` is installed separately with
  `--no-deps` because its TensorFlow dependency is unavailable on Python 3.13,
  while AuralPrimer uses the ONNX Basic Pitch path.
- emit the host-platform sidecar executable name (`aural_ingest.exe` on
  Windows, `aural_ingest` elsewhere) so Tauri external-bin packaging works for
  Windows and Linux/macOS release jobs

### Audio decoding
Host playback:
- use the in-process Rust/Symphonia decoder for AuralSong `mix.ogg`, `mix.mp3`, and `mix.wav`
- do not require FFmpeg or platform codec packs for normal playback

Ingest:
- non-WAV source conversion may use a bundled `ffmpeg` sidecar
- generated AuralSongs still write canonical `audio/mix.wav`

Recommended: keep FFmpeg contained to the ingest sidecar boundary and document license obligations.

---
## Sidecar invocation contract

AuralStudio calls sidecars via absolute paths from app resources.

- `aural_ingest` is invoked with args and emits JSONL progress.
- Sidecar reads/writes only within a provided `--out` directory.

This containment helps security and makes sandboxing easier.

### Portable build guard (Windows recovery)
- `build_sidecar.ps1` writes `dist/sidecar/build_manifest.json` with sidecar hash/timestamp.
- The build manifest carries `runtime-check` asset snapshots for Basic Pitch,
  Demucs, optional `demucs_ft_drums`, and MT3 checkpoints so stale/missing
  model assets are visible in release artifacts.
- `create_portable.ps1` stages `D:\AuralPrimer\AuralPrimerPortable\` with both `AuralPrimer.exe` and `AuralStudio.exe`.
- The script fails if copied sidecar hash/timestamp checks do not match the just-built sidecar.
- `npm run portable:verify-sidecar` verifies all staged sidecar copies share
  the build-manifest hash, independently recomputes ingest-source freshness,
  checks the portable manifest, and runs frozen `runtime-check` from the repo,
  portable, and `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` override contexts.
- This prevents shipping stale sidecar binaries in portable artifacts.

---
## Assets and model management

### Model storage
- store versioned models under `assets/models/<model-id>/<version>/...`
- ingestion stages declare exact model id/version used
- AuralSong manifest records stage fingerprints

### Model acquisition strategy
- MVP/v1: **models are downloaded post-install** (in-app) or imported manually.
- Features that require models remain optional until a compatible model pack is present.
- Consider multiple model packs (Lite vs Full) as separate downloads/imports (not separate installers).

---
## CI strategy (GitHub Actions suggested)

### Workflows
1. `lint-test` (PRs)
   - TS lint
   - Rust fmt/clippy
   - Python format + unit tests

2. `build-desktop` (release)
   - matrix: windows-latest, ubuntu-latest
   - build sidecars per OS
   - build tauri installer per OS
   - attach artifacts to release

### Caching
- cache node_modules
- cache cargo registry + target
- cache Python wheels

---
## Testing layers

### Unit
- schema parsing + validation
- pipeline stage fingerprinting and caching rules

### Golden tests (pipeline)
- fixed short audio fixtures
- assert beats/sections stable within tolerance

### End-to-end
- AuralStudio imports fixture audio -> produce AuralSong
- AuralPrimer loads AuralSong and runs visualizer smoke test

---
## License compliance
If you bundle ffmpeg:
- include `THIRD_PARTY_NOTICES.md`
- ensure the chosen ffmpeg build/license is compatible with your distribution goals

If you create a portable package:
- include the root project `LICENSE`
- record the packaged license file hash in `portable_manifest.json`

If you support post-install model downloads/imports:
- include model license text in the downloaded model pack
- record model pack id/version/license metadata alongside the model files
- for staged `modelpack.json` assets under `assets/models/<id>/<version>/`,
  require a non-empty `license` field before copying into portable/release
  artifacts and copy that value into `portable_manifest.json`
