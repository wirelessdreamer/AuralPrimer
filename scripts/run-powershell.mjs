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

function resolveWindowsPowerShell() {
  const candidates = [
    "powershell.exe",
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Windows/Sysnative/WindowsPowerShell/v1.0/powershell.exe",
    "pwsh.exe",
  ];
  return candidates.find(fileExists) ?? "powershell.exe";
}

function resolvePowerShellHost() {
  if (process.platform === "win32") {
    return "powershell.exe";
  }
  if (isWsl()) {
    return resolveWindowsPowerShell();
  }
  return "pwsh";
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

function escapePowerShellSingleQuoted(value) {
  return value.replaceAll("'", "''");
}

function resolveWindowsArch() {
  const cmdHost = fileExists("/mnt/c/Windows/System32/cmd.exe")
    ? "/mnt/c/Windows/System32/cmd.exe"
    : "cmd.exe";
  const probe = spawnSync(
    cmdHost,
    ["/d", "/s", "/c", "echo %PROCESSOR_ARCHITECTURE%|%PROCESSOR_ARCHITEW6432%"],
    {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  if (probe.status !== 0) {
    return "";
  }
  return probe.stdout.trim().toUpperCase();
}

function resolveWindowsTargetTriple() {
  const arch = resolveWindowsArch();
  if (arch.includes("ARM64")) {
    return "aarch64-pc-windows-msvc";
  }
  if (arch.includes("AMD64") || arch.includes("X86_64")) {
    return "x86_64-pc-windows-msvc";
  }
  return "";
}

const argv = process.argv.slice(2);
if (argv.length === 0) {
  console.error("usage: node scripts/run-powershell.mjs <script.ps1> [args...]");
  process.exit(2);
}

const host = resolvePowerShellHost();
const scriptArg = argv[0];
const scriptPath = path.isAbsolute(scriptArg) ? scriptArg : path.resolve(repoRoot, scriptArg);
const trailingArgs = argv.slice(1);

let fileArg = scriptPath;
let commandArgs = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", fileArg, ...trailingArgs];
const env = { ...process.env };
if (isWsl() && host.toLowerCase().endsWith(".exe")) {
  const windowsCwd = toWindowsPath(process.cwd());
  fileArg = toWindowsPath(scriptPath);
  const renderedArgs = trailingArgs
    .map((value) => `'${escapePowerShellSingleQuoted(value)}'`)
    .join(", ");
  const invokeArgs = renderedArgs ? `@(${renderedArgs})` : "@()";
  const command = [
    `$ErrorActionPreference = 'Stop'`,
    `Set-Location -LiteralPath '${escapePowerShellSingleQuoted(windowsCwd)}'`,
    `$scriptArgs = ${invokeArgs}`,
    `& '${escapePowerShellSingleQuoted(fileArg)}' @scriptArgs`,
  ].join("; ");
  commandArgs = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command];

  if (!env.TAURI_ENV_TARGET_TRIPLE && !env.CARGO_BUILD_TARGET && !env.TARGET) {
    const targetTriple = resolveWindowsTargetTriple();
    if (targetTriple) {
      env.TAURI_ENV_TARGET_TRIPLE = targetTriple;
    }
  }
}

const result = spawnSync(host, commandArgs, {
  cwd: process.cwd(),
  env,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
