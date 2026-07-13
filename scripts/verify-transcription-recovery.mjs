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

function assertGenericGuitarFingeringSupport(source, relPath) {
  assertMatch(
    source,
    /function candidateRoleKeys\(role: [^)]+\): [^{]+{\s+return isGuitarRole\(role\) \? \[role, "guitar"\] : \[role\];\s+}/,
    `${relPath} must try generic guitar fingering sidecars for concrete guitar roles`,
  );
  assertMatch(
    source,
    /const roleKeys = candidateRoleKeys\(role\);\s+for \(const key of roleKeys\) \{\s+const manifestPath = manifestFingeringPath\(manifestRaw, key\);\s+if \(manifestPath\) out\.push\(manifestPath\);\s+\}\s+for \(const key of roleKeys\) \{\s+out\.push\(`aural\/fingering\.\$\{key\}\.json`, `features\/fingering\.\$\{key\}\.json`\);\s+\}/s,
    `${relPath} must use generic guitar manifest and fallback sidecar paths for concrete guitar roles`,
  );
  assertMatch(
    source,
    /"guitar" && isGuitarRole\(expectedRole\)/,
    `${relPath} must accept generic guitar fingering files for concrete guitar roles`,
  );
  assertMatch(
    source,
    /function concreteTrackRole\(role: [^)]+\): InstrumentRole \{\s+return role === "guitar" \? "lead_guitar" : role;\s+\}/,
    `${relPath} must synthesize generic guitar fingering as the lead_guitar track`,
  );
  assertMatch(
    source,
    /case "guitar":\s+return 2;/,
    `${relPath} must route generic guitar fingering through the lead_guitar channel hint`,
  );
}

const transcriptionPy = read("python/ingest/src/aural_ingest/transcription.py");
assertMatch(
  transcriptionPy,
  /DEFAULT_DRUM_ENGINE = "beat_conditioned_multiband_decoder"/,
  "transcription.py must keep beat_conditioned_multiband_decoder as the quality-default drum engine",
);
assertMatch(
  transcriptionPy,
  /DEFAULT_TRANSCRIPTION_PROFILE = "gameplay_default"/,
  "transcription.py must keep gameplay_default as the default transcription profile",
);
assertMatch(
  transcriptionPy,
  /elif normalized == "beat_conditioned_multiband_decoder":\s+chain = \[\s+"beat_conditioned_multiband_decoder",\s+"spectral_flux_multiband",\s+"adaptive_beat_grid",\s+"combined_filter",\s+"dsp_bandpass_improved",\s+"dsp_spectral_flux",\s+"dsp_bandpass",\s+"aural_onset",\s+\]/s,
  "beat_conditioned_multiband_decoder fallback chain drifted from the quality-default recovery order",
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
  /<select id="ingestDrumFilter">\s+<option value="auto" selected>auto \(profile default\)<\/option>\s+<option value="beat_conditioned_multiband_decoder">beat_conditioned_multiband_decoder \(quality default\)<\/option>/s,
  "desktop ingest UI must default to profile routing and expose the quality-default drum engine",
);

const gameFingeringLoaderTs = read("apps/game/src/fingeringLoader.ts");
assertGenericGuitarFingeringSupport(gameFingeringLoaderTs, "apps/game/src/fingeringLoader.ts");

const desktopFingeringLoaderTs = read("apps/desktop/src/fingeringLoader.ts");
assertGenericGuitarFingeringSupport(desktopFingeringLoaderTs, "apps/desktop/src/fingeringLoader.ts");

const gameSongChartLoaderTs = read("apps/game/src/songChartLoader.ts");
assertMatch(
  gameSongChartLoaderTs,
  /function hasFingeringSidecar\(details: SongChartLoaderDetails\): boolean \{\s+return Boolean\(details\.has_aural_fingering \|\| fingeringRolesFromManifest\(details\.manifest_raw\)\.length > 0\);\s+\}/,
  "game song chart loader must detect manifest-only aural_fingering sidecars",
);
assertMatch(
  gameSongChartLoaderTs,
  /if \(hasFingeringSidecar\(details\)\) \{[\s\S]+?loadFingeringForRoles\([\s\S]+?fingeringRolesForStandaloneTracks\(details\),[\s\S]+?details\.manifest_raw,[\s\S]+?\);[\s\S]+?melodicTracks = fingeringFilesToMelodicTracks\(fingeringFiles\);[\s\S]+?\}/,
  "game song chart loader must synthesize melodic tracks from standalone fingering when notes.mid is absent",
);
assertMatch(
  gameSongChartLoaderTs,
  /refinedMelodicTracks\.length > 0\s+\? refinedMelodicTracks\.map\(\(t\) => t\.role\)\s+: fingeringRolesForStandaloneTracks\(details\)/,
  "game song chart loader must load standalone fingering roles when notes.mid has no usable melodic tracks",
);
assertMatch(
  gameSongChartLoaderTs,
  /const melodicTracks =\s+refinedMelodicTracks\.length > 0\s+\? applyFingeringToMelodicTracks\(refinedMelodicTracks, fingeringFiles\)\s+: fingeringFilesToMelodicTracks\(fingeringFiles\);/,
  "game song chart loader must synthesize melodic tracks from fingering when parsed MIDI yields no melodic tracks",
);

assertMatch(
  desktopMainTs,
  /async function readMelodicTrackSelection\(containerPath: string, details: AuralSongDetails\): Promise<MelodicTrackSelection\[\]> \{\s+const midiBytes = await readNotesMidiBytes\(containerPath, details\);\s+if \(!midiBytes\) return \[\];\s+return selectMelodicTracksFromMidiBytes\(midiBytes\);\s+\}/,
  "desktop chart loader must return an empty melodic selection when notes.mid has no usable MIDI payload",
);
assertMatch(
  desktopMainTs,
  /if \(details\.has_aural_fingering\) \{[\s\S]+?loadFingeringForRoles\([\s\S]+?details\.manifest_raw,[\s\S]+?\);[\s\S]+?currentFingeringNotes = fingeringFilesToVisualizerNotes\(fingeringFiles\);[\s\S]+?if \(currentMelodicTracks\.length === 0\) \{\s+currentMelodicTracks = fingeringFilesToMelodicTracks\(fingeringFiles\);\s+currentMelodicNotes = melodicTracksToVisualizerNotes\(currentMelodicTracks\);[\s\S]+?\}[\s\S]+?\}/,
  "desktop chart loading must synthesize melodic tracks from fingering sidecars when notes.mid yields none",
);

const gameVizSongContextTs = read("apps/game/src/vizSongContext.ts");
assertMatch(
  gameVizSongContextTs,
  /export type VizNote = \{[\s\S]+?role\?: MelodicTrackSelection\["role"\] \| "drums";[\s\S]+?instrument\?: string;[\s\S]+?channel\?: number;[\s\S]+?trackName\?: string;[\s\S]+?\};/,
  "game viz song context note type must expose role and instrument metadata",
);
assertMatch(
  gameVizSongContextTs,
  /role: "drums",\s+instrument: "drums",\s+channel: 9,/,
  "game viz song context must tag drum notes with drum role/instrument/channel metadata",
);
assertMatch(
  gameVizSongContextTs,
  /role: track\.role,\s+instrument: track\.role,\s+channel: track\.channel,\s+trackName: track\.trackName,/,
  "game viz song context must tag melodic notes with role/instrument/channel metadata",
);

const vizSdkTs = read("packages/viz-sdk/src/index.ts");
assertMatch(
  vizSdkTs,
  /notes\?: Array<\{[\s\S]+?role\?: string;[\s\S]+?instrument\?: string;[\s\S]+?channel\?: number;[\s\S]+?trackName\?: string;[\s\S]+?\}>;/,
  "viz SDK note type must expose role and instrument metadata to visualizers",
);

const drumHighwayTs = read("visualizers/viz-drum-highway/src/index.ts");
assertMatch(
  drumHighwayTs,
  /function isDrumSongNote\(note: SongNote\): boolean \{\s+if \(note\.channel === 9\) return true;\s+return typeof note\.trackName === "string" && \/\\b\(drum\|drums\|kit\|percussion\|perc\)\\b\/i\.test\(note\.trackName\);\s+\}/,
  "drum highway visualizer must identify drum host notes by channel 9 or drum-like track names",
);
assertMatch(
  drumHighwayTs,
  /const parsed = parseSongNotes\(ctx\.song\?\.notes\);\s+this\.notes = parsed\s+\.filter\(isDrumSongNote\)\s+\.map\(\(n\) => \{\s+const lane = midiToLane\(n\.pitch\);/,
  "drum highway visualizer must filter host notes to drum sources before GM drum lane mapping",
);

const buildSidecarPs1 = read("build_sidecar.ps1");
assertMatch(
  buildSidecarPs1,
  /requirements-runtime\.txt/,
  "build_sidecar.ps1 must install mirrored runtime dependencies before PyInstaller",
);
assertMatch(
  buildSidecarPs1,
  /"install Basic Pitch without TensorFlow transitive dependency"/,
  "build_sidecar.ps1 must install Basic Pitch without the unavailable TensorFlow transitive dependency",
);
assertMatch(
  buildSidecarPs1,
  /"install ingest sidecar runtime dependencies"/,
  "build_sidecar.ps1 must hydrate sidecar runtime dependencies in clean builds",
);
assertMatch(
  buildSidecarPs1,
  /"install ingest sidecar package after runtime dependencies"/,
  "build_sidecar.ps1 must install the editable package only after runtime dependencies are installed",
);
assertMatch(
  buildSidecarPs1,
  /function Get-SidecarExecutableName\(\)[\s\S]+return "aural_ingest\.exe"[\s\S]+return "aural_ingest"/,
  "build_sidecar.ps1 must choose the sidecar executable name per host platform",
);
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
assertMatch(
  createPortablePs1,
  /project_license = @\{\s+source_path = \$projectLicenseSource\s+portable_path = \$portableProjectLicense\s+sha256 = \$projectLicenseSha256\s+\}/s,
  "portable_manifest.json must record the packaged project license hash",
);
assertMatch(
  createPortablePs1,
  /third_party_notices = @\{\s+source_path = \$thirdPartyNoticesSource\s+portable_path = \$portableThirdPartyNotices\s+sha256 = \$thirdPartyNoticesSha256\s+\}/s,
  "portable_manifest.json must record the packaged third-party notices hash",
);
assertMatch(
  createPortablePs1,
  /modelpacks = \$portableDemucsModelpacks \+ \$portableMt3Modelpacks \+ \$portablePianoModelpacks/,
  "portable_manifest.json must include every staged modelpack/checkpoint family",
);

console.log("transcription recovery invariants verified");
