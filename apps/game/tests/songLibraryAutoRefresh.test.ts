/**
 * Source-level regression tests for the song-library auto-refresh wiring.
 *
 * Two complementary cases:
 *
 *  1. **Library-panel entry refresh.** Originally an imported SongPack did
 *     not surface in the Play Songs panel because `showSongLibraryStep()`
 *     (called on app boot, from the pause menu, from focus-toggle, and
 *     from `openPlaySongFlow`) did not trigger `refresh()`. The fix moved
 *     `void refresh()` into `showSongLibraryStep` so every library-entry
 *     path rescans the songs folder.
 *
 *  2. **Filesystem-watcher auto-refresh.** While the panel is already
 *     visible, a `.songpack/` dropped into the songs folder by an external
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

function loadMain(): string {
  return readFileSync(MAIN_TS, "utf-8");
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
  it("showSongLibraryStep triggers refresh() so every library entry rescans", () => {
    const src = loadMain();
    const body = extractFunctionBody(src, /function showSongLibraryStep\s*\(\s*\)\s*{/);

    expect(body).toMatch(/\bvoid\s+refresh\s*\(\s*\)\s*;/);
  });

  it("openPlaySongFlow no longer needs its own refresh() (deduped via showSongLibraryStep)", () => {
    // If a future change re-adds `void refresh()` to openPlaySongFlow without
    // also documenting why, refresh runs twice on Play-button click and races
    // its own previous in-flight invoke. Comment it out (or wrap it) before
    // re-adding.
    const src = loadMain();
    const body = extractFunctionBody(src, /function openPlaySongFlow\s*\(\s*\)\s*{/);

    // showSongLibraryStep MUST be called.
    expect(body).toMatch(/showSongLibraryStep\s*\(\s*\)/);
    // refresh MUST NOT be called directly here.
    expect(body).not.toMatch(/\bvoid\s+refresh\s*\(\s*\)\s*;/);
  });

  it("refresh() is reachable from boot-time showSongLibraryStep call", () => {
    // The boot-time call at the bottom of the file:
    //   showSongLibraryStep();
    //   toggleFocusBtn.disabled = true;
    // ensures the panel populates on app launch. If this line is removed,
    // first paint shows the initial "(not loaded)" text until the user
    // navigates somewhere and back.
    const src = loadMain();
    expect(src).toMatch(
      /\nshowSongLibraryStep\s*\(\s*\)\s*;\s*\n\s*toggleFocusBtn\.disabled\s*=\s*true\s*;/,
      "boot-time showSongLibraryStep() call must remain after the function definition"
    );
  });

  it("library refresh keeps the Refresh button as a manual fallback", () => {
    // The Refresh button is the user's escape hatch for cases the auto-call
    // doesn't cover. Don't remove it.
    const src = loadMain();
    expect(src).toMatch(/refreshBtn\.addEventListener\(\s*["']click["']\s*,\s*\(\)\s*=>\s*void\s+refresh\s*\(\s*\)/);
  });
});

describe("song-library auto-refresh on filesystem change", () => {
  // Use a pre-loaded source for these tests so the regex-heavy assertions
  // don't re-read main.ts repeatedly.
  const mainTsPath = join(__dirname, "..", "src", "main.ts");
  const mainTsSource = readFileSync(mainTsPath, "utf8");

  it("listens for songs_folder_changed events from the Rust watcher", () => {
    expect(mainTsSource).toMatch(/listen\(\s*["']songs_folder_changed["']/);
  });

  it("invokes refresh() inside the songs_folder_changed handler", () => {
    // The handler body should call refresh() so a single Tauri event triggers
    // the same code path as the manual refresh button.
    const handlerBlockMatch = mainTsSource.match(
      /listen\(\s*["']songs_folder_changed["'][^)]*?,\s*\(\s*\)\s*=>\s*\{([\s\S]*?)\}\s*\)/
    );
    expect(handlerBlockMatch).not.toBeNull();
    expect(handlerBlockMatch?.[1] ?? "").toMatch(/refresh\(\)/);
  });

  it("guards the watcher wiring behind haveTauri()", () => {
    // We only want to register the listener in the desktop shell, not in the
    // browser-only Vite dev server. The simplest test is to confirm the
    // listen() call sits inside a haveTauri() conditional block.
    expect(mainTsSource).toMatch(
      /if\s*\(\s*haveTauri\(\)\s*\)\s*\{[\s\S]*?listen\(\s*["']songs_folder_changed["']/
    );
  });

  it("calls start_songs_folder_watch as a boot-time backstop", () => {
    // Even if the Rust setup() race-loses to a missing folder, the frontend
    // re-arms the watcher idempotently on boot.
    expect(mainTsSource).toMatch(/invoke\(\s*["']start_songs_folder_watch["']/);
  });
});
