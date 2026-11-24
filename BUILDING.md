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

### WSL + Windows
The repo now routes `npm run ...tauri...`, `npm run portable:sidecar`, and `npm run portable:build` through thin launchers so the same commands work from native Windows or from WSL against the Windows checkout.

This also covers WSL-hosted task worktrees under `/home/...`: the launchers translate the WSL path to a Windows-accessible UNC path and invoke the Windows toolchain from there.

If you intentionally want a Linux Tauri build, run the Linux toolchain directly instead of the wrapper scripts.

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

From repo root (PowerShell or WSL):

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
- model weights normally remain post-install assets under `assets/models/`;
  portable/release builds may stage reviewed, pinned modelpacks/checkpoints
  when their id/version/hash/license metadata can be recorded in the packaging manifest
- stages `demucs_6.zip` into `D:\AuralPrimer\AuralPrimerPortable\modelpacks\demucs_6.zip`
  - default lookup order:
    - `dist/modelpacks/demucs_6.zip`
    - `assets/modelpacks/demucs_6.zip`
    - `modelpacks/demucs_6.zip`
    - `demucs_6.zip` (repo root)
  - packaging fails if `modelpack.json` is missing/invalid, if `id != demucs_6`, or if required stems (`keys, drums, guitar, bass, vocals`) are not declared
- optionally stages `demucs_ft_drums.zip` beside it when supplied or discovered
  through the same lookup order; packaging requires `id == demucs_ft_drums`,
  a declared drums stem role, a safe single weight path, source/hash metadata,
  license metadata, and a bundled `LICENSE`
- fails if portable sidecar hash/timestamp do not match the fresh sidecar
- writes:
  - `dist/sidecar/build_manifest.json`
  - `D:\AuralPrimer\AuralPrimerPortable\portable_manifest.json`

Useful flags:

```powershell
# Native Windows/manual invocation examples.
# From WSL, prefer `npm run portable:build` so the wrapper selects the right host.

# Reuse existing app binaries + existing sidecar
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -SkipDesktopBuild -SkipSidecarBuild -GameExePath C:\path\to\AuralPrimer.exe -StudioExePath C:\path\to\AuralStudio.exe -SidecarSourceExePath C:\path\to\aural_ingest.exe

# Provide explicit demucs_6 modelpack location
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -Demucs6ModelPackZipPath C:\path\to\demucs_6.zip

# Provide optional fine-tuned drums separator modelpack location
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -DemucsFtDrumsModelPackZipPath C:\path\to\demucs_ft_drums.zip

# Also create zip output
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -ZipOutput

# Provide explicit ffmpeg binary
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_portable.ps1 -FfmpegExePath C:\path\to\ffmpeg.exe
```

> If `ffmpeg.exe` is not found locally or on PATH, the build script will automatically download it from https://www.gyan.dev/ffmpeg/builds/.

## 7) Smoke test

1. Build one app (`game:build` or `studio:build`).
2. Launch the built app from its bundle folder.
3. Put a fixture AuralSong into the songs folder.
4. In AuralPrimer (game), refresh library, load song, and play.
5. In AuralStudio, verify import/creation tools and generated AuralSongs.

## 8) Quest build, signing, and store upload

The mixed-reality client is a separate Unity project (`UnityClient/Aural Primer`)
and does not go through the Tauri pipeline above.

### Build the APK

```
"C:\Program Files\Unity\Hub\Editor\6000.3.15f1\Editor\Unity.exe" -quit -batchmode -nographics -projectPath "D:\AuralPrimer\UnityClient\Aural Primer" -executeMethod AuralPrimer.EditorTools.BuildCommand.BuildAndroid -logFile -
```

Output lands at `UnityClient/Builds/AuralPrimer.apk`.

**Unity exits 0 even when scripts fail to compile** — when compilation fails
`-executeMethod` never runs and the exit code says nothing about it. Always
confirm `[build] result=Succeeded` in the log rather than trusting the exit code.

### Version code

Derived, not stored: minutes elapsed since a fixed epoch. Meta rejects any
upload whose version code is not strictly greater than the highest already
accepted, and a counter kept in `ProjectSettings.asset` is not monotonic here —
a build from a worktree, an unmerged branch, or a rebase hands back a number
already spent, and the rejection arrives at the end of a long build. Pass
`-versionCode N` or set `AURAL_VERSION_CODE` to pin a specific one.

### Release signing

Without a keystore the build is signed with Unity's debug key. That is correct
for `adb install` and rejected by the store, so release signing is read from the
environment:

| variable | meaning |
|---|---|
| `AURAL_KEYSTORE` | absolute path to the keystore |
| `AURAL_KEYSTORE_PASS` | store password |
| `AURAL_KEY_ALIAS` | key alias |
| `AURAL_KEY_PASS` | key password (same as the store password for PKCS12) |

They are read at build time and **never written to `ProjectSettings.asset`**,
which is a tracked file — setting a keystore through the Editor instead puts the
password of an unrotatable key into the history permanently. The build clears
both passwords out of `PlayerSettings` before the Editor can serialise them.

The keystore lives outside the repo at `~/.auralprimer/`, with the values in
`~/.auralprimer/signing.env` and the store API secret in
`~/.auralprimer/meta.env`. Both are mirrored to
`G:\My Drive\AuralPrimer Signing Key\`, which also carries a `README.txt`
written for someone finding it years from now with no memory of this setup.

> **Back that directory up somewhere you will still have in ten years.** Meta
> pins an app to its signing key. Lose the keystore or its password and that app
> ID can never be updated again — there is no recovery path, only a new listing.

### Upload

```
node scripts/upload-quest-build.mjs [channel] [--notes "..."] [--yes]
```

The App Secret comes from `META_APP_SECRET`, or failing that from
`~/.auralprimer/meta.env` beside the signing material — a file is offered as
well as a variable because the alternative in practice is pasting the secret
onto a command line, where it lands in shell history. The placeholder written by
setup is rejected rather than sent, so a half-finished setup fails locally
instead of at the API. Get the value from the Developer Dashboard → your app →
API, with an admin account; unlike the signing key it is rotatable, so a leak
means regenerating it rather than losing the app.

Also needs `ovr-platform-util` on `PATH` or at `OVR_PLATFORM_UTIL`. Channel defaults to
`ALPHA`; the production channel additionally requires `--yes`. The script
refuses debug-signed APKs before transferring 90MB the store would reject, and
attaches the IL2CPP symbols zip so crash reports symbolicate.

Age group is fixed at `MIXED_AGES` in the script. Meta treats this as permanent
after publishing.

## Notes
- Bundled visualizers live under Tauri resources at runtime.
- User visualizers are under app data directory.
- If you cannot install system packages (no sudo), you can still run `npm test` and frontend builds, but not Tauri compile/run.

## Working in this repo (quick orientation)

If you are picking the repo back up:

1. Read `README.md` for the docs index, then skim `wip.md`'s top "Status snapshot" entry for what is in flight today.
2. `spec.md` is authoritative for product behavior; do not silently drift from it. Spec changes are explicit edits and get called out in the PR/commit.
3. Development is **TDD-first** (see `spec.md §2.0` and `docs/testing-strategy.md`). Add or update tests before code; if a behavior intentionally changes, refresh the relevant golden fixture in the same change.
4. After completing any implementation task, **update `wip.md`** to keep checkboxes and the snapshot honest. This is restated at the bottom of `wip.md`.
5. Benchmarks live under `benchmarks/` and run via `npm run bench:frontend|python|rust|hardware`. Threshold policy is defined in `benchmarks/thresholds.yml` and is currently warn-only — see `docs/research-decision-gates.md`.
6. The portable build is the canonical end-to-end smoke (`npm run portable:build`); it enforces a sidecar-freshness guard and stages the `demucs_6` modelpack, plus optional `demucs_ft_drums` when present, with manifest validation.
7. App boundaries: `apps/desktop` is AuralStudio (authoring/import), `apps/game` is AuralPrimer (gameplay/runtime). `spec.md §1.1` defines the non-negotiable split — flag any change that crosses it.
