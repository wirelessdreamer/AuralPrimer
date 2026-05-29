#!/usr/bin/env node
/**
 * Packaged-portable boot smoke test.
 *
 * Launches the built `AuralPrimerPortable\AuralPrimer.exe`, waits a bounded
 * amount of time, and verifies the app *booted* without crashing:
 *
 *   1. the process stays alive for the liveness window (it didn't exit/panic
 *      during init), AND
 *   2. a runtime marker appears under the portable data dir, proving the app
 *      reached the point where it builds its window and arms the songs-folder
 *      watcher (i.e. the library-scan codepath ran):
 *        - data/webview/main/   (created by build_main_window)
 *        - data/songs/          (created by the songs-folder watcher at boot)
 *
 * To get a clean, isolated marker location we point the app at a throwaway
 * data dir via AURALPRIMER_PORTABLE_DATA_DIR (honored by
 * resolve_portable_data_dirs in apps/game/src-tauri/src/lib.rs), so the smoke
 * test never touches real user songs/config.
 *
 * LIMITATIONS (intentional, documented):
 *   - This is a *liveness + boot-marker* check, not a UI assertion. It does not
 *     drive WebView2, click anything, or read rendered DOM. The in-process
 *     jsdom integration test (apps/game/tests/distBundleSongLibrary
 *     .integration.test.ts) covers the bundle's runtime behavior; this script
 *     only catches "the packaged exe crashes on boot / never opens a window".
 *   - It requires a desktop session with WebView2 available. On a headless
 *     runner with no WebView2 runtime the app may exit early; treat a missing
 *     marker as a real failure only on a machine where the portable is
 *     expected to run.
 *   - If the portable exe isn't present it SKIPS (exit 0) so CI doesn't break
 *     when no portable was built. Pass --require to turn that skip into a
 *     failure.
 *
 * Usage:
 *   node scripts/smoke-portable.mjs [--exe <path>] [--timeout-ms N] [--require]
 * Env:
 *   AURALPRIMER_PORTABLE_EXE   override exe path
 *   AURALPRIMER_PORTABLE_ROOT  override portable root (exe = <root>/AuralPrimer.exe)
 */
import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function parseArgs(argv) {
  const out = { exe: null, timeoutMs: 25_000, require: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--exe") out.exe = argv[++i];
    else if (a === "--timeout-ms") out.timeoutMs = Number(argv[++i]) || out.timeoutMs;
    else if (a === "--require") out.require = true;
  }
  return out;
}

function resolveExe(cliExe) {
  if (cliExe) return resolve(cliExe);
  if (process.env.AURALPRIMER_PORTABLE_EXE) {
    return resolve(process.env.AURALPRIMER_PORTABLE_EXE);
  }
  const root =
    process.env.AURALPRIMER_PORTABLE_ROOT ||
    process.env.PORTABLE_ROOT ||
    "D:/AuralPrimer/AuralPrimerPortable";
  return resolve(root, "AuralPrimer.exe");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const exe = resolveExe(args.exe);

  if (process.platform !== "win32") {
    console.log(`[smoke-portable] SKIP: portable smoke test is Windows-only (platform=${process.platform}).`);
    return args.require ? 1 : 0;
  }

  if (!existsSync(exe)) {
    const msg = `[smoke-portable] portable exe not found: ${exe}`;
    if (args.require) {
      console.error(`${msg} (--require set -> failing)`);
      return 1;
    }
    console.log(`${msg} -> SKIP (no portable built).`);
    return 0;
  }

  // Isolated throwaway data dir so we read a clean marker and never touch real
  // user data.
  const dataDir = mkdtempSync(join(tmpdir(), "auralprimer-smoke-"));
  const webviewMarker = join(dataDir, "webview", "main");
  const songsMarker = join(dataDir, "songs");
  mkdirSync(dataDir, { recursive: true });

  console.log(`[smoke-portable] exe:      ${exe}`);
  console.log(`[smoke-portable] data dir: ${dataDir}`);
  console.log(`[smoke-portable] timeout:  ${args.timeoutMs}ms`);

  const child = spawn(exe, [], {
    cwd: dirname(exe),
    env: { ...process.env, AURALPRIMER_PORTABLE_DATA_DIR: dataDir, RUST_BACKTRACE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: false,
  });

  let exitedEarly = null; // { code, signal }
  let stderrTail = "";
  child.on("exit", (code, signal) => {
    exitedEarly = { code, signal };
  });
  child.stderr?.on("data", (d) => {
    stderrTail = (stderrTail + d.toString()).slice(-4000);
  });
  child.stdout?.on("data", (d) => {
    stderrTail = (stderrTail + d.toString()).slice(-4000);
  });

  const deadline = Date.now() + args.timeoutMs;
  let markerSeen = false;
  const pollMs = 500;

  while (Date.now() < deadline) {
    if (exitedEarly) break;
    if (existsSync(webviewMarker) || existsSync(songsMarker)) {
      markerSeen = true;
      break;
    }
    await sleep(pollMs);
  }

  // Give a short tail for the process to demonstrate it stays alive after the
  // marker appears (catches "boots, writes marker, then panics").
  let stayedAlive = !exitedEarly;
  if (markerSeen && !exitedEarly) {
    await sleep(Math.min(3000, Math.max(0, deadline - Date.now())));
    stayedAlive = !exitedEarly;
  }

  // Tear down the app.
  let killed = false;
  if (!exitedEarly && child.pid != null) {
    try {
      // taskkill /T ends the WebView2 child processes too.
      const tk = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
      });
      await new Promise((r) => tk.on("exit", r));
      killed = true;
    } catch {
      try {
        child.kill("SIGKILL");
        killed = true;
      } catch {
        /* ignore */
      }
    }
  }

  try {
    rmSync(dataDir, { recursive: true, force: true });
  } catch {
    /* best-effort cleanup */
  }

  // Verdict.
  if (exitedEarly && exitedEarly.code !== 0) {
    console.error(
      `[smoke-portable] FAIL: app exited early during boot ` +
        `(code=${exitedEarly.code} signal=${exitedEarly.signal}).`
    );
    if (stderrTail.trim()) {
      console.error(`[smoke-portable] --- last output ---\n${stderrTail.trim()}`);
    }
    return 1;
  }

  if (!markerSeen) {
    console.error(
      `[smoke-portable] FAIL: no boot marker appeared within ${args.timeoutMs}ms ` +
        `(expected '${webviewMarker}' or '${songsMarker}'). The app did not reach ` +
        `the window-build / library-scan step.`
    );
    if (stderrTail.trim()) {
      console.error(`[smoke-portable] --- last output ---\n${stderrTail.trim()}`);
    }
    return 1;
  }

  if (!stayedAlive) {
    console.error(
      `[smoke-portable] FAIL: app produced a boot marker but then exited ` +
        `(code=${exitedEarly?.code} signal=${exitedEarly?.signal}).`
    );
    return 1;
  }

  console.log(
    `[smoke-portable] PASS: app booted, wrote a runtime marker, and stayed alive ` +
      `(killed=${killed}).`
  );
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error(`[smoke-portable] ERROR: ${err?.stack ?? err}`);
    process.exit(1);
  });
