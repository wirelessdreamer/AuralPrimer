# Building AuralPrimer + AuralStudio

This repo uses **npm workspaces** + **Tauri**.

## 1) Prerequisites

### Node
- Node.js (repo currently uses Node v24)
- npm (repo uses npm workspaces)

### Rust (Tauri)
Install via rustup:

```bash
# PowerShell
winget install Rustlang.Rustup
# then open a new terminal
rustup default stable
```

### Windows build tools (required for `tauri dev/build`)
You need MSVC + Windows SDK. Easiest path:
- Install **Visual Studio 2022 Build Tools**
  - Workload: **Desktop development with C++**

Also ensure:
- **WebView2 Runtime** is installed (typically already present on Windows 10/11)

### Optional: ASIO host support (Studio app)
ASIO is opt-in because CPAL's ASIO backend requires the Steinberg ASIO SDK.

1. Download/extract the ASIO SDK locally.
2. Set `CPAL_ASIO_DIR` to that SDK root in your shell.
3. Run one of:

```powershell
npm run studio:dev:asio
npm run studio:build:asio
```

Without `--features asio`, the app still works with the default host stack (e.g. WASAPI).

### Linux system deps (if building on Linux)
See `docs/local-dev-prereqs.md`.

Debian/Ubuntu example:

```bash
sudo apt-get update
sudo apt-get install -y \
  pkg-config \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libayatana-appindicator3-dev \
  libssl-dev \
  build-essential
```

## 2) Install dependencies

From repo root:

```bash
npm ci
```

## 3) Run tests

```bash
npm test
```

## 4) Run apps (dev)

From repo root:

```bash
npm run game:dev
npm run studio:dev
```

Each command runs `tauri dev`, which starts Vite and the Rust backend.

## 5) Build release artifacts

### Important (Windows users): do not run `tauri build` from WSL
If you run the build inside WSL/Linux, you are building a Linux binary and it will require Linux GTK/WebKit deps.

From repo root:

```bash
npm run game:build
npm run studio:build
```

### Where output goes
- Game bundle: `apps/game/src-tauri/target/release/bundle/`
- Studio bundle: `apps/desktop/src-tauri/target/release/bundle/`

If you only want frontend builds:

```bash
npm run game:build:frontend
npm run studio:build:frontend
```

## 6) Build portable folder (with sidecar freshness guard)

From repo root (PowerShell):

```powershell
npm run portable:build
```

What this does:
- builds game + studio releases (unless `-SkipDesktopBuild`, or individually via `-SkipGameBuild` / `-SkipStudioBuild`)
- builds/copies `aural_ingest.exe` into `dist/sidecar/`
- stages portable output under `D:\AuralPrimer\AuralPrimerPortable\`
- writes two launchers:
  - `AuralPrimer.exe` (game / play songs)
  - `AuralStudio.exe` (content creation)
- stages `demucs_6.zip` into `D:\AuralPrimer\AuralPrimerPortable\modelpacks\demucs_6.zip`
  - default lookup order:
    - `dist/modelpacks/demucs_6.zip`
    - `assets/modelpacks/demucs_6.zip`
    - `modelpacks/demucs_6.zip`
    - `demucs_6.zip` (repo root)
  - packaging fails if `modelpack.json` is missing/invalid, if `id != demucs_6`, or if required stems (`keys, drums, guitar, bass, vocals`) are not declared
- fails if portable sidecar hash/timestamp do not match the fresh sidecar
- writes:
  - `dist/sidecar/build_manifest.json`
  - `D:\AuralPrimer\AuralPrimerPortable\portable_manifest.json`

Useful flags:

```powershell
# Reuse existing app binaries + existing sidecar
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -SkipDesktopBuild -SkipSidecarBuild -GameExePath C:\path\to\AuralPrimer.exe -StudioExePath C:\path\to\AuralStudio.exe -SidecarSourceExePath C:\path\to\aural_ingest.exe

# Provide explicit demucs_6 modelpack location
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -Demucs6ModelPackZipPath C:\path\to\demucs_6.zip

# Also create zip output
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -ZipOutput
```

## 7) Smoke test

1. Build one app (`game:build` or `studio:build`).
2. Launch the built app from its bundle folder.
3. Put a fixture SongPack into the songs folder.
4. In AuralPrimer (game), refresh library, load song, and play.
5. In AuralStudio, verify import/creation tools and generated SongPacks.

## Notes
- Bundled visualizers live under Tauri resources at runtime.
- User visualizers are under app data directory.
- If you cannot install system packages (no sudo), you can still run `npm test` and frontend builds, but not Tauri compile/run.
