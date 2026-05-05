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

function resolveWindowsCmdHost() {
  if (process.platform === "win32") {
    return process.env.ComSpec || "cmd.exe";
  }

  const candidates = [
    "/mnt/c/Windows/System32/cmd.exe",
    "/mnt/c/Windows/Sysnative/cmd.exe",
    "cmd.exe",
  ];
  return candidates.find(fileExists) ?? "cmd.exe";
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

function escapeCmdArg(value) {
  return value.replaceAll('"', '""');
}

function renderWindowsCmdInvocation(cmdPath, cwdPath, commandArgs) {
  const escapedArgs = commandArgs.map((value) => `"${escapeCmdArg(value)}"`).join(" ");
  const isUnc = cwdPath.startsWith("\\\\");
  const changeDir = isUnc ? `pushd "${cwdPath}"` : `cd /d "${cwdPath}"`;
  const restoreDir = isUnc ? " & set TAURI_EXIT=%ERRORLEVEL% & popd & exit /b %TAURI_EXIT%" : "";
  return `${changeDir} && "${cmdPath}" ${escapedArgs}${restoreDir}`.trim();
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/run-tauri.mjs <dev|build> [args...]");
  process.exit(2);
}

if (process.platform === "win32") {
  const cmdHost = resolveWindowsCmdHost();
  const tauriBinDir = path.join(repoRoot, "node_modules", ".bin");
  const env = {
    ...process.env,
    PATH: `${tauriBinDir};${process.env.PATH ?? ""}`,
  };
  const result = spawnSync(cmdHost, ["/d", "/s", "/c", "tauri.cmd", ...args], {
    cwd: process.cwd(),
    env,
    stdio: "inherit",
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

if (isWsl()) {
  const cmdHost = resolveWindowsCmdHost();
  const winCwd = toWindowsPath(process.cwd());
  const winTauriCmd = toWindowsPath(path.join(repoRoot, "node_modules", ".bin", "tauri.cmd"));
  const command = renderWindowsCmdInvocation(winTauriCmd, winCwd, args);
  const result = spawnSync(cmdHost, ["/d", "/s", "/c", command], {
    stdio: "inherit",
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

const tauriBin = path.join(repoRoot, "node_modules", ".bin", "tauri");
const result = spawnSync(tauriBin, args, {
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
