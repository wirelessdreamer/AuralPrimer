/**
 * Source-level regression test for the song-library auto-refresh fix.
 *
 * Context: a Psalm 19 SongPack imported via `aural_ingest import` did not
 * surface in the Play Songs panel because `showSongLibraryStep()` (which is
 * called on app boot, from the pause menu, from focus-toggle, AND from
 * `openPlaySongFlow`) did not trigger `refresh()`. The fix was to move the
 * `void refresh()` call into `showSongLibraryStep` itself so every entry
 * into the library panel rescans the songs folder.
 *
 * Full unit-testing main.ts is impractical (3,500+ lines of side-effectful
 * module init with Tauri invokes and DOM bindings). We instead pin the fix
 * at the source level: assert the call exists and lives inside the right
 * function. If a future refactor extracts the call (e.g., back into
 * `openPlaySongFlow`), this test fails and reminds the author that every
 * library-step entry point needs auto-refresh, not just the play-button
 * click path.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
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
    // doesn't cover (e.g., file dropped into the songs folder while the
    // user has been staring at the same panel for an hour). Don't remove it.
    const src = loadMain();
    expect(src).toMatch(/refreshBtn\.addEventListener\(\s*["']click["']\s*,\s*\(\)\s*=>\s*void\s+refresh\s*\(\s*\)/);
  });
});
