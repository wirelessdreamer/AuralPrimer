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

## Attribution — keep it current (before every commit)

**Any commit that adds or removes a third-party dependency MUST update the
attribution in `README.md` in the same commit.** The project ships commercially
under a strict license gate (permissive code only; no GPL runtime deps; no
non-commercial model weights), so the dependency list must stay complete and
accurate — attribution is a shipping requirement here, not optional metadata.

Watch these dependency manifests: `python/ingest/pyproject.toml` (+ its
`.egg-info` regen), any `package.json`, and the `src-tauri/Cargo.toml` files.
When one changes, before committing:

1. Add or remove the component in `README.md` → **Third-party components &
   attribution** — in BOTH the "Who makes what we use" org table AND the
   matching category table (music-ML / Python runtime / frontend / Rust), with
   the maker, its role, and the license.
2. Confirm the license clears the gate — for a neural model, verify the trained
   *weights* license separately from the code license. If it fails the gate it
   does not ship; pick a compliant alternative (see the license-gate note + the
   research docs).
3. On removal, delete the component's rows so the list never overstates what we
   actually bundle.

## CLAUDE.md note

The cross-project coding-behavior guidelines (Think before coding /
Simplicity first / Surgical changes / Goal-driven execution) live in the
user-global `~/.claude/CLAUDE.md`. They apply here too.
