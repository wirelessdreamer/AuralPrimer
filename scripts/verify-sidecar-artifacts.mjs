#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT";
const EXPECTED_MODEL_UPGRADE_GATES = [
  "adtof_external_runtime",
  "beat_this_barline_listening_review",
  "demucs_ft_drums_sdr",
  "drum_stemsep_external_runtime",
  "musdb_sdr_baseline",
  "qmul_hr_guitar_external_runtime",
  "rmvpe_mir_st500_vocals",
  "roformer_musdb_comparison",
];

function parseArgs(argv) {
  const args = {
    skipRuntimeChecks: false,
    timeoutMs: 300_000,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--skip-runtime-checks") {
      args.skipRuntimeChecks = true;
    } else if (arg === "--timeout-ms") {
      const parsed = Number(argv[++i]);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        throw new Error("--timeout-ms requires a positive number");
      }
      args.timeoutMs = parsed;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return args;
}

function fail(message) {
  throw new Error(`[verify-sidecar-artifacts] ${message}`);
}

function assert(condition, message) {
  if (!condition) {
    fail(message);
  }
}

function stripBom(text) {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

function readJson(relPath) {
  const absPath = path.resolve(repoRoot, relPath);
  assert(fs.existsSync(absPath), `required JSON file is missing: ${absPath}`);
  return JSON.parse(stripBom(fs.readFileSync(absPath, "utf8")));
}

function comparablePath(value) {
  const normalized = path.resolve(String(value)).replaceAll("/", "\\");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function assertSamePath(actual, expected, message) {
  assert(
    comparablePath(actual) === comparablePath(expected),
    `${message}: expected ${path.resolve(expected)}, got ${path.resolve(actual)}`,
  );
}

function assertSameStringSet(actualValues, expectedValues, label) {
  const actual = [...actualValues].sort();
  const expected = [...expectedValues].sort();
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${label}: expected ${expected.join(", ")}, got ${actual.join(", ")}`,
  );
}

function assertModelUpgradeGateSet(gates, label) {
  assert(gates.gates && typeof gates.gates === "object", `${label} gate detail map is missing`);
  assert(Array.isArray(gates.ready), `${label} gate ready list is not an array`);
  assert(Array.isArray(gates.pending), `${label} gate pending list is not an array`);
  const detailIds = Object.keys(gates.gates);
  assertSameStringSet(detailIds, EXPECTED_MODEL_UPGRADE_GATES, `${label} gate detail ids drifted`);
  assertSameStringSet(
    [...gates.ready, ...gates.pending],
    EXPECTED_MODEL_UPGRADE_GATES,
    `${label} ready+pending gate ids drifted`,
  );
  const duplicateIds = [...gates.ready, ...gates.pending].filter(
    (id, index, allIds) => allIds.indexOf(id) !== index,
  );
  assert(duplicateIds.length === 0, `${label} gate ids appear in both ready/pending: ${duplicateIds.join(", ")}`);
}

function parseTimestamp(value, label) {
  assert(typeof value === "string" && value.trim(), `${label} must be a timestamp string`);
  const normalized = value.replace(/(\.\d{3})\d+Z$/u, "$1Z");
  const parsed = Date.parse(normalized);
  assert(Number.isFinite(parsed), `${label} is not a parseable UTC timestamp: ${value}`);
  return parsed;
}

function statTimeMs(absPath) {
  assert(fs.existsSync(absPath), `required file is missing: ${absPath}`);
  return fs.statSync(absPath).mtimeMs;
}

function assertTimestampClose(actualMs, expectedMs, label) {
  assert(
    Math.abs(actualMs - expectedMs) <= 1_000,
    `${label} timestamp mismatch: expected ${new Date(expectedMs).toISOString()}, got ${new Date(actualMs).toISOString()}`,
  );
}

function assertTimestampAtLeast(actualMs, expectedMs, label) {
  assert(
    actualMs + 1_000 >= expectedMs,
    `${label} is stale: ${new Date(actualMs).toISOString()} < ${new Date(expectedMs).toISOString()}`,
  );
}

function collectIngestSourceFreshness() {
  const ingestRoot = path.resolve(repoRoot, "python", "ingest");
  const sourceRoots = [
    path.join(ingestRoot, "src", "aural_ingest"),
    path.join(ingestRoot, "scripts"),
  ];
  const leafFiles = [
    path.join(ingestRoot, "aural_ingest.spec"),
    path.join(ingestRoot, "pyproject.toml"),
    path.join(ingestRoot, "requirements-runtime.txt"),
  ];
  const excludedDirs = new Set(["__pycache__", ".pytest_cache", "aural_ingest.egg-info"]);
  const files = [];

  function walk(dir) {
    if (!fs.existsSync(dir)) {
      return;
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const absPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!excludedDirs.has(entry.name)) {
          walk(absPath);
        }
      } else if (entry.isFile()) {
        files.push(absPath);
      }
    }
  }

  for (const sourceRoot of sourceRoots) {
    walk(sourceRoot);
  }
  for (const leafFile of leafFiles) {
    if (fs.existsSync(leafFile) && fs.statSync(leafFile).isFile()) {
      files.push(leafFile);
    }
  }
  assert(files.length > 0, "no ingest source files were found");

  let latestPath = files[0];
  let latestMs = statTimeMs(latestPath);
  for (const file of files.slice(1)) {
    const mtimeMs = statTimeMs(file);
    if (mtimeMs > latestMs) {
      latestPath = file;
      latestMs = mtimeMs;
    }
  }

  return {
    fileCount: files.length,
    latestPath,
    latestMs,
  };
}

async function sha256File(absPath) {
  assert(fs.existsSync(absPath), `required file is missing: ${absPath}`);
  const hash = createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(absPath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

function uniquePaths(paths) {
  const seen = new Set();
  const out = [];
  for (const candidate of paths.filter(Boolean)) {
    const resolved = path.resolve(candidate);
    const key = comparablePath(resolved);
    if (!seen.has(key)) {
      seen.add(key);
      out.push(resolved);
    }
  }
  return out;
}

function envWithoutEvidenceOverride() {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.toLowerCase() === EVIDENCE_ROOT_ENV.toLowerCase()) {
      delete env[key];
    }
  }
  return env;
}

function parseRuntimeCheckJson(stdout, label) {
  const text = stripBom(stdout.trim());
  try {
    return JSON.parse(text);
  } catch {
    const firstBrace = text.indexOf("{");
    const lastBrace = text.lastIndexOf("}");
    if (firstBrace >= 0 && lastBrace > firstBrace) {
      return JSON.parse(text.slice(firstBrace, lastBrace + 1));
    }
    fail(`${label} did not emit parseable JSON`);
  }
}

function runRuntimeCheck(exePath, args, options) {
  const result = spawnSync(exePath, ["runtime-check", ...args], {
    cwd: options.cwd,
    env: options.env,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: options.timeoutMs,
    windowsHide: true,
  });
  if (result.error) {
    fail(`${options.label} failed to launch: ${result.error.message}`);
  }
  const output = parseRuntimeCheckJson(result.stdout ?? "", options.label);
  return { status: result.status, output, stderr: result.stderr ?? "" };
}

function assertNormalRuntimeCheck(result, expectedEvidenceRoot, label) {
  assert(result.status === 0, `${label} exited ${result.status}, expected 0`);
  assert(result.output?.ok === true, `${label} did not report ok=true`);
  const gates = result.output?.model_upgrade_gates;
  assert(gates && typeof gates === "object", `${label} omitted model_upgrade_gates`);
  assertSamePath(gates.evidence_root, expectedEvidenceRoot, `${label} evidence_root`);
  assertSamePath(
    gates.evidence_checklist,
    path.join(expectedEvidenceRoot, "benchmarks", "runtime", "model_upgrade_gate_evidence.md"),
    `${label} evidence_checklist`,
  );
  assert(
    gates.evidence_root_env_var === EVIDENCE_ROOT_ENV,
    `${label} evidence_root_env_var should be ${EVIDENCE_ROOT_ENV}`,
  );
  assert(
    gates.evidence_checklist_relative_path === "benchmarks/runtime/model_upgrade_gate_evidence.md",
    `${label} evidence checklist relative path drifted`,
  );
  const serializedGates = JSON.stringify(gates).replaceAll("/", "\\").toLowerCase();
  assert(
    !serializedGates.includes("c:\\users\\dreamer\\benchmarks"),
    `${label} still contains the stale frozen default evidence path`,
  );
  assert(typeof gates.ok === "boolean", `${label} gate ok flag is not boolean`);
  assertModelUpgradeGateSet(gates, label);
  return gates;
}

function assertStrictRuntimeCheck(result, normalGates, label) {
  const strictGates = result.output?.model_upgrade_gates;
  assert(strictGates && typeof strictGates === "object", `${label} omitted model_upgrade_gates`);
  assertModelUpgradeGateSet(strictGates, label);
  assertSamePath(strictGates.evidence_root, normalGates.evidence_root, `${label} evidence_root`);
  assertSamePath(strictGates.evidence_checklist, normalGates.evidence_checklist, `${label} evidence_checklist`);
  assert(
    strictGates.evidence_root_env_var === normalGates.evidence_root_env_var,
    `${label} evidence_root_env_var drifted from normal mode`,
  );
  assert(
    strictGates.evidence_checklist_relative_path === normalGates.evidence_checklist_relative_path,
    `${label} evidence_checklist_relative_path drifted from normal mode`,
  );
  assert(strictGates.ok === normalGates.ok, `${label} ok flag drifted from normal mode`);
  assertSameStringSet(strictGates.ready, normalGates.ready, `${label} ready gates drifted from normal mode`);
  assertSameStringSet(strictGates.pending, normalGates.pending, `${label} pending gates drifted from normal mode`);
  assert(
    strictGates.exit_code_affects_runtime_check === true,
    `${label} did not report exit_code_affects_runtime_check=true`,
  );
  if (normalGates.ok) {
    assert(result.status === 0, `${label} exited ${result.status}, expected 0 because gates are clear`);
  } else {
    assert(result.status !== 0, `${label} exited 0 even though model-upgrade gates are pending`);
    assert(
      Array.isArray(strictGates.pending) && strictGates.pending.length > 0,
      `${label} did not report pending gates`,
    );
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const buildManifestPath = path.resolve(repoRoot, "dist", "sidecar", "build_manifest.json");
  const portableManifestPath = path.resolve(repoRoot, "AuralPrimerPortable", "portable_manifest.json");
  const portableBuildManifestPath = path.resolve(repoRoot, "AuralPrimerPortable", "sidecar", "build_manifest.json");
  const buildManifest = readJson("dist/sidecar/build_manifest.json");
  const portableManifest = readJson("AuralPrimerPortable/portable_manifest.json");
  const portableSidecar = portableManifest.sidecar;
  assert(portableSidecar && typeof portableSidecar === "object", "portable_manifest.json is missing sidecar metadata");

  const expectedHash = buildManifest.sha256;
  assert(/^[a-f0-9]{64}$/u.test(expectedHash), "build manifest sidecar sha256 is missing or invalid");
  assert(portableSidecar.source_sha256 === expectedHash, "portable sidecar source_sha256 does not match build manifest");
  assert(portableSidecar.portable_sha256 === expectedHash, "portable sidecar portable_sha256 does not match build manifest");
  assert(portableSidecar.tauri_runtime_sha256 === expectedHash, "portable sidecar tauri_runtime_sha256 does not match build manifest");

  const exePaths = uniquePaths([
    buildManifest.source_path,
    buildManifest.packaged_path,
    ...(buildManifest.synced_tauri_binaries ?? []).map((entry) => entry.destination_path),
    portableSidecar.source_path,
    portableSidecar.portable_path,
    portableSidecar.tauri_runtime_path,
  ]);
  assert(exePaths.length >= 6, `expected at least six sidecar executable copies, found ${exePaths.length}`);

  const sizes = new Set();
  for (const exePath of exePaths) {
    const stat = fs.statSync(exePath);
    sizes.add(stat.size);
    const hash = await sha256File(exePath);
    assert(hash === expectedHash, `sidecar hash mismatch for ${exePath}: ${hash} != ${expectedHash}`);
  }
  assert(sizes.size === 1, "sidecar executable copies do not all have the same size");
  assert(Number(buildManifest.source_size_bytes) === [...sizes][0], "build manifest source_size_bytes does not match the executable size");
  assert(Number(buildManifest.packaged_size_bytes) === [...sizes][0], "build manifest packaged_size_bytes does not match the executable size");

  const distBuildManifestHash = await sha256File(buildManifestPath);
  const portableBuildManifestHash = await sha256File(portableBuildManifestPath);
  assert(distBuildManifestHash === portableBuildManifestHash, "portable build_manifest.json does not match dist/sidecar/build_manifest.json");
  assert(
    portableSidecar.build_manifest_sha256 === distBuildManifestHash,
    "portable_manifest.json sidecar.build_manifest_sha256 does not match the copied build manifest",
  );

  const freshness = collectIngestSourceFreshness();
  assert(
    Number(buildManifest.ingest_source_file_count) === freshness.fileCount,
    `build manifest ingest source file count is stale: expected ${freshness.fileCount}, got ${buildManifest.ingest_source_file_count}`,
  );
  const manifestLatestPath = path.resolve(buildManifest.ingest_source_latest_path);
  assert(fs.existsSync(manifestLatestPath), `manifest ingest_source_latest_path is missing: ${manifestLatestPath}`);
  assertTimestampClose(
    parseTimestamp(buildManifest.ingest_source_last_write_utc, "build_manifest.ingest_source_last_write_utc"),
    freshness.latestMs,
    "build manifest latest ingest source",
  );
  assertTimestampClose(
    statTimeMs(manifestLatestPath),
    freshness.latestMs,
    "build manifest ingest_source_latest_path",
  );
  assert(
    Number(portableSidecar.ingest_source_file_count) === freshness.fileCount,
    "portable manifest ingest source file count is stale",
  );
  assertTimestampClose(
    parseTimestamp(portableSidecar.ingest_source_last_write_utc, "portable_manifest.sidecar.ingest_source_last_write_utc"),
    freshness.latestMs,
    "portable manifest latest ingest source",
  );

  const sourceLastWrite = parseTimestamp(buildManifest.source_last_write_utc, "build_manifest.source_last_write_utc");
  const packagedLastWrite = parseTimestamp(buildManifest.packaged_last_write_utc, "build_manifest.packaged_last_write_utc");
  const ingestLastWrite = parseTimestamp(buildManifest.ingest_source_last_write_utc, "build_manifest.ingest_source_last_write_utc");
  assertTimestampAtLeast(sourceLastWrite, ingestLastWrite, "built sidecar");
  assertTimestampAtLeast(packagedLastWrite, sourceLastWrite, "packaged sidecar");
  assertTimestampAtLeast(
    parseTimestamp(portableSidecar.source_last_write_utc, "portable_manifest.sidecar.source_last_write_utc"),
    ingestLastWrite,
    "portable source sidecar",
  );
  assertTimestampAtLeast(
    parseTimestamp(portableSidecar.portable_last_write_utc, "portable_manifest.sidecar.portable_last_write_utc"),
    sourceLastWrite,
    "portable sidecar copy",
  );
  assertTimestampAtLeast(
    parseTimestamp(portableSidecar.tauri_runtime_last_write_utc, "portable_manifest.sidecar.tauri_runtime_last_write_utc"),
    sourceLastWrite,
    "portable root sidecar copy",
  );

  console.log(
    `[verify-sidecar-artifacts] sidecar copies verified: ${exePaths.length} files, sha256=${expectedHash}, size=${[...sizes][0]} bytes`,
  );
  console.log(
    `[verify-sidecar-artifacts] manifest freshness verified: ${freshness.fileCount} ingest files, latest=${path.relative(repoRoot, freshness.latestPath)}`,
  );

  if (!args.skipRuntimeChecks) {
    const distSidecarExe = path.resolve(buildManifest.packaged_path);
    const portableRoot = path.resolve(repoRoot, "AuralPrimerPortable");
    const portableSidecarExe = path.resolve(portableSidecar.portable_path);
    const defaultEnv = envWithoutEvidenceOverride();
    const repoRuntime = runRuntimeCheck(distSidecarExe, [], {
      cwd: repoRoot,
      env: defaultEnv,
      label: "repo sidecar runtime-check",
      timeoutMs: args.timeoutMs,
    });
    const repoGates = assertNormalRuntimeCheck(repoRuntime, repoRoot, "repo sidecar runtime-check");
    const repoStrict = runRuntimeCheck(distSidecarExe, ["--require-model-upgrade-gates"], {
      cwd: repoRoot,
      env: defaultEnv,
      label: "repo sidecar strict runtime-check",
      timeoutMs: args.timeoutMs,
    });
    assertStrictRuntimeCheck(repoStrict, repoGates, "repo sidecar strict runtime-check");

    const portableRuntime = runRuntimeCheck(portableSidecarExe, [], {
      cwd: portableRoot,
      env: defaultEnv,
      label: "portable sidecar runtime-check",
      timeoutMs: args.timeoutMs,
    });
    const portableGates = assertNormalRuntimeCheck(portableRuntime, portableRoot, "portable sidecar runtime-check");
    const portableStrict = runRuntimeCheck(portableSidecarExe, ["--require-model-upgrade-gates"], {
      cwd: portableRoot,
      env: defaultEnv,
      label: "portable sidecar strict runtime-check",
      timeoutMs: args.timeoutMs,
    });
    assertStrictRuntimeCheck(portableStrict, portableGates, "portable sidecar strict runtime-check");

    const overrideEnv = { ...defaultEnv, [EVIDENCE_ROOT_ENV]: repoRoot };
    const portableOverrideRuntime = runRuntimeCheck(portableSidecarExe, [], {
      cwd: portableRoot,
      env: overrideEnv,
      label: "portable sidecar evidence-root override runtime-check",
      timeoutMs: args.timeoutMs,
    });
    const overrideGates = assertNormalRuntimeCheck(
      portableOverrideRuntime,
      repoRoot,
      "portable sidecar evidence-root override runtime-check",
    );
    const portableOverrideStrict = runRuntimeCheck(portableSidecarExe, ["--require-model-upgrade-gates"], {
      cwd: portableRoot,
      env: overrideEnv,
      label: "portable sidecar evidence-root override strict runtime-check",
      timeoutMs: args.timeoutMs,
    });
    assertStrictRuntimeCheck(
      portableOverrideStrict,
      overrideGates,
      "portable sidecar evidence-root override strict runtime-check",
    );

    console.log(
      `[verify-sidecar-artifacts] runtime-check roots verified in normal and strict modes: repo=${repoRoot}, portable=${portableRoot}, override=${repoRoot}`,
    );
    if (!repoGates.ok) {
      console.log(
        `[verify-sidecar-artifacts] model-upgrade gates pending as expected for this artifact set: ${repoGates.pending.join(", ")}`,
      );
    }
  }

  console.log("[verify-sidecar-artifacts] PASS");
}

main().catch((error) => {
  console.error(error?.stack ?? String(error));
  process.exit(1);
});
