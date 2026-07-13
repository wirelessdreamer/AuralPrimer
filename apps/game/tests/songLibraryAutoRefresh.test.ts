/**
 * Source-level regression tests for the song-library auto-refresh wiring.
 *
 * Two complementary cases:
 *
 *  1. **Library-panel entry refresh.** Originally an imported AuralSong did
 *     not surface in the Play Songs panel because `showSongLibraryStep()`
 *     (called on app boot, from the pause menu, from focus-toggle, and
 *     from `openPlaySongFlow`) did not trigger `refresh()`. The fix moved
 *     `void refresh()` into `showSongLibraryStep` so every library-entry
 *     path rescans the songs folder.
 *
 *  2. **Filesystem-watcher auto-refresh.** While the panel is already
 *     visible, a `.auralsong/` dropped into the songs folder by an external
 *     tool (e.g. `aural_ingest import` from a separate shell) used to stay
 *     invisible until the user clicked Refresh. The Rust side now mounts a
 *     `notify`-based watcher and emits a debounced `songs_folder_changed`
 *     Tauri event; the frontend listens for it inside a `haveTauri()`
 *     guard and calls `refresh()`. A boot-time `start_songs_folder_watch`
 *     invoke is the backstop in case setup() race-loses to a missing
 *     folder.
 *
 * Full unit-testing main.ts is impractical (4.5k+ lines, side-effectful
 * Tauri/DOM init). We pin both wirings at the source level so future
 * refactors that drop a call get caught.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MAIN_TS = resolve(__dirname, "..", "src", "main.ts");
// Phase 2.C extracted the refresh function + the filesystem-watcher wiring +
// the songs-folder override handlers into songLibraryPanel.ts. Phase 2.P moved
// showSongLibraryStep / openPlaySongFlow / setRoute / etc. into
// routeController.ts -- main.ts still wraps showSongLibraryStep and owns the
// queueMicrotask boot wiring, the panel module owns the watcher, and the
// route controller owns the step-show calls.
const PANEL_TS = resolve(__dirname, "..", "src", "songLibraryPanel.ts");
const ROUTE_TS = resolve(__dirname, "..", "src", "routeController.ts");

function loadMain(): string {
  return readFileSync(MAIN_TS, "utf-8");
}

function loadPanel(): string {
  return readFileSync(PANEL_TS, "utf-8");
}

function loadRoute(): string {
  return readFileSync(ROUTE_TS, "utf-8");
}

function extractFunctionBody(src: string, signature: RegExp): string {
  const match = signature.exec(src);
  if (!match) {
    throw new Error(`function signature not found: ${signature}`);
  }
  // Walk braces from the opening `{` after the signature.
  let depth = 0;
  let started = false;
  let start = -1;
  for (let i = match.index; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") {
      if (!started) {
        start = i + 1;
        started = true;
      }
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        return src.slice(start, i);
      }
    }
  }
  throw new Error(`unclosed function body for ${signature}`);
}

describe("song-library auto-refresh on show", () => {
  it("showSongLibraryStep triggers songLibraryPanel.refresh() so every library entry rescans", () => {
    // Phase 2.P moved showSongLibraryStep into routeController.ts. The handler
    // reaches the panel via deps.songLibraryPanel.refresh().
    const src = loadRoute();
    const body = extractFunctionBody(src, /function showSongLibraryStep\s*\(\s*\)\s*:\s*void\s*{/);
    expect(body).toMatch(/\bvoid\s+deps\.songLibraryPanel\.refresh\s*\(\s*\)\s*;/);
  });

  it("openPlaySongFlow defers refresh to showSongLibraryStep (no double-call)", () => {
    // If a future change re-adds songLibraryPanel.refresh() to openPlaySongFlow
    // without also documenting why, refresh runs twice on Play-button click
    // and races its own previous in-flight invoke. Comment it out (or wrap
    // it) before re-adding.
    const src = loadRoute();
    const body = extractFunctionBody(src, /function openPlaySongFlow\s*\(\s*\)\s*:\s*void\s*{/);
    // showSongLibraryStep MUST be called.
    expect(body).toMatch(/showSongLibraryStep\s*\(\s*\)/);
    // Direct panel refresh MUST NOT be called here.
    expect(body).not.toMatch(/songLibraryPanel\.refresh\s*\(\s*\)/);
  });

  it("refresh() is reachable from boot-time showSongLibraryStep call", () => {
    // The boot-time call ensures the panel populates on app launch. It MUST
    // be wrapped in a `queueMicrotask` (or equivalent deferred scheduler)
    // because `showSongLibraryStep` transitively reads `let players`, which
    // is declared further down the file -- calling it synchronously at
    // module top-level triggers a TDZ ReferenceError that silently halts
    // the rest of module init (and leaves the Refresh button + filesystem
    // watcher unwired). See the matching dist-bundle runtime test.
    const src = loadMain();
    expect(src).toMatch(
      /queueMicrotask\(\s*\(\s*\)\s*=>\s*\{[\s\S]*?showSongLibraryStep\s*\(\s*\)\s*;[\s\S]*?\}\s*\)\s*;/
    );
  });

  it("library refresh keeps the Refresh button as a manual fallback", () => {
    // The Refresh button is the user's escape hatch for cases the auto-call
    // doesn't cover. Don't remove it. After Phase 2.C this listener lives
    // in songLibraryPanel.ts.
    const panel = loadPanel();
    expect(panel).toMatch(/refreshBtn\.addEventListener\(\s*["']click["']\s*,\s*\(\)\s*=>\s*void\s+refresh\s*\(\s*\)/);
  });
});

describe("song-library auto-refresh on filesystem change", () => {
  // Phase 2.C moved this wiring from main.ts to songLibraryPanel.ts. The
  // assertions stay identical in spirit; only the file under inspection
  // changed.
  const panelTsPath = join(__dirname, "..", "src", "songLibraryPanel.ts");
  const panelTsSource = readFileSync(panelTsPath, "utf8");

  it("listens for songs_folder_changed events from the Rust watcher", () => {
    expect(panelTsSource).toMatch(/listen\(\s*["']songs_folder_changed["']/);
  });

  it("invokes refresh() inside the songs_folder_changed handler", () => {
    // The handler body should call refresh() so a single Tauri event triggers
    // the same code path as the manual refresh button.
    const handlerBlockMatch = panelTsSource.match(
      /listen\(\s*["']songs_folder_changed["'][^)]*?,\s*\(\s*\)\s*=>\s*\{([\s\S]*?)\}\s*\)/
    );
    expect(handlerBlockMatch).not.toBeNull();
    expect(handlerBlockMatch?.[1] ?? "").toMatch(/refresh\(\)/);
  });

  it("guards the watcher wiring behind deps.haveTauri()", () => {
    // We only want to register the listener in the desktop shell, not in the
    // browser-only Vite dev server. The simplest test is to confirm the
    // listen() call sits inside a haveTauri() conditional block.
    expect(panelTsSource).toMatch(
      /if\s*\(\s*deps\.haveTauri\(\)\s*\)\s*\{[\s\S]*?listen\(\s*["']songs_folder_changed["']/
    );
  });

  it("calls start_songs_folder_watch as a boot-time backstop", () => {
    // Even if the Rust setup() race-loses to a missing folder, the frontend
    // re-arms the watcher idempotently on boot.
    expect(panelTsSource).toMatch(/invoke\(\s*["']start_songs_folder_watch["']/);
  });
});
