import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");

function isWsl() {
  if (process.platform !== "linux") {
    return false;
  }
  return Boolean(process.env.WSL_DISTRO_NAME || process.env.WSL_INTEROP);
}

function fileExists(p) {
  try {
    return fs.existsSync(p);
  } catch {
    return false;
  }
}

function toWindowsPath(inputPath) {
  const result = spawnSync("wslpath", ["-w", inputPath], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || `wslpath failed for ${inputPath}`);
  }
  return result.stdout.trim();
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/run-tauri.mjs <dev|build> [args...]");
  process.exit(2);
}

if (isWsl()) {
  const cmdHost = fileExists("/mnt/c/Windows/System32/cmd.exe")
    ? "/mnt/c/Windows/System32/cmd.exe"
    : "cmd.exe";
  const winCwd = toWindowsPath(process.cwd());
  const winTauriCmd = toWindowsPath(path.join(repoRoot, "node_modules", ".bin", "tauri.cmd"));
  const escapedArgs = args.map((value) => `"${value.replaceAll('"', '\\"')}"`).join(" ");
  const command = `cd /d "${winCwd}" && "${winTauriCmd}" ${escapedArgs}`.trim();
  const result = spawnSync(cmdHost, ["/d", "/s", "/c", command], {
    stdio: "inherit",
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

const tauriBin =
  process.platform === "win32"
    ? path.join(repoRoot, "node_modules", ".bin", "tauri.cmd")
    : path.join(repoRoot, "node_modules", ".bin", "tauri");
const result = spawnSync(tauriBin, args, {
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
