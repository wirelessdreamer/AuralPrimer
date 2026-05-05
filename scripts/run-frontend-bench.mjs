import { existsSync, mkdirSync, copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const args = new Set(process.argv.slice(2));
const outputPath = resolve("benchmarks/frontend/vitest-bench.latest.json");
const baselinePath = resolve("benchmarks/frontend/vitest-bench.baseline.json");
mkdirSync(dirname(outputPath), { recursive: true });

const vitestBin = resolve("node_modules/vitest/vitest.mjs");
const vitestArgs = [
  "bench",
  "apps/game/benchmarks/frontend.bench.ts",
  "--run",
  "--outputJson",
  outputPath,
];
const fallbackArgs = [
  "bench",
  "apps/game/benchmarks/frontend.bench.ts",
  "--run",
  "--outputJson",
  outputPath,
];

if (args.has("--compare")) {
  if (existsSync(baselinePath)) {
    vitestArgs.push("--compare", baselinePath);
    fallbackArgs.push("--compare", baselinePath);
  } else {
    console.warn(`No frontend benchmark baseline found at ${baselinePath}; writing latest artifact only.`);
  }
}

const result = existsSync(vitestBin)
  ? spawnSync(process.execPath, [vitestBin, ...vitestArgs], {
      stdio: "inherit",
      shell: false,
    })
  : spawnSync(process.platform === "win32" ? "npx.cmd" : "npx", ["vitest", ...fallbackArgs], {
  stdio: "inherit",
  shell: false,
    });

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

if (args.has("--update-baseline")) {
  copyFileSync(outputPath, baselinePath);
  console.log(`Updated frontend benchmark baseline: ${baselinePath}`);
}

console.log(`Frontend benchmark artifact: ${outputPath}`);
