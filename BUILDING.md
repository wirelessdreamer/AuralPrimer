# Building AuralPrimer (Desktop)

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

### Important: don’t copy `node_modules` across OS/filesystems
If you moved the repo from WSL/Linux to Windows, **delete and reinstall** Node deps on Windows:

```bat
rmdir /s /q node_modules
del /f /q package-lock.json
npm install
```

(This avoids common optional-dependency and `.bin` shim issues.)

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

## 4) Run the desktop app (dev)

From repo root:

```bash
npm run desktop:dev
```

This runs `tauri dev`, which starts Vite and the Rust backend.

## 5) Build a release artifact (what you can “test”)

### Important (Windows users): don’t run `tauri build` from WSL
If you run the build inside **WSL (Ubuntu)**, you are building a **Linux** binary and it will require Linux GTK/WebKit deps (via `pkg-config`).

The error you pasted (`pkg-config ... gdk-sys ... pango`) indicates you are building inside Linux/WSL.

**To build a Windows installer (`.msi`)**:
- open **PowerShell** (or `cmd.exe`) on Windows (not WSL)
- run the commands from the repo root on the Windows filesystem

If you *do* want to build the Linux app in WSL, install the deps from `docs/local-dev-prereqs.md` (at minimum `pkg-config`, `libgtk-3-dev`, `libwebkit2gtk-4.1-dev`).

From repo root:

```bash
npm run desktop:build
```

### Where the build output goes
Tauri outputs platform-specific artifacts under:

- `apps/desktop/src-tauri/target/release/bundle/`

Typical outputs:
- Linux: `*.AppImage`, `*.deb` (depending on system tooling)
- Windows: `*.msi` / installer bundle

If you only want to confirm the web UI builds:

```bash
npm run desktop:build:frontend
```

## 6) Smoke-testing the build

1. Run `npm run desktop:build`
2. Install/run the produced artifact from `apps/desktop/src-tauri/target/release/bundle/`
3. Put a fixture SongPack into the app’s songs folder (the UI shows the default folder path):
   - `assets/test_fixtures/songpacks/minimal_valid.songpack` (directory SongPack)
4. Launch the app → Refresh → Details → Load audio → Play.

## Notes
- **Bundled visualizers** (resources) live under the Tauri resource dir at runtime. User visualizers are under the app data dir.
- If you can’t install system packages (no sudo), you can still run `npm test` and build the frontend, but you won’t be able to compile/run Tauri locally.
