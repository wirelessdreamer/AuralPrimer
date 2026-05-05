import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const executable = process.platform === "win32" ? "py" : "python3";
const executableArgs = process.platform === "win32" ? ["-3"] : [];
const result = spawnSync(
  executable,
  [
    ...executableArgs,
    resolve(root, "scripts", "check-benchmark-thresholds.py"),
    "--frontend-json",
    resolve(root, "benchmarks", "frontend", "vitest-bench.latest.json"),
    "--python-json",
    resolve(root, "benchmarks", "python", "pytest-benchmark.latest.json"),
    "--hardware-json",
    resolve(root, "benchmarks", "hardware", "local-profile.latest.json"),
    ...process.argv.slice(2),
  ],
  { cwd: root, stdio: "inherit", shell: false },
);

process.exit(result.status ?? 1);
