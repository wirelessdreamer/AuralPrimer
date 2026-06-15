# `apps/game` frontend architecture

The game frontend is a Vite-built TypeScript bundle that runs inside the Tauri
shell (`apps/game/src-tauri`). `main.ts` is the **bootstrap module** —
it lays out the HTML, grabs DOM handles, constructs every panel/controller,
wires them together, and runs the per-frame visualizer tick loop.

Everything else lives in a sibling module with a small public surface and
explicit dependencies declared via an `init…(deps): Handle` factory.

## The `init(deps): Handle` pattern

Every extracted panel/controller follows the same shape:

```ts
export type FooPanelDeps = {
  /* host hooks the panel needs at call time */
  consoleBridge: ConsoleBridge;
  somethingElse: (msg: string) => void;
};

export type FooPanelHandle = {
  /* what the host can call back into */
  refresh: () => Promise<void>;
  disable: () => void;
};

export function initFooPanel(deps: FooPanelDeps): FooPanelHandle {
  const el = document.getElementById("foo");
  if (!el) throw new Error("initFooPanel: #foo missing");

  el.addEventListener("click", () => { /* own the wiring */ });

  async function refresh() { /* … */ }
  function disable() { /* … */ }

  return { refresh, disable };
}
```

**Key invariants:**

- Panels never read host module state directly. Everything comes through `deps`
  (eager values) or `deps.getX()` getters (lazy reads of host `let`s).
- Handles never expose mutable state. The host calls handle methods; the
  handle decides what changes inside the panel.
- DOM grabs live **inside** the init function. The host doesn't grab DOM and
  pass refs in — that creates two ownership paths for the same element.
- Init throws a `Error("init…: required DOM (#a/#b/#c) missing")` if any DOM
  element is absent. Fail loud at boot, not at first user click.

## Two patterns for breaking circular dependencies

### Pattern A — function-declaration hoisting (the simple case)

When panel A's deps reference a function declared further down in `main.ts`,
declare the dep as an arrow that calls the function at invocation time:

```ts
const panelA = initPanelA({
  doThing: () => doThing(),  // doThing is declared 100 lines below
});

// … later …
function doThing() { /* … */ }
```

`function` declarations hoist, so the reference works. The arrow doesn't
fire until the user interacts.

### Pattern B — `let`-ref forward-declaration (mutual deps)

When panel A needs panel B and panel B needs panel A, neither can be the
first one constructed. Use a mutable `let _ref: Handle | null = null`:

```ts
let _bRef: PanelBHandle | null = null;
const panelA = initPanelA({
  // panelB doesn't exist yet — the arrow captures _bRef which gets
  // populated after panelB is constructed.
  askB: () => _bRef?.askB() ?? null,
});

const panelB: PanelBHandle = initPanelB({
  askA: () => panelA.askA(),  // panelA is a `const` already in scope
});
_bRef = panelB;
```

The lambdas only fire after boot (user interaction or RAF tick), by which
point both panels exist.

**Used by:**
- `pluginsPanel ↔ playersPanel` (Phase 2.M) — plugins panel needs the
  players' preferred plugin id; players panel needs the plugins panel for
  selection-mode control.
- `playSurfaceController ↔ routeController` (Phase 2.P) — surface controller
  needs `currentRoute`; route controller needs `syncPlaySurfaceMode`.

## The TDZ workaround for self-rendering inits

Some panels render their initial state as part of `init()`. If that render
synchronously calls back into a host `let` that's declared further down,
you get `ReferenceError: Cannot access 'X' before initialization` (a
Temporal Dead Zone violation).

**Fix:** wrap the initial render in `queueMicrotask` so module evaluation
finishes before the render fires:

```ts
// Inside initPlayersPanel:
queueMicrotask(() => {
  rerenderAndApplyAvailability();
});
```

The dist-bundle integration test (`distBundleSongLibrary.integration.test.ts`)
catches this class of bug by importing the actual built bundle into a jsdom
context and exercising the boot path — a TDZ violation surfaces as a
`ReferenceError` that aborts the entire boot.

## Module map

`main.ts` (~1,135 lines, down from 3,517 pre-Phase 2 — **–67.7%**) owns:
- The bootstrap sequence (DOM setup, panel construction in dependency order)
- The 30+ module-level state `let`s for the current AuralSong / transport
- The visualizer lifecycle (`startVisualizer` / `stopVisualizer` /
  `restartVisualizerForPluginSelection` / tick loop) — kept inline because
  every panel feeds it
- The audio session flow (`selectAuralSong` / `loadAudioFromSelectedAuralSong`
  / `startSelectedSongSession`)
- Boot orchestration (panel `.refresh()` calls + the `beforeunload` cleanup)

Everything else lives in a sibling module:

| Module | Owns | Phase |
|---|---|---|
| `appShellHtml.ts` | The full HTML template assigned to `#app` | 2.R |
| `audioBackend.ts` / `htmlAudioTimebase.ts` / `nativeAudioTimebase.ts` / `webAudioTimebase.ts` | Transport timebase implementations | pre-Phase 2 |
| `audioOutputPanel.ts` | Audio host + device picker + apply controls | 2.D |
| `audioTransportPanel.ts` | Load / Play / Pause / Stop / Seek + Loop set/clear + `setAudioStatus` / `setVizStatus` | 2.Q |
| `capsPanel.ts` | Caps pill rows + per-player chip availability sweep + `computeSongCapabilities` | 2.L |
| `chartLoader.ts` | MIDI chart parsing (drums + melodic tracks) | pre-Phase 2 |
| `consoleBridge.ts` | Mirror frontend logs to Rust via `frontend_log` | 2.I |
| `hud.ts` | HUD utility — extract key/mode from manifest | pre-Phase 2 |
| `ingestClient.ts` / `ingestUi.ts` | Sidecar ingest flow (AuralStudio-only) | pre-Phase 2 |
| `instrumentHints.ts` | Pure helpers — classify charts/manifest by instrument | 2.L |
| `instrumentTypes.ts` / `manifestTypes.ts` | Shared types | mid-Phase 2 |
| `lyricsGenerator.ts` | Per-song lyrics generation (offline) | pre-Phase 2 |
| `lyricsPanel.ts` | Per-frame playback lyrics rendering | 2.F |
| `metronome.ts` | Metronome engine | pre-Phase 2 |
| `midiInput.ts` | MIDI input state tracker (used by `midiPanel`) | pre-Phase 2 |
| `midiPanel.ts` | MIDI in/out DOM + listeners + output clock | 2.F |
| `modelsPanel.ts` | Preferred-modelpack list + install controls | 2.K |
| `pauseMenu.ts` | Pause-menu overlay + 2 modes + Resume/Back wiring | 2.J |
| `playbackRateAndMetronomePanel.ts` | Playback rate input + metronome enable/volume | 2.V |
| `playSurfaceController.ts` | Melodic surface — tab/piano-roll + layout mode toggles | 2.O |
| `playersPanel.ts` | Player chip rendering + add-player + default plugin id mapping | 2.B |
| `plugins.ts` / `pluginsUi.ts` | Plugin discovery (builtin + bundled + user) | pre-Phase 2 |
| `pluginsPanel.ts` | `#pluginSelect` DOM + availability gating + auto/user mode | 2.M |
| `refinementLoader.ts` | Per-instrument refinement overlay loader | 2.E |
| `routeController.ts` | Top-level route nav + play-step state + focus mode | 2.P |
| `scrollSpeedController.ts` | Note-spacing slider + persistence + transport multiplier | 2.A |
| `secondaryStagesController.ts` | Players 2..N visualizer canvases + per-frame render | 2.U |
| `sessionStart.ts` | Pre-Phase 2 — session-start helper | pre-Phase 2 |
| `songChartLoader.ts` | `readSongChartSelection` — read notes.mid + apply refinement overlays | 2.T |
| `songDetailsView.ts` | HUD key chip + selected-song labels + details rail | 2.N |
| `songLibraryPanel.ts` | Song-library list + filesystem watcher + override controls | 2.C |
| `auralsongAudioLoader.ts` | AuralSong audio → transport timebase loader | pre-Phase 2 |
| `tabRenderer.ts` | Legacy shim — `export * from "@auralprimer/viz-tab"` | 2.G |
| `transportController.ts` | Transport playback control + scroll-speed multiplier | pre-Phase 2 |
| `vizSongContext.ts` | Pure compute — assemble notes/lyrics/charts for visualizer init | 2.S |

## When to extract another module

Look for one of:

1. **Concentrated DOM ownership** — N elements queried + N listeners wired
   that no other module touches. Easy lift.
2. **State that only one feature reads/writes** — a `let` plus 3+ functions
   that mutate it form a natural panel.
3. **A pure compute over module state** — pulls cleanly into a free function
   plus a host-side thin wrapper that feeds it the state.

When you DON'T extract:

- **Cross-cutting state** — the visualizer lifecycle reads from every panel
  and writes to many. It's the orchestrator, not a panel.
- **Tiny utility** — `function clamp(v, lo, hi)` doesn't need its own file.
- **Trivial DOM grab + apply** — if it's three lines and one element, leave it.

## Phase 2 history

The decomposition spanned commits 2.A through 2.V across two sessions, with
one sidecar improvement (2.G) and one HTML template extraction (2.R). The
running scoreboard (main.ts line count, line count cut, percent of original):

| Phase | Module | Lines after | Cut | % of 3,517 |
|---|---|---|---|---|
| Start | — | 3,517 | — | 100.0% |
| 2.A | `scrollSpeedController.ts` | — | — | — |
| 2.B-2.G | playersPanel / songLibraryPanel / lyricsPanel / midiPanel / refinementLoader / audioOutputPanel / viz-tab | 2,484 | −1,033 | 70.6% |
| 2.I | `consoleBridge.ts` | — | — | — |
| 2.J | `pauseMenu.ts` (+fixed 2 silent runtime bugs from 2.F) | — | — | — |
| 2.K | `modelsPanel.ts` | 2,278 | −206 | 64.8% |
| 2.L | `instrumentHints.ts` + `capsPanel.ts` | — | — | — |
| 2.M | `pluginsPanel.ts` (first mutual-dep break via let-ref) | 1,930 | −348 | 54.9% |
| 2.N | `songDetailsView.ts` | — | — | — |
| 2.O | `playSurfaceController.ts` | 1,801 | −129 | 51.2% |
| 2.P | `routeController.ts` | 1,724 | −77 | 49.0% |
| 2.Q | `audioTransportPanel.ts` | 1,681 | −43 | 47.8% |
| 2.R | `appShellHtml.ts` (extracted 352-line template) | 1,329 | −352 | 37.8% |
| 2.S | `vizSongContext.ts` (+ orphan cleanup) | 1,279 | −50 | 36.4% |
| 2.T | `songChartLoader.ts` | 1,251 | −28 | 35.6% |
| 2.U | `secondaryStagesController.ts` | 1,144 | −107 | 32.5% |
| 2.V | `playbackRateAndMetronomePanel.ts` | 1,135 | −9 | 32.3% |

Total: **3,517 → 1,135 lines, −2,382 lines, −67.7%.**
