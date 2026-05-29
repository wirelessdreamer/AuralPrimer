/**
 * Runtime integration test for the Play Songs panel.
 *
 * Source-level regression tests can verify that the right calls APPEAR in
 * main.ts, but they cannot catch a bundle that minifies / hoists / shadows
 * in a way that silently breaks the actual runtime behavior. This test
 * loads the COMPILED dist bundle in jsdom with mocked Tauri invokes and
 * asserts the Play Songs panel actually populates -- i.e., `refresh()`
 * runs, doesn't throw, and updates `statusEl` away from the initial
 * "(not loaded)" placeholder.
 *
 * If this test fails, the released portable binary will show a stuck
 * "(not loaded)" status text and an empty song list. That's the exact
 * failure the user hit twice. We pin behavior at the runtime layer so a
 * future bundle-config change can't silently regress it.
 */
import { describe, expect, it, beforeAll } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST_DIR = resolve(__dirname, "..", "dist");
const DIST_HTML = join(DIST_DIR, "index.html");
const ASSETS_DIR = join(DIST_DIR, "assets");

function findMainBundle(): string | null {
  if (!existsSync(ASSETS_DIR)) return null;
  // Vite emits a single main entry chunk; pick the largest .js file as a
  // sturdy heuristic (the main module dwarfs any code-split helpers).
  const fs = require("node:fs") as typeof import("node:fs");
  const candidates = fs
    .readdirSync(ASSETS_DIR)
    .filter((n) => n.endsWith(".js"))
    .map((n) => ({ name: n, size: fs.statSync(join(ASSETS_DIR, n)).size }))
    .sort((a, b) => b.size - a.size);
  return candidates[0] ? join(ASSETS_DIR, candidates[0].name) : null;
}

describe("dist bundle Play Songs runtime", () => {
  const distHtmlExists = existsSync(DIST_HTML);
  const mainBundle = distHtmlExists ? findMainBundle() : null;

  if (!distHtmlExists || !mainBundle) {
    // In CI the frontend is built before tests, so a missing dist/ means the
    // build step regressed -- FAIL loudly instead of silently skipping (that
    // silent skip is exactly how the "stale portable / broken bundle" failures
    // slipped past CI). Locally (no CI env var) we still skip so the suite is
    // runnable without a prior `npm -w @auralprimer/game run build`.
    if (process.env.CI) {
      it("dist bundle must be built before tests run in CI", () => {
        throw new Error(
          "apps/game/dist/ is missing in CI. Build the frontend " +
            "(`npm -w @auralprimer/game run build`) before `npm run test:coverage` " +
            "so the dist-bundle runtime check actually runs."
        );
      });
    } else {
      it.skip("dist not built; skipping runtime check (run `npm -w @auralprimer/game run build` first)", () => {});
    }
    return;
  }

  let dom: JSDOM;
  let statusElInitialText: string;
  const invokeCalls: string[] = [];

  beforeAll(async () => {
    const html = readFileSync(DIST_HTML, "utf-8");
    const bundleSrc = readFileSync(mainBundle, "utf-8");

    dom = new JSDOM(html, {
      url: "http://localhost/",
      runScripts: "outside-only",
      pretendToBeVisual: true,
    });

    // Swallow async errors from stubbed canvas / audio operations in jsdom
    // so vitest doesn't surface them as unhandled-error warnings. We only
    // care about whether the songs-library codepath populates statusEl;
    // the bundle's visualizer/audio-engine code does plenty of unrelated
    // background work in a real browser that won't fly in jsdom.
    dom.window.addEventListener("error", (e: ErrorEvent) => e.preventDefault());
    dom.window.addEventListener("unhandledrejection", (e) =>
      (e as unknown as { preventDefault: () => void }).preventDefault()
    );

    const win = dom.window as unknown as Window & typeof globalThis & {
      __TAURI_INTERNALS__?: unknown;
    };

    // Stub Tauri internals so the bundle's invoke() path resolves instead
    // of throwing. Tauri 2's frontend lib looks for window.__TAURI_INTERNALS__
    // .invoke; failing that, it falls back to throwing "Tauri runtime not
    // available". Either path is fine as long as the bundle handles it -- we
    // just need to know whether the bundle's runtime is broken or merely
    // gated on a real Tauri host.
    Object.defineProperty(win, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {
        invoke: (cmd: string) => {
          invokeCalls.push(cmd);
          if (cmd === "get_songs_folder") {
            return Promise.resolve("/tmp/test-songs");
          }
          if (cmd === "scan_songpacks") {
            return Promise.resolve([]);
          }
          // Many other invokes happen during module init (model packs, MIDI
          // ports, etc.). Resolve them with empty placeholders so they don't
          // throw and obscure the test signal.
          return Promise.resolve(null);
        },
        ipc: () => {},
      },
    });

    // Bundle expects an event-listener registry; provide a no-op.
    Object.defineProperty(win, "__TAURI__", {
      configurable: true,
      value: {},
    });

    // Stub canvas 2d context: jsdom doesn't implement canvas rendering, and
    // the bundle calls .getContext("2d") during visualizer init. Without
    // this, the bundle throws "missing 2d context" before reaching the
    // showSongLibraryStep boot call -- masking the actual songs-library bug.
    const HTMLCanvasElementCtor = (win as unknown as { HTMLCanvasElement: {
      prototype: { getContext: (...args: unknown[]) => unknown };
    }}).HTMLCanvasElement;
    HTMLCanvasElementCtor.prototype.getContext = function () {
      // Minimal CanvasRenderingContext2D-shaped stub that satisfies callers
      // doing fillRect, clearRect, save, etc.
      const noop = () => {};
      return new Proxy({}, {
        get: (_t, prop) => {
          if (prop === "canvas") return this;
          if (typeof prop === "symbol") return undefined;
          return noop;
        },
        set: () => true,
      });
    };

    // Stub AudioContext + similar audio APIs the visualizer / audio engine
    // may touch during init. We don't assert anything about audio behavior;
    // we just need init to not throw.
    Object.defineProperty(win, "AudioContext", {
      configurable: true,
      value: class { constructor() {} resume() { return Promise.resolve(); } close() { return Promise.resolve(); } createGain() { return {}; } createBuffer() { return {}; } createBufferSource() { return { connect: () => {} }; } destination = {}; },
    });
    Object.defineProperty(win, "webkitAudioContext", {
      configurable: true,
      value: (win as unknown as { AudioContext: unknown }).AudioContext,
    });

    statusElInitialText = "(not loaded)";

    // Execute the bundle inside the jsdom window using node:vm. Vite bundles
    // the entry as an ESM module that emits a trailing `export {};` — eval()
    // chokes on that, but vm.runInContext (script mode) treats the file as
    // top-level code and ignores ESM-only syntax via the V8 parser. We strip
    // any trailing `export {};` just in case.
    const cleaned = bundleSrc.replace(/\bexport\s*\{[^}]*\}\s*;?\s*$/m, "");
    const context = vm.createContext(win);
    let executionError: unknown = null;
    try {
      vm.runInContext(cleaned, context);
    } catch (e) {
      executionError = e;
    }
    if (executionError) {
      throw new Error(
        `dist bundle threw during execution: ${
          (executionError as Error).stack ?? executionError
        }`
      );
    }

    // The bundle is async; let the microtask queue + promise resolutions drain.
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
  });

  it("module init injects the Play Songs DOM template", () => {
    const statusEl = dom.window.document.getElementById("status");
    expect(statusEl).not.toBeNull();
    expect(statusEl?.tagName).toBe("PRE");
  });

  it("bundle invokes get_songs_folder + scan_songpacks at boot (refresh ran)", () => {
    // If neither command was invoked, refresh() didn't actually fire. That
    // matches the user-observed bug where statusEl stayed at "(not loaded)".
    expect(invokeCalls).toContain("get_songs_folder");
    expect(invokeCalls).toContain("scan_songpacks");
  });

  it("statusEl no longer reads '(not loaded)' after refresh settles", () => {
    const statusEl = dom.window.document.getElementById("status");
    expect(statusEl).not.toBeNull();
    const text = statusEl?.textContent ?? "";
    expect(text).not.toBe(statusElInitialText);
    // Should match either the success line or an error string -- but NOT the
    // pristine initial placeholder.
    expect(text.length).toBeGreaterThan(0);
  });

  it("statusEl shows the songsFolder + tracks line on a clean refresh", () => {
    const statusEl = dom.window.document.getElementById("status");
    const text = statusEl?.textContent ?? "";
    expect(text).toMatch(/songsFolder:/);
    expect(text).toMatch(/tracks:\s*0/);
  });
});
