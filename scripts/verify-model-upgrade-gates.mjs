#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

const EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT";
const CHECKLIST_REL_PATH = "benchmarks/runtime/model_upgrade_gate_evidence.md";
const PROGRESS_REL_PATH = "docs/model-upgrades-progress-2026-07-07.md";
const PLAN_REL_PATH = "docs/model-upgrades-plan-2026-07-07.md";
const BEAT_THIS_HELPER_REL_PATH = "benchmarks/meter/beat_this_review_evidence.py";
const BEAT_THIS_TEMPLATE_REL_PATH = "benchmarks/meter/beat_this_dbn_barline_listening_review.template.json";
const BEAT_THIS_SMOKE_REL_PATH = "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md";
const BEAT_THIS_EVIDENCE_REL_PATH = "benchmarks/meter/beat_this_dbn_barline_listening_review.json";
const THRESHOLDS_REL_PATH = "benchmarks/thresholds.yml";
const EXTERNAL_RUNTIME_VALIDATOR_REL_PATHS = [
  "python/ingest/scripts/validate_adtof_runtime.py",
  "python/ingest/scripts/validate_drum_stemsep_runtime.py",
  "python/ingest/scripts/validate_qmul_hr_guitar_runtime.py",
  "python/ingest/scripts/validate_rmvpe_runtime.py",
  "python/ingest/scripts/validate_roformer_runtime.py",
];
const GATE_BENCHMARK_RUNNER_REL_PATHS = [
  "benchmarks/quality/run_musdb_separation_sdr.py",
  "benchmarks/vocals/run_mir_st500_vocals.py",
];
const EXTERNAL_RUNTIME_SETUP_DOC_REL_PATHS = [
  "python/ingest/scripts/SETUP-ADTOF.md",
  "python/ingest/scripts/SETUP-DRUM-STEMSEP.md",
  "python/ingest/scripts/SETUP-QMUL-HR-GUITAR.md",
  "python/ingest/scripts/SETUP-RMVPE.md",
  "python/ingest/scripts/SETUP-ROFORMER-SEPARATION.md",
];

const REQUIRED_GATES = [
  "beat_this_barline_listening_review",
  "musdb_sdr_baseline",
  "demucs_ft_drums_sdr",
  "roformer_musdb_comparison",
  "rmvpe_mir_st500_vocals",
  "adtof_external_runtime",
  "drum_stemsep_external_runtime",
  "qmul_hr_guitar_external_runtime",
];

const REQUIRED_EVIDENCE_PATTERNS = [
  BEAT_THIS_EVIDENCE_REL_PATH,
  "benchmarks/quality/runs/*_musdb_separation_sdr.json",
  "benchmarks/runtime/runs/*_roformer_runtime.json",
  "benchmarks/runtime/runs/*_rmvpe_runtime.json",
  "benchmarks/runtime/runs/*_adtof_runtime.json",
  "benchmarks/runtime/runs/*_drum_stemsep_runtime.json",
  "benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json",
  "benchmarks/vocals/gt_runs/*_mir_st500_vocals.json",
];

const REQUIRED_BEAT_THIS_CASES = [
  "psalm_121_my_help.feedpak",
  "psalm_130_please_hear_me.feedpak",
  "psalm_5_every_morning.feedpak",
];
const BEAT_THIS_REVIEWED_AT_UTC_REQUIREMENT =
  "ISO-8601 UTC `reviewed_at_utc` value ending in `Z`";
const BEAT_THIS_REVIEWED_AT_UTC_TEMPLATE_VALUE = '"reviewed_at_utc": "TODO"';
const BEAT_THIS_REVIEWED_AT_UTC_PLACEHOLDER =
  '"reviewed_at_utc": "<ISO-8601 UTC timestamp ending in Z>"';
const BEAT_THIS_REVIEWED_AT_UTC_GENERATOR = "(Get-Date).ToUniversalTime()";

const REQUIRED_MODEL_UPGRADE_DECISION_IDS = [
  "t1_2_yourmt3_drums_test30_useful_not_promoted",
  "t3_7_1_yourmt3_guitarset_limit40_positive",
  "t3_7_1_guitar_techs_full_negative_broad_promotion",
  "bass_torchcrepe_strict_low_string_proxy",
  "t3_8_key_guitarset_full_baseline",
  "t3_8_chords_guitarset_full_baseline",
];

const REQUIRED_MODEL_UPGRADE_DECISION_REPORTS = [
  "benchmarks/drums/gt_runs/yourmt3_mr_mt3_test30.json",
  "benchmarks/guitar/gt_runs/guitarset_mic_limit40_yourmt3_vs_baselines.json",
  "benchmarks/guitar/gt_runs/guitar_techs_directinput_full_duration_shards_104.json",
  "benchmarks/bass/gt_runs/bass_hexdebleed_60_strict.json",
  "benchmarks/guitar/gt_runs/guitarset_key_mic_full.json",
  "benchmarks/guitar/gt_runs/guitarset_chords_mireval_full.json",
];

function fail(message) {
  throw new Error(`[verify-model-upgrade-gates] ${message}`);
}

function assert(condition, message) {
  if (!condition) {
    fail(message);
  }
}

function abs(relPath) {
  return path.resolve(repoRoot, relPath);
}

function read(relPath) {
  const absPath = abs(relPath);
  assert(fs.existsSync(absPath), `required file is missing: ${relPath}`);
  return fs.readFileSync(absPath, "utf8");
}

function readJson(relPath) {
  const text = read(relPath);
  return JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);
}

function assertIncludes(source, needle, label) {
  assert(source.includes(needle), `${label} must include ${JSON.stringify(needle)}`);
}

function assertMatch(source, pattern, label) {
  assert(pattern.test(source), label);
}

function assertAllIncluded(source, needles, label) {
  for (const needle of needles) {
    assertIncludes(source, needle, label);
  }
}

function markdownTableGateIds(source) {
  return Array.from(source.matchAll(/^\|\s*`([^`]+)`\s*\|/gm), (match) => match[1]);
}

function constStringArray(source, constName, label) {
  const pattern = new RegExp(`const\\s+${constName}\\s*=\\s*\\[([\\s\\S]*?)\\];`);
  const match = pattern.exec(source);
  assert(match, `${label} must declare ${constName}`);
  return Array.from(match[1].matchAll(/"([^"]+)"/g), (item) => item[1]);
}

function stripBom(text) {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
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

function sourcePythonExe() {
  if (process.env.PYTHON && process.env.PYTHON.trim()) {
    return path.resolve(process.env.PYTHON.trim());
  }
  return process.platform === "win32"
    ? abs("python/ingest/.venv/Scripts/python.exe")
    : abs("python/ingest/.venv/bin/python");
}

function runSourceRuntimeCheck(args, label) {
  const pythonExe = sourcePythonExe();
  assert(fs.existsSync(pythonExe), `${label} python executable is missing: ${pythonExe}`);
  const env = { ...process.env };
  delete env[EVIDENCE_ROOT_ENV];
  const result = spawnSync(pythonExe, ["-m", "aural_ingest.cli", "runtime-check", ...args], {
    cwd: repoRoot,
    env,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: 300_000,
    windowsHide: true,
  });
  if (result.error) {
    fail(`${label} failed to launch: ${result.error.message}`);
  }
  return {
    status: result.status,
    output: parseRuntimeCheckJson(result.stdout ?? "", label),
    stderr: result.stderr ?? "",
  };
}

function assertSourceRuntimeCheckContract() {
  const normal = runSourceRuntimeCheck([], "source runtime-check");
  assert(normal.status === 0, `source runtime-check exited ${normal.status}, expected 0`);
  assert(normal.output?.ok === true, "source runtime-check did not report ok=true");
  const normalGates = normal.output?.model_upgrade_gates;
  assert(normalGates && typeof normalGates === "object", "source runtime-check omitted model_upgrade_gates");
  assertSamePath(normalGates.evidence_root, repoRoot, "source runtime-check evidence_root");
  assertSamePath(
    normalGates.evidence_checklist,
    path.join(repoRoot, CHECKLIST_REL_PATH),
    "source runtime-check evidence_checklist",
  );
  assert(
    normalGates.evidence_root_env_var === EVIDENCE_ROOT_ENV,
    `source runtime-check evidence_root_env_var should be ${EVIDENCE_ROOT_ENV}`,
  );
  assert(
    normalGates.evidence_checklist_relative_path === CHECKLIST_REL_PATH,
    "source runtime-check evidence_checklist_relative_path drifted",
  );
  assert(normalGates.exit_code_affects_runtime_check === false, "source runtime-check normal mode must not affect exit code");
  assert(typeof normalGates.ok === "boolean", "source runtime-check gate ok flag is not boolean");
  assertSameStringSet(Object.keys(normalGates.gates || {}), REQUIRED_GATES, "source runtime-check gate detail ids drifted");
  assertSameStringSet(
    [...(normalGates.ready || []), ...(normalGates.pending || [])],
    REQUIRED_GATES,
    "source runtime-check ready+pending gate ids drifted",
  );

  const strict = runSourceRuntimeCheck(["--require-model-upgrade-gates"], "source strict runtime-check");
  const strictGates = strict.output?.model_upgrade_gates;
  assert(strictGates && typeof strictGates === "object", "source strict runtime-check omitted model_upgrade_gates");
  assert(strictGates.exit_code_affects_runtime_check === true, "source strict runtime-check did not enforce gates");
  assertSamePath(strictGates.evidence_root, normalGates.evidence_root, "source strict runtime-check evidence_root");
  assertSamePath(strictGates.evidence_checklist, normalGates.evidence_checklist, "source strict runtime-check evidence_checklist");
  assert(strictGates.ok === normalGates.ok, "source strict runtime-check ok flag drifted from normal mode");
  assertSameStringSet(strictGates.ready, normalGates.ready, "source strict runtime-check ready gates drifted");
  assertSameStringSet(strictGates.pending, normalGates.pending, "source strict runtime-check pending gates drifted");
  if (normalGates.ok) {
    assert(strict.status === 0, `source strict runtime-check exited ${strict.status}, expected 0 because gates are clear`);
  } else {
    assert(strict.status !== 0, "source strict runtime-check exited 0 even though model-upgrade gates are pending");
    assert(Array.isArray(strictGates.pending) && strictGates.pending.length > 0, "source strict runtime-check did not report pending gates");
  }
}

const packageJson = readJson("package.json");
assert(
  packageJson.scripts?.["ci:verify:model-upgrade-gates"] === "node ./scripts/verify-model-upgrade-gates.mjs",
  "package.json must expose ci:verify:model-upgrade-gates",
);
assert(
  packageJson.scripts?.["bench:thresholds"] === "node ./scripts/run-benchmark-thresholds.mjs",
  "package.json must expose bench:thresholds",
);

const checklist = read(CHECKLIST_REL_PATH);
const checklistGateIds = markdownTableGateIds(checklist);
assert(
  JSON.stringify(checklistGateIds) === JSON.stringify(REQUIRED_GATES),
  `${CHECKLIST_REL_PATH} gate table must list the runtime gate ids in order`,
);
assertIncludes(checklist, "runtime-check --require-model-upgrade-gates", CHECKLIST_REL_PATH);
assertIncludes(checklist, EVIDENCE_ROOT_ENV, CHECKLIST_REL_PATH);
assertAllIncluded(checklist, REQUIRED_EVIDENCE_PATTERNS, CHECKLIST_REL_PATH);
assertAllIncluded(checklist, REQUIRED_BEAT_THIS_CASES, CHECKLIST_REL_PATH);
assertIncludes(checklist, BEAT_THIS_REVIEWED_AT_UTC_REQUIREMENT, CHECKLIST_REL_PATH);
assertIncludes(checklist, BEAT_THIS_REVIEWED_AT_UTC_TEMPLATE_VALUE, CHECKLIST_REL_PATH);
assertIncludes(checklist, BEAT_THIS_REVIEWED_AT_UTC_GENERATOR, CHECKLIST_REL_PATH);
assertAllIncluded(
  checklist,
  [
    "barlines_ok=true",
    "listening_ok=true",
    "non-`TODO` reviewer metadata",
    "The helper defaults to `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT`",
    "current working directory",
    "ordered by the UTC timestamp prefix",
    "filesystem modified time is only a fallback",
    "failed same-identity reports for missing input",
    "exploratory defaults",
    "outside the strict gate globs",
    "summary.tracks_ok >= 10",
    'dataset="musdb18_or_musdb18_hq"',
    'split="test"',
    "finite aggregate/per-role SDR",
    "zero failed/skipped tracks",
    "every required role counted for every successful track",
    "MUSDB test-split SDR reports",
    "RoFormer aggregate `median_sdr_mean` is not below the Demucs baseline",
    "cases_ok == case_count",
    "cases_err == 0",
    "extra.limit=null",
    "finite aggregate precision/recall/F1",
    "runtime.configured=true",
    "newest report for each engine-specific glob",
    "newer failed validation keeps that runtime gate pending",
    "newest matching benchmark",
    "newer same-identity failure keeps the gate",
    "an `events[]` array of matching length",
    "normalized JSON-number `time`, integer `note`, and integer `velocity` payloads",
    "a `notes[]` array of matching length",
    "normalized JSON-number `t_on`/`t_off`, integer `pitch`, integer `velocity`, and non-empty `instrument` payloads",
    "--require-events",
    "--require-notes",
    "--write-gate-evidence",
    "benchmarks/quality/runs",
    "benchmarks/vocals/gt_runs",
  ],
  CHECKLIST_REL_PATH,
);

const progress = read(PROGRESS_REL_PATH);
assertIncludes(progress, CHECKLIST_REL_PATH, PROGRESS_REL_PATH);
assertIncludes(progress, EVIDENCE_ROOT_ENV, PROGRESS_REL_PATH);
assertAllIncluded(progress, REQUIRED_GATES, PROGRESS_REL_PATH);
assertAllIncluded(
  progress,
  [
    "strict mode still exits nonzero",
    "manual/external promotion gates",
    "executes source `runtime-check`",
    "`--require-model-upgrade-gates` modes",
    "strict-mode exit behavior",
    "--write-gate-evidence",
    "benchmarks/quality/runs/*_musdb_separation_sdr.json",
    "benchmarks/vocals/gt_runs/*_mir_st500_vocals.json",
    "exploratory output directories outside the strict gate globs",
    "Beat This manual-review helper now mirror",
    "Evidence glob scans now",
    "skip unreadable or non-regular candidate",
    "order stamped reports by the UTC timestamp",
    "older success cannot outrank a newer failed report",
    "failed same-identity gate",
    "matching identity report as authoritative",
    'dataset="musdb18_or_musdb18_hq"',
    'split="test"',
    "Beat This manual-review gate",
    "MUSDB gates",
    "at least 10 successful tracks",
    "external-runtime gates",
    "zero failed/skipped tracks",
    "generated `events[]`",
    "normalized JSON-number `time`",
    "integer `note`, and integer `velocity` payloads",
    "generated `notes[]`",
    "normalized JSON-number `t_on`/`t_off`",
    "integer `pitch`, integer `velocity`",
    "non-empty `instrument` payloads",
    "latest-authoritative per",
    "newer failed validation keeps the gate pending",
    "newer failed Demucs or RoFormer benchmark",
    "matching `melodic_rmvpe` test/vocal report",
    "full case coverage",
    "extra.limit=null",
    "`median_sdr_mean` is not below",
    "benchmarks/thresholds.yml",
    "warn-mode model-upgrade decision",
  ],
  PROGRESS_REL_PATH,
);

const plan = read(PLAN_REL_PATH);
assertAllIncluded(
  plan,
  [
    "not the live",
    "model_upgrade_gate_evidence.md",
    "npm run ci:verify:model-upgrade-gates",
    "runtime-check --require-model-upgrade-gates",
  ],
  PLAN_REL_PATH,
);

const thresholds = read(THRESHOLDS_REL_PATH);
assertIncludes(thresholds, "model_upgrade_decisions", THRESHOLDS_REL_PATH);
assertIncludes(thresholds, "does not clear external/runtime promotion gates", THRESHOLDS_REL_PATH);
assertAllIncluded(thresholds, REQUIRED_MODEL_UPGRADE_DECISION_IDS, THRESHOLDS_REL_PATH);
assertAllIncluded(thresholds, REQUIRED_MODEL_UPGRADE_DECISION_REPORTS, THRESHOLDS_REL_PATH);

const cliPy = read("python/ingest/src/aural_ingest/cli.py");
assertIncludes(cliPy, `MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "${EVIDENCE_ROOT_ENV}"`, "cli.py");
assertMatch(
  cliPy,
  /MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = \(\s+Path\("benchmarks"\) \/ "runtime" \/ "model_upgrade_gate_evidence\.md"\s+\)/,
  "cli.py must point runtime-check at the model-upgrade gate evidence checklist",
);
assertIncludes(cliPy, '"evidence_checklist": str(evidence_root / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST)', "cli.py");
assertIncludes(cliPy, "evidence_checklist_relative_path", "cli.py");
assertIncludes(cliPy, "require_model_upgrade_gates", "cli.py");
assertIncludes(cliPy, "def looks_like_evidence_root(path: Path) -> bool:", "cli.py");
assert(
  !cliPy.includes('(path / "benchmarks").is_dir()'),
  "cli.py evidence root detection must require the model-upgrade gate checklist, not any benchmarks directory",
);
assertAllIncluded(
  cliPy,
  [
    "metadata_errors",
    "gate must be 'beat_this_barline_listening_review'",
    "reviewed_by must identify the reviewer",
    "reviewed_at_utc must record the review timestamp",
    "reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z",
    "source_smoke_report must be 'benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md'",
  ],
  "cli.py",
);
assertAllIncluded(cliPy, REQUIRED_GATES, "cli.py");
assertAllIncluded(
  cliPy,
  [
    "import stat",
    "MUSDB_SDR_EVIDENCE_GLOB",
    "ADTOF_RUNTIME_EVIDENCE_GLOB",
    "DRUM_STEMSEP_RUNTIME_EVIDENCE_GLOB",
    "RMVPE_RUNTIME_EVIDENCE_GLOB",
    "ROFORMER_RUNTIME_EVIDENCE_GLOB",
    "QMUL_HR_GUITAR_RUNTIME_EVIDENCE_GLOB",
    "MIR_ST500_VOCALS_EVIDENCE_GLOB",
  ],
  "cli.py",
);
assertMatch(
  cliPy,
  /def _evidence_filename_timestamp\(path: Path\) -> float \| None:[\s\S]+?datetime\.strptime[\s\S]+?replace\(tzinfo=timezone\.utc\)[\s\S]+?def _evidence_candidates\(pattern: Path\) -> list\[Path\]:[\s\S]+?except OSError:[\s\S]+?continue[\s\S]+?stat\.S_ISREG\(stat_result\.st_mode\)[\s\S]+?filename_timestamp = _evidence_filename_timestamp\(path\)[\s\S]+?stat_result\.st_mtime[\s\S]+?return \[path for \*_sort_key, path in sorted\(candidates, reverse=True\)\]/,
  "cli.py must skip unreadable/non-regular evidence candidates and sort stamped reports by filename timestamp before mtime fallback",
);

const cliMiscTest = read("python/ingest/tests/test_cli_misc.py");
const evidenceWriterMissingInputTest = read("python/ingest/tests/test_model_upgrade_evidence_writers.py");
assertIncludes(
  cliMiscTest,
  "test_evidence_candidates_skip_stale_stat_failures",
  "test_cli_misc.py",
);
assertAllIncluded(
  cliMiscTest,
  [
    "monkeypatch.setattr(path_type, \"stat\", fake_stat)",
    "stale_report",
    "median_sdr_mean",
    "test_evidence_candidates_sort_by_filename_timestamp_before_mtime",
    "older_success",
    "newer_failure",
  ],
  "test_cli_misc.py",
);
assertAllIncluded(
  evidenceWriterMissingInputTest,
  [
    "test_runtime_validation_writers_emit_failed_gate_evidence_for_missing_inputs",
    "input_missing",
    "missing-drums.wav",
    "missing-guitar.wav",
    "missing-vocals.wav",
    "missing-mix.wav",
  ],
  "test_model_upgrade_evidence_writers.py",
);
assertAllIncluded(
  cliMiscTest,
  [
    "test_validate_roformer_runtime_script_requires_all_roles_for_gate_evidence",
    "--write-gate-evidence requires --require-role bass --require-role drums",
  ],
  "test_cli_misc.py",
);
assertAllIncluded(
  cliMiscTest,
  [
    "test_model_upgrade_gate_snapshot_accepts_beat_this_review_evidence",
    "test_beat_this_review_evidence_requires_review_metadata",
    "test_beat_this_review_evidence_requires_utc_review_timestamp",
    "test_model_upgrade_evidence_root_ignores_cwd_without_gate_checklist",
    "test_mir_st500_vocals_report_evidence_requires_success_ok",
    "test_runtime_validation_report_evidence_treats_latest_engine_report_as_authoritative",
    "test_musdb_sdr_report_evidence_treats_latest_matching_identity_as_authoritative",
    "test_musdb_sdr_report_evidence_requires_expected_dataset_and_test_split",
    "test_mir_st500_vocals_report_evidence_treats_latest_matching_algorithm_as_authoritative",
    "test_runtime_validation_report_evidence_requires_specific_runtime_field",
    "test_runtime_validation_report_evidence_requires_runtime_ready",
    "test_runtime_validation_report_evidence_requires_event_array",
    "test_runtime_validation_report_evidence_requires_event_payload_shape",
    "test_runtime_validation_report_evidence_requires_note_payload_shape",
    "test_runtime_validation_report_evidence_requires_roformer_stem_paths",
    "test_musdb_sdr_report_evidence_rejects_nondefault_demucs_baseline_modelpack",
    "test_musdb_sdr_report_evidence_requires_promotion_sample_size",
    "test_musdb_sdr_report_evidence_rejects_failed_or_partial_tracks",
    "test_musdb_sdr_report_evidence_rejects_nonzero_failed_tracks",
    "test_musdb_sdr_report_evidence_rejects_nonfinite_aggregate_sdr",
    "test_musdb_sdr_comparison_rejects_candidate_below_baseline",
    "test_mir_st500_vocals_report_evidence_rejects_partial_algorithm_coverage",
    "test_mir_st500_vocals_report_evidence_rejects_limited_promotion_report",
    "test_mir_st500_vocals_report_evidence_rejects_nonfinite_summary_metric",
  ],
  "test_cli_misc.py",
);
assertAllIncluded(
  cliPy,
  [
    "report ok is not true",
    "runtime.ready or runtime.configured must be true",
    "runtime_required_field",
    "runtime.configured must be true",
    "latest_candidate_only",
    "latest_matching_identity_only",
    "matching_identity_candidate_count",
    "MUSDB_SDR_EVIDENCE_MIN_TRACKS = 10",
    'MUSDB_SDR_EVIDENCE_REQUIRED_DATASET = "musdb18_or_musdb18_hq"',
    'MUSDB_SDR_EVIDENCE_REQUIRED_SPLIT = "test"',
    "required_dataset",
    "required_split",
    "summary.tracks_ok must be at least",
    "extra.limit must be null for full test/vocal coverage",
    "stem_paths must be an object when roles are required",
    "length must equal",
    "not isinstance(value, (int, float))",
    "def _runtime_event_payload_rejection",
    "def _runtime_note_payload_rejection",
    "must be an integer MIDI note in [0, 127]",
    "must be an integer MIDI velocity in [1, 127]",
    "instrument must be a non-empty string",
    "non-default Demucs report does not satisfy the default Demucs baseline",
    "summary.median_sdr_mean must be finite",
    "case count must equal report case_count",
    "must be finite and between 0 and 1",
    "def _musdb_sdr_comparison_status",
    "median_sdr_mean",
  ],
  "cli.py",
);
assertIncludes(
  read("python/ingest/src/aural_ingest/ground_truth_benchmark.py"),
  '"ok": case_count > 0 and int(overall.get("cases_err") or 0) == 0',
  "ground_truth_benchmark.py",
);

const sidecarVerifier = read("scripts/verify-sidecar-artifacts.mjs");
assertSameStringSet(
  constStringArray(sidecarVerifier, "EXPECTED_MODEL_UPGRADE_GATES", "verify-sidecar-artifacts.mjs"),
  REQUIRED_GATES,
  "verify-sidecar-artifacts.mjs expected gate ids drifted from repo checklist",
);
assertAllIncluded(sidecarVerifier, REQUIRED_GATES, "verify-sidecar-artifacts.mjs");
assertAllIncluded(
  sidecarVerifier,
  [
    "EXPECTED_MODEL_UPGRADE_GATES",
    "assertModelUpgradeGateSet",
    "gate detail ids drifted",
    "ready+pending gate ids drifted",
    "gate ids appear in both ready/pending",
    "exit_code_affects_runtime_check === true",
    "strictGates.evidence_root",
    "strictGates.evidence_checklist",
    "ready gates drifted from normal mode",
    "pending gates drifted from normal mode",
    "portable sidecar strict runtime-check",
    "portable sidecar evidence-root override strict runtime-check",
    "runtime-check roots verified in normal and strict modes",
  ],
  "verify-sidecar-artifacts.mjs",
);

for (const relPath of EXTERNAL_RUNTIME_VALIDATOR_REL_PATHS) {
  const validator = read(relPath);
  assertAllIncluded(
    validator,
    [
      "MODEL_UPGRADE_EVIDENCE_ROOT_ENV = \"AURAL_MODEL_UPGRADE_EVIDENCE_ROOT\"",
      "MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST",
      "RUNTIME_EVIDENCE_DIR = Path(\"benchmarks\") / \"runtime\" / \"runs\"",
      "def _model_upgrade_evidence_root",
      ".strip()",
      "Path.cwd().resolve()",
      "def _gate_evidence_output_path",
      "strftime(\"%Y%m%d_%H%M%S_%f\")",
      "--write-gate-evidence",
      "parser.error(\"--write-gate-evidence cannot be combined with --output\")",
      "\"status\": \"input_missing\"",
      "\"reason\": reason",
    ],
    relPath,
  );
}
assertIncludes(
  read("python/ingest/scripts/validate_adtof_runtime.py"),
  "parser.error(\"--write-gate-evidence requires --require-events\")",
  "validate_adtof_runtime.py",
);
assertIncludes(
  read("python/ingest/scripts/validate_drum_stemsep_runtime.py"),
  "parser.error(\"--write-gate-evidence requires --require-events\")",
  "validate_drum_stemsep_runtime.py",
);
assertIncludes(
  read("python/ingest/scripts/validate_qmul_hr_guitar_runtime.py"),
  "parser.error(\"--write-gate-evidence requires --require-notes\")",
  "validate_qmul_hr_guitar_runtime.py",
);
assertAllIncluded(
  read("python/ingest/scripts/validate_roformer_runtime.py"),
  [
    'PROMOTION_REQUIRED_ROLES = ("bass", "drums", "other", "vocals")',
    "parser.error(f\"--write-gate-evidence requires {required}\")",
  ],
  "validate_roformer_runtime.py",
);

for (const relPath of GATE_BENCHMARK_RUNNER_REL_PATHS) {
  const runner = read(relPath);
  assertAllIncluded(
    runner,
    [
      "MODEL_UPGRADE_EVIDENCE_ROOT_ENV = \"AURAL_MODEL_UPGRADE_EVIDENCE_ROOT\"",
      "MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST",
      "def _model_upgrade_evidence_root",
      ".strip()",
      "Path.cwd().resolve()",
      "def _gate_evidence_output_path",
      "strftime(\"%Y%m%d_%H%M%S_%f\")",
      "--write-gate-evidence",
      "parser.error(\"--write-gate-evidence cannot be combined with --output\")",
      "promotion_usable",
    ],
    relPath,
  );
}
assertAllIncluded(
  read("benchmarks/quality/run_musdb_separation_sdr.py"),
  [
    "QUALITY_EXPLORATORY_DIR",
    "MUSDB_PROMOTION_MIN_TRACKS = 10",
    "promotion_min_tracks",
    "parser.error(\"--write-gate-evidence requires --split test\")",
    "MUSDB gate evidence requires at least",
  ],
  "run_musdb_separation_sdr.py",
);
assertAllIncluded(
  read("benchmarks/vocals/run_mir_st500_vocals.py"),
  [
    "VOCALS_EXPLORATORY_DIR",
    "args.limit is None",
    "MIR-ST500 gate evidence requires an unbounded test/vocal melodic_rmvpe run",
    "MIR-ST500 benchmark had case errors",
  ],
  "run_mir_st500_vocals.py",
);
assertAllIncluded(
  read("python/ingest/tests/test_musdb_separation_sdr_runner.py"),
  [
    "promotion_usable",
    "promotion_min_tracks",
    "test_musdb_sdr_runner_rejects_gate_evidence_without_test_split",
    "--write-gate-evidence requires --split test",
    "\"10\"",
    "failed/skipped/no successful",
  ],
  "test_musdb_separation_sdr_runner.py",
);
assertAllIncluded(
  read("python/ingest/tests/test_mir_st500_vocals_runner.py"),
  [
    "promotion_usable",
    "test_mir_st500_vocals_runner_exits_nonzero_when_report_has_case_errors",
    "case errors",
    "report[\"extra\"][\"limit\"] is None",
  ],
  "test_mir_st500_vocals_runner.py",
);

for (const relPath of EXTERNAL_RUNTIME_SETUP_DOC_REL_PATHS) {
  const setupDoc = read(relPath);
  assertAllIncluded(
    setupDoc,
    [
      "--write-gate-evidence",
      EVIDENCE_ROOT_ENV,
      "benchmarks/runtime/runs",
    ],
    relPath,
  );
  assert(
    !/--output\s+(?:D:\\AuralPrimer\\)?benchmarks\\runtime\\runs\\\d{8}_/.test(setupDoc),
    `${relPath} must not use hand-picked runtime gate evidence output filenames`,
  );
}
assertAllIncluded(
  read("python/ingest/scripts/SETUP-ROFORMER-SEPARATION.md"),
  [
    "single-track exploratory smoke outside the strict gate glob",
    "--limit 1",
    "--output D:\\AuralPrimer\\benchmarks\\quality\\exploratory_runs\\roformer_musdb_smoke.json",
    "10-track gate sample",
    "--limit 10",
    "--write-gate-evidence",
  ],
  "SETUP-ROFORMER-SEPARATION.md",
);

const helper = read(BEAT_THIS_HELPER_REL_PATH);
assertIncludes(helper, `GATE_ID = "${REQUIRED_GATES[0]}"`, BEAT_THIS_HELPER_REL_PATH);
assertIncludes(
  helper,
  `MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "${EVIDENCE_ROOT_ENV}"`,
  BEAT_THIS_HELPER_REL_PATH,
);
assertIncludes(helper, `EVIDENCE_RELATIVE_PATH = "${BEAT_THIS_EVIDENCE_REL_PATH}"`, BEAT_THIS_HELPER_REL_PATH);
assertIncludes(helper, `TEMPLATE_RELATIVE_PATH = "${BEAT_THIS_TEMPLATE_REL_PATH}"`, BEAT_THIS_HELPER_REL_PATH);
assertIncludes(helper, `SMOKE_REPORT_RELATIVE_PATH = "${BEAT_THIS_SMOKE_REL_PATH}"`, BEAT_THIS_HELPER_REL_PATH);
assertAllIncluded(helper, REQUIRED_BEAT_THIS_CASES, BEAT_THIS_HELPER_REL_PATH);
assertAllIncluded(
  helper,
  [
    "MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST",
    '"barlines_ok": approval_default',
    '"listening_ok": approval_default',
    "def evidence_root",
    "Path.cwd().resolve()",
    "default_evidence_path",
    "default_template_path",
    "gate must be",
    "reviewed_by must identify the reviewer",
    "reviewed_at_utc must record the review timestamp",
    "reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z",
    "source_smoke_report must be",
    "must be true",
    "return 0 if status[\"ready\"] else 1",
  ],
  BEAT_THIS_HELPER_REL_PATH,
);

const template = readJson(BEAT_THIS_TEMPLATE_REL_PATH);
assert(template.version === 1, `${BEAT_THIS_TEMPLATE_REL_PATH} must use version 1`);
assert(template.gate === REQUIRED_GATES[0], `${BEAT_THIS_TEMPLATE_REL_PATH} must target the Beat This gate`);
assert(
  template.source_smoke_report === BEAT_THIS_SMOKE_REL_PATH,
  `${BEAT_THIS_TEMPLATE_REL_PATH} must point at the Beat This smoke report`,
);
assert(template.reviewed_by === "TODO", `${BEAT_THIS_TEMPLATE_REL_PATH} must stay non-passing until reviewed`);
assert(template.reviewed_at_utc === "TODO", `${BEAT_THIS_TEMPLATE_REL_PATH} must stay non-passing until reviewed`);
for (const caseId of REQUIRED_BEAT_THIS_CASES) {
  const item = template.cases?.[caseId];
  assert(item && typeof item === "object", `${BEAT_THIS_TEMPLATE_REL_PATH} missing case ${caseId}`);
  assert(item.barlines_ok === false, `${caseId} barlines_ok must default to false`);
  assert(item.listening_ok === false, `${caseId} listening_ok must default to false`);
}

const smoke = read(BEAT_THIS_SMOKE_REL_PATH);
assertAllIncluded(smoke, REQUIRED_BEAT_THIS_CASES, BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, "Manual review evidence contract", BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, "non-`TODO`", BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, "`reviewed_by` metadata", BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, BEAT_THIS_REVIEWED_AT_UTC_REQUIREMENT, BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, BEAT_THIS_REVIEWED_AT_UTC_PLACEHOLDER, BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, BEAT_THIS_REVIEWED_AT_UTC_GENERATOR, BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, "model_upgrade_gates.beat_this_barline_listening_review", BEAT_THIS_SMOKE_REL_PATH);
assertIncludes(smoke, BEAT_THIS_EVIDENCE_REL_PATH, BEAT_THIS_SMOKE_REL_PATH);

const beatThisTest = read("python/ingest/tests/test_beat_this_review_evidence.py");
assertIncludes(beatThisTest, "test_beat_this_review_helper_matches_runtime_gate_contract", "test_beat_this_review_evidence.py");
assertIncludes(beatThisTest, "test_beat_this_review_template_is_non_passing_until_reviewed", "test_beat_this_review_evidence.py");
assertIncludes(beatThisTest, "test_beat_this_review_helper_rejects_unreviewed_metadata_even_when_approved", "test_beat_this_review_evidence.py");
assertIncludes(beatThisTest, "test_beat_this_review_helper_rejects_non_utc_review_timestamp", "test_beat_this_review_evidence.py");
assertIncludes(beatThisTest, "test_beat_this_review_helper_uses_runtime_check_evidence_root_fallbacks", "test_beat_this_review_evidence.py");

const evidenceWriterTest = read("python/ingest/tests/test_model_upgrade_evidence_writers.py");
assertAllIncluded(
  evidenceWriterTest,
  [
    "test_model_upgrade_evidence_writers_resolve_runtime_check_root",
    "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT",
    "model_upgrade_gate_evidence.md",
    "fnmatch.fnmatch",
    "monkeypatch.setenv(EVIDENCE_ROOT_ENV, \" \")",
  ],
  "test_model_upgrade_evidence_writers.py",
);

assertSourceRuntimeCheckContract();

console.log("model-upgrade gate evidence contract verified");
