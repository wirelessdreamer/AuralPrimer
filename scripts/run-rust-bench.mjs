import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(root, "benchmarks", "rust");
mkdirSync(outDir, { recursive: true });

const crates = [
  resolve(root, "apps", "desktop", "src-tauri"),
  resolve(root, "apps", "game", "src-tauri"),
];

const runs = [];
let exitCode = 0;

for (const crateDir of crates) {
  const benchesDir = resolve(crateDir, "benches");
  const crateName = crateDir.includes(`${resolve(root, "apps", "game")}`)
    ? "game"
    : "desktop";
  const benchFiles = existsSync(benchesDir)
    ? readdirSync(benchesDir).filter((name) => name.endsWith(".rs"))
    : [];

  if (benchFiles.length === 0) {
    runs.push({
      crate: crateName,
      crate_dir: crateDir,
      status: "skipped",
      reason: "no Rust benchmark targets found",
    });
    continue;
  }

  const outputPath = resolve(outDir, `${crateName}-cargo-bench.log`);
  const result = spawnSync("cargo", ["bench"], {
    cwd: crateDir,
    encoding: "utf-8",
    shell: false,
  });
  writeFileSync(outputPath, `${result.stdout ?? ""}${result.stderr ?? ""}`, "utf-8");
  runs.push({
    crate: crateName,
    crate_dir: crateDir,
    status: result.status === 0 ? "ok" : "failed",
    output: outputPath,
  });
  if (result.status !== 0) {
    exitCode = result.status ?? 1;
  }
}

const summaryPath = resolve(outDir, "bench-rust-summary.json");
writeFileSync(
  summaryPath,
  JSON.stringify(
    {
      generated_at_utc: new Date().toISOString(),
      status: exitCode === 0 ? "ok" : "failed",
      runs,
    },
    null,
    2,
  ),
  "utf-8",
);
console.log(summaryPath);
process.exit(exitCode);
