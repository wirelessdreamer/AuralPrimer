# Packaging & CI (no external runtime dependencies)

## Interpretation of “no external dependencies”
At runtime, end users should not need to install:
- Python
- FFmpeg
- ML runtimes

**Model weights are not bundled in installers.**
- If needed, they are obtained **post-install** via in-app download or manual offline import.
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
- **do not include model weights** in installers

### Audio decoding
If ingest requires MP3 decoding:
- bundle `ffmpeg` as another sidecar executable
- or use a library-based decoder for mp3 (but ffmpeg is the most robust)

Recommended: bundle ffmpeg and document license obligations.

---
## Sidecar invocation contract

AuralStudio calls sidecars via absolute paths from app resources.

- `aural_ingest` is invoked with args and emits JSONL progress.
- Sidecar reads/writes only within a provided `--out` directory.

This containment helps security and makes sandboxing easier.

### Portable build guard (Windows recovery)
- `build_sidecar.ps1` writes `dist/sidecar/build_manifest.json` with sidecar hash/timestamp.
- `create_portable.ps1` stages `D:\AuralPrimer\AuralPrimerPortable\` with both `AuralPrimer.exe` and `AuralStudio.exe`.
- The script fails if copied sidecar hash/timestamp checks do not match the just-built sidecar.
- This prevents shipping stale sidecar binaries in portable artifacts.

---
## Assets and model management

### Model storage
- store versioned models under `assets/models/<model-id>/<version>/...`
- ingestion stages declare exact model id/version used
- SongPack manifest records stage fingerprints

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
- AuralStudio imports fixture audio -> produce SongPack
- AuralPrimer loads SongPack and runs visualizer smoke test

---
## License compliance
If you bundle ffmpeg:
- include `THIRD_PARTY_NOTICES.md`
- ensure the chosen ffmpeg build/license is compatible with your distribution goals

If you support post-install model downloads/imports:
- include model license text in the downloaded model pack
- record model pack id/version/license metadata alongside the model files
