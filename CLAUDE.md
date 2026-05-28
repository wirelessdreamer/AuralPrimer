# AuralPrimer — project instructions

## Build & verify

**Always rebuild the portable after every change the user will test in the
app.** The user runs `D:\AuralPrimer\AuralPrimerPortable\AuralPrimer.exe`,
which embeds a snapshot of the frontend + sidecar. Source/dist changes are
invisible there until the portable is rebuilt — do not ask the user to
verify a UI/behavior change without first producing a fresh portable.

- **Frontend-only change** (TS/CSS in `apps/game`, `visualizers/*`,
  `packages/*`): rebuild the game exe so it re-embeds the new dist, then
  repack. `--no-bundle` does NOT propagate through the compound
  `game:build` npm script, so build the exe directly to avoid the WiX MSI
  failure:
  ```
  npm -w @auralprimer/game run build            # vite -> dist/
  cd apps/game && node ../../scripts/run-tauri.mjs build --no-bundle
  pwsh create_portable.ps1 -SkipStudioBuild -SkipSidecarBuild
  ```
- **Sidecar change** (Python in `python/ingest`): also rebuild the sidecar
  (drop `-SkipSidecarBuild`). Needed before the user re-imports a song.
- The portable repack fails if `AuralPrimer.exe` is running (webview cache
  lock). If repack fails on a locked file, ask the user to close the app.

## CLAUDE.md note

The cross-project coding-behavior guidelines (Think before coding /
Simplicity first / Surgical changes / Goal-driven execution) live in the
user-global `~/.claude/CLAUDE.md`. They apply here too.
