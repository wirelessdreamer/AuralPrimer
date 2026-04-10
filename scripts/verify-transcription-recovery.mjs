import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();

function read(relPath) {
  return fs.readFileSync(path.join(repoRoot, relPath), "utf8");
}

function assertMatch(source, pattern, message) {
  if (!pattern.test(source)) {
    throw new Error(message);
  }
}

const transcriptionPy = read("python/ingest/src/aural_ingest/transcription.py");
assertMatch(
  transcriptionPy,
  /DEFAULT_DRUM_ENGINE = "combined_filter"/,
  "transcription.py must keep combined_filter as the default drum engine",
);
assertMatch(
  transcriptionPy,
  /elif normalized == "combined_filter":\s+chain = \[\s+"combined_filter",\s+"dsp_bandpass_improved",\s+"adaptive_beat_grid",\s+"dsp_spectral_flux",\s+"dsp_bandpass",\s+"aural_onset",\s+\]/s,
  "combined_filter fallback chain drifted from the Milestone 4A recovery order",
);
assertMatch(
  transcriptionPy,
  /elif normalized == "adaptive_beat_grid":\s+chain = \[\s+"adaptive_beat_grid",\s+"combined_filter",\s+"dsp_bandpass_improved",\s+"dsp_spectral_flux",\s+"dsp_bandpass",\s+"aural_onset",\s+\]/s,
  "adaptive_beat_grid fallback chain drifted from the Milestone 4A recovery order",
);

const desktopMainTs = read("apps/desktop/src/main.ts");
assertMatch(
  desktopMainTs,
  /<select id="ingestDrumFilter">\s+<option value="combined_filter">combined_filter \(default heuristic\)<\/option>\s+<option value="adaptive_beat_grid">adaptive_beat_grid<\/option>/s,
  "desktop ingest UI must present combined_filter as the default heuristic drum engine",
);

const buildSidecarPs1 = read("build_sidecar.ps1");
assertMatch(
  buildSidecarPs1,
  /\$runtimeCheck = Invoke-CapturedCommand \$sourceAbs @\("runtime-check"\)/,
  "build_sidecar.ps1 must run the packaged sidecar runtime-check before publishing artifacts",
);
assertMatch(
  buildSidecarPs1,
  /\$manifestPath = Join-Path \$outDirAbs "build_manifest\.json"/,
  "build_sidecar.ps1 must emit build_manifest.json for downstream freshness validation",
);

const createPortablePs1 = read("create_portable.ps1");
assertMatch(
  createPortablePs1,
  /throw "Portable sidecar hash mismatch:/,
  "create_portable.ps1 must reject stale portable sidecars via hash mismatch checks",
);
assertMatch(
  createPortablePs1,
  /throw "Portable sidecar manifest hash mismatch:/,
  "create_portable.ps1 must validate the copied portable sidecar manifest hash",
);
assertMatch(
  createPortablePs1,
  /throw "Portable sidecar timestamp is older than source sidecar/,
  "create_portable.ps1 must reject portable sidecars older than the freshly built source sidecar",
);

console.log("transcription recovery invariants verified");
