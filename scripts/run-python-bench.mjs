import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(root, "benchmarks", "python");
const latest = resolve(outDir, "pytest-benchmark.latest.json");
const baseline = resolve(outDir, "pytest-benchmark.baseline.json");
mkdirSync(outDir, { recursive: true });

const args = new Set(process.argv.slice(2));
const updateBaseline = args.has("--update-baseline");
const compare = args.has("--compare");

const executable = process.platform === "win32" ? "py" : "python3";
const executableArgs = process.platform === "win32" ? ["-3"] : [];
const ingestRoot = resolve(root, "python", "ingest");
const benchmarkJson = resolve(root, "benchmarks", "python", "pytest-benchmark.latest.json");
const memoryJson = resolve(root, "benchmarks", "python", "ingest-memory.latest.json");

const env = {
  ...process.env,
  AURAL_RUN_RUNTIME_BENCHMARKS: "1",
  AURAL_PY_BENCH_MEMORY_JSON: memoryJson,
  PYTHONPATH: resolve(ingestRoot, "src"),
};

const result = spawnSync(
  executable,
  [
    ...executableArgs,
    "-m",
    "pytest",
    "tests/test_ingest_runtime_benchmarks.py",
    "--benchmark-json",
    benchmarkJson,
    "--benchmark-storage",
    resolve(root, "benchmarks", "python", ".pytest-benchmark"),
    "--no-cov",
    "-q",
  ],
  {
    cwd: ingestRoot,
    env,
    stdio: "inherit",
    shell: false,
  },
);

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

if (compare && !existsSync(baseline)) {
  console.warn(`No Python benchmark baseline found at ${baseline}; wrote latest only.`);
}

if (updateBaseline) {
  copyFileSync(latest, baseline);
  console.log(`Updated Python benchmark baseline: ${baseline}`);
}
