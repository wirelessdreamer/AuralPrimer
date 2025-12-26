# Local Development Prerequisites

This repo targets **Windows + Linux**.

## Node
- Node.js (current repo uses Node v24)
- npm (repo uses npm workspaces)

## Rust (Tauri host)
Tauri uses Rust for the desktop host backend.

Install via rustup:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"
```

## Linux system dependencies (Tauri)
On Linux, Tauri’s WebView backend depends on native GTK/WebKit libraries, discovered via `pkg-config`.

You must install (names may vary by distro):
- `pkg-config`
- WebKitGTK development package (commonly `libwebkit2gtk-4.1-dev`)
- GTK development package (commonly `libgtk-3-dev`)
- other common build essentials (commonly `build-essential`, `libssl-dev`, `libayatana-appindicator3-dev`)

Example (Debian/Ubuntu):
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

If you cannot install system packages (no sudo), you can still:
- develop/run the **TypeScript** packages (`npm test`)
- develop/run the **Python** pipeline

…but you won’t be able to compile/run the Tauri desktop host locally.

## Python (ingest pipeline)
- Python 3.11+

Optional (recommended for importing mp3/ogg/flac):
- `ffmpeg` available on `PATH`

Dev deps:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r python/ingest/requirements-dev.txt
pytest -q
```

### Notes
- If `ffmpeg` is not installed, `aural_ingest import` currently only supports `.wav` inputs.
