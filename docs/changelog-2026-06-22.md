# Changelog — 2026-06-22

A batch of game-app features and fixes, plus piano-import validation and three
research deep-dives (see the `docs/research-*-2026-06-22.md` companions).

## Audio / transport

- **A/V sync calibration** (`apps/game/src/avCalibration.ts`): a Rock Band-style
  latency calibrator. A metronome plays; the user taps to the beat they *hear*;
  the median click→tap delay is the true end-to-end audio latency (Bluetooth
  included) and is applied as an offset so falling notes hit the PLAY HERE line
  in sync. Lives in **Configure → Audio → A/V Sync** (Calibrate / Reset).
  Persisted (`localStorage`), with live nudge hotkeys **Ctrl+[ / Ctrl+] / Ctrl+0**.
- **Manual A/V offset + auto latency** (`transportController.ts`,
  `nativeAudioTimebase.ts`): transport subtracts a manual offset *and* a better
  auto estimate (output latency now modelled as double-buffered, not one buffer).
  Default 0 = unchanged behaviour.
- **Pitch-preserving slowdown** (`apps/game/src-tauri/src/native_audio.rs`): a
  WSOLA time-stretcher replaces the old resample-based rate change, so 0.5×–2×
  keeps the original pitch. Engaged only when `rate != 1` (1× is byte-identical
  to before). Transport label is now "Speed (keeps pitch)". Unit-tested for
  DC-flatness, cursor-tracks-rate, no-NaN, and seek re-anchor.

## Visualization

- **Thin holds / full-width onsets** (`viz-tab/src/index.ts`): piano-roll
  sustains render as a thin centered stem with a thin glow; the onset cap stays
  full key width — distinct attack vs ring-out.
- **Key/mode header fix** (`songDetailsView.ts`, `main.ts`): the HUD key now
  uses the same `inferKeySignature` on the primary track's notes as the
  instrument panel, instead of a hardcoded "C major" default.
- **Nashville numbers** — two surfaces:
  - Piano-roll label mode (`viz-tab` `pitchToNashville` + `nashville` option +
    Band Setup checkbox).
  - **`viz-nashville` dropdown visualizer rewritten** from a placeholder into a
    real scale-degree piano roll driven by `init.song.notes` + inferred key.
- **Sheet music** — two surfaces:
  - `SheetMusicRenderer` (`viz-tab/src/sheetMusic.ts`): grand staff, key
    signature, note heads, playhead; a Piano-roll/Sheet toggle on the melodic
    surface.
  - **New `@auralprimer/viz-sheet` dropdown visualizer** wrapping the renderer
    (offscreen render → blit onto the host frame canvas). Registered in
    `plugins.ts`.

## Layout

- **Play area constrained to the viewport** (`style.css`): the Band Setup stage
  no longer grows past the screen (START stayed reachable). The tab container
  now flex-shrinks (`min-height: 0`) instead of a `70vh` floor, and the body is
  capped to `calc(100dvh - 172px)` with the rail scrolling internally.

## Build / infra

- Project now builds on this machine with **Python 3.11** (the sidecar's
  `basic-pitch`→`tensorflow<2.15.1` pin has no 3.13 wheels) and **PowerShell 7**;
  a project-local `python/ingest/.venv` (3.11) is what `build_sidecar.ps1`
  prefers. `numpy<2` is required for the TF 2.14 ABI despite the pyproject floor.

## Validation

- Imported three piano instrumentals (`C:\PianoPsalms\*`) end-to-end via the
  sidecar; all routed `keys → piano_auto` (Basic Pitch playable path), validated
  `ok`, internal scores 0.88–0.95.
