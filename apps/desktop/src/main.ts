import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import {
  featureDir as packFeatureDir,
  isManifestPack,
} from "@auralprimer/auralsong/packKind";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { TransportController } from "./transportController";
import type { TransportTimebase } from "./audioBackend";
import {
  NativeAudioTimebase,
  type NativeAudioDeviceInfo,
  type NativeAudioDeviceSelection,
  type NativeAudioHostInfo,
  type NativeAudioHostSelection
} from "./nativeAudioTimebase";
import { Metronome } from "./metronome";
import { extractKeyModeFromManifest } from "./hud";
import { ingestImport, type IngestImportRequest, type IngestSubcommand } from "./ingestClient";
import { initModelSetupPanel } from "./modelSetupPanel";
import { buildIngestRequestFromForm, inferIngestTitleArtistFromSourcePath } from "./ingestUi";
import { PREFERRED_MODEL_PACKS } from "./models/preferredModelPacks";
import {
  installModelPackFromPath,
  installModelPackFromUrl,
  listInstalledModelPacks,
  type InstalledModelPack
} from "./models/modelManager";
import { BUILTIN_PLUGINS, type PluginDescriptor, loadPlugin, scanBundledPlugins, scanUserPlugins } from "./plugins";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { generateLyricsJsonFromPlainText } from "./lyricsGenerator";
import {
  selectDrumChartFromMidiBytes,
  selectMelodicTracksFromMidiBytes,
  type DrumChartSelection,
  type MelodicTrackSelection,
} from "./chartLoader";
import { loadDrumChartFromTab } from "./drumTabChart";
import {
  FINGERING_ROLES,
  fingeringFilesToMelodicTracks,
  fingeringFilesToVisualizerNotes,
  fingeringRolesFromManifest,
  loadFingeringForRoles,
  mergeFingeringIntoVisualizerNotes,
  type VisualizerSongNote,
} from "./fingeringLoader";
import { refineWorkspaceHtml } from "./refineWorkspaceHtml";
import { initRefineWorkspace, type RefineWorkspaceHandle } from "./refineWorkspace";
import { lyricTimingHtml } from "./lyricTimingHtml";
import { initLyricTimingWorkspace, type LyricTimingHandle } from "./lyricTimingWorkspace";
import { initAvCalibration } from "@auralprimer/av-sync";
import {
  getAudioOffsetMs,
  getVideoOffsetMs,
  getEffectiveOffsetMs,
  loadAvCalibration,
  setAvCalibration,
} from "./avOffset";
import {
  MELODIC_ROLES,
  MELODIC_ROLE_LABELS,
  invalidateCleanupCache,
  auralsongJsonExists,
  drumTabRelPath,
  getRoleReadiness,
  melodicStemRoles,
  detectMelodicStems,
  parseSidecarStatusLine,
  classifySpectroResult,
  classifyCandidateResult,
  needsArrangementPrep,
  type RoleReadiness,
  type RowReady,
  type SidecarRunResult,
  type SpectroOutcome,
} from "./cleanupReadiness";

function haveTauri(): boolean {
  // Tauri v2 does **not** necessarily expose `window.__TAURI__` unless
  // `app.withGlobalTauri` is enabled in tauri.conf.json.
  // The JS APIs rely on `window.__TAURI_INTERNALS__`.
  const w = window as unknown as { __TAURI__?: unknown; __TAURI_INTERNALS__?: unknown };
  return typeof w.__TAURI_INTERNALS__ !== "undefined" || typeof w.__TAURI__ !== "undefined";
}

async function safeInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!haveTauri()) {
    throw new Error("This action requires the desktop app (run via `tauri dev`).");
  }
  return invoke<T>(cmd, args);
}

async function pickFolder(): Promise<string | null> {
  if (!haveTauri()) {
    throw new Error("Folder picker requires the desktop app (run via `tauri dev`).");
  }
  const res = await open({ directory: true, multiple: false });
  if (res === null) return null;
  if (Array.isArray(res)) return res[0] ?? null;
  return res;
}

async function pickFiles(extensions: string[], multiple: boolean): Promise<string[]> {
  if (!haveTauri()) {
    throw new Error("File picker requires the desktop app (run via `tauri dev`).");
  }
  const res = await open({
    directory: false,
    multiple,
    filters: [{ name: extensions.join(", "), extensions: extensions.map((e) => e.replace(/^\./, "")) }]
  });
  if (res === null) return [];
  if (Array.isArray(res)) return res;
  return [res];
}

type ManifestSummary = {
  schema_version?: string;
  song_id?: string;
  title?: string;
  artist?: string;
  duration_sec?: number;
};

type AuralSongScanEntry = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest?: ManifestSummary;
  error?: string;
};

function isDemoAuralSong(e: AuralSongScanEntry): boolean {
  // Deterministic id for our built-in first-run song.
  return (e.manifest?.song_id ?? "") === "demo_sine_440hz";
}

async function waitForUiPaint(): Promise<void> {
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

/**
 * In-container subdirectory holding feature artifacts (notes.mid, lyrics.json,
 * spectrogram, refine candidates). feedpak relocates these under `aural/`;
 * legacy `.auralsong` packs keep them under `features/`. The Tauri read
 * commands accept both prefixes, so callers select by container suffix.
 */
function featureDir(containerPath: string): "aural" | "features" {
  return packFeatureDir(containerPath);
}

/**
 * The lyrics rel-path to READ for a pack, resolved from the manifest `lyrics`
 * pointer when known (a sloppak keeps its lyrics at pack ROOT via the manifest
 * `lyrics` key), else the AuralPrimer default `<featureDir>/lyrics.json`
 * (Phase 7). The pointer is read from `get_auralsong_details` fields
 * defensively — pass the already-fetched details when available to avoid an
 * extra round-trip; otherwise this returns the feature-dir default.
 */
function lyricsReadRelPath(containerPath: string, details?: unknown): string {
  const d = (details ?? null) as
    | {
        lyrics_rel?: unknown;
        lyrics_rel_path?: unknown;
        lyrics_path?: unknown;
        manifest_raw?: { lyrics?: unknown } | null;
      }
    | null;
  const pointer =
    (typeof d?.lyrics_rel === "string" && d.lyrics_rel) ||
    (typeof d?.lyrics_rel_path === "string" && d.lyrics_rel_path) ||
    (typeof d?.lyrics_path === "string" && d.lyrics_path) ||
    (typeof d?.manifest_raw?.lyrics === "string" && d.manifest_raw.lyrics) ||
    "";
  return pointer || `${featureDir(containerPath)}/lyrics.json`;
}

type ManifestArtifactPointers = {
  lyrics?: unknown;
  drum_tab?: unknown;
  keys?: unknown;
  harmony?: unknown;
  vocal_pitch?: unknown;
  vocal_pitch_contour?: unknown;
  song_timeline?: unknown;
  aural_notes_mid?: unknown;
};

function manifestArtifactRelPath(
  manifestRaw: ManifestArtifactPointers | undefined,
  key: keyof ManifestArtifactPointers,
): string | null {
  const value = manifestRaw?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function artifactReadRelPath(
  containerPath: string,
  details: AuralSongDetails,
  key: keyof ManifestArtifactPointers,
  legacyFeatureName: string,
): string | null {
  const manifestRaw = details.manifest_raw as ManifestArtifactPointers | undefined;
  const manifestPointer = manifestArtifactRelPath(manifestRaw, key);
  if (manifestPointer) return manifestPointer;
  return isManifestPack(containerPath) ? null : `features/${legacyFeatureName}`;
}

async function readOptionalArtifactJson(
  containerPath: string,
  details: AuralSongDetails,
  key: keyof ManifestArtifactPointers,
  legacyFeatureName: string,
): Promise<unknown | null> {
  const relPath = artifactReadRelPath(containerPath, details, key, legacyFeatureName);
  if (!relPath) return null;
  try {
    return await invoke<unknown>("read_auralsong_json", { containerPath, relPath });
  } catch {
    return null;
  }
}

function applySongMeterFromTimeline(timeline: unknown): void {
  let songBpm = 120;
  let songTimeSig: [number, number] = [4, 4];
  const tl = timeline as
    | {
        tempos?: Array<{ bpm?: number }>;
        time_signatures?: Array<{ ts?: number[] }>;
      }
    | null;
  if (tl) {
    const bpm = tl.tempos?.[0]?.bpm;
    if (typeof bpm === "number" && bpm > 0) songBpm = bpm;
    const ts = tl.time_signatures?.[0]?.ts;
    if (Array.isArray(ts) && ts.length >= 2) songTimeSig = [Math.round(ts[0]), Math.round(ts[1])];
  }
  transportController.setSongMeter(songBpm, songTimeSig);
  transport = transportController.getState();
}

type AuralSongDetails = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest_summary?: ManifestSummary;
  manifest_raw?: unknown;
  has_beats: boolean;
  has_tempo_map: boolean;
  has_sections: boolean;
  has_events: boolean;
  has_lyrics?: boolean;
  has_notes_mid?: boolean;
  has_song_timeline?: boolean;
  has_drum_tab?: boolean;
  has_keys?: boolean;
  has_harmony?: boolean;
  has_vocal_pitch?: boolean;
  has_vocal_pitch_contour?: boolean;
  has_aural_fingering?: boolean;
  // Arrangement/lyrics readiness fields consumed defensively (owned by the Rust
  // lane, CONTRACT C6). All optional; the exact names are feature-detected in
  // needsArrangementPrep / arrangementCount / lyricsReadRelPath.
  arrangement_count?: number;
  arrangements?: unknown;
  lyrics_rel_path?: string;
  lyrics_path?: string;
  has_mix_mp3: boolean;
  has_mix_ogg: boolean;
  has_mix_wav?: boolean;
  charts: string[];
  error?: string;
};

type SongCapabilities = {
  features: {
    beats: boolean;
    tempo_map: boolean;
    sections: boolean;
    events: boolean;
    lyrics: boolean;
    notes_mid: boolean;
    drum_tab: boolean;
    song_timeline: boolean;
    keys: boolean;
    harmony: boolean;
    vocal_pitch: boolean;
    vocal_pitch_contour: boolean;
    aural_fingering: boolean;
  };
  audio: {
    wav: boolean;
    mp3: boolean;
    ogg: boolean;
  };
  charts: {
    any: boolean;
    byInstrument: Partial<Record<Instrument, boolean>>;
  };
};

type LyricsFile = {
  format: string;
  granularity?: string;
  job_id?: string;
  lines: Array<{
    start: number;
    end: number;
    text: string;
    chunks?: Array<{ start: number; end: number; text: string; char_start: number; char_end: number }>;
  }>;
};

type IngestRuntimeDependencyStatus = {
  ok: boolean;
  required?: boolean;
  role?: string;
  missing_behavior?: string;
  version?: string;
  error?: string;
};

type IngestRuntimeEngineStatus = {
  ok: boolean;
  loadable?: boolean;
  transcribe_smoke_ok?: boolean;
  version?: string;
  description?: string;
  modelpack_version?: string;
  modelpack_root?: string;
  checkpoint_path?: string;
  error?: string;
};

type IngestRuntimeCheckPayload = {
  ok: boolean;
  dependencies?: Record<string, IngestRuntimeDependencyStatus>;
  drum_engines?: Record<string, IngestRuntimeEngineStatus>;
};

type IngestRuntimeCheckResult = {
  ok: boolean;
  exit_code: number;
  command: string[];
  stdout: string;
  stderr: string;
  payload?: IngestRuntimeCheckPayload;
};

type IngestSummaryState = "idle" | "running" | "success" | "error";
type DesktopAnalysisImportMode = IngestSubcommand | "stem-dir";
type InputStemRole = "drums" | "bass" | "guitar" | "lead_guitar" | "rhythm_guitar" | "keys" | "vocals" | "other";

type AudioBlob = {
  mime: string;
  bytes: number[];
};

type MidiBlob = {
  bytes: number[];
};

type IngestImportProgressEvent = {
  stream: "stdout" | "stderr";
  line: string;
  parsed?: unknown;
};

// Grouped so the dropdown reads as a decision, not a 22-item list of internal
// method names. Every `value` is unchanged -- ingestMelodicMethodSelect.value is
// sent straight to the sidecar.
const MELODIC_METHOD_GROUPS: Array<{ label: string; options: Array<[string, string]> }> = [
  {
    label: "Recommended",
    options: [["auto", "Automatic (recommended)"]],
  },
  {
    label: "Piano",
    options: [
      ["piano_auto", "Piano — automatic"],
      ["piano_basic_pitch_playable", "Piano — simplified so it's playable"],
      ["piano_basic_pitch", "Piano — model"],
      ["piano_basic_pitch_clean", "Piano — model, cleaned up"],
      ["piano_polyphonic_clean", "Piano — chords, cleaned up"],
      ["piano_polyphonic", "Piano — chords, raw"],
    ],
  },
  {
    label: "General melody",
    options: [
      ["basic_pitch", "General pitch model"],
      ["pyin", "Single-note pitch tracker"],
      ["melodic_combined", "Combined detectors"],
      ["melodic_octave_fix", "Combined, with octave correction"],
      ["melodic_yin_octave_hps_fix", "YIN, with octave correction"],
      ["melodic_adaptive", "Adaptive"],
      ["melodic_yin_bass80", "Bass-tuned (80 Hz floor)"],
      ["melodic_hpss_combined", "Harmonic / percussive split"],
      ["melodic_template_multipass", "Template multi-pass"],
    ],
  },
  {
    label: "Research — expect rough results",
    options: [
      ["piano_transkun_clean", "Piano — Transkun, cleaned up"],
      ["piano_pti_clean", "Piano — PTI, cleaned up"],
      ["piano_hft_clean", "Piano — hFT, cleaned up"],
      ["piano_transkun", "Piano — Transkun, raw"],
      ["piano_pti", "Piano — PTI, raw"],
      ["piano_hft", "Piano — hFT, raw"],
    ],
  },
];

const melodicMethodOptionsHtml = MELODIC_METHOD_GROUPS.map(
  (group) =>
    `<optgroup label="${group.label}">` +
    group.options.map(([value, label]) => `<option value="${value}">${label}</option>`).join("") +
    `</optgroup>`
).join("");

type MidiTrackInfo = {
  index: number;
  name: string;
  note_count: number;
  channels: number[];
  pitch_min: number | null;
  pitch_max: number | null;
  suggested_role: string;
};

type TrackAssignment = {
  track_index: number;
  role: string;
};

type StemMidiCreateRequest = {
  title: string;
  artist: string;
  stemWavPaths: string[];
  midiPath: string;
  trackAssignments?: TrackAssignment[];
};

type StemMidiCreateResult = { auralsong_path: string };

type RawSongDetectedPart = {
  path: string;
  detected_role: string;
  game_role?: string | null;
};

type RawSongFolderInspection = {
  folder_path: string;
  title_guess: string;
  stem_wav_paths: string[];
  midi_paths: string[];
  stem_parts: RawSongDetectedPart[];
  midi_parts: RawSongDetectedPart[];
  lyrics_txt_path?: string | null;
  karaoke_json_path?: string | null;
  vocal_stem_path?: string | null;
  mix_wav_path?: string | null;
  mapped_game_roles: string[];
  midi_chart_ready: boolean;
  source_midi_offset_sec?: number | null;
  source_midi_offset_pair_count: number;
  warnings: string[];
};

type ImportRawSongFolderRequest = {
  folder_path: string;
  title?: string;
  artist?: string;
};

type ImportRawSongFolderResult = {
  auralsong_path: string;
  stems_count: number;
  midi_files_count: number;
  lyrics_included: boolean;
  midi_chart_included: boolean;
  mapped_game_roles: string[];
  source_midi_offset_sec?: number | null;
  source_midi_offset_pair_count: number;
  warnings: string[];
};

const root = document.getElementById("app");
if (!root) throw new Error("missing #app");

root.innerHTML = `
  <div class="appShell">
    <div id="runtimeBanner" class="runtimeBanner" aria-live="polite"></div>
    <header class="appHeader">
      <button id="navHome" class="brandBtn" aria-label="AuralStudio Home">
        <span class="logoMark" aria-hidden="true"></span>
        <span class="brandText">
          <span class="brandName">AuralStudio</span>
          <span class="brandTag">import | cleanup | edit</span>
        </span>
      </button>

      <nav class="topNav" aria-label="Primary">
        <button id="navMake" class="navBtn">Import</button>
        <button id="navPlay" class="navBtn">Cleanup &amp; Edit</button>
        <button id="navConfig" class="navBtn">Configure</button>
      </nav>
    </header>

    <main class="appMain">
      <section class="route isActive" data-route="home">
        <div class="hero">
          <div class="heroLogo">
            <span class="logoMark logoMark--xl" aria-hidden="true"></span>
            <div>
              <h1 class="heroTitle">AuralStudio</h1>
              <div class="meta heroMeta">Import raw source material, clean up AuralSongs, and edit pack metadata without the gameplay shell.</div>
            </div>
          </div>
          <div class="menuGrid" role="list">
            <button class="menuCard" id="homeMake" role="listitem">
              <div class="menuTitle">Import</div>
              <div class="meta">Suno folders, stem folders, and perform-analysis import.</div>
            </button>
            <button class="menuCard" id="homePlay" role="listitem">
              <div class="menuTitle">Cleanup &amp; Edit</div>
              <div class="meta">Inspect AuralSongs, regenerate lyrics, and review pack contents.</div>
            </button>
            <button class="menuCard" id="homeConfig" role="listitem">
              <div class="menuTitle">Configure</div>
              <div class="meta">Song library path, model packs, and import defaults.</div>
            </button>
          </div>
        </div>
      </section>

      <section class="route" data-route="play">
        <div class="twoCol cleanupLayout playLayout" id="playLayout">
          <section class="panel cleanupListPanel">
            <div class="panelHeader">
              <h2>Cleanup &amp; Edit</h2>
              <div class="row" style="margin:0;gap:8px;align-items:center">
                <span id="cleanupBuildAllStatus" class="meta cleanupBuildAllStatus"></span>
                <button id="cleanupBuildAll" title="For every listed song missing either, build the spectrogram then run candidate precompute so it's ready to open">Prep all unbuilt</button>
                <button id="refresh">Refresh</button>
              </div>
            </div>
            <p class="meta cleanupListHint">Pick a song to prepare for cleanup. Studio focuses on inspection and authoring; use <strong>AuralPrimer</strong> for live playback.</p>
            <pre id="status" class="cleanupStatusLine">(not loaded)</pre>
            <div id="list" class="cleanupList"></div>
          </section>

          <section class="panel cleanupActionPanel">
            <div id="cleanupAction" class="cleanupAction">
              <p class="meta">(select an AuralSong on the left)</p>
            </div>
            <pre id="auralsongEditorStatus" class="meta" style="display:none">(select an AuralSong to inspect or edit)</pre>
            <details class="cleanupRawDetails">
              <summary>Raw details (features / audio / manifest)</summary>
              <div id="details" class="details"></div>
            </details>
          </section>
        </div>
      </section>

      <section class="route" data-route="make">
        <div class="importFlow">

          <!-- Step 1. The old page put two rival "import" panels side by side and
               never asked which one you wanted; this asks first and shows only the
               matching form. -->
          <section class="panel importStep">
            <div class="panelHeader">
              <h2>Import a song</h2>
              <div class="meta">step 1 of 2</div>
            </div>
            <p class="meta">What are you starting from?</p>
            <div class="importChoiceGrid">
              <button class="menuCard importChoiceCard isActive" id="importKindSuno"
                      type="button" data-import-kind="suno">
                <div class="menuTitle">A Suno export folder</div>
                <div class="meta">Stems and MIDI are already separated. Fast &mdash; about a minute.</div>
              </button>
              <button class="menuCard importChoiceCard" id="importKindAudio"
                      type="button" data-import-kind="audio">
                <div class="menuTitle">One audio file</div>
                <div class="meta">MP3, WAV, FLAC, OGG or M4A. We separate the parts and write out the notes. Slow &mdash; 10 to 30 minutes.</div>
              </button>
              <button class="menuCard importChoiceCard" id="importKindStems"
                      type="button" data-import-kind="stems">
                <div class="menuTitle">A folder of separated stems</div>
                <div class="meta">You already have drums/bass/vocals as separate files. We skip separation and go straight to writing the notes.</div>
              </button>
            </div>
          </section>

          <!-- Step 2a. Suno: same controls and ids as before. -->
          <section class="panel importStep" id="importPanelSuno">
            <div class="panelHeader">
              <h2>Choose your Suno folder</h2>
              <div class="meta">step 2 of 2</div>
            </div>
            <p class="meta">
              We scan the folder for stem WAVs, MIDI, <code>lyrics.txt</code> and optional karaoke
              JSON, check what is there, then build the song.
            </p>
            <div class="row">
              <button id="stemMidiPickFolderMake">Choose Suno folder...</button>
              <div class="meta grow" id="stemMidiFolderLabelMake">(no folder selected)</div>
            </div>
            <div id="stemMidiSummaryMake" class="meta makeSummary"></div>
            <div class="row importRunRow">
              <button id="stemMidiImportMake" class="importRunBtn">Import song</button>
            </div>
            <div class="meta">Imports an editable draft you clean up in Refine &mdash; export a <code>.feedpak</code> when it's finished.</div>
            <pre id="stemMidiStatusMake" class="meta">(not imported)</pre>
            <div id="stemMidiNextStepsMake" class="postImportCard" style="display:none;">
              <div class="postImportTitle">&#10003; Song imported</div>
              <div class="postImportHint">Next: clean up the auto-transcription so the gameplay chart matches your intent.</div>
              <div class="row">
                <button class="postImportPrimary" id="stemMidiOpenRefine">Open in Refine workspace</button>
                <button id="stemMidiPrecomputeRefine">Run candidate precompute</button>
                <button id="stemMidiOpenCleanup">View in Cleanup &amp; Edit</button>
              </div>
              <pre id="stemMidiPrecomputeStatus" class="meta" style="margin-top:8px; display:none;"></pre>
            </div>
          </section>

          <!-- Step 2b. Analysis import. Hidden, never removed: every id below is
               read by buildIngestRequestFromForm and must stay queryable. -->
          <section class="panel importStep" id="importPanelAnalysis" hidden>
            <div class="panelHeader">
              <h2 id="importAnalysisHeading">Point us at your audio</h2>
              <div class="meta">step 2 of 2</div>
            </div>
            <p class="meta importStageNote">
              We decode the audio, find the beat and tempo, separate it into parts, split lead from
              rhythm guitar, then write out the drum and melody notes. The defaults are good &mdash;
              normally you only need the file.
            </p>

            <div class="importField">
              <label class="importLabel" for="ingestSourcePath">Where is it?</label>
              <div class="row">
                <input id="ingestSourcePath" class="grow" type="text" placeholder="C:\\music\\song.wav" />
                <button id="ingestBrowseSource">Browse...</button>
              </div>
              <div class="row importSubRow">
                <label class="meta" for="ingestMode">That path is</label>
                <select id="ingestMode">
                  <option value="import">One audio file</option>
                  <option value="import-dir">A folder &mdash; use the audio file inside it</option>
                  <option value="stem-dir">A folder of already-separated stems</option>
                </select>
              </div>
            </div>

            <div class="importField">
              <label class="importLabel" for="ingestTitle">What is it called?</label>
              <div class="row">
                <input id="ingestTitle" class="grow" type="text" placeholder="Title" />
                <input id="ingestArtist" class="grow" type="text" placeholder="Artist" />
              </div>
              <div class="meta">Filled in from the filename when you pick a file. Edit it if the guess is wrong.</div>
            </div>

            <details id="importAdvanced" class="importAdvanced">
              <summary>Advanced &mdash; engines and tuning</summary>
              <div class="importAdvancedBody">

                <div class="importField">
                  <label class="importLabel" for="ingestDrumFilter">How drum hits are found</label>
                  <select id="ingestDrumFilter">
                    <option value="auto" selected>Automatic (recommended)</option>
                    <option value="beat_conditioned_multiband_decoder">Beat-aware multiband (quality default)</option>
                    <option value="spectral_flux_multiband">Spectral change, multiband</option>
                    <option value="combined_filter">Standard</option>
                    <option value="adaptive_beat_grid">Snap hits to the beat grid</option>
                    <option value="dsp_bandpass_improved">Frequency bands (improved)</option>
                    <option value="dsp_spectral_flux">Spectral change</option>
                    <option value="aural_onset">AuralPrimer onset detector</option>
                    <option value="dsp_bandpass">Frequency bands (basic)</option>
                    <option value="librosa_superflux">SuperFlux onsets</option>
                    <option value="mr_mt3_drums">MR-MT3 (research)</option>
                    <option value="yourmt3_drums">YourMT3 (research)</option>
                    <option value="drum_crnn">Neural, 5 kit pieces</option>
                  </select>
                  <div class="meta">
                    Anything marked <em>needs model pack</em> is not installed yet.
                    <button id="importOpenModels" type="button" class="importInlineLink">Open Configure &rsaquo; Models</button>
                  </div>
                </div>

                <div class="importField">
                  <label class="importLabel" for="ingestMelodicMethod">How melody notes are found</label>
                  <select id="ingestMelodicMethod">
                    ${melodicMethodOptionsHtml}
                  </select>
                </div>

                <div class="importField importFieldPair">
                  <div class="importSubField">
                    <label class="importLabel" for="ingestProfile">Analysis depth</label>
                    <input id="ingestProfile" type="text" value="full" />
                    <div class="meta">&ldquo;full&rdquo; runs every stage. Change only if you were told to.</div>
                  </div>
                  <div class="importSubField">
                    <label class="importLabel" for="ingestShifts">Separation passes</label>
                    <input id="ingestShifts" type="number" min="1" step="1" value="1" />
                    <div class="meta">More passes are slower and occasionally cleaner. 1 is right for almost everything.</div>
                  </div>
                </div>

                <div class="importField">
                  <label class="importCheck"><input id="ingestMultiFilter" type="checkbox" /> Cross-check drum hits with a second detector</label>
                  <div class="meta">Slower. Helps on busy or heavily compressed mixes.</div>
                </div>

                <div class="importField">
                  <label class="importLabel" for="ingestOutPath">Save the result somewhere else</label>
                  <input id="ingestOutPath" class="grow" type="text" placeholder="(leave blank to use your songs folder)" />
                </div>

                <div class="importField">
                  <label class="importLabel" for="ingestConfig">Extra settings</label>
                  <input id="ingestConfig" class="grow" type="text" placeholder='{"ingest_timestamp":"..."} or C:\\cfg.json' />
                  <div class="meta">Raw JSON, or a path to a .json file. Most people never touch this.</div>
                </div>

              </div>
            </details>

            <div class="row importRunRow">
              <button id="ingestRun" class="importRunBtn">Analyze and import</button>
            </div>

            <div id="ingestSummary" class="ingestSummary ingestSummary--idle">
              <div class="row ingestSummaryHeader">
                <div class="grow">
                  <div id="ingestSummaryBadge" class="ingestSummaryBadge ingestSummaryBadge--idle">Ready</div>
                  <div id="ingestSummaryTitle" class="ingestSummaryTitle">Ready to import</div>
                </div>
                <div id="ingestSummaryProgressText" class="meta ingestSummaryProgressText">0%</div>
              </div>
              <progress id="ingestSummaryProgress" class="ingestSummaryProgress" max="100" value="0"></progress>
              <div id="ingestSummaryDetail" class="meta ingestSummaryDetail">Pick a file, then press Analyze and import.</div>
              <div class="row ingestSummaryActions">
                <button id="ingestOpenCleanup" type="button" disabled>Review in Cleanup &amp; Edit</button>
              </div>
            </div>

            <details id="ingestLogPanel" class="ingestLogPanel">
              <summary>Import log</summary>
              <pre id="ingestStatus" class="meta">(not started)</pre>
            </details>
          </section>

        </div>
      </section>

      ${refineWorkspaceHtml()}

      ${lyricTimingHtml()}

      <section class="route" data-route="config">
        <div class="twoCol">
          <section class="panel">
            <div class="panelHeader">
              <h2>Configure</h2>
              <div class="meta">library + model packs</div>
            </div>

            <h3>Song Library</h3>
            <div class="row">
              <button id="clearOverride">Use default</button>
            </div>
            <div class="row">
              <input id="songsFolder" type="text" placeholder="Songs folder path" />
              <button id="setOverride">Set folder</button>
            </div>

            <h3>Models</h3>
            <p class="meta">Model packs install into <code>assets/models/&lt;id&gt;/&lt;version&gt;/</code> under the app data directory.</p>

            <h4>Needs setup — external engines &amp; licenses</h4>
            <p class="meta">Optional engines you install yourself. Gated models need their license accepted on the provider's site before the weights can be downloaded.</p>
            <div id="modelSetupPanel" class="mt3RuntimeStatus meta">Checking model setup…</div>

            <div class="row">
              <button id="modelsRefresh">Refresh</button>
            </div>

            <div class="row">
              <label class="meta">Import local modelpack zip</label>
              <input id="modelpackPath" type="text" placeholder="/path/to/modelpack.zip" />
              <button id="modelpackImport">Install</button>
            </div>

            <h4>Preferred packs</h4>
            <div id="preferredModels"></div>

            <h4>Installed</h4>
            <pre id="modelsStatus">(not loaded)</pre>

            <h4>Optional drum engines</h4>
            <p class="meta">
              MR-MT3 and YourMT3 are research-grade neural drum transcribers. They only become
              selectable in Import once their model pack is installed above.
            </p>
            <div class="mt3RuntimePanel">
              <div class="row mt3RuntimeHeader">
                <button id="ingestRuntimeRefresh" type="button">Re-check</button>
              </div>
              <div id="ingestRuntimeStatus" class="mt3RuntimeStatus meta">Checking&hellip;</div>
            </div>
          </section>

          <section class="panel">
            <div class="panelHeader">
              <h2>Audio</h2>
              <div class="meta">Output sync calibration</div>
            </div>

            <h3>A/V Sync</h3>
            <p class="meta">Align the playhead with what you hear during Cleanup &amp; Edit. Calibrate measures two delays — <strong>audio</strong> (output latency, 150–250&nbsp;ms on Bluetooth) and <strong>video</strong> (display lag). Shared with AuralPrimer: calibrate once, both apps use it. Fine-tune audio with Ctrl+[ / Ctrl+].</p>
            <div class="row">
              <label class="meta">Audio</label>
              <span id="avSyncAudioValue" class="meta">0 ms</span>
              <label class="meta">Video</label>
              <span id="avSyncVideoValue" class="meta">0 ms</span>
              <label class="meta">Effective</label>
              <span id="avSyncValue" class="meta">0 ms</span>
            </div>
            <div class="row">
              <button id="avSyncCalibrate">Calibrate…</button>
              <button id="avSyncReset">Reset</button>
            </div>
          </section>

          <section class="panel">
            <div class="panelHeader">
              <h2>Workflow split</h2>
              <div class="meta">Studio vs AuralPrimer</div>
            </div>
            <p class="meta">
              <strong>AuralStudio</strong> is the authoring app: import raw material, inspect AuralSongs, regenerate lyrics,
              and maintain the library.
            </p>
            <p class="meta">
              <strong>AuralPrimer</strong> is the play app: highway, transport, audio playback, MIDI clock, and performance UX.
            </p>
          </section>
        </div>
      </section>

      <section class="legacyPlaybackScaffold" aria-hidden="true" hidden>
        <div id="globalHud" class="hud">
          <div class="hudLabel">Key / Mode</div>
          <div class="hudValue" id="hudKeyMode">C major</div>
        </div>
        <div id="playbackLegacyHost">
          <div id="players"></div>
          <button id="addPlayer">Add</button>
          <button id="toggleFocus">Focus</button>
          <select id="pluginSelect"></select>
          <button id="pluginRefresh">Refresh</button>
          <button id="vizStart">Start visualizer</button>
          <button id="vizStop" disabled>Stop</button>
          <canvas id="viz" width="800" height="240"></canvas>
          <pre id="vizStatus">(not running)</pre>
          <button id="audioLoad" disabled>Load audio</button>
          <button id="audioPlay" disabled>Play</button>
          <button id="audioPause" disabled>Pause</button>
          <button id="audioStop" disabled>Stop</button>
          <select id="audioBackend" disabled>
            <option value="native">Native (Rust)</option>
          </select>
          <select id="audioOutputHost"></select>
          <button id="audioOutputHostRefresh">Refresh</button>
          <button id="audioOutputHostApply">Apply</button>
          <select id="audioOutputDevice"></select>
          <button id="audioOutputDeviceRefresh">Refresh</button>
          <button id="audioOutputDeviceApply">Apply</button>
          <input id="playbackRate" type="number" min="0.25" max="2" step="0.05" value="1" />
          <button id="playbackRateApply">Set rate</button>
          <label><input id="metronomeEnabled" type="checkbox" /> enabled</label>
          <input id="metronomeVolume" type="range" min="0" max="1" step="0.05" value="0.25" />
          <input id="audioSeek" type="number" min="0" step="0.25" value="0" />
          <button id="audioSeekGo" disabled>Go</button>
          <input id="loopT0" type="number" min="0" step="0.25" value="0" />
          <input id="loopT1" type="number" min="0" step="0.25" value="4" />
          <button id="loopSet" disabled>Set</button>
          <button id="loopClear" disabled>Clear</button>
          <pre id="audioStatus">(no audio)</pre>
          <label><input id="midiFollowEnabled" type="checkbox" checked /> follow external clock</label>
          <select id="midiInPort"></select>
          <button id="midiInRefresh">Refresh</button>
          <button id="midiInConnect">Connect</button>
          <button id="midiInDisconnect">Disconnect</button>
          <input id="midiTempoScale" type="number" min="0.25" max="4" step="0.05" value="1" />
          <label><input id="midiInSysexEnabled" type="checkbox" /> allow SysEx input</label>
          <pre id="midiStatus">(midi clock: not connected)</pre>
          <pre id="midiInEvents">(midi input events)</pre>
          <label><input id="midiOutEnabled" type="checkbox" /> send MIDI clock</label>
          <select id="midiOutPort"></select>
          <button id="midiOutRefresh">Refresh</button>
          <button id="midiOutSelect">Select</button>
          <label><input id="midiOutSysexEnabled" type="checkbox" /> allow SysEx output</label>
          <button id="midiOutStart">Start</button>
          <button id="midiOutContinue">Continue</button>
          <button id="midiOutStop">Stop</button>
          <input id="midiMsgChannel" type="number" min="1" max="16" step="1" value="1" />
          <input id="midiMsgNote" type="number" min="0" max="127" step="1" value="60" />
          <input id="midiMsgVelocity" type="number" min="0" max="127" step="1" value="100" />
          <button id="midiMsgNoteOn">Note On</button>
          <button id="midiMsgNoteOff">Note Off</button>
          <button id="midiMsgAllNotesOff">All Notes Off</button>
          <input id="midiMsgCc" type="number" min="0" max="127" step="1" value="1" />
          <input id="midiMsgCcValue" type="number" min="0" max="127" step="1" value="64" />
          <button id="midiMsgCcSend">Send CC</button>
          <input id="midiOutRawHex" class="grow" type="text" placeholder="90 3C 64" />
          <button id="midiOutRawSend">Send Raw</button>
          <pre id="midiOutStatus">(midi clock out: disabled)</pre>
        </div>
      </section>
    </main>
  </div>
`;

// In browser-only mode, make it explicit and disable desktop-only actions.
{
  const banner = document.getElementById("runtimeBanner") as HTMLDivElement | null;
  if (banner && !haveTauri()) {
    banner.innerHTML = `
      <div class="runtimeBannerInner">
        <strong>Browser mode</strong> — you opened the web build (no Tauri runtime detected).<br />
        Desktop-only features (file picker, AuralSong scanning, native audio, etc.) are disabled here.
        <div class="meta">Run <code>npm run desktop:dev</code> or launch the installed app to use these features.</div>
      </div>
    `;
  }
}

type Route = "home" | "play" | "make" | "refine" | "lyrics" | "config";

type ConsoleLogCategory = "gamestate" | "play" | "debugging" | "ingest";
type ConsoleLogLevel = "log" | "warn" | "error";

function serializeConsoleDetails(details: unknown): string | undefined {
  if (typeof details === "undefined") {
    return undefined;
  }
  if (typeof details === "string") {
    return details;
  }
  try {
    return JSON.stringify(details);
  } catch {
    return String(details);
  }
}

function bridgeConsoleLog(level: ConsoleLogLevel, category: ConsoleLogCategory, message: string, details?: unknown): void {
  if (!haveTauri()) return;
  const detailsText = serializeConsoleDetails(details);
  void invoke("frontend_log", {
    level,
    category,
    message,
    details: detailsText ?? null
  }).catch(() => {
    // avoid recursive logging loops on transport failures
  });
}

function logConsole(category: ConsoleLogCategory, message: string, details?: unknown) {
  const tag = `[${category}] ${message}`;
  if (typeof details === "undefined") {
    console.log(tag);
  } else {
    console.log(tag, details);
  }
  bridgeConsoleLog("log", category, message, details);
}

function warnConsole(category: ConsoleLogCategory, message: string, details?: unknown) {
  const tag = `[${category}] ${message}`;
  if (typeof details === "undefined") {
    console.warn(tag);
  } else {
    console.warn(tag, details);
  }
  bridgeConsoleLog("warn", category, message, details);
}

function errorConsole(category: ConsoleLogCategory, message: string, details?: unknown) {
  const tag = `[${category}] ${message}`;
  if (typeof details === "undefined") {
    console.error(tag);
  } else {
    console.error(tag, details);
  }
  bridgeConsoleLog("error", category, message, details);
}

function setRoute(route: Route) {
  const routes = Array.from(document.querySelectorAll<HTMLElement>(".route"));
  for (const el of routes) {
    const r = el.dataset.route as Route | undefined;
    el.classList.toggle("isActive", r === route);
  }

  const navMap: Record<Route, string> = {
    home: "navHome",
    play: "navPlay",
    make: "navMake",
    refine: "navRefine",  // no top-bar nav button for refine yet -- entered via the Cleanup & Edit details pane
    lyrics: "navLyrics",  // no top-bar nav button -- entered via the Cleanup & Edit details pane
    config: "navConfig"
  };

  for (const [r, id] of Object.entries(navMap) as Array<[Route, string]>) {
    document.getElementById(id)?.classList.toggle("isActive", r === route);
  }

  // Keep the experience tidy: stop visuals/audio when leaving Play.
  if (route !== "play") {
    try {
      stopVisualizer();
      transportController.pause();
    } catch {
      // ignore
    }
  }

  // Always scroll to top of content on navigation.
  document.documentElement.scrollTop = 0;
  logConsole("gamestate", `route -> ${route}`);
}

document.getElementById("navHome")?.addEventListener("click", () => setRoute("home"));
document.getElementById("navPlay")?.addEventListener("click", () => setRoute("play"));
document.getElementById("navMake")?.addEventListener("click", () => setRoute("make"));
document.getElementById("navConfig")?.addEventListener("click", () => setRoute("config"));

document.getElementById("homePlay")?.addEventListener("click", () => setRoute("play"));
document.getElementById("homeMake")?.addEventListener("click", () => setRoute("make"));
document.getElementById("homeConfig")?.addEventListener("click", () => setRoute("config"));

// --- Audio/visual sync calibration -------------------------------------
// Same calibrator as the game (the shared @auralprimer/av-sync package): a
// Rock Band-style two-pass tap calibrator measures audio latency (output
// delay, incl. Bluetooth) and video latency (display lag). Both are persisted
// to the shared settings.json so calibrating in either app applies to both.
// The Cleanup & Edit playhead reads the effective offset (audio - video) so
// the cursor sits on what the user actually hears. Nudge audio with Ctrl+[ /
// Ctrl+], reset with Ctrl+0.
const AV_OFFSET_STEP_MS = 5;

function renderStudioAvReadout(): void {
  const fmt = (ms: number) => `${ms > 0 ? "+" : ""}${ms} ms`;
  const a = document.getElementById("avSyncAudioValue");
  const v = document.getElementById("avSyncVideoValue");
  const eff = document.getElementById("avSyncValue");
  if (a) a.textContent = fmt(getAudioOffsetMs());
  if (v) v.textContent = fmt(getVideoOffsetMs());
  if (eff) eff.textContent = fmt(getEffectiveOffsetMs());
}

async function applyStudioAvCalibration(audioMs: number, videoMs: number): Promise<void> {
  await setAvCalibration(audioMs, videoMs);
  renderStudioAvReadout();
}

const avCalibration = initAvCalibration({
  onApply: ({ audioMs, videoMs }) => void applyStudioAvCalibration(audioMs, videoMs),
  getInitial: () => ({ audioMs: getAudioOffsetMs(), videoMs: getVideoOffsetMs() }),
  log: (message, details) => console.log(`[studio] ${message}`, details ?? ""),
});
document.getElementById("avSyncCalibrate")?.addEventListener("click", () => avCalibration.open());
document.getElementById("avSyncReset")?.addEventListener("click", () => void applyStudioAvCalibration(0, 0));

// Load the shared persisted offsets and reflect them in the Configure readout.
void loadAvCalibration().then(renderStudioAvReadout);

window.addEventListener("keydown", (ev) => {
  if (!ev.ctrlKey || ev.metaKey || ev.repeat) return;
  const target = ev.target;
  if (target instanceof HTMLElement) {
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
  }
  // Ctrl+[ / ] / 0 nudge the AUDIO offset (the dominant one); video lag is
  // measured, not tweaked by ear.
  const curAudio = getAudioOffsetMs();
  const curVideo = getVideoOffsetMs();
  if (ev.key === "[") {
    ev.preventDefault();
    void applyStudioAvCalibration(curAudio - AV_OFFSET_STEP_MS, curVideo);
  } else if (ev.key === "]") {
    ev.preventDefault();
    void applyStudioAvCalibration(curAudio + AV_OFFSET_STEP_MS, curVideo);
  } else if (ev.key === "0") {
    ev.preventDefault();
    void applyStudioAvCalibration(0, 0);
  }
});

const hudKeyModeEl = document.getElementById("hudKeyMode") as HTMLDivElement;

const vizCanvas = document.getElementById("viz") as HTMLCanvasElement;
const vizStatusEl = document.getElementById("vizStatus") as HTMLPreElement;
const pluginSelect = document.getElementById("pluginSelect") as HTMLSelectElement;
const pluginRefreshBtn = document.getElementById("pluginRefresh") as HTMLButtonElement;
const vizStartBtn = document.getElementById("vizStart") as HTMLButtonElement;
const vizStopBtn = document.getElementById("vizStop") as HTMLButtonElement;

const playLayoutEl = document.getElementById("playLayout") as HTMLDivElement;
const toggleFocusBtn = document.getElementById("toggleFocus") as HTMLButtonElement;
const playersEl = document.getElementById("players") as HTMLDivElement;
const addPlayerBtn = document.getElementById("addPlayer") as HTMLButtonElement;

const capsEl = document.createElement("div");
capsEl.id = "songCaps";
capsEl.className = "caps";
// Insert just above the viz canvas.
vizCanvas.insertAdjacentElement("beforebegin", capsEl);

const audioLoadBtn = document.getElementById("audioLoad") as HTMLButtonElement;
const audioPlayBtn = document.getElementById("audioPlay") as HTMLButtonElement;
const audioPauseBtn = document.getElementById("audioPause") as HTMLButtonElement;
const audioStopBtn = document.getElementById("audioStop") as HTMLButtonElement;
const audioSeekInput = document.getElementById("audioSeek") as HTMLInputElement;
const audioSeekGoBtn = document.getElementById("audioSeekGo") as HTMLButtonElement;
const loopT0Input = document.getElementById("loopT0") as HTMLInputElement;
const loopT1Input = document.getElementById("loopT1") as HTMLInputElement;
const loopSetBtn = document.getElementById("loopSet") as HTMLButtonElement;
const loopClearBtn = document.getElementById("loopClear") as HTMLButtonElement;
const audioStatusEl = document.getElementById("audioStatus") as HTMLPreElement;
const audioBackendSelect = document.getElementById("audioBackend") as HTMLSelectElement;
const audioOutputHostSelect = document.getElementById("audioOutputHost") as HTMLSelectElement;
const audioOutputHostRefreshBtn = document.getElementById("audioOutputHostRefresh") as HTMLButtonElement;
const audioOutputHostApplyBtn = document.getElementById("audioOutputHostApply") as HTMLButtonElement;
const audioOutputDeviceSelect = document.getElementById("audioOutputDevice") as HTMLSelectElement;
const audioOutputDeviceRefreshBtn = document.getElementById("audioOutputDeviceRefresh") as HTMLButtonElement;
const audioOutputDeviceApplyBtn = document.getElementById("audioOutputDeviceApply") as HTMLButtonElement;
const playbackRateInput = document.getElementById("playbackRate") as HTMLInputElement;
const playbackRateApplyBtn = document.getElementById("playbackRateApply") as HTMLButtonElement;
const metronomeEnabledInput = document.getElementById("metronomeEnabled") as HTMLInputElement;
const metronomeVolumeInput = document.getElementById("metronomeVolume") as HTMLInputElement;

const midiFollowEnabledInput = document.getElementById("midiFollowEnabled") as HTMLInputElement;
const midiInPortSelect = document.getElementById("midiInPort") as HTMLSelectElement;
const midiInRefreshBtn = document.getElementById("midiInRefresh") as HTMLButtonElement;
const midiInConnectBtn = document.getElementById("midiInConnect") as HTMLButtonElement;
const midiInDisconnectBtn = document.getElementById("midiInDisconnect") as HTMLButtonElement;
const midiTempoScaleInput = document.getElementById("midiTempoScale") as HTMLInputElement;
const midiInSysexEnabledInput = document.getElementById("midiInSysexEnabled") as HTMLInputElement;
const midiStatusEl = document.getElementById("midiStatus") as HTMLPreElement;
const midiInEventsEl = document.getElementById("midiInEvents") as HTMLPreElement;

const midiOutEnabledInput = document.getElementById("midiOutEnabled") as HTMLInputElement;
const midiOutPortSelect = document.getElementById("midiOutPort") as HTMLSelectElement;
const midiOutRefreshBtn = document.getElementById("midiOutRefresh") as HTMLButtonElement;
const midiOutSelectBtn = document.getElementById("midiOutSelect") as HTMLButtonElement;
const midiOutStartBtn = document.getElementById("midiOutStart") as HTMLButtonElement;
const midiOutContinueBtn = document.getElementById("midiOutContinue") as HTMLButtonElement;
const midiOutStopBtn = document.getElementById("midiOutStop") as HTMLButtonElement;
const midiOutSysexEnabledInput = document.getElementById("midiOutSysexEnabled") as HTMLInputElement;
const midiMsgChannelInput = document.getElementById("midiMsgChannel") as HTMLInputElement;
const midiMsgNoteInput = document.getElementById("midiMsgNote") as HTMLInputElement;
const midiMsgVelocityInput = document.getElementById("midiMsgVelocity") as HTMLInputElement;
const midiMsgNoteOnBtn = document.getElementById("midiMsgNoteOn") as HTMLButtonElement;
const midiMsgNoteOffBtn = document.getElementById("midiMsgNoteOff") as HTMLButtonElement;
const midiMsgAllNotesOffBtn = document.getElementById("midiMsgAllNotesOff") as HTMLButtonElement;
const midiMsgCcInput = document.getElementById("midiMsgCc") as HTMLInputElement;
const midiMsgCcValueInput = document.getElementById("midiMsgCcValue") as HTMLInputElement;
const midiMsgCcSendBtn = document.getElementById("midiMsgCcSend") as HTMLButtonElement;
const midiOutRawHexInput = document.getElementById("midiOutRawHex") as HTMLInputElement;
const midiOutRawSendBtn = document.getElementById("midiOutRawSend") as HTMLButtonElement;
const midiOutStatusEl = document.getElementById("midiOutStatus") as HTMLPreElement;

const modelsRefreshBtn = document.getElementById("modelsRefresh") as HTMLButtonElement;
const preferredModelsEl = document.getElementById("preferredModels") as HTMLDivElement;
const modelsStatusEl = document.getElementById("modelsStatus") as HTMLPreElement;
const modelpackPathInput = document.getElementById("modelpackPath") as HTMLInputElement;
const modelpackImportBtn = document.getElementById("modelpackImport") as HTMLButtonElement;

const ingestModeSelect = document.getElementById("ingestMode") as HTMLSelectElement;
const ingestSourcePathInput = document.getElementById("ingestSourcePath") as HTMLInputElement;
const ingestBrowseSourceBtn = document.getElementById("ingestBrowseSource") as HTMLButtonElement;
const ingestOutPathInput = document.getElementById("ingestOutPath") as HTMLInputElement;
const ingestProfileInput = document.getElementById("ingestProfile") as HTMLInputElement;
const ingestShiftsInput = document.getElementById("ingestShifts") as HTMLInputElement;
const ingestMultiFilterInput = document.getElementById("ingestMultiFilter") as HTMLInputElement;
const ingestDrumFilterSelect = document.getElementById("ingestDrumFilter") as HTMLSelectElement;
const ingestMelodicMethodSelect = document.getElementById("ingestMelodicMethod") as HTMLSelectElement;
const ingestConfigInput = document.getElementById("ingestConfig") as HTMLInputElement;
const ingestTitleInput = document.getElementById("ingestTitle") as HTMLInputElement;
const ingestArtistInput = document.getElementById("ingestArtist") as HTMLInputElement;
const ingestRunBtn = document.getElementById("ingestRun") as HTMLButtonElement;
const ingestSummaryEl = document.getElementById("ingestSummary") as HTMLDivElement;
const ingestSummaryBadgeEl = document.getElementById("ingestSummaryBadge") as HTMLDivElement;
const ingestSummaryTitleEl = document.getElementById("ingestSummaryTitle") as HTMLDivElement;
const ingestSummaryProgressTextEl = document.getElementById("ingestSummaryProgressText") as HTMLDivElement;
const ingestSummaryProgressEl = document.getElementById("ingestSummaryProgress") as HTMLProgressElement;
const ingestSummaryDetailEl = document.getElementById("ingestSummaryDetail") as HTMLDivElement;
const ingestOpenCleanupBtn = document.getElementById("ingestOpenCleanup") as HTMLButtonElement;
const ingestLogPanelEl = document.getElementById("ingestLogPanel") as HTMLDetailsElement;
const ingestStatusEl = document.getElementById("ingestStatus") as HTMLPreElement;
const ingestRuntimeRefreshBtn = document.getElementById("ingestRuntimeRefresh") as HTMLButtonElement;
const ingestRuntimeStatusEl = document.getElementById("ingestRuntimeStatus") as HTMLDivElement;

// Import route step-1 chooser. The route used to show two rival "import" panels
// side by side with nothing saying which one applied to you; it now asks first
// and reveals only the matching form. Both panels stay in the DOM (hidden, never
// removed) so every getElementById handle above keeps resolving.
type ImportKind = "suno" | "audio" | "stems";

const importChoiceCards = Array.from(
  document.querySelectorAll<HTMLButtonElement>(".importChoiceCard")
);
const importPanelSunoEl = document.getElementById("importPanelSuno") as HTMLElement;
const importPanelAnalysisEl = document.getElementById("importPanelAnalysis") as HTMLElement;
const importAnalysisHeadingEl = document.getElementById("importAnalysisHeading") as HTMLElement;
const importOpenModelsBtn = document.getElementById("importOpenModels") as HTMLButtonElement;

function importAnalysisHeadingFor(kind: ImportKind): string {
  return kind === "stems" ? "Point us at your stems folder" : "Point us at your audio";
}

function markImportKindActive(kind: ImportKind): void {
  for (const card of importChoiceCards) {
    card.classList.toggle("isActive", card.dataset.importKind === kind);
  }
}

function setImportKind(kind: ImportKind): void {
  markImportKindActive(kind);
  importPanelSunoEl.hidden = kind !== "suno";
  importPanelAnalysisEl.hidden = kind === "suno";
  if (kind === "suno") return;

  importAnalysisHeadingEl.textContent = importAnalysisHeadingFor(kind);
  // Keep the mode select in step with the card, but don't fight a user who
  // picked "scan folder" by hand: only correct it when it contradicts the card.
  const wanted = kind === "stems" ? "stem-dir" : "import";
  const contradicts = kind === "stems" ? ingestModeSelect.value !== "stem-dir" : ingestModeSelect.value === "stem-dir";
  if (contradicts) {
    ingestModeSelect.value = wanted;
    ingestModeSelect.dispatchEvent(new Event("change"));
  }
}

/** Keep the cards honest when the mode select is changed directly. */
function syncImportKindFromMode(): void {
  if (!importPanelSunoEl.hidden) return;
  const kind: ImportKind = ingestModeSelect.value === "stem-dir" ? "stems" : "audio";
  markImportKindActive(kind);
  importAnalysisHeadingEl.textContent = importAnalysisHeadingFor(kind);
}
const modelSetupPanelEl = document.getElementById("modelSetupPanel") as HTMLDivElement;
if (haveTauri()) {
  void initModelSetupPanel(modelSetupPanelEl);
} else {
  modelSetupPanelEl.innerHTML = `<div class="meta">Model setup is available in the desktop app.</div>`;
}

const stemMidiPickFolderBtn = document.getElementById("stemMidiPickFolderMake") as HTMLButtonElement;
const stemMidiImportBtn = document.getElementById("stemMidiImportMake") as HTMLButtonElement;
const stemMidiFolderLabel = document.getElementById("stemMidiFolderLabelMake") as HTMLDivElement;
const stemMidiSummaryEl = document.getElementById("stemMidiSummaryMake") as HTMLDivElement;
const stemMidiStatusEl = document.getElementById("stemMidiStatusMake") as HTMLPreElement;
// Post-import success card -- shown after the Suno import succeeds so the
// user has clear next-step actions (refine workspace / candidate precompute
// / cleanup) instead of a status-log dead-end.
const stemMidiNextStepsEl = document.getElementById("stemMidiNextStepsMake") as HTMLElement;
const stemMidiOpenRefineBtn = document.getElementById("stemMidiOpenRefine") as HTMLButtonElement;
const stemMidiPrecomputeBtn = document.getElementById("stemMidiPrecomputeRefine") as HTMLButtonElement;
const stemMidiOpenCleanupBtn = document.getElementById("stemMidiOpenCleanup") as HTMLButtonElement;
const stemMidiPrecomputeStatusEl = document.getElementById("stemMidiPrecomputeStatus") as HTMLPreElement;

const statusEl = document.getElementById("status") as HTMLPreElement;
const listEl = document.getElementById("list") as HTMLDivElement;
const detailsEl = document.getElementById("details") as HTMLDivElement;
const refreshBtn = document.getElementById("refresh") as HTMLButtonElement;
const songsFolderInput = document.getElementById("songsFolder") as HTMLInputElement;
const setOverrideBtn = document.getElementById("setOverride") as HTMLButtonElement;
const clearOverrideBtn = document.getElementById("clearOverride") as HTMLButtonElement;

const auralsongEditorStatusEl = document.getElementById("auralsongEditorStatus") as HTMLPreElement;
const cleanupActionEl = document.getElementById("cleanupAction") as HTMLDivElement;

// "Prep all unbuilt" -- full prep: for every listed song that's missing
// either artifact, build the spectrogram AND run candidate precompute so it
// reaches the "Open" state in one click. Sequential (CPU-heavy); candidate
// precompute runs several transcription algorithms per melodic instrument, so
// this is the slow path. Each step only runs for the roles that actually lack
// it, and candidates are skipped for a stem-less pack.
const cleanupBuildAllBtn = document.getElementById("cleanupBuildAll") as HTMLButtonElement | null;
const cleanupBuildAllStatusEl = document.getElementById("cleanupBuildAllStatus") as HTMLSpanElement | null;

async function refreshRowReadyChip(path: string): Promise<void> {
  try {
    applyRowReadiness(path, await probeRowReadiness(path));
  } catch {
    /* leave the row's status cells as-is on probe failure */
  }
}

cleanupBuildAllBtn?.addEventListener("click", async () => {
  if (!cleanupBuildAllBtn) return;
  const setStatus = (m: string) => {
    if (cleanupBuildAllStatusEl) cleanupBuildAllStatusEl.textContent = m;
  };
  const rows = Array.from(listEl.querySelectorAll<HTMLTableRowElement>("tr.cleanupSongRow:not(.isInvalid)"));
  setStatus("Checking…");
  // Per song, collect the roles missing a spectrogram and the roles missing
  // candidates, so each step only runs where it's actually needed.
  const todo: {
    path: string;
    title: string;
    specRoles: string[];
    candRoles: string[];
    needsPrep: boolean;
  }[] = [];
  for (const row of rows) {
    const path = row.getAttribute("data-path");
    if (!path) continue;
    // Step 0: a manifest pack (sloppak/feedpak) whose melodic notes.mid hasn't
    // been derived from its arrangements yet needs prep BEFORE spectrogram.
    const needsPrep = await packNeedsArrangementPrep(path);
    const { roles } = await detectMelodicStems(path);
    const specRoles: string[] = [];
    const candRoles: string[] = [];
    for (const role of roles) {
      const rr = await getRoleReadiness(path, role, { force: true });
      if (!rr.spectrogram) specRoles.push(role);
      if (!rr.candidates) candRoles.push(role);
    }
    // Drums: packs with a drum chart get a drums spectrogram for the Refine
    // lane editor. Spectrogram only — drums have no candidate system (the
    // drum tab is the source of truth), so candRoles never includes drums.
    try {
      if (await auralsongJsonExists(path, await drumTabRelPath(path))) {
        const fd = featureDir(path);
        const hasDrumSpec = await auralsongJsonExists(path, `${fd}/spectrogram/drums/spectrogram.json`);
        if (!hasDrumSpec) specRoles.push("drums");
      }
    } catch {
      /* skip drums on probe failure */
    }
    if (needsPrep || specRoles.length || candRoles.length) {
      const title = row.querySelector(".cleanupSongTitle")?.textContent ?? path;
      todo.push({ path, title, specRoles, candRoles, needsPrep });
    }
  }
  if (todo.length === 0) {
    setStatus("All songs fully prepped (spectrogram + candidates).");
    return;
  }
  cleanupBuildAllBtn.disabled = true;
  cleanupBuildAllBtn.textContent = "Prepping…";
  cleanupBuildAllStatusEl?.classList.add("isBusy");
  let prepBuilt = 0; // arrangement→notes.mid derivations
  let specBuilt = 0;
  let candBuilt = 0;
  let candSkipped = 0; // silent stems (no audible content) — benign, not failed
  let noStem = 0;
  let failed = 0;
  const tally = (): string => {
    const parts: string[] = [];
    if (prepBuilt) parts.push(`${prepBuilt} notes`);
    if (specBuilt) parts.push(`${specBuilt} spec`);
    if (candBuilt) parts.push(`${candBuilt} cand`);
    if (candSkipped) parts.push(`${candSkipped} silent`);
    if (noStem) parts.push(`${noStem} skipped`);
    if (failed) parts.push(`${failed} failed`);
    return parts.length ? ` · ${parts.join(", ")}` : "";
  };
  for (let i = 0; i < todo.length; i++) {
    const t = todo[i]!;
    markRowBuilding(t.path);
    let stemless = false;
    // 0) Derive melodic notes.mid from arrangements (sloppak/feedpak) before
    //    anything else, so the melodic highway exists in the primer.
    if (t.needsPrep) {
      setStatus(`Prepping ${i + 1}/${todo.length}: ${t.title} — deriving notes…${tally()}`);
      const out = await prepArrangementsForSong(t.path);
      if (out.ok) prepBuilt += 1;
      else failed += 1;
    }
    // 1) Spectrogram (only the roles that lack one).
    if (t.specRoles.length) {
      setStatus(`Building ${i + 1}/${todo.length}: ${t.title} — spectrogram…${tally()}`);
      let outcome: SpectroOutcome = "error";
      try {
        const res = await safeInvoke<SidecarRunResult>("ingest_spectrogram", {
          req: { container_path: t.path, instruments: t.specRoles },
        });
        outcome = classifySpectroResult(res);
      } catch {
        outcome = "error";
      }
      if (outcome === "ok") specBuilt += 1;
      else if (outcome === "nostem") {
        noStem += 1;
        stemless = true;
      } else failed += 1;
    }
    // 2) Candidate precompute (only the roles that lack candidates). Skipped
    //    for a stem-less pack, where there's nothing to transcribe.
    if (t.candRoles.length && !stemless) {
      setStatus(`Prepping ${i + 1}/${todo.length}: ${t.title} — candidates (${t.candRoles.join(", ")})…${tally()}`);
      try {
        const res = await safeInvoke<SidecarRunResult>("ingest_refine_candidates", {
          req: { container_path: t.path, instruments: t.candRoles },
        });
        // A silent stem ("no audible content" after the silence gate) is a
        // benign SKIP, not a failure — band songs carry an empty keys stem
        // with nothing to transcribe. Only real errors count as failed.
        const outcome = classifyCandidateResult(res, t.candRoles);
        if (outcome.built.length) candBuilt += 1;
        candSkipped += outcome.skipped.length;
        if (outcome.failed.length) failed += 1;
      } catch {
        failed += 1;
      }
    }
    invalidateCleanupCache(t.path);
    await refreshRowReadyChip(t.path);
    listEl
      .querySelector(`tr.cleanupSongRow[data-path="${cssEscape(t.path)}"]`)
      ?.classList.remove("isBuilding");
  }
  cleanupBuildAllBtn.disabled = false;
  cleanupBuildAllBtn.textContent = "Prep all unbuilt";
  cleanupBuildAllStatusEl?.classList.remove("isBusy");
  const parts = [
    `Built ${specBuilt} spectrogram${specBuilt === 1 ? "" : "s"}`,
    `${candBuilt} candidate set${candBuilt === 1 ? "" : "s"}`,
  ];
  if (prepBuilt) parts.unshift(`Derived ${prepBuilt} note set${prepBuilt === 1 ? "" : "s"}`);
  if (candSkipped) parts.push(`${candSkipped} silent stem${candSkipped === 1 ? "" : "s"} skipped`);
  if (noStem) parts.push(`${noStem} with no melodic stem`);
  if (failed) parts.push(`${failed} failed`);
  setStatus(`${parts.join(", ")}.`);
  // Refresh the open action panel so its readiness/buttons reflect the new state.
  if (selectedAuralSongPath) {
    void selectAuralSong(selectedAuralSongPath, { autoLoadAudio: false });
  }
});

// Disable desktop-only actions when running without the Tauri runtime.
if (!haveTauri()) {
  setOverrideBtn.disabled = true;
  clearOverrideBtn.disabled = true;

  stemMidiPickFolderBtn.disabled = true;
  stemMidiImportBtn.disabled = true;

  ingestBrowseSourceBtn.disabled = true;
  ingestRunBtn.disabled = true;
  ingestRuntimeRefreshBtn.disabled = true;

  midiInPortSelect.disabled = true;
  midiInRefreshBtn.disabled = true;
  midiInConnectBtn.disabled = true;
  midiInDisconnectBtn.disabled = true;
  midiTempoScaleInput.disabled = true;
  midiInSysexEnabledInput.disabled = true;

  midiOutEnabledInput.disabled = true;
  midiOutPortSelect.disabled = true;
  midiOutRefreshBtn.disabled = true;
  midiOutSelectBtn.disabled = true;
  midiOutStartBtn.disabled = true;
  midiOutContinueBtn.disabled = true;
  midiOutStopBtn.disabled = true;
  midiOutSysexEnabledInput.disabled = true;
  midiMsgChannelInput.disabled = true;
  midiMsgNoteInput.disabled = true;
  midiMsgVelocityInput.disabled = true;
  midiMsgNoteOnBtn.disabled = true;
  midiMsgNoteOffBtn.disabled = true;
  midiMsgAllNotesOffBtn.disabled = true;
  midiMsgCcInput.disabled = true;
  midiMsgCcValueInput.disabled = true;
  midiMsgCcSendBtn.disabled = true;
  midiOutRawHexInput.disabled = true;
  midiOutRawSendBtn.disabled = true;

  audioOutputHostSelect.disabled = true;
  audioOutputHostRefreshBtn.disabled = true;
  audioOutputHostApplyBtn.disabled = true;

  audioOutputDeviceSelect.disabled = true;
  audioOutputDeviceRefreshBtn.disabled = true;
  audioOutputDeviceApplyBtn.disabled = true;
}

function renderPlugins() {
  // Base render; actual availability gating happens once we know selected song details.
  renderPluginsWithAvailability(selectedAuralSongDetails);
}

async function refreshPlugins() {
  // Always include package-based built-ins.
  availablePlugins = [...BUILTIN_PLUGINS];

  try {
    // Bundled built-ins (resources).
    const bundled = await scanBundledPlugins();

    // User plugins from configured visualizers folder.
    const user = await scanUserPlugins();

    // Merge, dedup by id: prefer bundled over package over user.
    const byId = new Map<string, PluginDescriptor>();

    for (const p of availablePlugins) byId.set(p.id, p);
    for (const p of bundled) byId.set(p.id, p);
    for (const p of user) if (!byId.has(p.id)) byId.set(p.id, p);

    availablePlugins = Array.from(byId.values());
  } catch (e) {
    // This will fail in browser-only mode (no Tauri). That's ok.
    setVizStatus(`plugin scan failed (expected in browser-only mode): ${String(e)}`);
  }

  // Sort: built-ins first, then user.
  availablePlugins.sort((a, b) => {
    if (a.source !== b.source) return a.source === "builtin" ? -1 : 1;
    return a.id.localeCompare(b.id);
  });

  renderPlugins();
}

function escapeHtml(s: string): string {
  // Avoid hardcoding HTML entity strings here (some tooling auto-decodes them).
  // Using DOM encoding keeps this correct and simple.
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

function yesNo(v: boolean): string {
  return v ? "yes" : "no";
}

// Escape a value for safe use inside a CSS attribute selector. Container paths
// hold backslashes + colons on Windows, which break unescaped selectors.
function cssEscape(s: string): string {
  const cssApi = (window as unknown as { CSS?: { escape?: (v: string) => string } }).CSS;
  if (cssApi?.escape) return cssApi.escape(s);
  return s.replace(/["\\\]:.]/g, "\\$&");
}

function formatModelPackLicense(pack: InstalledModelPack): string {
  if (typeof pack.license === "string") return pack.license;
  if (pack.license && typeof pack.license === "object") {
    const record = pack.license as Record<string, unknown>;
    for (const key of ["name", "id", "spdx", "text"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return "not declared";
}

function formatInstalledModelPacks(installed: InstalledModelPack[]): string {
  if (!installed.length) return "(no model packs installed)";
  return installed
    .map((pack) => {
      const lines = [
        `${pack.id}@${pack.version}${pack.ok ? "" : " [invalid]"}`,
        `  root: ${pack.root_dir}`,
        `  license: ${formatModelPackLicense(pack)}`
      ];
      if (pack.license_path) lines.push(`  license_path: ${pack.license_path}`);
      if (pack.error) lines.push(`  error: ${pack.error}`);
      return lines.join("\n");
    })
    .join("\n\n");
}

const MT3_ENGINE_LABELS: Record<string, string> = {
  mr_mt3_drums: "MR-MT3 (research)",
  yourmt3_drums: "YourMT3 (research)"
};

function runtimeBadge(label: string, found: boolean): string {
  const cls = found ? "importAuditStatus importAuditStatus--found" : "importAuditStatus importAuditStatus--missing";
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

function setMt3EngineOptionStatus(engineId: string, status: "checking" | "detected" | "missing", detail = "") {
  const option = ingestDrumFilterSelect.querySelector(`option[value="${engineId}"]`) as HTMLOptionElement | null;
  if (!option) return;
  const baseLabel = MT3_ENGINE_LABELS[engineId] ?? engineId;
  if (status === "checking") {
    option.disabled = true;
    option.textContent = `${baseLabel} (checking…)`;
    return;
  }
  option.disabled = status === "missing";
  const suffix = status === "detected" ? "installed" : detail || "needs model pack";
  option.textContent = `${baseLabel} (${suffix})`;
}

function renderMt3RuntimeState(
  result: IngestRuntimeCheckResult | null,
  options: { loading?: boolean; error?: string } = {}
) {
  if (!haveTauri()) {
    ingestRuntimeStatusEl.innerHTML = `<div class="meta">Optional engine status can only be checked in the desktop app.</div>`;
    setMt3EngineOptionStatus("mr_mt3_drums", "missing", "needs the desktop app");
    setMt3EngineOptionStatus("yourmt3_drums", "missing", "needs the desktop app");
    return;
  }

  if (options.loading) {
    ingestRuntimeStatusEl.innerHTML = `<div class="meta">Checking packaged sidecar runtime and local modelpacks…</div>`;
    setMt3EngineOptionStatus("mr_mt3_drums", "checking");
    setMt3EngineOptionStatus("yourmt3_drums", "checking");
    return;
  }

  if (options.error) {
    ingestRuntimeStatusEl.innerHTML = `<pre class="error">${escapeHtml(options.error)}</pre>`;
    setMt3EngineOptionStatus("mr_mt3_drums", "missing", "check failed");
    setMt3EngineOptionStatus("yourmt3_drums", "missing", "check failed");
    return;
  }

  const payload = result?.payload;
  const dependencyEntries = Object.entries(payload?.dependencies ?? {});
  const engineEntries = Object.entries(payload?.drum_engines ?? {});
  const runtimeOk = Boolean(result?.ok && payload?.ok);

  for (const engineId of Object.keys(MT3_ENGINE_LABELS)) {
    setMt3EngineOptionStatus(engineId, "missing", "needs model pack");
  }

  const depsHtml = dependencyEntries.length
    ? dependencyEntries
        .map(([name, dep]) => {
          // An optional backend that isn't installed is expected, not a
          // failure — e.g. Basic Pitch's TensorFlow backend when we ship and
          // run the ONNX one. Only a MISSING REQUIRED dependency earns the red
          // badge; optional ones read as informational so the panel doesn't
          // cry wolf about something we deliberately don't bundle.
          const optional = dep.required === false;
          const badgeFound = dep.ok || optional;
          const badgeLabel = dep.ok
            ? "Installed"
            : optional
              ? "Optional — not installed"
              : "Not installed";
          const note = dep.version
            ? `v${dep.version}`
            : !dep.ok && optional && dep.missing_behavior
              ? dep.missing_behavior
              : dep.error ?? "";
          return `
            <div class="mt3RuntimeRow">
              <div class="mt3RuntimeLabel">${escapeHtml(name)}</div>
              <div>${runtimeBadge(badgeLabel, badgeFound)}</div>
              <div class="meta mt3RuntimeNote">${escapeHtml(note)}</div>
            </div>
          `;
        })
        .join("\n")
    : `<div class="meta">No dependency information returned.</div>`;

  const enginesHtml = engineEntries.length
    ? engineEntries
        .map(([engineId, engine]) => {
          const detected = Boolean(engine.ok && engine.loadable && engine.transcribe_smoke_ok);
          const notes = [
            engine.modelpack_version ? `pack ${engine.modelpack_version}` : "",
            engine.error ? engine.error : ""
          ]
            .filter(Boolean)
            .join(" | ");
          setMt3EngineOptionStatus(engineId, detected ? "detected" : "missing", detected ? "" : "needs model pack");
          return `
            <div class="mt3RuntimeRow">
              <div class="mt3RuntimeLabel">${escapeHtml(MT3_ENGINE_LABELS[engineId] ?? engineId)}</div>
              <div>${runtimeBadge(detected ? "Installed" : "Not installed", detected)}</div>
              <div class="meta mt3RuntimeNote">${escapeHtml(notes || "runtime ready")}</div>
            </div>
          `;
        })
        .join("\n")
    : `<div class="meta">No MT3 engines were reported.</div>`;

  ingestRuntimeStatusEl.innerHTML = `
    <div class="mt3RuntimeGrid">
      <div class="mt3RuntimeCard">
        <div class="meta mt3RuntimeTitle">Dependencies</div>
        ${depsHtml}
      </div>
      <div class="mt3RuntimeCard">
        <div class="meta mt3RuntimeTitle">Drum engines</div>
        ${enginesHtml}
      </div>
    </div>
    <div class="meta mt3RuntimeFootnote">
      ${runtimeOk ? "Status checked against the packaged sidecar runtime." : "Some optional engines are not ready. Install their model pack above."}
    </div>
  `;
}

/**
 * Last successful runtime-check, cached so the panel paints immediately.
 * A cold sidecar start costs ~56s; without this the panel shows nothing but
 * "Checking…" long enough that it reads as a hang.
 */
const INGEST_RUNTIME_CACHE_KEY = "auralstudio.ingestRuntimeCheck.v1";

function readCachedRuntimeCheck(): IngestRuntimeCheckResult | null {
  try {
    const raw = localStorage.getItem(INGEST_RUNTIME_CACHE_KEY);
    return raw ? (JSON.parse(raw) as IngestRuntimeCheckResult) : null;
  } catch {
    return null;
  }
}

async function refreshIngestRuntimeStatus() {
  const cached = readCachedRuntimeCheck();
  if (cached?.payload?.ok) renderMt3RuntimeState(cached);
  else renderMt3RuntimeState(null, { loading: true });
  ingestRuntimeRefreshBtn.disabled = true;
  try {
    const result = await safeInvoke<IngestRuntimeCheckResult>("ingest_runtime_check");
    if (!result.ok || !result.payload?.ok) {
      const message = result.stderr || result.stdout || `runtime-check failed with exit code ${result.exit_code}`;
      renderMt3RuntimeState(result, { error: message });
      return;
    }
    renderMt3RuntimeState(result);
    try {
      localStorage.setItem(INGEST_RUNTIME_CACHE_KEY, JSON.stringify(result));
    } catch {
      /* quota / private mode — the panel just loses its warm start */
    }
  } catch (e) {
    renderMt3RuntimeState(null, { error: String(e) });
  } finally {
    ingestRuntimeRefreshBtn.disabled = false;
  }
}

function setHudKeyMode(manifestRaw: unknown, artifacts?: { keys?: unknown | null; harmony?: unknown | null }) {
  const km = extractKeyModeFromManifest(manifestRaw, artifacts);
  hudKeyModeEl.textContent = `${km.key} ${km.mode}`;
}

function renderDetails(details: AuralSongDetails) {
  const title = details.manifest_summary?.title ?? "(missing title)";
  const artist = details.manifest_summary?.artist ?? "";

  const raw = details.manifest_raw ? JSON.stringify(details.manifest_raw, null, 2) : "(no manifest)";

  detailsEl.innerHTML = `
    <h3>Details</h3>
    <div class="meta">${escapeHtml(details.kind)} | ${escapeHtml(details.container_path)}</div>

    <h4>${escapeHtml(title)} ${escapeHtml(artist)}</h4>

    ${details.error ? `<pre class="error">${escapeHtml(details.error)}</pre>` : ""}

    <h4>Features</h4>
    <ul>
      <li>beats: ${escapeHtml(yesNo(details.has_beats))}</li>
      <li>tempo_map: ${escapeHtml(yesNo(details.has_tempo_map))}</li>
      <li>sections: ${escapeHtml(yesNo(details.has_sections))}</li>
      <li>events: ${escapeHtml(yesNo(details.has_events))}</li>
      <li>lyrics: ${escapeHtml(yesNo(Boolean(details.has_lyrics)))}</li>
      <li>song_timeline: ${escapeHtml(yesNo(Boolean(details.has_song_timeline)))}</li>
      <li>drum_tab: ${escapeHtml(yesNo(Boolean(details.has_drum_tab)))}</li>
      <li>keys: ${escapeHtml(yesNo(Boolean(details.has_keys)))}</li>
      <li>harmony: ${escapeHtml(yesNo(Boolean(details.has_harmony)))}</li>
      <li>vocal_pitch: ${escapeHtml(yesNo(Boolean(details.has_vocal_pitch)))}</li>
      <li>vocal_pitch_contour: ${escapeHtml(yesNo(Boolean(details.has_vocal_pitch_contour)))}</li>
      <li>aural_fingering: ${escapeHtml(yesNo(Boolean(details.has_aural_fingering)))}</li>
    </ul>

    <h4>Audio</h4>
    <ul>
      <li>stem.mp3: ${escapeHtml(yesNo(details.has_mix_mp3))}</li>
      <li>stem.ogg: ${escapeHtml(yesNo(details.has_mix_ogg))}</li>
      <li>stem.wav: ${escapeHtml(yesNo(Boolean(details.has_mix_wav)))}</li>
    </ul>

    <h4>Charts</h4>
    ${details.charts.length ? `<ul>${details.charts.map((c) => `<li>${escapeHtml(c)}</li>`).join("\n")}</ul>` : "(none)"}

    <h4>Refine</h4>
    <div class="row">
      <button id="openRefineWorkspace" data-auralsong-path="${escapeHtml(details.container_path)}">Open in Refine workspace</button>
      <span class="meta">Per-region candidate cleanup. Run <code>aural_ingest refine-candidates &lt;auralsong&gt; --instrument keys</code> first.</span>
    </div>

    <h4>Lyric Timing</h4>
    <div class="row">
      <button id="openLyricTiming" data-auralsong-path="${escapeHtml(details.container_path)}">Open Lyric Timing editor</button>
      <span class="meta">DAW-style lyric/syllable timing editor. Generate lyrics first if this song has none.</span>
    </div>

    <h4>${escapeHtml(isManifestPack(details.container_path) ? "manifest.yaml" : "manifest.json")}</h4>
    <pre>${escapeHtml(raw)}</pre>
  `;
  // Wire the Refine button after innerHTML replaces it.
  const refineBtn = document.getElementById("openRefineWorkspace") as HTMLButtonElement | null;
  if (refineBtn) {
    refineBtn.addEventListener("click", () => {
      const path = refineBtn.getAttribute("data-auralsong-path") || "";
      if (!path) return;
      setRoute("refine");
      void refineWorkspace?.openForAuralSong(path);
    });
  }
  // Wire the Lyric Timing button after innerHTML replaces it.
  const lyricBtn = document.getElementById("openLyricTiming") as HTMLButtonElement | null;
  if (lyricBtn) {
    lyricBtn.addEventListener("click", () => {
      const path = lyricBtn.getAttribute("data-auralsong-path") || "";
      if (!path) return;
      setRoute("lyrics");
      void lyricTimingWorkspace?.openForAuralSong(path);
    });
  }
}

// -----------------
// Cleanup & Edit — readiness + action panel
// -----------------

// Readiness + stem-detection logic (MELODIC_ROLES, getRoleReadiness,
// melodicStemRoles, detectMelodicStems, classifySpectroResult, …) lives in
// ./cleanupReadiness, extracted so it can be unit-tested without booting the
// app. Imported at the top of this file.

function statusIcon(ok: boolean): string {
  return ok
    ? `<i class="ti ti-circle-check cleanupOk" aria-hidden="true"></i>`
    : `<i class="ti ti-circle-dashed cleanupPending" aria-hidden="true"></i>`;
}

// Currently selected melodic stem for the cleanup action panel.
let cleanupSelectedRole = "keys";

async function renderCleanupAction(details: AuralSongDetails): Promise<void> {
  const pack = details.container_path;
  const title = details.manifest_summary?.title ?? "(missing title)";
  const artist = details.manifest_summary?.artist ?? "";

  cleanupActionEl.innerHTML = `<p class="meta">Checking readiness…</p>`;

  const { roles, primary, readiness } = await detectMelodicStems(pack);
  if (!roles.includes(cleanupSelectedRole)) cleanupSelectedRole = primary;
  const role = cleanupSelectedRole;
  const r = readiness.get(role) ?? (await getRoleReadiness(pack, role));

  const roleLabel = (x: string) => MELODIC_ROLE_LABELS[x] ?? x;

  const optionsHtml = roles
    .map(
      (rr) =>
        `<option value="${escapeHtml(rr)}" ${rr === role ? "selected" : ""}>${escapeHtml(roleLabel(rr))}</option>`,
    )
    .join("");

  const bothReady = r.spectrogram && r.candidates;
  // Manifest packs (sloppak/feedpak) derive their melodic gameplay notes from
  // arrangement wire JSONs — offer that when the pack declares arrangements but
  // has no aural/notes.mid yet (consumes the Rust details fields defensively).
  const canPrepNotes = needsArrangementPrep(
    pack,
    details as unknown as Parameters<typeof needsArrangementPrep>[1],
  );
  // Drums use the pack-root drum_tab (authored at import / in the lane editor),
  // not the melodic candidate system — so relabel that readout row and drop the
  // "Compute candidates" action for them.
  const isDrums = role === "drums";

  cleanupActionEl.innerHTML = `
    <div class="cleanupHeader">
      <h3 class="cleanupTitle">${escapeHtml(title)}</h3>
      <div class="meta cleanupArtist">${escapeHtml(artist || "(unknown artist)")}</div>
      <div class="meta cleanupPath" title="${escapeHtml(pack)}">${escapeHtml(pack)}</div>
    </div>

    <div class="cleanupInstRow">
      <label class="meta" for="cleanupInstrument">Instrument</label>
      <select id="cleanupInstrument">${optionsHtml}</select>
    </div>

    <div class="cleanupReadout">
      <div class="cleanupReadoutRow">
        ${statusIcon(r.spectrogram)}
        <div class="cleanupReadoutText">
          <div class="cleanupReadoutTitle">Spectrogram overlay</div>
          <div class="meta">the pitch view you edit against</div>
        </div>
        <div class="cleanupReadoutState ${r.spectrogram ? "isReady" : ""}">${r.spectrogram ? "built" : "not built yet"}</div>
      </div>
      <div class="cleanupReadoutRow">
        ${statusIcon(r.candidates)}
        <div class="cleanupReadoutText">
          <div class="cleanupReadoutTitle">${isDrums ? "Drum tab" : "Note candidates"}</div>
          <div class="meta">${isDrums ? "the kit hits you clean up" : "the transcription you clean up"}</div>
        </div>
        <div class="cleanupReadoutState ${r.candidates ? "isReady" : ""}">${r.candidates ? "ready" : isDrums ? "no drum tab" : "not computed yet"}</div>
      </div>
    </div>

    <div class="cleanupActions">
      ${canPrepNotes ? `<button id="cleanupPrepNotes" class="cleanupPrimary">
        <i class="ti ti-music" aria-hidden="true"></i> Derive notes from arrangements
      </button>` : ""}
      <button id="cleanupBuildSpectro" class="${r.spectrogram ? "" : "cleanupPrimary"}">
        <i class="ti ti-photo" aria-hidden="true"></i> ${r.spectrogram ? "Rebuild spectrogram" : "Build spectrogram"}
      </button>
      ${isDrums ? "" : `<button id="cleanupComputeCandidates" class="${r.candidates ? "" : "cleanupPrimary"}">
        <i class="ti ti-wand" aria-hidden="true"></i> Compute candidates
      </button>`}
      <button id="cleanupOpenEditor" class="${bothReady ? "cleanupPrimary" : ""}" ${bothReady ? "" : "disabled"}>
        <i class="ti ti-edit" aria-hidden="true"></i> Open cleanup editor
      </button>
    </div>
    <pre id="cleanupRunStatus" class="meta cleanupRunStatus" style="display:none"></pre>

    <div class="cleanupTools">
      <div class="meta cleanupToolsLabel">Tools</div>
      <div class="row">
        <button id="auralsongRefreshSelection">Reload selection</button>
        <button id="auralsongGenerateLyrics">Generate lyrics.json from .txt</button>
      </div>
    </div>
  `;

  const runStatusEl = document.getElementById("cleanupRunStatus") as HTMLPreElement;
  const instSelect = document.getElementById("cleanupInstrument") as HTMLSelectElement;
  const buildSpectroBtn = document.getElementById("cleanupBuildSpectro") as HTMLButtonElement;
  const computeBtn = document.getElementById("cleanupComputeCandidates") as HTMLButtonElement | null;
  const openEditorBtn = document.getElementById("cleanupOpenEditor") as HTMLButtonElement;
  const reloadBtn = document.getElementById("auralsongRefreshSelection") as HTMLButtonElement;
  const lyricsBtn = document.getElementById("auralsongGenerateLyrics") as HTMLButtonElement;
  const prepNotesBtn = document.getElementById("cleanupPrepNotes") as HTMLButtonElement | null;

  instSelect.addEventListener("change", () => {
    cleanupSelectedRole = instSelect.value || primary;
    void renderCleanupAction(details);
  });

  const setRunStatus = (msg: string) => {
    runStatusEl.style.display = "block";
    runStatusEl.textContent = msg;
  };

  const reRenderAfterRun = async () => {
    invalidateCleanupCache(pack);
    await renderCleanupAction(details);
  };

  prepNotesBtn?.addEventListener("click", async () => {
    prepNotesBtn.disabled = true;
    setRunStatus("Deriving melodic notes from arrangements…");
    try {
      const out = await prepArrangementsForSong(pack);
      setRunStatus(out.msg);
    } finally {
      // Re-probe the readiness cache so the row chip + this panel reflect the
      // freshly written notes.mid (and drop the "Prep notes" action).
      await refreshRowReadyChip(pack);
      await reRenderAfterRun();
    }
  });

  buildSpectroBtn.addEventListener("click", async () => {
    buildSpectroBtn.disabled = true;
    setRunStatus(`Building spectrogram for ${roleLabel(role)}…`);
    try {
      const res = await safeInvoke<SidecarRunResult>("ingest_spectrogram", {
        req: { container_path: pack, instruments: roles },
      });
      if (res.ok) {
        const parsed = parseSidecarStatusLine(res.stdout);
        const rolesObj = (parsed?.roles ?? {}) as Record<string, { n_frames?: number }>;
        const summary = Object.entries(rolesObj)
          .map(([k, v]) => `${roleLabel(k)}=${v?.n_frames ?? "?"}f`)
          .join(", ");
        setRunStatus(`Spectrogram built: ${summary || `exit ${res.exit_code}`}`);
      } else {
        const tail = res.stderr.trim().split(/\r?\n/).slice(-3).join("\n");
        setRunStatus(`Build failed (exit ${res.exit_code}):\n${tail || "(no stderr)"}`);
      }
    } catch (e) {
      setRunStatus(`Build failed: ${String(e)}`);
    } finally {
      await reRenderAfterRun();
    }
  });

  computeBtn?.addEventListener("click", async () => {
    computeBtn.disabled = true;
    setRunStatus(`Computing candidates for ${roleLabel(role)}…`);
    try {
      const res = await safeInvoke<SidecarRunResult>("ingest_refine_candidates", {
        req: { container_path: pack, instruments: [role] },
      });
      if (res.ok) {
        const parsed = parseSidecarStatusLine(res.stdout);
        const instsObj = (parsed?.instruments ?? {}) as Record<string, { regions?: number }>;
        const summary = Object.entries(instsObj)
          .map(([k, v]) => `${roleLabel(k)}=${v?.regions ?? "?"}r`)
          .join(", ");
        setRunStatus(`Candidates ready: ${summary || `exit ${res.exit_code}`}`);
      } else {
        const tail = res.stderr.trim().split(/\r?\n/).slice(-3).join("\n");
        setRunStatus(`Compute failed (exit ${res.exit_code}):\n${tail || "(no stderr)"}`);
      }
    } catch (e) {
      setRunStatus(`Compute failed: ${String(e)}`);
    } finally {
      await reRenderAfterRun();
    }
  });

  openEditorBtn.addEventListener("click", () => {
    if (openEditorBtn.disabled) return;
    cleanupSelectedRole = role;
    setRoute("refine");
    void refineWorkspace.openForAuralSong(pack, { instrument: role });
  });

  reloadBtn.addEventListener("click", () => {
    if (!selectedAuralSongPath) {
      setAuralSongEditorStatus("Select an AuralSong first");
      return;
    }
    void selectAuralSong(selectedAuralSongPath, { autoLoadAudio: false }).catch((e) =>
      setAuralSongEditorStatus(String(e)),
    );
  });

  lyricsBtn.addEventListener("click", () => {
    void generateLyricsForSelectedAuralSong().catch((e) => setAuralSongEditorStatus(String(e)));
  });
}

// -----------------
// Cleanup & Edit status table — per-row readiness + inline actions
// -----------------

// Last-probed readiness per pack, so the table can re-sort without re-probing.
const cleanupRowReady = new Map<string, RowReady>();

// Probe a song's primary melodic stem (spectrogram + candidates) plus whether
// it has timed lyrics — the three status columns of the Cleanup & Edit table.
async function probeRowReadiness(path: string): Promise<RowReady> {
  const { primary, readiness } = await detectMelodicStems(path);
  const r = readiness.get(primary) ?? (await getRoleReadiness(path, primary));
  // Fetch details once so both the lyrics-pointer resolution (sloppak lyrics
  // live at pack root via the manifest key) and the arrangement-prep check
  // share the same read.
  let details: AuralSongDetails | null = null;
  try {
    details = await safeInvoke<AuralSongDetails>("get_auralsong_details", { containerPath: path });
  } catch {
    details = null;
  }
  const lyr = await auralsongJsonExists(path, lyricsReadRelPath(path, details));
  const prep = isManifestPack(path)
    ? needsArrangementPrep(path, details as unknown as Parameters<typeof needsArrangementPrep>[1])
    : false;
  return { spec: r.spectrogram, cand: r.candidates, lyr, prep };
}

function ctStatCell(state: "yes" | "no" | "pending"): string {
  if (state === "yes") return `<i class="ti ti-circle-check cleanupOk" aria-hidden="true"></i>`;
  if (state === "no") return `<span class="ctDash">—</span>`;
  return `<span class="ctDash ctPending" aria-hidden="true">·</span>`;
}

// Fill a row's status cells + contextual action button once readiness is known.
// No-op if the row isn't in the DOM (e.g. filtered out).
function applyRowReadiness(path: string, r: RowReady): void {
  cleanupRowReady.set(path, r);
  const row = listEl.querySelector<HTMLTableRowElement>(
    `tr.cleanupSongRow[data-path="${cssEscape(path)}"]`,
  );
  if (!row) return;
  const set = (cell: string, v: boolean): void => {
    const td = row.querySelector(`td[data-cell="${cell}"]`);
    if (td) td.innerHTML = ctStatCell(v ? "yes" : "no");
  };
  set("spec", r.spec);
  set("cand", r.cand);
  set("lyr", r.lyr);
  const btn = row.querySelector<HTMLButtonElement>("button[data-act]");
  if (btn) {
    if (r.prep) {
      // A sloppak/feedpak whose melodic notes haven't been derived yet — offer
      // "Prep notes" (ingest_prep_arrangements) before spectrogram/candidates.
      btn.dataset.act = "prep";
      btn.className = "ctBtn ctBuild";
      btn.textContent = "Prep notes";
    } else if (r.spec && r.cand) {
      btn.dataset.act = "open";
      btn.className = "ctBtn ctOpen";
      btn.innerHTML = `Open <i class="ti ti-chevron-right" aria-hidden="true"></i>`;
    } else if (!r.spec) {
      btn.dataset.act = "build";
      btn.className = "ctBtn ctBuild";
      btn.textContent = "Build";
    } else {
      // spectrogram built but candidates not computed yet -> route to the detail
      // panel, where "Compute candidates" (the dependency-gated step) lives.
      btn.dataset.act = "select";
      btn.className = "ctBtn";
      btn.innerHTML = `Prep <i class="ti ti-chevron-right" aria-hidden="true"></i>`;
    }
  }
}

// Mark a table row as actively building during "Build all unbuilt", so the
// table itself shows progress (current row highlighted + spinner) rather than
// only a one-line header status.
function markRowBuilding(path: string): void {
  const row = listEl.querySelector<HTMLTableRowElement>(
    `tr.cleanupSongRow[data-path="${cssEscape(path)}"]`,
  );
  if (!row) return;
  row.classList.add("isBuilding");
  const btn = row.querySelector<HTMLButtonElement>("button[data-act]");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Building…";
  }
  const spec = row.querySelector('td[data-cell="spec"]');
  if (spec) spec.innerHTML = `<span class="ctBuilding" aria-hidden="true">⋯</span>`;
}

// Run the spectrogram build for one song (shared by the inline table action +
// "Build all unbuilt"). Returns the outcome + a short status message.
// classifySpectroResult (no-stem vs error) lives in ./cleanupReadiness.
async function buildSpectrogramForSong(
  path: string,
): Promise<{ kind: SpectroOutcome; msg: string }> {
  const { roles } = await detectMelodicStems(path);
  try {
    const res = await safeInvoke<SidecarRunResult>("ingest_spectrogram", {
      req: { container_path: path, instruments: roles },
    });
    invalidateCleanupCache(path);
    const kind = classifySpectroResult(res);
    if (kind === "ok") return { kind, msg: "Spectrogram built" };
    if (kind === "nostem") return { kind, msg: "No melodic stem — nothing to build" };
    const tail = res.stderr.trim().split(/\r?\n/).slice(-2).join(" ");
    return { kind, msg: `Build failed (exit ${res.exit_code}): ${tail || "(no stderr)"}` };
  } catch (e) {
    invalidateCleanupCache(path);
    return { kind: "error", msg: `Build failed: ${String(e)}` };
  }
}

// Whether a pack needs melodic-notes derivation from its arrangements, probed
// from `get_auralsong_details`. A manifest pack (feedpak/sloppak) that declares
// arrangements but has no `aural/notes.mid` yet qualifies (CONTRACT: consumes
// the Rust details fields defensively via needsArrangementPrep). Best-effort —
// a details failure reports "no prep needed" rather than throwing.
async function packNeedsArrangementPrep(path: string): Promise<boolean> {
  if (!isManifestPack(path)) return false;
  try {
    const details = await safeInvoke<AuralSongDetails>("get_auralsong_details", {
      containerPath: path,
    });
    return needsArrangementPrep(path, details as unknown as Parameters<typeof needsArrangementPrep>[1]);
  } catch {
    return false;
  }
}

// Derive melodic gameplay notes (aural/notes.mid) + song_timeline.json from a
// sloppak/feedpak's arrangement wire JSONs via the sidecar's prep-arrangements
// command (CONTRACT C1: Tauri command `ingest_prep_arrangements`, arg
// `{ container }`). Returns the outcome + a short status message. Invalidates
// the readiness cache so a subsequent probe re-reads the freshly written files.
async function prepArrangementsForSong(
  path: string,
): Promise<{ ok: boolean; msg: string }> {
  try {
    const res = await safeInvoke<SidecarRunResult>("ingest_prep_arrangements", {
      req: { container_path: path },
    });
    invalidateCleanupCache(path);
    if (res.ok) {
      const parsed = parseSidecarStatusLine(res.stdout);
      // The sidecar always emits a `notes_mid` key; only "written" means a
      // notes.mid was actually produced (others are "skipped_*").
      const wrote =
        !!parsed &&
        typeof parsed === "object" &&
        (parsed as { notes_mid?: unknown }).notes_mid === "written";
      return { ok: true, msg: wrote ? "Derived notes from arrangements" : "Prep complete" };
    }
    const tail = res.stderr.trim().split(/\r?\n/).slice(-2).join(" ");
    return { ok: false, msg: `Prep failed (exit ${res.exit_code}): ${tail || "(no stderr)"}` };
  } catch (e) {
    invalidateCleanupCache(path);
    return { ok: false, msg: `Prep failed: ${String(e)}` };
  }
}

// Open the per-song cleanup (refine) editor — the table's "Open" inline action.
function openCleanupEditorForSong(path: string, role?: string): void {
  if (role) cleanupSelectedRole = role;
  void selectAuralSong(path, { autoLoadAudio: false });
  setRoute("refine");
  void refineWorkspace.openForAuralSong(path);
}

// -----------------
// Plugin loader
// -----------------

let vizCtx2d: CanvasRenderingContext2D;
{
  const ctx = vizCanvas.getContext("2d");
  if (ctx === null) throw new Error("missing 2d context");
  vizCtx2d = ctx;
}

let viz: Visualizer | null = null;
let vizRaf: number | null = null;
let lastFrameMs: number | null = null;
let selectedAuralSongPath: string | null = null;
let selectedAuralSongDetails: AuralSongDetails | null = null;
let selectedDrumChartSelection: DrumChartSelection | null = null;

let availablePlugins: PluginDescriptor[] = [...BUILTIN_PLUGINS];
let loadedPluginDispose: (() => void) | null = null;

let transport: TransportState = {
  t: 0,
  isPlaying: false,
  playbackRate: 1,
  bpm: 120,
  timeSignature: [4, 4]
};

let currentLyrics: LyricsFile | null = null;
let currentKeys: unknown | null = null;
let currentHarmony: unknown | null = null;
let currentVocalPitch: unknown | null = null;
let currentVocalPitchContour: unknown | null = null;
let currentSongTimeline: unknown | null = null;
let currentMelodicTracks: MelodicTrackSelection[] = [];
let currentMelodicNotes: VisualizerSongNote[] = [];
let currentFingeringNotes: VisualizerSongNote[] = [];

// Desktop default: use Rust native audio engine.
let currentTimebase: TransportTimebase = new NativeAudioTimebase({ sampleRateHz: 48_000, channels: 2 });
let transportController = new TransportController(currentTimebase, {
  bpm: 120,
  timeSignature: [4, 4]
});
const nativeTimebase = currentTimebase instanceof NativeAudioTimebase ? currentTimebase : null;
let audioOutputHosts: NativeAudioHostInfo[] = [];
let audioOutputDevices: NativeAudioDeviceInfo[] = [];

let currentPlaybackRate = 1;

// Rhythm-game flow: once a song is loaded, make the Now Playing panel the focus.
let playFocusMode = false;
function setPlayFocusMode(enabled: boolean) {
  playFocusMode = enabled;
  playLayoutEl.classList.toggle("isFocus", enabled);
  toggleFocusBtn.textContent = enabled ? "Library" : "Focus";
  // Canvas size may change; ensure we resize so the visualizer fills the space.
  resizeVizCanvas();
  logConsole("gamestate", `play focus mode -> ${enabled ? "focus" : "normal"}`);
}

toggleFocusBtn.addEventListener("click", () => {
  setPlayFocusMode(!playFocusMode);
});

// Player/track selection scaffold (multi-lane-ready)
type Instrument = "lead_guitar" | "rhythm_guitar" | "bass" | "drums" | "keys" | "vocals";
const INSTRUMENT_LABELS: Record<Instrument, string> = {
  lead_guitar: "Lead Guitar",
  rhythm_guitar: "Rhythm Guitar",
  bass: "Bass",
  drums: "Drums",
  keys: "Keys",
  vocals: "Vocals"
};

async function readNotesMidiBytes(containerPath: string, details: AuralSongDetails): Promise<Uint8Array | null> {
  if (!details.has_notes_mid) return null;
  const manifestRaw = details.manifest_raw as ManifestArtifactPointers | undefined;
  const relPath = manifestArtifactRelPath(manifestRaw, "aural_notes_mid") ?? `${featureDir(containerPath)}/notes.mid`;
  try {
    const midi = await invoke<MidiBlob>("read_auralsong_mid", { containerPath, relPath });
    return midi.bytes.length ? new Uint8Array(midi.bytes) : null;
  } catch (e) {
    warnConsole("debugging", `failed to load/parse notes MIDI from ${containerPath}`, e);
    return null;
  }
}

function melodicTracksToVisualizerNotes(tracks: readonly MelodicTrackSelection[]): VisualizerSongNote[] {
  const notes = tracks.flatMap((track) =>
    track.notes.map((note) => ({
      t_on: note.t_on,
      t_off: note.t_off,
      pitch: note.pitch,
      velocity: note.velocity,
      string: note.string,
      fret: note.fret,
      s: note.s,
      f: note.f,
      role: track.role,
      instrument: track.role,
      channel: track.channel,
      trackName: track.trackName,
    })),
  );
  notes.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch || (a.channel ?? -1) - (b.channel ?? -1));
  return notes;
}

async function readDrumChartSelection(containerPath: string, details: AuralSongDetails): Promise<DrumChartSelection | null> {
  if (details.has_drum_tab) {
    const relPath = artifactReadRelPath(containerPath, details, "drum_tab", "drum_tab.json") ?? "drum_tab.json";
    const selection = await loadDrumChartFromTab(containerPath, relPath);
    if (selection) return selection;
  }

  const midiBytes = await readNotesMidiBytes(containerPath, details);
  return midiBytes ? selectDrumChartFromMidiBytes(midiBytes) : null;
}

async function readMelodicTrackSelection(containerPath: string, details: AuralSongDetails): Promise<MelodicTrackSelection[]> {
  const midiBytes = await readNotesMidiBytes(containerPath, details);
  if (!midiBytes) return [];
  return selectMelodicTracksFromMidiBytes(midiBytes);
}

function computeSongCapabilities(
  details: AuralSongDetails | null,
  drumSelection: DrumChartSelection | null,
  melodicTracks: readonly MelodicTrackSelection[] = currentMelodicTracks,
): SongCapabilities {
  const charts = details?.charts ?? [];
  const byInstrument: SongCapabilities["charts"]["byInstrument"] = {};
  const drumChartAvailable = Boolean(drumSelection?.events.length);
  const hasMelodicRole = (role: Instrument): boolean =>
    melodicTracks.some((track) => track.role === role && track.notes.length > 0);
  const melodicChartAvailable = (Object.keys(INSTRUMENT_LABELS) as Instrument[]).some(hasMelodicRole);

  // Heuristic mapping: chart filenames often carry role/instrument hints.
  // We’ll firm this up later with a proper chart manifest, but this gives the UX a useful signal now.
  const anyMatch = (re: RegExp) => charts.some((c) => re.test(c));
  byInstrument.lead_guitar = hasMelodicRole("lead_guitar") || anyMatch(/lead|guitar(?!_rhythm)|gtr/i);
  byInstrument.rhythm_guitar = hasMelodicRole("rhythm_guitar") || anyMatch(/rhythm|guitar_rhythm|rhythm_guitar/i);
  byInstrument.bass = hasMelodicRole("bass") || anyMatch(/bass/i);
  byInstrument.drums = drumChartAvailable || anyMatch(/drum|kit/i);
  byInstrument.keys = hasMelodicRole("keys") || anyMatch(/keys|piano|synth/i);
  byInstrument.vocals =
    hasMelodicRole("vocals") ||
    Boolean(details?.has_lyrics || details?.has_vocal_pitch || details?.has_vocal_pitch_contour) ||
    anyMatch(/vocal|vox|lyrics/i);

  return {
    features: {
      beats: Boolean(details?.has_beats),
      tempo_map: Boolean(details?.has_tempo_map),
      sections: Boolean(details?.has_sections),
      events: Boolean(details?.has_events),
      lyrics: Boolean(details?.has_lyrics),
      notes_mid: Boolean(details?.has_notes_mid),
      drum_tab: Boolean(details?.has_drum_tab),
      song_timeline: Boolean(details?.has_song_timeline),
      keys: Boolean(details?.has_keys),
      harmony: Boolean(details?.has_harmony),
      vocal_pitch: Boolean(details?.has_vocal_pitch),
      vocal_pitch_contour: Boolean(details?.has_vocal_pitch_contour),
      aural_fingering: Boolean(details?.has_aural_fingering),
    },
    audio: {
      wav: Boolean(details?.has_mix_wav),
      mp3: Boolean(details?.has_mix_mp3),
      ogg: Boolean(details?.has_mix_ogg),
    },
    charts: {
      any: charts.length > 0 || drumChartAvailable || melodicChartAvailable,
      byInstrument,
    },
  };
}

function renderCaps(details: AuralSongDetails | null, drumSelection: DrumChartSelection | null) {
  const caps = computeSongCapabilities(details, drumSelection, currentMelodicTracks);

  const pill = (label: string, ok: boolean, hint?: string) => {
    const cls = ok ? "capPill capPill--ok" : "capPill capPill--missing";
    const title = hint ? ` title="${escapeHtml(hint)}"` : "";
    return `<span class="${cls}"${title}>${escapeHtml(label)}</span>`;
  };

  const featurePills = [
    pill("beats", caps.features.beats, "features/notes.mid (structure track beat pulses)"),
    pill("tempo", caps.features.tempo_map, "features/notes.mid (SetTempo + TimeSignature meta)"),
    pill("sections", caps.features.sections, "features/notes.mid (section markers)"),
    pill("events", caps.features.events, "features/notes.mid (drums ch10 + melodic ch1 notes)"),
    pill("lyrics", caps.features.lyrics, "features/lyrics.json"),
    pill("midi", caps.features.notes_mid, "features/notes.mid"),
    pill("drum tab", caps.features.drum_tab, "drum_tab.json"),
    pill("timeline", caps.features.song_timeline, "song_timeline.json"),
    pill("keys", caps.features.keys, "keys.json"),
    pill("harmony", caps.features.harmony, "harmony.json"),
    pill("vocal pitch", caps.features.vocal_pitch, "vocal_pitch.json"),
    pill("vocal contour", caps.features.vocal_pitch_contour, "vocal_pitch_contour.json"),
    pill("fingering", caps.features.aural_fingering, "aural/fingering.<role>.json"),
  ].join("\n");

  const drumHint = drumSelection
    ? `${drumSelection.reason === "drum_tab" ? "drum_tab.json" : "features/notes.mid"} (${drumSelection.mode}, ${drumSelection.reason}, events=${drumSelection.events.length})`
    : "chart availability (heuristic)";
  const chartPills = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
    .map((inst) => {
      const hint = inst === "drums" ? drumHint : "chart availability (heuristic)";
      return pill(INSTRUMENT_LABELS[inst], Boolean(caps.charts.byInstrument[inst]), hint);
    })
    .join("\n");

  const audioPills = [
    pill("stem.wav", caps.audio.wav),
    pill("stem.mp3", caps.audio.mp3),
    pill("stem.ogg", caps.audio.ogg),
  ].join("\n");

  capsEl.innerHTML = `
    <div class="capsRow">
      <span class="capsLabel">Data</span>
      <div class="capsPills">${featurePills}</div>
    </div>
    <div class="capsRow">
      <span class="capsLabel">Charts</span>
      <div class="capsPills">${chartPills}</div>
    </div>
    <div class="capsRow">
      <span class="capsLabel">Audio</span>
      <div class="capsPills">${audioPills}</div>
    </div>
  `;
}

function applyInstrumentAvailability(details: AuralSongDetails | null, drumSelection: DrumChartSelection | null) {
  const caps = computeSongCapabilities(details, drumSelection, currentMelodicTracks);
  for (const chip of Array.from(playersEl.querySelectorAll<HTMLElement>(".playerChip"))) {
    const sel = chip.querySelector<HTMLSelectElement>("select.playerInstrument");
    if (!sel) continue;
    for (const opt of Array.from(sel.options)) {
      const inst = opt.value as Instrument;
      const has = Boolean(caps.charts.byInstrument[inst]);
      // We only disable if we have *some* chart data but not for this instrument.
      // If there are no charts at all, leave enabled (future non-chart gameplay).
      const disable = caps.charts.any ? !has : false;
      opt.disabled = disable;
      opt.textContent = disable ? `${INSTRUMENT_LABELS[inst]} (no chart)` : INSTRUMENT_LABELS[inst];
    }
    // If current selection is now disabled, pick first enabled.
    if (sel.selectedOptions.length && sel.selectedOptions[0].disabled) {
      const firstEnabled = Array.from(sel.options).find((o) => !o.disabled);
      if (firstEnabled) {
        sel.value = firstEnabled.value;
        const playerId = chip.getAttribute("data-player-id");
        const nextInstrument = firstEnabled.value as Instrument;
        if (playerId) {
          players = players.map((p) => (p.id === playerId ? { ...p, instrument: nextInstrument } : p));
        }
      }
    }
  }
}

function pluginRequirements(id: string): { ok: (d: AuralSongDetails | null) => boolean; reason: string } {
  // Minimal v1 mapping (can evolve per plugin manifest later)
  switch (id) {
    case "viz-lyrics":
      return {
        ok: (d) => Boolean(d?.has_lyrics || d?.has_vocal_pitch || d?.has_vocal_pitch_contour),
        reason: "Requires lyrics or vocal pitch artifacts"
      };
    case "viz-drum-highway":
      return {
        ok: (d) => Boolean(d?.has_notes_mid || d?.has_drum_tab),
        reason: "Requires features/notes.mid or drum_tab.json"
      };
    // Placeholder visualizers: they can run with transport only.
    default:
      return { ok: () => true, reason: "" };
  }
}

function buildVizSongContext(): {
  lyrics?: LyricsFile;
  vocalPitch?: unknown;
  vocalPitchContour?: unknown;
  songTimeline?: unknown;
  keys?: unknown;
  harmony?: unknown;
  notes?: VisualizerSongNote[];
} {
  const drumNotes =
    selectedDrumChartSelection?.events.map((ev) => ({
      t_on: ev.t,
      t_off: ev.t + 0.08,
      pitch: ev.midi,
      velocity: ev.velocity ?? 100,
      role: "drums",
      instrument: "drums",
      channel: 9,
      trackName: ev.trackName
    })) ?? [];
  const melodicNotes = mergeFingeringIntoVisualizerNotes(currentMelodicNotes, currentFingeringNotes);
  const notes = [...drumNotes, ...melodicNotes].sort(
    (a, b) => a.t_on - b.t_on || a.pitch - b.pitch || (a.channel ?? -1) - (b.channel ?? -1),
  );

  return {
    lyrics: currentLyrics ?? undefined,
    vocalPitch: currentVocalPitch ?? undefined,
    vocalPitchContour: currentVocalPitchContour ?? undefined,
    songTimeline: currentSongTimeline ?? undefined,
    keys: currentKeys ?? undefined,
    harmony: currentHarmony ?? undefined,
    notes: notes.length > 0 ? notes : undefined
  };
}

function renderPluginsWithAvailability(details: AuralSongDetails | null) {
  // Re-render options with disabled state + hint.
  pluginSelect.innerHTML = availablePlugins
    .map((p, idx) => {
      const req = pluginRequirements(p.id);
      const ok = req.ok(details);
      const label = `${p.name} (${p.source})${ok ? "" : " — missing data"}`;
      const disabled = ok ? "" : "disabled";
      const title = ok || !req.reason ? "" : ` title="${escapeHtml(req.reason)}"`;
      return `<option value="${idx}" ${disabled}${title}>${escapeHtml(label)}</option>`;
    })
    .join("\n");

  // If selected plugin became disabled, choose first enabled.
  if (pluginSelect.selectedOptions.length && pluginSelect.selectedOptions[0].disabled) {
    const firstEnabled = Array.from(pluginSelect.options).find((o) => !o.disabled);
    if (firstEnabled) pluginSelect.value = firstEnabled.value;
  }
}

type Player = { id: string; name: string; instrument: Instrument };
let players: Player[] = [{ id: "p1", name: "Player 1", instrument: "lead_guitar" }];

function renderPlayers(): void {
  playersEl.innerHTML = `
    <div class="playersGrid">
      ${players
        .map((p) => {
          const options = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
            .map((inst) => `<option value="${inst}" ${p.instrument === inst ? "selected" : ""}>${INSTRUMENT_LABELS[inst]}</option>`)
            .join("\n");
          return `
            <div class="playerChip" data-player-id="${p.id}">
              <span class="playerName">${escapeHtml(p.name)}</span>
              <select class="playerInstrument" aria-label="Instrument for ${escapeHtml(p.name)}">
                ${options}
              </select>
              <button class="removePlayer" title="Remove player" ${players.length <= 1 ? "disabled" : ""}>×</button>
            </div>
          `;
        })
        .join("\n")}
    </div>
  `;

  for (const chip of Array.from(playersEl.querySelectorAll<HTMLElement>(".playerChip"))) {
    const id = chip.getAttribute("data-player-id");
    if (!id) continue;

    const sel = chip.querySelector<HTMLSelectElement>("select.playerInstrument");
    sel?.addEventListener("change", () => {
      const inst = sel.value as Instrument;
      players = players.map((p) => (p.id === id ? { ...p, instrument: inst } : p));
      window.dispatchEvent(
        new CustomEvent("auralprimer:players-updated", {
          detail: { players },
        })
      );
    });

    const remove = chip.querySelector<HTMLButtonElement>("button.removePlayer");
    remove?.addEventListener("click", () => {
      if (players.length <= 1) return;
      players = players.filter((p) => p.id !== id);
      renderPlayers();
    });
  }
}

// Ensure instruments/plugin availability is applied even if players are added after song selection.
function rerenderPlayersAndApplyAvailability() {
  renderPlayers();
  applyInstrumentAvailability(selectedAuralSongDetails, selectedDrumChartSelection);
}

addPlayerBtn.addEventListener("click", () => {
  const nextIdx = players.length + 1;
  const id = `p${nextIdx}`;
  const defaultInst: Instrument = nextIdx === 2 ? "rhythm_guitar" : nextIdx === 3 ? "bass" : "drums";
  players = [...players, { id, name: `Player ${nextIdx}`, instrument: defaultInst }];
  rerenderPlayersAndApplyAvailability();
});

rerenderPlayersAndApplyAvailability();

const metronome = new Metronome({ enabled: false, volume: 0.25 });

type MidiPortInfo = {
  id: number;
  name: string;
  stable_id?: string;
  backend?: string;
};

type MidiOutputSelection = { id: number; name: string; stable_id?: string | null };
type MidiInputSelection = { id: number; name: string; stable_id?: string | null };

type MidiInputSavedSettings = {
  port: MidiInputSelection | null;
  tempo_scale: number;
  allow_sysex: boolean;
};

type MidiInputMessageEvent = {
  timestamp_us: number;
  message_type: string;
  status: number;
  channel?: number | null;
  data1?: number | null;
  data2?: number | null;
  value14?: number | null;
  value_signed?: number | null;
  bytes: number[];
};

let midiConnected = false;
let midiOutSysexEnabled = false;
let midiInputEventLines: string[] = [];

function setMidiStatus(msg: string) {
  midiStatusEl.textContent = msg;
}

function setMidiInputEventsStatus(msg: string) {
  midiInEventsEl.textContent = msg;
}

function appendMidiInputEventLine(line: string) {
  const s = line.trim();
  if (!s) return;
  midiInputEventLines.push(s);
  if (midiInputEventLines.length > 14) {
    midiInputEventLines = midiInputEventLines.slice(-14);
  }
  setMidiInputEventsStatus(midiInputEventLines.join("\n"));
}

function formatMidiInputMessage(ev: MidiInputMessageEvent): string {
  const ch = typeof ev.channel === "number" ? ` ch${ev.channel + 1}` : "";
  const d1 = typeof ev.data1 === "number" ? ` d1=${ev.data1}` : "";
  const d2 = typeof ev.data2 === "number" ? ` d2=${ev.data2}` : "";
  const bend = typeof ev.value_signed === "number" ? ` bend=${ev.value_signed}` : "";
  const hex = ev.bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  return `${ev.message_type}${ch}${d1}${d2}${bend} [${hex}]`;
}

function midiUiChannelToZeroBased(channelFromUi: number): number {
  const ch = Math.floor(channelFromUi);
  if (!Number.isFinite(ch) || ch < 1 || ch > 16) {
    throw new Error("MIDI channel must be 1-16");
  }
  return ch - 1;
}

function requireMidiDataByte(name: string, value: number): number {
  const v = Math.floor(value);
  if (!Number.isFinite(v) || v < 0 || v > 127) {
    throw new Error(`${name} must be 0-127`);
  }
  return v;
}

function parseRawMidiHexBytes(raw: string): number[] {
  const tokens = raw
    .trim()
    .split(/[\s,]+/)
    .filter((t) => t.length > 0);
  if (!tokens.length) {
    throw new Error("Enter one or more hex bytes (example: 90 3C 64)");
  }

  return tokens.map((tok) => {
    const clean = tok.startsWith("0x") || tok.startsWith("0X") ? tok.slice(2) : tok;
    if (!/^[0-9a-fA-F]{1,2}$/.test(clean)) {
      throw new Error(`Invalid hex byte: ${tok}`);
    }
    const v = Number.parseInt(clean, 16);
    if (!Number.isFinite(v) || v < 0 || v > 255) {
      throw new Error(`Invalid hex byte: ${tok}`);
    }
    return v;
  });
}

async function generateLyricsForSelectedAuralSong(): Promise<void> {
  if (!selectedAuralSongPath || !selectedAuralSongDetails) {
    setVizStatus("Select an AuralSong first");
    return;
  }

  // If it's a zip auralsong, offer to convert to a directory auralsong so we can write features.
  if (selectedAuralSongDetails.kind !== "directory") {
    const ok = confirm(
      "This song is a zipped pack (read-only).\n\nConvert it to a directory pack so we can write the lyrics file?"
    );
    if (!ok) {
      setVizStatus("Lyrics generation cancelled");
      return;
    }

    try {
      const newPath = await safeInvoke<string>("convert_auralsong_to_directory", { containerPath: selectedAuralSongPath });
      selectedAuralSongPath = newPath;
      // Refresh details for the new directory auralsong.
      selectedAuralSongDetails = await safeInvoke<AuralSongDetails>("get_auralsong_details", { containerPath: newPath });
      setVizStatus(`Converted to directory AuralSong: ${newPath}`);
    } catch (e) {
      setVizStatus(`Conversion failed: ${String(e)}`);
      return;
    }
  }

  const durationSec = Number(selectedAuralSongDetails.manifest_summary?.duration_sec ?? 0);
  if (!Number.isFinite(durationSec) || durationSec <= 0) {
    setVizStatus("Cannot generate lyrics: manifest duration_sec missing/invalid");
    return;
  }

  const files = await pickFiles(["txt"], false);
  const lyricPath = files[0];
  if (!lyricPath) {
    setVizStatus("Lyrics generation cancelled");
    return;
  }

  try {
    setVizStatus("Generating lyrics…");

    const text = await safeInvoke<string>("read_text_file", { path: lyricPath });
    const lyricsJson = generateLyricsJsonFromPlainText({
      lyricsText: text,
      durationSec,
      jobId: "auralprimer_mvp"
    });

    // Write to the manifest lyrics pointer (pack-root lyrics.json for a
    // sloppak) so generated lyrics land where the game + Studio actually read
    // them; feedpak/legacy fall back to <featureDir>/lyrics.json as before.
    const lyricsDetails = await safeInvoke("get_auralsong_details", {
      containerPath: selectedAuralSongPath,
    }).catch(() => null);
    await safeInvoke("write_auralsong_features_json", {
      containerPath: selectedAuralSongPath,
      relPath: lyricsReadRelPath(selectedAuralSongPath, lyricsDetails),
      value: lyricsJson,
    });

    // Update local state so viz init sees it without requiring the user to click Details again.
    currentLyrics = lyricsJson as unknown as LyricsFile;

    setVizStatus("Generated lyrics.json (MVP line-level timings)");
    await refresh();
  } catch (e) {
    setVizStatus(`Lyrics generation failed: ${String(e)}`);
  }
}

function setMidiOutStatus(msg: string) {
  midiOutStatusEl.textContent = msg;
}

function midiPortBackendLabel(ports: MidiPortInfo[]): string {
  return ports.find((p) => p.backend?.trim())?.backend?.trim() || "native";
}

function renderMidiPortOptions(ports: MidiPortInfo[], emptyLabel: string): string {
  if (!ports.length) {
    return `<option value="" selected>${escapeHtml(emptyLabel)}</option>`;
  }
  return ports
    .map((p) => {
      const titleParts = [
        p.backend ? `backend=${p.backend}` : "",
        p.stable_id ? `id=${p.stable_id}` : "",
      ].filter(Boolean);
      const title = titleParts.length ? ` title="${escapeHtml(titleParts.join(" "))}"` : "";
      return `<option value="${p.id}"${title}>${escapeHtml(p.name)}</option>`;
    })
    .join("\n");
}

function findSavedMidiPortMatch(
  ports: MidiPortInfo[],
  saved: MidiInputSelection | MidiOutputSelection | null
): MidiPortInfo | undefined {
  if (!saved) return undefined;
  if (saved.stable_id?.trim()) {
    const stableMatch = ports.find((p) => p.stable_id === saved.stable_id);
    if (stableMatch) return stableMatch;
  }
  return ports.find((p) => p.id === saved.id && p.name === saved.name)
    ?? ports.find((p) => p.name === saved.name);
}

function midiPortNamesPreview(ports: MidiPortInfo[], maxPorts = 4): string {
  const names = ports.slice(0, maxPorts).map((p) => p.name.trim()).filter(Boolean);
  const suffix = ports.length > names.length ? `, +${ports.length - names.length} more` : "";
  return names.length ? `${names.join(", ")}${suffix}` : "(unnamed ports)";
}

async function refreshMidiInputPorts() {
  const previousDisabled = midiInRefreshBtn.disabled;
  try {
    midiInRefreshBtn.disabled = true;
    setMidiStatus("midi input: refreshing ports...");
    const ports = await invoke<MidiPortInfo[]>("list_midi_input_ports");
    midiInPortSelect.innerHTML = renderMidiPortOptions(ports, "No MIDI inputs found");

    let savedWarning = "";
    try {
      const saved = await invoke<MidiInputSavedSettings>("midi_clock_input_get_saved_settings");
      midiTempoScaleInput.value = String(saved.tempo_scale ?? 1);
      midiInSysexEnabledInput.checked = Boolean(saved.allow_sysex);

      const match = findSavedMidiPortMatch(ports, saved.port);
      if (match) {
        midiInPortSelect.value = String(match.id);
      }
    } catch (settingsError) {
      savedWarning = `; saved settings ignored: ${String(settingsError)}`;
    }

    if (!ports.length) {
      midiInPortSelect.value = "";
      setMidiStatus(
        "midi input: 0 ports found via native MIDI backend. Windows uses WinRT; macOS uses CoreMIDI; Linux uses ALSA. If another app sees the keyboard, close apps that may hold the port, replug the keyboard, then refresh."
      );
      return;
    }

    const backend = midiPortBackendLabel(ports);
    const selectedName = midiInPortSelect.selectedOptions[0]?.textContent?.trim();
    setMidiStatus(
      `midi input: ${ports.length} port(s) found via ${backend}: ${midiPortNamesPreview(ports)}${selectedName ? `; selected ${selectedName}` : ""}${savedWarning}`
    );
  } catch (e) {
    midiInPortSelect.innerHTML = renderMidiPortOptions([], "MIDI input refresh failed");
    midiInPortSelect.value = "";
    setMidiStatus(`midi input ports error: ${String(e)}`);
  } finally {
    midiInRefreshBtn.disabled = previousDisabled;
  }
}

async function refreshMidiOutputPorts() {
  try {
    const ports = await invoke<MidiPortInfo[]>("list_midi_output_ports");
    midiOutPortSelect.innerHTML = renderMidiPortOptions(ports, "No MIDI outputs found");

    // Best-effort: apply saved selection.
    const [saved, savedSysex] = await Promise.all([
      invoke<MidiOutputSelection | null>("midi_clock_output_get_saved_port"),
      invoke<boolean>("midi_output_get_saved_allow_sysex")
    ]);
    midiOutSysexEnabled = Boolean(savedSysex);
    midiOutSysexEnabledInput.checked = midiOutSysexEnabled;

    const match = findSavedMidiPortMatch(ports, saved);
    if (match) {
      midiOutPortSelect.value = String(match.id);
    }
  } catch (e) {
    midiOutPortSelect.innerHTML = renderMidiPortOptions([], "MIDI output refresh failed");
    midiOutPortSelect.value = "";
    setMidiOutStatus(`midi output ports error: ${String(e)}`);
  }
}

async function selectMidiOutputPortAndPersist() {
  const portId = Number(midiOutPortSelect.value);
  if (!Number.isFinite(portId)) {
    setMidiOutStatus("midi output: no port selected");
    return;
  }
  await invoke("midi_clock_output_select_port_and_persist", { portId });
  await invoke("midi_output_set_allow_sysex_and_persist", { enabled: midiOutSysexEnabled });
  setMidiOutStatus(`midi output: selected port=${portId} sysex=${midiOutSysexEnabled ? "on" : "off"}`);
}

let midiOutEnabled = false;
let midiOutRunning = false;
let midiOutEverStarted = false;
let lastMidiOutBpmSent = 0;
let lastMidiOutBpmSentAtMs = 0;

async function midiOutSetBpmIfNeeded(bpm: number) {
  if (!midiOutEnabled) return;
  if (!Number.isFinite(bpm) || bpm <= 0) return;

  const now = performance.now();
  // Throttle updates; and avoid spamming tiny fluctuations.
  if (now - lastMidiOutBpmSentAtMs < 200 && Math.abs(bpm - lastMidiOutBpmSent) < 0.05) return;

  await invoke("midi_clock_output_set_bpm", { bpm });
  lastMidiOutBpmSent = bpm;
  lastMidiOutBpmSentAtMs = now;
}

async function midiOutSeek(tSec: number) {
  if (!midiOutEnabled) return;
  if (!Number.isFinite(tSec) || tSec < 0) return;
  await invoke("midi_clock_output_seek", { tSec });
}

async function midiOutStartOrContinue() {
  if (!midiOutEnabled) return;
  // Ensure port selection is applied.
  await selectMidiOutputPortAndPersist();

  const st = transportController.getState();
  await midiOutSetBpmIfNeeded(st.bpm);
  await midiOutSeek(st.t);

  if (midiOutRunning) return;

  if (!midiOutEverStarted || st.t <= 0.0001) {
    await invoke("midi_clock_output_start");
    midiOutEverStarted = true;
    midiOutRunning = true;
    setMidiOutStatus("midi clock out: START");
  } else {
    await invoke("midi_clock_output_continue");
    midiOutRunning = true;
    setMidiOutStatus("midi clock out: CONTINUE");
  }
}

async function midiOutStop() {
  if (!midiOutEnabled) return;
  await invoke("midi_clock_output_stop");
  midiOutRunning = false;
  midiOutEverStarted = true;
  setMidiOutStatus("midi clock out: STOP");
}

async function setMidiOutSysex(enabled: boolean, persist: boolean): Promise<void> {
  midiOutSysexEnabled = Boolean(enabled);
  midiOutSysexEnabledInput.checked = midiOutSysexEnabled;

  if (persist) {
    await invoke("midi_output_set_allow_sysex_and_persist", { enabled: midiOutSysexEnabled });
  } else {
    await invoke("midi_output_set_allow_sysex", { enabled: midiOutSysexEnabled });
  }
}

async function sendMidiNoteOnFromUi() {
  const channel = midiUiChannelToZeroBased(Number(midiMsgChannelInput.value));
  const note = requireMidiDataByte("note", Number(midiMsgNoteInput.value));
  const velocity = requireMidiDataByte("velocity", Number(midiMsgVelocityInput.value));
  await invoke("midi_output_send_note_on", { channel, note, velocity });
  setMidiOutStatus(`midi out note on: ch${channel + 1} note=${note} vel=${velocity}`);
}

async function sendMidiNoteOffFromUi() {
  const channel = midiUiChannelToZeroBased(Number(midiMsgChannelInput.value));
  const note = requireMidiDataByte("note", Number(midiMsgNoteInput.value));
  const velocity = requireMidiDataByte("velocity", Number(midiMsgVelocityInput.value));
  await invoke("midi_output_send_note_off", { channel, note, velocity });
  setMidiOutStatus(`midi out note off: ch${channel + 1} note=${note} vel=${velocity}`);
}

async function sendMidiCcFromUi() {
  const channel = midiUiChannelToZeroBased(Number(midiMsgChannelInput.value));
  const controller = requireMidiDataByte("cc", Number(midiMsgCcInput.value));
  const value = requireMidiDataByte("cc value", Number(midiMsgCcValueInput.value));
  await invoke("midi_output_send_control_change", { channel, controller, value });
  setMidiOutStatus(`midi out cc: ch${channel + 1} cc=${controller} value=${value}`);
}

async function sendMidiAllNotesOffFromUi() {
  const channel = midiUiChannelToZeroBased(Number(midiMsgChannelInput.value));
  await invoke("midi_output_all_notes_off", { channel });
  setMidiOutStatus(`midi out: all notes off ch${channel + 1}`);
}

async function sendMidiRawFromUi() {
  const bytes = parseRawMidiHexBytes(midiOutRawHexInput.value);
  await invoke("midi_output_send_raw", { bytes });
  setMidiOutStatus(`midi out raw: ${bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ")}`);
}

async function connectMidiClockInput() {
  const portId = Number(midiInPortSelect.value);
  const tempoScale = Number(midiTempoScaleInput.value);
  const allowSysex = midiInSysexEnabledInput.checked;
  if (!Number.isFinite(portId)) {
    setMidiStatus("midi input: no port selected; refresh after connecting the keyboard");
    return;
  }
  await invoke("midi_clock_input_start_and_persist", { portId, tempoScale, allowSysex });
  midiConnected = true;
  const portName = midiInPortSelect.selectedOptions[0]?.textContent?.trim() || `port ${portId}`;
  setMidiStatus(`midi input connected: ${portName} scale=${tempoScale} sysex=${allowSysex ? "on" : "off"}`);
}

async function disconnectMidiClockInput() {
  await invoke("midi_clock_input_stop");
  midiConnected = false;
  transportController.setExternalClockRunning(false);
  setMidiStatus("midi clock input disconnected");
}

async function shutdownMidiOutputService() {
  // Always safe; it just joins the thread if it exists.
  try {
    await invoke("midi_clock_output_shutdown");
  } catch {
    // ignore
  }
}

let lastLoadedAudio: { blob: Blob; mime: string } | null = null;

function setAudioStatus(msg: string) {
  audioStatusEl.textContent = msg;
  logConsole("play", msg);
}

// Ensure the UI reflects the desktop-only backend.
audioBackendSelect.value = "native";

function sameOutputHostSelection(
  a: NativeAudioHostSelection | null | undefined,
  b: NativeAudioHostSelection | null | undefined
): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.id === b.id;
}

function sameOutputDeviceSelection(
  a: NativeAudioDeviceSelection | null | undefined,
  b: NativeAudioDeviceSelection | null | undefined
): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.name === b.name && a.channels === b.channels && a.sample_rate_hz === b.sample_rate_hz;
}

function formatOutputDeviceLabel(d: NativeAudioDeviceSelection): string {
  const srKhz = (d.sample_rate_hz / 1000).toFixed(1);
  return `${d.name} (${d.channels}ch, ${srKhz}kHz)`;
}

async function refreshAudioOutputHosts() {
  if (!nativeTimebase || !haveTauri()) {
    audioOutputHostSelect.innerHTML = `<option value="">System default</option>`;
    audioOutputHostSelect.disabled = true;
    audioOutputHostRefreshBtn.disabled = true;
    audioOutputHostApplyBtn.disabled = true;
    return;
  }

  audioOutputHostRefreshBtn.disabled = true;
  try {
    const [hosts, selected] = await Promise.all([
      nativeTimebase.listOutputHosts(),
      nativeTimebase.getSelectedOutputHost()
    ]);
    audioOutputHosts = hosts;

    const options = [
      `<option value="">System default</option>`,
      ...audioOutputHosts.map((h, idx) => {
        const defaultTag = h.is_default ? " [default]" : "";
        return `<option value="${idx}">${escapeHtml(h.name + defaultTag)}</option>`;
      })
    ];
    audioOutputHostSelect.innerHTML = options.join("\n");

    const selectedIdx = audioOutputHosts.findIndex((h) => sameOutputHostSelection(h, selected));
    audioOutputHostSelect.value = selectedIdx >= 0 ? String(selectedIdx) : "";
    audioOutputHostSelect.disabled = false;
    audioOutputHostApplyBtn.disabled = false;
  } catch (e) {
    audioOutputHosts = [];
    audioOutputHostSelect.innerHTML = `<option value="">System default</option>`;
    audioOutputHostSelect.value = "";
    audioOutputHostSelect.disabled = true;
    audioOutputHostApplyBtn.disabled = true;
    setAudioStatus(`output host refresh failed: ${String(e)}`);
  } finally {
    audioOutputHostRefreshBtn.disabled = false;
  }
}

async function applyAudioOutputHostSelection() {
  if (!nativeTimebase) return;

  const raw = audioOutputHostSelect.value.trim();
  const idx = raw === "" ? Number.NaN : Number(raw);
  const selected =
    Number.isFinite(idx) && idx >= 0 && idx < audioOutputHosts.length ? audioOutputHosts[idx] : null;
  const label = selected ? selected.name : "System default";

  audioOutputHostApplyBtn.disabled = true;
  audioOutputHostRefreshBtn.disabled = true;
  audioOutputHostSelect.disabled = true;
  setAudioStatus(`switching output host to ${label}...`);

  try {
    await nativeTimebase.setOutputHost(selected);
    await refreshAudioOutputHosts();
    await refreshAudioOutputDevices();
    const latencySec = nativeTimebase.getOutputLatencySec?.();
    const latencyMsg =
      typeof latencySec === "number" && Number.isFinite(latencySec)
        ? ` (est latency ${(latencySec * 1000).toFixed(1)}ms)`
        : "";
    setAudioStatus(`output host set: ${label}${latencyMsg}`);
  } catch (e) {
    setAudioStatus(`output host switch failed: ${String(e)}`);
    await refreshAudioOutputHosts();
  }
}

async function refreshAudioOutputDevices() {
  if (!nativeTimebase || !haveTauri()) {
    audioOutputDeviceSelect.innerHTML = `<option value="">System default</option>`;
    audioOutputDeviceSelect.disabled = true;
    audioOutputDeviceRefreshBtn.disabled = true;
    audioOutputDeviceApplyBtn.disabled = true;
    return;
  }

  audioOutputDeviceRefreshBtn.disabled = true;
  try {
    const [devices, selected] = await Promise.all([
      nativeTimebase.listOutputDevices(),
      nativeTimebase.getSelectedOutputDevice()
    ]);
    audioOutputDevices = devices;

    const options = [
      `<option value="">System default</option>`,
      ...audioOutputDevices.map((d, idx) => {
        const label = formatOutputDeviceLabel(d);
        const defaultTag = d.is_default ? " [default]" : "";
        return `<option value="${idx}">${escapeHtml(label + defaultTag)}</option>`;
      })
    ];
    audioOutputDeviceSelect.innerHTML = options.join("\n");

    const selectedIdx = audioOutputDevices.findIndex((d) => sameOutputDeviceSelection(d, selected));
    audioOutputDeviceSelect.value = selectedIdx >= 0 ? String(selectedIdx) : "";
    audioOutputDeviceSelect.disabled = false;
    audioOutputDeviceApplyBtn.disabled = false;
  } catch (e) {
    audioOutputDevices = [];
    audioOutputDeviceSelect.innerHTML = `<option value="">System default</option>`;
    audioOutputDeviceSelect.value = "";
    audioOutputDeviceSelect.disabled = true;
    audioOutputDeviceApplyBtn.disabled = true;
    setAudioStatus(`output device refresh failed: ${String(e)}`);
  } finally {
    audioOutputDeviceRefreshBtn.disabled = false;
  }
}

async function applyAudioOutputDeviceSelection() {
  if (!nativeTimebase) return;

  const raw = audioOutputDeviceSelect.value.trim();
  const idx = raw === "" ? Number.NaN : Number(raw);
  const selected =
    Number.isFinite(idx) && idx >= 0 && idx < audioOutputDevices.length ? audioOutputDevices[idx] : null;
  const label = selected ? formatOutputDeviceLabel(selected) : "System default";

  audioOutputDeviceApplyBtn.disabled = true;
  audioOutputDeviceRefreshBtn.disabled = true;
  audioOutputDeviceSelect.disabled = true;
  setAudioStatus(`switching output device to ${label}...`);

  try {
    await nativeTimebase.setOutputDevice(selected);
    const latencySec = nativeTimebase.getOutputLatencySec?.();
    const latencyMsg =
      typeof latencySec === "number" && Number.isFinite(latencySec)
        ? ` (est latency ${(latencySec * 1000).toFixed(1)}ms)`
        : "";
    setAudioStatus(`output device set: ${label}${latencyMsg} (saved preference)`);
  } catch (e) {
    setAudioStatus(`output device switch failed: ${String(e)}`);
  } finally {
    await refreshAudioOutputDevices();
  }
}

function setVizStatus(msg: string) {
  vizStatusEl.textContent = msg;
  auralsongEditorStatusEl.textContent = msg;
  logConsole("debugging", msg);
}

function setStemMidiStatus(msg: string) {
  stemMidiStatusEl.textContent = msg;
}

function setIngestStatus(msg: string) {
  ingestStatusEl.textContent = msg;
  logConsole("ingest", msg);
}

function setIngestSummary(
  state: IngestSummaryState,
  title: string,
  detail: string,
  options: {
    progressPct?: number;
    badge?: string;
    canReview?: boolean;
    showLog?: boolean;
  } = {}
) {
  const clampedProgress = Math.max(0, Math.min(100, Math.round(options.progressPct ?? 0)));
  const badgeLabel =
    options.badge ??
    (state === "running"
      ? "Importing"
      : state === "success"
        ? "Done"
        : state === "error"
          ? "Failed"
          : "Ready");

  ingestSummaryEl.className = `ingestSummary ingestSummary--${state}`;
  ingestSummaryBadgeEl.className = `ingestSummaryBadge ingestSummaryBadge--${state}`;
  ingestSummaryBadgeEl.textContent = badgeLabel;
  ingestSummaryTitleEl.textContent = title;
  ingestSummaryDetailEl.textContent = detail;
  ingestSummaryProgressEl.value = clampedProgress;
  ingestSummaryProgressTextEl.textContent =
    state === "success"
      ? "100%"
      : state === "error"
        ? `${clampedProgress}%`
        : `${clampedProgress}%`;
  ingestOpenCleanupBtn.disabled = !options.canReview;
  ingestLogPanelEl.open = options.showLog ?? state !== "success";
}

function setAuralSongEditorStatus(msg: string) {
  auralsongEditorStatusEl.textContent = msg;
}

function debugIngestConsole(message: string, details?: unknown) {
  logConsole("ingest", message, details);
}

let ingestInFlight = false;
let ingestLogLines: string[] = [];
let ingestProgressPct = 0;
let ingestStemInspection: RawSongFolderInspection | null = null;

function resetIngestStatusLog(firstLine: string) {
  ingestLogLines = [firstLine];
  setIngestStatus(ingestLogLines.join("\n"));
  debugIngestConsole(firstLine);
}

function appendIngestStatusLine(line: string) {
  const s = line.trim();
  if (!s) return;
  ingestLogLines.push(s);
  if (ingestLogLines.length > 14) {
    ingestLogLines = ingestLogLines.slice(-14);
  }
  setIngestStatus(ingestLogLines.join("\n"));
  debugIngestConsole(s);
}

function formatIngestProgressEvent(ev: IngestImportProgressEvent): string {
  if (ev.stream === "stderr") {
    return `[stderr] ${ev.line}`;
  }

  const parsed = ev.parsed;
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const obj = parsed as Record<string, unknown>;
    const id = typeof obj.id === "string" ? obj.id : "progress";
    const progress = typeof obj.progress === "number" ? `${Math.round(obj.progress * 100)}%` : "";
    const msg = typeof obj.message === "string" ? obj.message : "";
    const pct = progress ? `${progress} ` : "";
    const suffix = msg ? ` | ${msg}` : "";
    return `${pct}${id}${suffix}`.trim();
  }

  return ev.line;
}

function formatIngestStageLabel(rawId: string): string {
  const cleaned = rawId.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Import";
  return cleaned.replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function parseIngestProgressEvent(
  ev: IngestImportProgressEvent
): { progressPct: number; title: string; detail: string } | null {
  if (ev.stream !== "stdout") return null;
  const parsed = ev.parsed;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const obj = parsed as Record<string, unknown>;
  const rawId = typeof obj.id === "string" ? obj.id : "progress";
  const progress = typeof obj.progress === "number" ? Math.round(obj.progress * 100) : 0;
  const message = typeof obj.message === "string" && obj.message.trim() ? obj.message.trim() : "working";
  return {
    progressPct: progress,
    title: `Import running: ${formatIngestStageLabel(rawId)}`,
    detail: `${formatIngestStageLabel(rawId)} | ${message}`
  };
}

function inferIngestOutPathFromCommand(command: string[]): string | undefined {
  const outIdx = command.findIndex((part) => part === "--out");
  if (outIdx < 0) return undefined;
  const value = (command[outIdx + 1] ?? "").trim();
  return value || undefined;
}

let stemMidiFolderPath: string | null = null;
let stemMidiInspection: RawSongFolderInspection | null = null;

function stemMidiBaseName(path: string): string {
  return path.replace(/^.*[\\\/]/, "");
}

function formatDetectedRoleLabel(role: string): string {
  switch (role) {
    case "mix":
      return "Mix";
    case "drums":
      return "Drums";
    case "bass":
      return "Bass";
    case "lead_guitar":
      return "Lead Guitar";
    case "rhythm_guitar":
      return "Rhythm Guitar";
    case "guitar":
      return "Guitar";
    case "synth":
      return "Synth";
    case "keys":
      return "Keyboard / Keys";
    case "vocals":
      return "Vocals";
    case "backing_vocals":
      return "Backing Vocals";
    case "fx":
      return "FX";
    default:
      return "Unknown";
  }
}

function formatGameRoleLabel(role?: string | null): string {
  switch (role) {
    case "drums":
      return "Drums";
    case "bass":
      return "Bass";
    case "lead_guitar":
      return "Lead Guitar";
    case "rhythm_guitar":
      return "Rhythm Guitar";
    case "keys":
      return "Keys / Synth";
    case "vocals":
      return "Vocals";
    default:
      return "Unmapped";
  }
}

function describeSourceMidiOffset(offsetSec?: number | null, pairCount = 0): string {
  if (offsetSec === null || typeof offsetSec === "undefined" || !Number.isFinite(offsetSec)) {
    return "source MIDI timing will be used as-is";
  }
  const abs = Math.abs(offsetSec);
  if (abs < 0.001) {
    return pairCount > 0
      ? `source MIDI already matches the audio stem starts (${pairCount} matched pair${pairCount === 1 ? "" : "s"})`
      : "source MIDI timing will be used as-is";
  }
  const direction = offsetSec > 0 ? "earlier" : "later";
  const pairNote = pairCount > 0 ? ` using ${pairCount} matched pair${pairCount === 1 ? "" : "s"}` : "";
  return `source MIDI will shift ${direction} by ${abs.toFixed(3)}s${pairNote}`;
}

function renderDetectedPartList(parts: RawSongDetectedPart[]): string {
  if (!parts.length) {
    return `<div class="meta">(none detected)</div>`;
  }
  return parts
    .map((part) => {
      const detectedLabel = formatDetectedRoleLabel(part.detected_role);
      const mappedLabel = part.game_role ? formatGameRoleLabel(part.game_role) : null;
      const roleLabel = mappedLabel && mappedLabel !== detectedLabel
        ? `${detectedLabel} -> ${mappedLabel}`
        : detectedLabel;
      return `<div class="meta">${escapeHtml(roleLabel)}: ${escapeHtml(stemMidiBaseName(part.path))}</div>`;
    })
    .join("");
}

function findDetectedParts(
  parts: RawSongDetectedPart[],
  options: { gameRoles?: string[]; detectedRoles?: string[] }
): RawSongDetectedPart[] {
  const gameRoleSet = new Set(options.gameRoles ?? []);
  const detectedRoleSet = new Set(options.detectedRoles ?? []);
  return parts.filter((part) => {
    if (part.game_role && gameRoleSet.has(part.game_role)) return true;
    return detectedRoleSet.has(part.detected_role);
  });
}

function renderAuditState(value: boolean | null): string {
  if (value === null) {
    return `<span class="importAuditStatus importAuditStatus--na">n/a</span>`;
  }
  return value
    ? `<span class="importAuditStatus importAuditStatus--found">found</span>`
    : `<span class="importAuditStatus importAuditStatus--missing">missing</span>`;
}

function summarizePartKinds(parts: RawSongDetectedPart[]): string {
  const labels = Array.from(new Set(parts.map((part) => formatDetectedRoleLabel(part.detected_role))));
  return labels.join(", ");
}

function renderStemMidiAuditTable(inspection: RawSongFolderInspection): string {
  const drumsAudio = findDetectedParts(inspection.stem_parts, { gameRoles: ["drums"], detectedRoles: ["drums"] });
  const drumsMidi = findDetectedParts(inspection.midi_parts, { gameRoles: ["drums"], detectedRoles: ["drums"] });
  const bassAudio = findDetectedParts(inspection.stem_parts, { gameRoles: ["bass"], detectedRoles: ["bass"] });
  const bassMidi = findDetectedParts(inspection.midi_parts, { gameRoles: ["bass"], detectedRoles: ["bass"] });
  const guitarAudio = findDetectedParts(inspection.stem_parts, {
    gameRoles: ["lead_guitar", "rhythm_guitar"],
    detectedRoles: ["guitar", "lead_guitar", "rhythm_guitar"],
  });
  const guitarMidi = findDetectedParts(inspection.midi_parts, {
    gameRoles: ["lead_guitar", "rhythm_guitar"],
    detectedRoles: ["guitar", "lead_guitar", "rhythm_guitar"],
  });
  const keysAudio = findDetectedParts(inspection.stem_parts, {
    gameRoles: ["keys"],
    detectedRoles: ["keys", "synth"],
  });
  const keysMidi = findDetectedParts(inspection.midi_parts, {
    gameRoles: ["keys"],
    detectedRoles: ["keys", "synth"],
  });
  const vocalsAudio = findDetectedParts(inspection.stem_parts, {
    gameRoles: ["vocals"],
    detectedRoles: ["vocals", "backing_vocals"],
  });
  const vocalsMidi = findDetectedParts(inspection.midi_parts, {
    gameRoles: ["vocals"],
    detectedRoles: ["vocals", "backing_vocals"],
  });
  const lyricAlignSource = inspection.vocal_stem_path ?? inspection.mix_wav_path ?? inspection.stem_wav_paths[0] ?? null;

  const rows = [
    {
      label: "Drums",
      audio: drumsAudio.length > 0,
      midi: drumsMidi.length > 0,
      note: drumsMidi.length > 1 ? `${drumsMidi.length} MIDI parts merged` : "mapped to Drums",
    },
    {
      label: "Bass",
      audio: bassAudio.length > 0,
      midi: bassMidi.length > 0,
      note: bassMidi.length > 0 ? "mapped to Bass" : "missing bass MIDI",
    },
    {
      label: "Guitar",
      audio: guitarAudio.length > 0,
      midi: guitarMidi.length > 0,
      note: guitarMidi.length > 0 ? summarizePartKinds(guitarMidi) : "missing guitar MIDI",
    },
    {
      label: "Keys / Synth",
      audio: keysAudio.length > 0,
      midi: keysMidi.length > 0,
      note: keysMidi.length > 1 ? `${keysMidi.length} MIDI parts merged` : (keysMidi.length > 0 ? summarizePartKinds(keysMidi) : "missing keys MIDI"),
    },
    {
      label: "Vocals",
      audio: vocalsAudio.length > 0,
      midi: vocalsMidi.length > 0,
      note: inspection.karaoke_json_path || inspection.lyrics_txt_path ? "lyric timing source present" : "no lyrics source",
    },
    {
      label: "Lyrics",
      audio: Boolean(lyricAlignSource),
      midi: null,
      note: inspection.karaoke_json_path
        ? "karaoke JSON"
        : inspection.lyrics_txt_path
          ? lyricAlignSource
            ? `lyrics.txt + ${stemMidiBaseName(lyricAlignSource)}`
            : "lyrics.txt (uniform fallback)"
          : "missing lyrics",
    },
  ];

  const body = rows
    .map((row) => `
      <tr>
        <th scope="row">${escapeHtml(row.label)}</th>
        <td>${renderAuditState(row.audio)}</td>
        <td>${renderAuditState(row.midi)}</td>
        <td class="importAuditNotes">${escapeHtml(row.note)}</td>
      </tr>
    `)
    .join("");

  return `
    <div class="importAuditWrap">
      <table class="importAuditTable">
        <thead>
        <tr>
          <th>Track</th>
          <th>Audio</th>
          <th>MIDI</th>
          <th>Notes</th>
        </tr>
      </thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function renderStemMidiSelection() {
  stemMidiPickFolderBtn.textContent = "Choose Suno folder...";
  stemMidiImportBtn.textContent = "Import song";
  stemMidiFolderLabel.textContent = stemMidiFolderPath ?? "(no folder selected)";
  stemMidiImportBtn.disabled = !stemMidiInspection;
  if (!stemMidiInspection) {
    stemMidiSummaryEl.innerHTML = stemMidiFolderPath
      ? `<div class="meta">Scanning ${escapeHtml(stemMidiBaseName(stemMidiFolderPath))}...</div>`
      : `<div class="meta">(choose a Suno folder)</div>`;
    return;
  } else {
    const summary = [
      `${stemMidiInspection.stem_wav_paths.length} WAV${stemMidiInspection.stem_wav_paths.length === 1 ? "" : "s"}`,
      `${stemMidiInspection.midi_paths.length} MIDI${stemMidiInspection.midi_paths.length === 1 ? "" : "s"}`,
      stemMidiInspection.lyrics_txt_path ? "lyrics.txt" : "no lyrics.txt",
      stemMidiInspection.mapped_game_roles.length
        ? stemMidiInspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")
        : "no mapped roles",
      stemMidiInspection.midi_chart_ready
        ? describeSourceMidiOffset(stemMidiInspection.source_midi_offset_sec, stemMidiInspection.source_midi_offset_pair_count)
        : "no playable chart mapping yet",
      stemMidiInspection.karaoke_json_path
        ? "karaoke JSON"
        : stemMidiInspection.vocal_stem_path
          ? "vocals align"
          : undefined,
    ].filter(Boolean);
    stemMidiSummaryEl.textContent = summary.join(" | ");
  }
  renderStemMidiTrackList();
}

function renderStemMidiTrackList() {
  if (!stemMidiInspection) {
    stemMidiSummaryEl.innerHTML = "";
    return;
  }

  const warningItems = stemMidiInspection.warnings.length
    ? `<div class="error" style="margin-top:8px"><strong>Warnings:</strong><br />${stemMidiInspection.warnings.map((warning) => escapeHtml(warning)).join("<br />")}</div>`
    : "";

  const lyricSource = stemMidiInspection.karaoke_json_path
    ? `Using existing karaoke JSON: ${escapeHtml(stemMidiBaseName(stemMidiInspection.karaoke_json_path))}`
    : stemMidiInspection.lyrics_txt_path
      ? `Will align ${escapeHtml(stemMidiBaseName(stemMidiInspection.lyrics_txt_path))} using ${escapeHtml(stemMidiBaseName(stemMidiInspection.vocal_stem_path ?? stemMidiInspection.mix_wav_path ?? stemMidiInspection.stem_wav_paths[0] ?? ""))}`
      : "No lyrics source detected";

  stemMidiSummaryEl.innerHTML = `
    <div class="meta" style="margin-top:6px;font-weight:600">Folder check</div>
    <div class="meta">default title: ${escapeHtml(stemMidiInspection.title_guess)}</div>
    <div class="meta">game mapping: ${escapeHtml(stemMidiInspection.mapped_game_roles.length ? stemMidiInspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ") : "none")}</div>
    <div class="meta">mix audio: ${escapeHtml(stemMidiInspection.mix_wav_path ? stemMidiBaseName(stemMidiInspection.mix_wav_path) : "sum the detected stems")}</div>
    <div class="meta">chart timing: ${escapeHtml(
      stemMidiInspection.midi_chart_ready
        ? describeSourceMidiOffset(stemMidiInspection.source_midi_offset_sec, stemMidiInspection.source_midi_offset_pair_count)
        : "recognized gameplay MIDI was not found; import will keep the source files but may not generate a playable chart"
    )}</div>
    <div class="meta">lyrics: ${lyricSource}</div>
    <div class="meta" style="margin-top:8px;font-weight:600">Track detection</div>
    ${renderStemMidiAuditTable(stemMidiInspection)}
    ${warningItems}
  `;
}

async function inspectStemMidiFolder(folderPath: string): Promise<void> {
  try {
    await waitForUiPaint();
    const inspection = await safeInvoke<RawSongFolderInspection>("inspect_raw_song_folder", { folderPath });
    stemMidiInspection = inspection;
    renderStemMidiSelection();
    const warningCount = inspection.warnings.length;
    const warningSuffix = warningCount ? ` | ${warningCount} warning${warningCount === 1 ? "" : "s"}` : "";
    const mappedSuffix = inspection.mapped_game_roles.length
      ? ` | mapped: ${inspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")}`
      : "";
    const timingSuffix = inspection.midi_chart_ready
      ? ` | chart: ${describeSourceMidiOffset(inspection.source_midi_offset_sec, inspection.source_midi_offset_pair_count)}`
      : " | chart: no playable source MIDI mapping";
    setStemMidiStatus(`validated: ${inspection.stem_wav_paths.length} WAV(s), ${inspection.midi_paths.length} MIDI file(s)${warningSuffix}${mappedSuffix}${timingSuffix}`);
  } catch (e) {
    stemMidiInspection = null;
    renderStemMidiSelection();
    setStemMidiStatus(`Folder validation failed: ${String(e)}`);
    throw e;
  }
}

async function stemMidiCreateAuralSong() {
  if (!stemMidiFolderPath) {
    setStemMidiStatus("choose a Suno folder first");
    return;
  }

  if (!stemMidiInspection) {
    await inspectStemMidiFolder(stemMidiFolderPath);
  }

  setStemMidiStatus("importing...");
  stemMidiImportBtn.disabled = true;
  // Clear any previous post-import card so the user sees clean state for
  // this attempt.
  hideStemMidiNextSteps();
  try {
    await waitForUiPaint();
    const res = await safeInvoke<ImportRawSongFolderResult>("import_raw_song_folder", {
      req: {
        folder_path: stemMidiFolderPath,
      } satisfies ImportRawSongFolderRequest,
    });
    const lines = [
      `imported: ${res.auralsong_path}`,
      `detected ${res.stems_count} WAV stem(s), ${res.midi_files_count} MIDI file(s)${res.lyrics_included ? " | lyrics ready" : ""}`,
    ];
    if (res.midi_chart_included) {
      lines.push(
        `playable chart: source MIDI imported${res.source_midi_offset_sec !== null && typeof res.source_midi_offset_sec !== "undefined"
          ? ` | ${describeSourceMidiOffset(res.source_midi_offset_sec, res.source_midi_offset_pair_count)}`
          : ""}`
      );
    } else {
      lines.push("playable chart: source MIDI could not be promoted automatically");
    }
    if (res.mapped_game_roles.length) {
      lines.push(`game roles: ${res.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")}`);
    }
    if (res.warnings.length) {
      lines.push(`warnings:\n- ${res.warnings.join("\n- ")}`);
    }
    setStemMidiStatus(lines.join("\n"));
    void refresh();
    // Surface clear next-step actions so the user isn't left staring at a
    // status log with no idea what to do next. The card stays visible until
    // a new import attempt starts.
    showStemMidiNextSteps(res.auralsong_path, res.mapped_game_roles);
  } finally {
    stemMidiImportBtn.disabled = !stemMidiInspection;
  }
}

let stemMidiLastImportedPath: string | null = null;

function gameRolesToRefineInstruments(gameRoles: string[]): string[] {
  // Map the AuralSong manifest's mapped_game_roles list to the instrument
  // ids the refine-candidates sidecar expects. Drop drums because they use
  // the lane editor/drum_tab flow instead of melodic candidate regions.
  const out: string[] = [];
  for (const role of gameRoles) {
    switch (role) {
      case "keys":
      case "bass":
      case "guitar":
      case "lead_guitar":
      case "rhythm_guitar":
      case "vocals":
        out.push(role);
        break;
      default:
        break;
    }
  }
  if (out.length === 0) out.push("keys");
  return Array.from(new Set(out));
}

function showStemMidiNextSteps(auralsongPath: string, gameRoles: string[]): void {
  stemMidiLastImportedPath = auralsongPath;
  stemMidiNextStepsEl.style.display = "block";
  stemMidiNextStepsEl.dataset.auralsongPath = auralsongPath;
  const insts = gameRolesToRefineInstruments(gameRoles);
  stemMidiNextStepsEl.dataset.instruments = insts.join(",");
  stemMidiPrecomputeStatusEl.style.display = "none";
  stemMidiPrecomputeStatusEl.textContent = "";
  stemMidiPrecomputeBtn.disabled = false;
  stemMidiPrecomputeBtn.textContent =
    insts.length === 1
      ? `Run candidate precompute (${insts[0]})`
      : `Run candidate precompute (${insts.length} instruments)`;
}

function hideStemMidiNextSteps(): void {
  stemMidiNextStepsEl.style.display = "none";
  stemMidiLastImportedPath = null;
}

stemMidiOpenRefineBtn.addEventListener("click", () => {
  const path = stemMidiNextStepsEl.dataset.auralsongPath || stemMidiLastImportedPath;
  if (!path) return;
  setRoute("refine");
  void refineWorkspace.openForAuralSong(path);
});

stemMidiOpenCleanupBtn.addEventListener("click", () => {
  setRoute("play");
  // Best effort: trigger a refresh of the AuralSong list + auto-select the
  // imported one so the user lands on the details view.
  const path = stemMidiNextStepsEl.dataset.auralsongPath || stemMidiLastImportedPath;
  if (path) {
    void selectAuralSong(path).catch(() => {
      // If selection fails (timing race with refresh), the user can pick
      // the AuralSong manually from the list.
    });
  }
});

stemMidiPrecomputeBtn.addEventListener("click", async () => {
  const path = stemMidiNextStepsEl.dataset.auralsongPath || stemMidiLastImportedPath;
  if (!path) return;
  const instruments = (stemMidiNextStepsEl.dataset.instruments || "keys")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  stemMidiPrecomputeStatusEl.style.display = "block";
  stemMidiPrecomputeStatusEl.textContent =
    `running aural_ingest refine-candidates for ${instruments.join(", ")}...`;
  stemMidiPrecomputeBtn.disabled = true;
  try {
    type RefineRes = {
      ok: boolean;
      exit_code: number;
      stdout: string;
      stderr: string;
      payload?: unknown;
    };
    const res = await safeInvoke<RefineRes>("ingest_refine_candidates", {
      req: {
        container_path: path,
        instruments,
      },
    });
    if (res.ok) {
      // The sidecar writes a JSON status line to stdout; surface a brief
      // summary instead of dumping all of it.
      let summary = `precompute complete (exit ${res.exit_code})`;
      try {
        const lines = res.stdout.trim().split(/\r?\n/);
        const parsed = JSON.parse(lines[lines.length - 1] ?? "{}");
        if (parsed && typeof parsed === "object" && "instruments" in parsed) {
          const insts = parsed.instruments as Record<string, { regions?: number }>;
          const counts = Object.entries(insts)
            .map(([k, v]) => `${k}=${v.regions ?? "?"}r`)
            .join(", ");
          summary = `precompute complete: ${counts}`;
        }
      } catch {
        // not JSON -- fall back to the generic message
      }
      stemMidiPrecomputeStatusEl.textContent = summary;
    } else {
      const stderrTail = res.stderr.trim().split(/\r?\n/).slice(-3).join("\n");
      stemMidiPrecomputeStatusEl.textContent =
        `precompute failed (exit ${res.exit_code}):\n${stderrTail || "(no stderr)"}`;
    }
  } catch (e) {
    stemMidiPrecomputeStatusEl.textContent = `precompute failed: ${String(e)}`;
  } finally {
    stemMidiPrecomputeBtn.disabled = false;
  }
});
function ingestSourceExtensions(mode: IngestSubcommand): string[] {
  return ["wav", "mp3", "ogg", "flac", "m4a"];
}

function currentIngestMode(): DesktopAnalysisImportMode {
  const mode = ingestModeSelect.value;
  if (mode === "import-dir" || mode === "stem-dir") {
    return mode;
  }
  return "import";
}

function setIngestSourcePlaceholder(mode: DesktopAnalysisImportMode) {
  ingestBrowseSourceBtn.textContent = mode === "import" ? "Browse..." : "Pick folder...";
  if (mode === "stem-dir") {
    ingestSourcePathInput.placeholder = "C:\\music\\split-stems-folder";
    return;
  }
  if (mode === "import-dir") {
    ingestSourcePathInput.placeholder = "C:\\music\\folder";
  } else {
    ingestSourcePathInput.placeholder = "C:\\music\\song.wav";
  }
}

function inferIngestMetadataFromSelectedSource() {
  const sourcePath = ingestSourcePathInput.value.trim();
  if (!sourcePath) return;

  const guessed = inferIngestTitleArtistFromSourcePath(sourcePath);
  let applied = false;
  if (!ingestTitleInput.value.trim() && guessed.title) {
    ingestTitleInput.value = guessed.title;
    applied = true;
  }
  if (!ingestArtistInput.value.trim() && guessed.artist) {
    ingestArtistInput.value = guessed.artist;
    applied = true;
  }

  if (guessed.title || guessed.artist) {
    debugIngestConsole("metadata guess", {
      sourcePath,
      guessed,
      applied
    });
  }
}

function normalizeInputStemRole(role: string | null | undefined): InputStemRole | null {
  const key = (role ?? "").trim().toLowerCase();
  switch (key) {
    case "drums":
    case "bass":
    case "guitar":
    case "lead_guitar":
    case "rhythm_guitar":
    case "keys":
    case "vocals":
    case "other":
      return key;
    default:
      return null;
  }
}

function buildIngestInputStemPaths(inspection: RawSongFolderInspection): Partial<Record<InputStemRole, string>> {
  const paths: Partial<Record<InputStemRole, string>> = {};
  const assign = (role: InputStemRole | null, path: string) => {
    if (!role || paths[role]) return;
    paths[role] = path;
  };

  // Pass 1: stems whose Rust-side detected_role normalizes to a target
  // InputStemRole get first pick (e.g. detected "vocals" beats detected
  // "backing_vocals" which normalizes to null and is skipped here).
  //
  // The "backing_vocals" / "fx" / "synth" labels all currently fall to
  // null in normalizeInputStemRole, so the loop quietly enforces the
  // canonical-stem-wins rule today. Documenting it explicitly because
  // adding any of those raw labels to the InputStemRole switch later
  // would silently re-introduce the "Backing Vocals" wins regression
  // we just fixed in the Python driver.
  for (const part of inspection.stem_parts) {
    assign(normalizeInputStemRole(part.detected_role), part.path);
  }

  // Pass 2: fall back to the Rust-side game_role mapping. Both
  // "vocals" and "backing_vocals" detected_role values collapse to
  // game_role "vocals", so this pass only matters when pass 1 left
  // a role unfilled (e.g. only a "Backing Vocals" stem exists in the
  // folder). The early-return inside `assign` preserves whatever
  // pass 1 already chose.
  for (const part of inspection.stem_parts) {
    assign(normalizeInputStemRole(part.game_role), part.path);
  }

  return paths;
}

async function parseIngestConfigObject(configValue: string): Promise<Record<string, unknown>> {
  const trimmed = configValue.trim();
  if (!trimmed) return {};

  const raw = trimmed.startsWith("{") ? trimmed : await safeInvoke<string>("read_text_file", { path: trimmed });
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`invalid config JSON: ${String(error)}`);
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("config JSON must be an object");
  }
  return parsed as Record<string, unknown>;
}

async function buildStemDirConfig(
  configValue: string,
  inputStemPaths: Partial<Record<InputStemRole, string>>
): Promise<string> {
  const baseConfig = await parseIngestConfigObject(configValue);
  const existingStemPaths =
    baseConfig.input_stem_paths && typeof baseConfig.input_stem_paths === "object" && !Array.isArray(baseConfig.input_stem_paths)
      ? (baseConfig.input_stem_paths as Record<string, unknown>)
      : {};

  return JSON.stringify({
    ...baseConfig,
    disable_stem_separation: true,
    input_stem_paths: {
      ...existingStemPaths,
      ...inputStemPaths
    }
  });
}

async function inspectIngestStemFolder(folderPath: string): Promise<RawSongFolderInspection> {
  const inspection = await safeInvoke<RawSongFolderInspection>("inspect_raw_song_folder", { folderPath });
  ingestStemInspection = inspection;
  if (!ingestTitleInput.value.trim() && inspection.title_guess) {
    ingestTitleInput.value = inspection.title_guess;
  }

  const inputStemPaths = buildIngestInputStemPaths(inspection);
  const availableRoles = Object.keys(inputStemPaths);
  const warnings = inspection.warnings.length;
  const mixLabel = inspection.mix_wav_path ? "mix found" : "mix will be synthesized from stems";
  const rolesLabel = availableRoles.length ? availableRoles.join(", ") : "no reusable stem roles detected";
  const warningLabel = warnings ? ` | ${warnings} warning${warnings === 1 ? "" : "s"}` : "";
  setIngestStatus(
    `validated stem folder: ${inspection.stem_wav_paths.length} WAV(s) | ${mixLabel} | roles: ${rolesLabel}${warningLabel}`
  );
  return inspection;
}

async function ingestBrowseSource() {
  const mode = currentIngestMode();
  if (mode === "stem-dir") {
    const dir = await pickFolder();
    if (!dir) return;
    ingestSourcePathInput.value = dir;
    await inspectIngestStemFolder(dir);
    return;
  }

  if (mode === "import-dir") {
    const dir = await pickFolder();
    if (!dir) return;
    ingestSourcePathInput.value = dir;
    inferIngestMetadataFromSelectedSource();
    return;
  }

  const files = await pickFiles(ingestSourceExtensions(mode), false);
  if (!files.length) return;
  ingestSourcePathInput.value = files[0];
  inferIngestMetadataFromSelectedSource();
}

async function runIngestImport() {
  const mode = currentIngestMode();
  if (mode === "stem-dir") {
    const folderPath = ingestSourcePathInput.value.trim();
    if (!folderPath) {
      setIngestStatus("pick a pre-split stems folder first");
      setIngestSummary("error", "Import setup failed", "Pick a pre-split stems folder first.", {
        progressPct: 0,
        showLog: true
      });
      return;
    }
  } else {
    inferIngestMetadataFromSelectedSource();
  }

  let req: IngestImportRequest;
  try {
    if (mode === "stem-dir") {
      const folderPath = ingestSourcePathInput.value.trim();
      const inspection =
        ingestStemInspection?.folder_path === folderPath
          ? ingestStemInspection
          : await inspectIngestStemFolder(folderPath);
      const inputStemPaths = buildIngestInputStemPaths(inspection);
      if (!Object.keys(inputStemPaths).length) {
        setIngestStatus("no reusable stems were detected in that folder");
        setIngestSummary("error", "Import setup failed", "No reusable stems were detected in that folder.", {
          progressPct: 0,
          showLog: true
        });
        return;
      }

      req = buildIngestRequestFromForm({
        sourcePath: folderPath,
        mode: "import-dir",
        outAuralSongPath: ingestOutPathInput.value,
        profile: ingestProfileInput.value,
        config: await buildStemDirConfig(ingestConfigInput.value, inputStemPaths),
        title: ingestTitleInput.value,
        artist: ingestArtistInput.value,
        drumFilter: ingestDrumFilterSelect.value,
        melodicMethod: ingestMelodicMethodSelect.value,
        shiftsText: ingestShiftsInput.value,
        multiFilter: ingestMultiFilterInput.checked
      });
    } else {
      req = buildIngestRequestFromForm({
        sourcePath: ingestSourcePathInput.value,
        mode,
        outAuralSongPath: ingestOutPathInput.value,
        profile: ingestProfileInput.value,
        config: ingestConfigInput.value,
        title: ingestTitleInput.value,
        artist: ingestArtistInput.value,
        drumFilter: ingestDrumFilterSelect.value,
        melodicMethod: ingestMelodicMethodSelect.value,
        shiftsText: ingestShiftsInput.value,
        multiFilter: ingestMultiFilterInput.checked
      });
    }
  } catch (e) {
    setIngestStatus(String(e));
    setIngestSummary("error", "Import setup failed", String(e), { progressPct: 0, showLog: true });
    return;
  }

  ingestInFlight = true;
  ingestProgressPct = 0;
  resetIngestStatusLog(mode === "stem-dir" ? "running ingest sidecar from provided stems..." : "running ingest sidecar...");
  debugIngestConsole("invoke ingest_import", req);
  setIngestSummary("running", "Import running", "Preparing ingest sidecar and validating inputs.", {
    progressPct: 0,
    canReview: false,
    showLog: true
  });
  ingestRunBtn.disabled = true;
  ingestRunBtn.textContent = "Importing...";
  for (const card of importChoiceCards) card.disabled = true;
  try {
    await waitForUiPaint();
    const res = await ingestImport(req);
    debugIngestConsole("ingest finished", {
      ok: res.ok,
      exitCode: res.exit_code,
      command: res.command
    });
    if (res.stdout.trim()) {
      debugIngestConsole("stdout", res.stdout);
    }
    if (res.stderr.trim()) {
      debugIngestConsole("stderr", res.stderr);
    }
    if (res.ok) {
      const outPath = inferIngestOutPathFromCommand(res.command);
      if (outPath) {
        appendIngestStatusLine(`output: ${outPath}`);
        setIngestSummary(
          "success",
          "Import complete",
          `AuralSong created at ${outPath}. Review it in Cleanup & Edit.`,
          {
            progressPct: 100,
            canReview: true,
            showLog: false
          }
        );
      } else {
        setIngestSummary("success", "Import complete", "AuralSong created. Review it in Cleanup & Edit.", {
          progressPct: 100,
          canReview: true,
          showLog: false
        });
      }
      appendIngestStatusLine(`import complete (exit ${res.exit_code})`);
      // Surface any user-supplied reference MIDI we preserved (Suno
      // gameplay export is the canonical case). The Refine workspace will
      // render these as a guide layer alongside the sidecar's
      // per-instrument transcription candidates.
      const preservedMidis = res.preserved_reference_midis ?? [];
      if (preservedMidis.length > 0) {
        appendIngestStatusLine(
          `preserved ${preservedMidis.length} reference MIDI file(s) for Refine: ` +
            preservedMidis.join(", ")
        );
      }
      void refresh();
    } else {
      const stderr = res.stderr.trim() || "(no stderr)";
      const firstErrorLine = stderr.split(/\r?\n/, 1)[0] || "Ingest failed.";
      setIngestSummary("error", "Import failed", firstErrorLine, {
        progressPct: ingestProgressPct,
        canReview: false,
        showLog: true
      });
      appendIngestStatusLine(`import failed (exit ${res.exit_code})`);
      appendIngestStatusLine(stderr);
    }
  } catch (e) {
    errorConsole("ingest", "invoke ingest_import failed", e);
    setIngestSummary("error", "Import failed", String(e), {
      progressPct: ingestProgressPct,
      canReview: false,
      showLog: true
    });
    appendIngestStatusLine(String(e));
  } finally {
    ingestInFlight = false;
    ingestRunBtn.disabled = false;
    ingestRunBtn.textContent = "Analyze and import";
    for (const card of importChoiceCards) card.disabled = false;
  }
}

function resizeVizCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = vizCanvas.clientWidth || 800;
  const cssHeight = vizCanvas.clientHeight || 240;

  vizCanvas.width = Math.floor(cssWidth * dpr);
  vizCanvas.height = Math.floor(cssHeight * dpr);

  // Reset transform each time.
  vizCtx2d.setTransform(dpr, 0, 0, dpr, 0, 0);

  viz?.onResize(cssWidth, cssHeight, dpr);
}

function stopVisualizer(opts?: { keepStatus?: boolean }) {
  if (vizRaf != null) {
    cancelAnimationFrame(vizRaf);
    vizRaf = null;
  }
  lastFrameMs = null;

  try {
    viz?.dispose();
  } finally {
    viz = null;
  }

  // Cleanup any dynamically imported user plugin.
  if (loadedPluginDispose) {
    try {
      loadedPluginDispose();
    } finally {
      loadedPluginDispose = null;
    }
  }

  transport = { ...transport, t: 0, isPlaying: false };
  if (!opts?.keepStatus) {
    setVizStatus("(not running)");
  }
  vizStartBtn.disabled = false;
  vizStopBtn.disabled = true;
}

async function selectAuralSong(containerPath: string, opts?: { autoLoadAudio?: boolean }) {
  detailsEl.innerHTML = "Loading details...";
  currentLyrics = null;
  currentKeys = null;
  currentHarmony = null;
  currentVocalPitch = null;
  currentVocalPitchContour = null;
  currentSongTimeline = null;
  currentMelodicTracks = [];
  currentMelodicNotes = [];
  currentFingeringNotes = [];
  try {
    const details = await invoke<AuralSongDetails>("get_auralsong_details", {
      containerPath,
    });
    renderDetails(details);
    selectedAuralSongDetails = details;
    selectedDrumChartSelection = null;
    void renderCleanupAction(details).catch((e) =>
      setAuralSongEditorStatus(`readiness check failed: ${String(e)}`),
    );

    try {
      const lyr = await invoke<unknown>("read_auralsong_json", {
        containerPath,
        relPath: lyricsReadRelPath(containerPath, details),
      });
      currentLyrics = (lyr ?? null) as LyricsFile | null;
    } catch {
      currentLyrics = null;
    }

    currentKeys = await readOptionalArtifactJson(containerPath, details, "keys", "keys.json");
    currentHarmony = await readOptionalArtifactJson(containerPath, details, "harmony", "harmony.json");
    currentSongTimeline = await readOptionalArtifactJson(
      containerPath,
      details,
      "song_timeline",
      "song_timeline.json",
    );
    const manifestRaw = details.manifest_raw as ManifestArtifactPointers | undefined;
    if (!currentSongTimeline && isManifestPack(containerPath) && !manifestArtifactRelPath(manifestRaw, "song_timeline")) {
      try {
        currentSongTimeline = await invoke<unknown>("read_auralsong_json", {
          containerPath,
          relPath: "song_timeline.json",
        });
      } catch {
        currentSongTimeline = null;
      }
    }
    applySongMeterFromTimeline(currentSongTimeline);
    setHudKeyMode(details.manifest_raw, { keys: currentKeys, harmony: currentHarmony });

    const vocalPitchRel = artifactReadRelPath(containerPath, details, "vocal_pitch", "vocal_pitch.json");
    if (vocalPitchRel) {
      try {
        currentVocalPitch = await invoke<unknown>("read_auralsong_json", {
          containerPath,
          relPath: vocalPitchRel,
        });
      } catch {
        currentVocalPitch = null;
      }
    }

    const vocalPitchContourCandidates = isManifestPack(containerPath)
      ? [artifactReadRelPath(containerPath, details, "vocal_pitch_contour", "vocal_pitch_contour.json")].filter(
          (rel): rel is string => Boolean(rel),
        )
      : ["features/vocal_pitch_contour.json", "features/pitch_contour.json"];
    for (const vocalPitchContourRel of vocalPitchContourCandidates) {
      try {
        currentVocalPitchContour = await invoke<unknown>("read_auralsong_json", {
          containerPath,
          relPath: vocalPitchContourRel,
        });
        break;
      } catch {
        currentVocalPitchContour = null;
      }
    }

    currentMelodicTracks = await readMelodicTrackSelection(containerPath, details);
    currentMelodicNotes = melodicTracksToVisualizerNotes(currentMelodicTracks);

    if (details.has_aural_fingering) {
      const manifestRoles = fingeringRolesFromManifest(details.manifest_raw);
      const roles = manifestRoles.length > 0 ? manifestRoles : FINGERING_ROLES;
      const fingeringFiles = await loadFingeringForRoles(
        containerPath,
        roles,
        { warn: warnConsole },
        details.manifest_raw,
      );
      currentFingeringNotes = fingeringFilesToVisualizerNotes(fingeringFiles);
      if (currentMelodicTracks.length === 0) {
        currentMelodicTracks = fingeringFilesToMelodicTracks(fingeringFiles);
        currentMelodicNotes = melodicTracksToVisualizerNotes(currentMelodicTracks);
      }
    }

    selectedDrumChartSelection = await readDrumChartSelection(containerPath, details);
    renderCaps(details, selectedDrumChartSelection);
    applyInstrumentAvailability(details, selectedDrumChartSelection);
    renderPluginsWithAvailability(details);

    selectedAuralSongPath = containerPath;
    setAuralSongEditorStatus(`selected AuralSong: ${containerPath}`);
    logConsole("gamestate", "selected auralsong", {
      containerPath,
      autoLoadAudio: Boolean(opts?.autoLoadAudio),
    });
  } catch (e) {
    detailsEl.innerHTML = `<pre class="error">${escapeHtml(String(e))}</pre>`;
    setAuralSongEditorStatus(`selection failed: ${String(e)}`);
  }
}
async function loadAudioFromSelectedAuralSong() {
  if (!selectedAuralSongPath) {
    setAudioStatus("Select an AuralSong first (click Details)");
    return;
  }

  setAudioStatus("Loading audio…");
  audioLoadBtn.disabled = true;

  try {
    // Prefer the direct-native path (avoids sending large WAV bytes over IPC).
    if (transportController.loadAudioFromAuralSong) {
      await transportController.loadAudioFromAuralSong(selectedAuralSongPath);
      transportController.setPlaybackRate(currentPlaybackRate);

      // We no longer have the raw bytes in JS (by design).
      lastLoadedAudio = null;
      setAudioStatus(`loaded: ${selectedAuralSongPath}`);
    } else {
      // Fallback: read audio into JS, then send back into Rust for decode.
      const blob = await invoke<AudioBlob>("read_auralsong_audio", {
        containerPath: selectedAuralSongPath
      });

      // Phase 1.5: Native backend decodes MP3/OGG/WAV via Rust.
      // Convert number[] to Uint8Array
      const bytes = new Uint8Array(blob.bytes);
      const b = new Blob([bytes], { type: blob.mime });

      // Also load into the timebase-backed transport for accurate sync.
      lastLoadedAudio = { blob: b, mime: blob.mime };
      await transportController.loadAudio(lastLoadedAudio);
      transportController.setPlaybackRate(currentPlaybackRate);

      setAudioStatus(`loaded: ${blob.mime} (${bytes.byteLength} bytes)`);
    }

    audioPlayBtn.disabled = false;
    audioPauseBtn.disabled = false;
    audioStopBtn.disabled = false;
    audioSeekGoBtn.disabled = false;
    loopSetBtn.disabled = false;
    loopClearBtn.disabled = false;

    // If user hasn’t started a visualizer yet, auto-start the selected one.
    if (!viz) {
      void startVisualizer().catch((e) => {
        stopVisualizer({ keepStatus: true });
        setVizStatus(String(e));
      });
    }
  } catch (e) {
    setAudioStatus(String(e));
    throw e;
  } finally {
    audioLoadBtn.disabled = false;
  }
}

function stopAudio() {
  transportController.stop();
  transport = transportController.getState();
}

function currentSelectedPlugin(): PluginDescriptor {
  const idx = pluginSelect.selectedIndex;
  if (idx < 0 || idx >= availablePlugins.length) return availablePlugins[0];
  return availablePlugins[idx];
}

async function startVisualizer() {
  stopVisualizer();

  const plugin = currentSelectedPlugin();
  setVizStatus(`Loading plugin… (${plugin.id})`);

  if (plugin.id === "viz-lyrics" && !currentLyrics && !currentVocalPitch && !currentVocalPitchContour) {
    const ok = confirm(
      "This auralsong has no lyric animation or vocal pitch artifacts.\n\nGenerate lyrics now from a .txt lyrics file? (directory AuralSongs only)"
    );
    if (ok) {
      await generateLyricsForSelectedAuralSong();
    }
  }

  const loaded = await loadPlugin(plugin);
  loadedPluginDispose = loaded.dispose ?? null;

  viz = loaded.module.createVisualizer();

  await viz.init({
    canvas: vizCanvas,
    ctx2d: vizCtx2d,
    song: buildVizSongContext(),
    players: players.map((p) => ({
      id: p.id,
      name: p.name,
      instrument: p.instrument
    }))
  });
  resizeVizCanvas();

  transport = { ...transport, isPlaying: true, t: 0 };
  vizStartBtn.disabled = true;
  vizStopBtn.disabled = false;
  setVizStatus(`running: ${plugin.id}`);

  const tick = (ms: number) => {
    if (!viz) return;

    if (lastFrameMs == null) lastFrameMs = ms;
    const dt = (ms - lastFrameMs) / 1000;
    lastFrameMs = ms;

    transport = transportController.tick(dt);

    // If MIDI clock out is enabled, keep its BPM tracking the transport.
    // (Transport bpm will be influenced by external clock if follow is enabled.)
    void midiOutSetBpmIfNeeded(transport.bpm);

    metronome.update(transport);

    viz.update(dt, transport);
    viz.render({
      canvas: vizCanvas,
      ctx2d: vizCtx2d,
      width: vizCanvas.width / (window.devicePixelRatio || 1),
      height: vizCanvas.height / (window.devicePixelRatio || 1),
      dpr: window.devicePixelRatio || 1,
      state: transport,
    });

    vizRaf = requestAnimationFrame(tick);
  };

  vizRaf = requestAnimationFrame(tick);
}

window.addEventListener("resize", () => resizeVizCanvas());

vizStartBtn.addEventListener("click", () => {
  void startVisualizer().catch((e) => {
    // Important: stopVisualizer() normally resets the status text.
    // Preserve the error message so users can see what went wrong.
    stopVisualizer({ keepStatus: true });
    setVizStatus(String(e));
  });
});

vizStopBtn.addEventListener("click", () => stopVisualizer());

// Backend switching intentionally removed: desktop build uses Rust native audio engine only.

audioOutputHostRefreshBtn.addEventListener("click", () => {
  void refreshAudioOutputHosts();
});

audioOutputHostApplyBtn.addEventListener("click", () => {
  void applyAudioOutputHostSelection();
});

audioOutputDeviceRefreshBtn.addEventListener("click", () => {
  void refreshAudioOutputDevices();
});

audioOutputDeviceApplyBtn.addEventListener("click", () => {
  void applyAudioOutputDeviceSelection();
});

// Playback rate controls

playbackRateApplyBtn.addEventListener("click", () => {
  const r = Number(playbackRateInput.value);
  if (!Number.isFinite(r) || r <= 0) return;
  currentPlaybackRate = r;
  transportController.setPlaybackRate(r);
  transport = transportController.getState();
  setAudioStatus(`playbackRate set: ${r.toFixed(2)}x`);
});

// Metronome controls

metronomeEnabledInput.addEventListener("change", () => {
  metronome.setEnabled(metronomeEnabledInput.checked);
  setAudioStatus(`metronome: ${metronome.getEnabled() ? "on" : "off"}`);
});

metronomeVolumeInput.addEventListener("input", () => {
  const v = Number(metronomeVolumeInput.value);
  if (!Number.isFinite(v)) return;
  metronome.setVolume(v);
});

// MIDI follow defaults to enabled.
transportController.setFollowExternalClock(true);
midiFollowEnabledInput.addEventListener("change", () => {
  transportController.setFollowExternalClock(midiFollowEnabledInput.checked);
  setMidiStatus(`follow external clock: ${midiFollowEnabledInput.checked ? "on" : "off"}`);
});

midiInRefreshBtn.addEventListener("click", () => {
  void refreshMidiInputPorts();
});

midiInConnectBtn.addEventListener("click", () => {
  void connectMidiClockInput().catch((e) => setMidiStatus(String(e)));
});

midiInDisconnectBtn.addEventListener("click", () => {
  void disconnectMidiClockInput().catch((e) => setMidiStatus(String(e)));
});

midiInSysexEnabledInput.addEventListener("change", () => {
  if (midiConnected) {
    void connectMidiClockInput().catch((e) => setMidiStatus(String(e)));
  } else {
    setMidiStatus(`midi input SysEx: ${midiInSysexEnabledInput.checked ? "enabled (on next connect)" : "disabled"}`);
  }
});

midiOutEnabledInput.addEventListener("change", () => {
  midiOutEnabled = midiOutEnabledInput.checked;
  if (midiOutEnabled) {
    setMidiOutStatus("midi clock out: enabled");
    void refreshMidiOutputPorts();
  } else {
    // Stop sending clock when disabled.
    void midiOutStop();
    setMidiOutStatus("midi clock out: disabled");
  }
});

midiOutRefreshBtn.addEventListener("click", () => {
  void refreshMidiOutputPorts();
});

midiOutSelectBtn.addEventListener("click", () => {
  void selectMidiOutputPortAndPersist().catch((e) => setMidiOutStatus(String(e)));
});

midiOutSysexEnabledInput.addEventListener("change", () => {
  void setMidiOutSysex(midiOutSysexEnabledInput.checked, true).catch((e) => setMidiOutStatus(String(e)));
});

midiOutStartBtn.addEventListener("click", () => {
  midiOutEnabledInput.checked = true;
  midiOutEnabled = true;
  void midiOutStartOrContinue().catch((e) => setMidiOutStatus(String(e)));
});

midiOutContinueBtn.addEventListener("click", () => {
  midiOutEnabledInput.checked = true;
  midiOutEnabled = true;
  midiOutEverStarted = true;
  void selectMidiOutputPortAndPersist()
    .then(() => invoke("midi_clock_output_continue"))
    .then(() => {
      midiOutRunning = true;
      setMidiOutStatus("midi clock out: CONTINUE");
    })
    .catch((e) => setMidiOutStatus(String(e)));
});

midiOutStopBtn.addEventListener("click", () => {
  void midiOutStop().catch((e) => setMidiOutStatus(String(e)));
});

midiMsgNoteOnBtn.addEventListener("click", () => {
  void sendMidiNoteOnFromUi().catch((e) => setMidiOutStatus(String(e)));
});

midiMsgNoteOffBtn.addEventListener("click", () => {
  void sendMidiNoteOffFromUi().catch((e) => setMidiOutStatus(String(e)));
});

midiMsgCcSendBtn.addEventListener("click", () => {
  void sendMidiCcFromUi().catch((e) => setMidiOutStatus(String(e)));
});

midiMsgAllNotesOffBtn.addEventListener("click", () => {
  void sendMidiAllNotesOffFromUi().catch((e) => setMidiOutStatus(String(e)));
});

midiOutRawSendBtn.addEventListener("click", () => {
  void sendMidiRawFromUi().catch((e) => setMidiOutStatus(String(e)));
});

// MIDI clock event listeners (from Rust)
void listen("midi_clock_start", () => {
  transportController.setExternalClockRunning(true);
  setMidiStatus("midi clock: START");
});

void listen("midi_clock_stop", () => {
  transportController.setExternalClockRunning(false);
  setMidiStatus("midi clock: STOP");
});

void listen<{ bpm: number; raw_bpm: number; tempo_scale: number }>("midi_clock_bpm", (ev) => {
  transportController.setExternalClockBpm(ev.payload.bpm);
  // Keep transport bpm in sync even before ticks advance.
  transport = { ...transport, bpm: ev.payload.bpm };
});

void listen<{ dt_sec: number }>("midi_clock_tick", (ev) => {
  // Advance transport based on device tick timing.
  transportController.pushExternalClockDelta(ev.payload.dt_sec);
});

void listen<{ t_sec: number }>("midi_clock_seek", (ev) => {
  transportController.seekFromExternalClock(ev.payload.t_sec);
  setMidiStatus(`midi clock: SEEK ${ev.payload.t_sec.toFixed(2)}s`);
});

void listen<MidiInputMessageEvent>("midi_input_message", (ev) => {
  if (ev.payload.message_type !== "clock") {
    appendMidiInputEventLine(formatMidiInputMessage(ev.payload));
  }

  window.dispatchEvent(
    new CustomEvent<MidiInputMessageEvent>("auralprimer:midi-input", {
      detail: ev.payload,
    })
  );
});

void listen<IngestImportProgressEvent>("ingest_import_progress", (ev) => {
  if (!ingestInFlight) return;
  appendIngestStatusLine(formatIngestProgressEvent(ev.payload));
  const parsed = parseIngestProgressEvent(ev.payload);
  if (parsed) {
    ingestProgressPct = parsed.progressPct;
    setIngestSummary("running", parsed.title, parsed.detail, {
      progressPct: parsed.progressPct,
      canReview: false,
      showLog: true
    });
  }
});

// Audio controls

audioLoadBtn.addEventListener("click", () => {
  void loadAudioFromSelectedAuralSong().catch((e) => setAudioStatus(String(e)));
});

audioPlayBtn.addEventListener("click", () => {
  logConsole("play", "play requested");
  void transportController.play()
    .then(() => {
      logConsole("play", "play started");
      return midiOutStartOrContinue();
    })
    .catch((e) => setAudioStatus(String(e)));
});

audioPauseBtn.addEventListener("click", () => {
  transportController.pause();
  void midiOutStop();
  setAudioStatus("paused");
});

audioStopBtn.addEventListener("click", () => {
  stopAudio();
  void midiOutStop();
  void midiOutSeek(0);
  setAudioStatus("stopped");
});

audioSeekGoBtn.addEventListener("click", () => {
  const t = Number(audioSeekInput.value);
  if (!Number.isFinite(t)) {
    warnConsole("play", "seek ignored: invalid value", { value: audioSeekInput.value });
    return;
  }
  transportController.seek(t);
  void midiOutSeek(t);
  setAudioStatus(`seek: ${t.toFixed(2)}s`);
});

loopSetBtn.addEventListener("click", () => {
  const t0 = Number(loopT0Input.value);
  const t1 = Number(loopT1Input.value);
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return;

  transportController.setLoop({ t0, t1 });
  transport = transportController.getState();
  setAudioStatus(`loop set: ${transport.loop?.t0 ?? 0}..${transport.loop?.t1 ?? 0}`);
});

loopClearBtn.addEventListener("click", () => {
  transportController.setLoop(undefined);
  transport = transportController.getState();
  setAudioStatus("loop cleared");
});

function renderPreferredModelPacks() {
  preferredModelsEl.innerHTML = `
    <ul>
      ${PREFERRED_MODEL_PACKS.map((p) => {
        const disabled = p.url ? "" : "disabled";
        const hint = p.url ? "" : "(no download url configured yet)";
        return `
          <li>
            <div class="row">
              <div class="grow">
                <strong>${escapeHtml(p.id)}</strong> <span class="meta">v${escapeHtml(p.version)}</span>
                <div class="meta">${escapeHtml(p.description ?? "")} ${escapeHtml(hint)}</div>
              </div>
              <button class="installPreferred" data-id="${escapeHtml(p.id)}" ${disabled}>Install</button>
            </div>
          </li>
        `;
      }).join("\n")}
    </ul>
  `;

  for (const btn of Array.from(preferredModelsEl.querySelectorAll("button.installPreferred"))) {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-id");
      if (!id) return;
      const pack = PREFERRED_MODEL_PACKS.find((p) => p.id === id);
      if (!pack) return;
      void installModelPackFromUrl(pack)
        .then(() => Promise.allSettled([refreshModels(), refreshIngestRuntimeStatus()]))
        .catch((e) => {
          modelsStatusEl.textContent = String(e);
        });
    });
  }
}

async function refreshModels() {
  modelsStatusEl.textContent = "Loading…";
  try {
    const installed = await listInstalledModelPacks();
    modelsStatusEl.textContent = formatInstalledModelPacks(installed);
  } catch (e) {
    modelsStatusEl.textContent = String(e);
  }
}

modelsRefreshBtn.addEventListener("click", () => {
  void Promise.allSettled([refreshModels(), refreshIngestRuntimeStatus()]);
});

modelpackImportBtn.addEventListener("click", () => {
  const p = modelpackPathInput.value;
  void installModelPackFromPath(p)
    .then(() => Promise.allSettled([refreshModels(), refreshIngestRuntimeStatus()]))
    .catch((e) => {
      modelsStatusEl.textContent = String(e);
    });
});

ingestRuntimeRefreshBtn.addEventListener("click", () => {
  void refreshIngestRuntimeStatus();
});

ingestOpenCleanupBtn.addEventListener("click", () => {
  setRoute("home");
});

// Initialize sizing for first paint.
resizeVizCanvas();
renderPreferredModelPacks();
void Promise.allSettled([refreshModels(), refreshIngestRuntimeStatus()]);

// Refine workspace -- owns the Cleanup & Edit per-region cleanup flow.
// Initialized late because its template lives inside `root.innerHTML` above,
// which the const above the template literal needs to be in scope for.
// Wired so the back button returns to Cleanup & Edit (the `play` route),
// and so status updates flow into the same audioStatusEl the rest of
// Studio uses for one-line status feedback.
const refineWorkspace: RefineWorkspaceHandle = initRefineWorkspace({
  setStatus: (msg) => {
    audioStatusEl.textContent = msg;
    logConsole("gamestate", `refine: ${msg}`);
  },
  onBack: () => setRoute("play"),
});

// Lyric-timing workspace -- a DAW-style editor for feedpak lyrics (a port of
// AuraWave's "Waveform timing editor"). Like the refine workspace it lives
// inside `root.innerHTML` so it's initialized late, and its back button
// returns to Cleanup & Edit (the `play` route).
const lyricTimingWorkspace: LyricTimingHandle = initLyricTimingWorkspace({
  setStatus: (msg) => {
    audioStatusEl.textContent = msg;
    logConsole("gamestate", `lyrics: ${msg}`);
  },
  onBack: () => setRoute("play"),
});

async function refresh() {
  statusEl.textContent = "Loading…";
  listEl.innerHTML = "";
  detailsEl.innerHTML = "";

  try {
    const songsFolder = await invoke<string>("get_songs_folder");
    const entries = await invoke<AuralSongScanEntry[]>("scan_auralsongs");

    // Prefer the built-in demo song on first load so the app is immediately playable.
    // Order: demo first, then the rest alphabetically by title.
    entries.sort((a, b) => {
      const ad = isDemoAuralSong(a);
      const bd = isDemoAuralSong(b);
      if (ad !== bd) return ad ? -1 : 1;
      const at = (a.manifest?.title ?? "").toLowerCase();
      const bt = (b.manifest?.title ?? "").toLowerCase();
      return at.localeCompare(bt);
    });

    songsFolderInput.value = songsFolder;
    statusEl.textContent = `songsFolder: ${songsFolder}\ncount: ${entries.length}`;

    // ---- Status table: Song | Spectrogram | Candidates | Lyrics | action ----
    let sortKey: "title" | "spec" | "cand" | "lyr" = "title";
    let sortDir: 1 | -1 = 1;

    const rowHtml = (e: AuralSongScanEntry): string => {
      const title = e.manifest?.title ?? "(missing title)";
      const artist = e.manifest?.artist ?? "";
      const selected = e.container_path === selectedAuralSongPath ? " isSelected" : "";
      if (!e.ok) {
        const err = escapeHtml(e.error || "invalid pack");
        return `<tr class="cleanupSongRow isInvalid" data-path="${escapeHtml(e.container_path)}">`
          + `<td class="ctSong"><div class="cleanupSongTitle">${escapeHtml(title)}</div>`
          + `<div class="meta ctErr" title="${err}">${err}</div></td>`
          + `<td class="ctStat ctInvalid" colspan="3"><i class="ti ti-alert-triangle" aria-hidden="true"></i> Invalid</td>`
          + `<td class="ctAction"></td></tr>`;
      }
      const known = cleanupRowReady.get(e.container_path);
      const cell = (k: keyof RowReady): string =>
        ctStatCell(known ? (known[k] ? "yes" : "no") : "pending");
      let act = "select";
      let actCls = "ctBtn";
      let actLabel = `Open <i class="ti ti-chevron-right" aria-hidden="true"></i>`;
      if (known) {
        if (known.prep) { act = "prep"; actCls = "ctBtn ctBuild"; actLabel = "Prep notes"; }
        else if (known.spec && known.cand) { act = "open"; actCls = "ctBtn ctOpen"; }
        else if (!known.spec) { act = "build"; actCls = "ctBtn ctBuild"; actLabel = "Build"; }
        else { actLabel = `Prep <i class="ti ti-chevron-right" aria-hidden="true"></i>`; }
      }
      return `<tr class="cleanupSongRow${selected}" data-path="${escapeHtml(e.container_path)}" role="button" tabindex="0">`
        + `<td class="ctSong"><div class="cleanupSongTitle">${escapeHtml(title)}</div>`
        + `<div class="meta cleanupSongArtist">${escapeHtml(artist || "(unknown artist)")}</div></td>`
        + `<td class="ctStat" data-cell="spec">${cell("spec")}</td>`
        + `<td class="ctStat" data-cell="cand">${cell("cand")}</td>`
        + `<td class="ctStat" data-cell="lyr">${cell("lyr")}</td>`
        + `<td class="ctAction"><button type="button" class="${actCls}" data-act="${act}">${actLabel}</button></td></tr>`;
    };

    const sortedEntries = (): AuralSongScanEntry[] => {
      const arr = [...entries];
      arr.sort((a, b) => {
        if (a.ok !== b.ok) return a.ok ? -1 : 1; // valid songs always first
        if (sortKey === "title") {
          const at = (a.manifest?.title ?? "").toLowerCase();
          const bt = (b.manifest?.title ?? "").toLowerCase();
          return at.localeCompare(bt) * sortDir;
        }
        const av = cleanupRowReady.get(a.container_path)?.[sortKey] ? 1 : 0;
        const bv = cleanupRowReady.get(b.container_path)?.[sortKey] ? 1 : 0;
        return (bv - av) * sortDir;
      });
      return arr;
    };

    const inlineBuild = async (path: string, btn: HTMLButtonElement): Promise<void> => {
      btn.disabled = true;
      btn.textContent = "Building…";
      const res = await buildSpectrogramForSong(path);
      statusEl.textContent = res.msg;
      try {
        applyRowReadiness(path, await probeRowReadiness(path));
      } catch {
        /* leave the row's cells as-is */
      }
      if (path === selectedAuralSongPath && selectedAuralSongDetails) {
        void renderCleanupAction(selectedAuralSongDetails);
      }
    };

    const inlinePrep = async (path: string, btn: HTMLButtonElement): Promise<void> => {
      btn.disabled = true;
      btn.textContent = "Prepping…";
      const res = await prepArrangementsForSong(path);
      statusEl.textContent = res.msg;
      try {
        applyRowReadiness(path, await probeRowReadiness(path));
      } catch {
        /* leave the row's cells as-is */
      }
      if (path === selectedAuralSongPath && selectedAuralSongDetails) {
        void renderCleanupAction(selectedAuralSongDetails);
      }
    };

    const wireRows = (): void => {
      for (const row of Array.from(listEl.querySelectorAll<HTMLTableRowElement>("tr.cleanupSongRow"))) {
        if (row.classList.contains("isInvalid")) continue;
        const path = row.getAttribute("data-path");
        if (!path) continue;
        const select = (): void => {
          for (const r of Array.from(listEl.querySelectorAll("tr.cleanupSongRow"))) {
            r.classList.toggle("isSelected", r === row);
          }
          void selectAuralSong(path, { autoLoadAudio: false });
        };
        row.addEventListener("click", (ev) => {
          const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-act]");
          if (btn) {
            ev.stopPropagation();
            const act = btn.dataset.act;
            if (act === "open") { openCleanupEditorForSong(path); return; }
            if (act === "prep") { void inlinePrep(path, btn); return; }
            if (act === "build") { void inlineBuild(path, btn); return; }
            select();
            return;
          }
          select();
        });
        row.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(); }
        });
      }
    };

    const renderTable = (): void => {
      const arrow = (k: string): string => (sortKey === k ? (sortDir === 1 ? " ▲" : " ▼") : "");
      listEl.innerHTML = `
        <table class="cleanupTable">
          <thead><tr>
            <th class="ctColSong" data-sort="title">Song${arrow("title")}</th>
            <th class="ctColStat" data-sort="spec" title="Spectrogram overlay">Spec${arrow("spec")}</th>
            <th class="ctColStat" data-sort="cand" title="Note candidates">Cand${arrow("cand")}</th>
            <th class="ctColStat" data-sort="lyr" title="Timed lyrics">Lyrics${arrow("lyr")}</th>
            <th class="ctColAct"></th>
          </tr></thead>
          <tbody>${sortedEntries().map(rowHtml).join("")}</tbody>
        </table>`;
      wireRows();
      for (const th of Array.from(listEl.querySelectorAll<HTMLTableCellElement>("th[data-sort]"))) {
        th.addEventListener("click", () => {
          const k = th.getAttribute("data-sort") as typeof sortKey;
          if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
          else { sortKey = k; sortDir = k === "title" ? 1 : -1; }
          renderTable();
        });
      }
    };

    renderTable();

    // Probe readiness for each valid song and fill its status cells + action.
    void Promise.all(
      entries
        .filter((e) => e.ok)
        .map(async (e) => {
          try {
            applyRowReadiness(e.container_path, await probeRowReadiness(e.container_path));
          } catch {
            /* leave the row pending if probing fails */
          }
        }),
    );

    // UX: if nothing is selected yet, preload the first valid AuralSong.
    if (!selectedAuralSongPath) {
      const firstOk = entries.find((e) => e.ok);
      if (firstOk?.container_path) {
        await selectAuralSong(firstOk.container_path, { autoLoadAudio: false });
        listEl.querySelector<HTMLTableRowElement>(
          `tr.cleanupSongRow[data-path="${cssEscape(firstOk.container_path)}"]`,
        )?.classList.add("isSelected");
      }
    }
  } catch (e) {
    statusEl.textContent = String(e);
    listEl.innerHTML = `
      <p>
        This view must be run via <code>tauri dev</code> (the browser-only Vite dev server cannot invoke Rust commands).
      </p>
    `;
  }
}

refreshBtn.addEventListener("click", () => void refresh());

setOverrideBtn.addEventListener("click", () => {
  const v = songsFolderInput.value.trim();
  if (!v) return;
  void invoke("set_songs_folder_override", { songsFolder: v }).then(() => refresh());
});

clearOverrideBtn.addEventListener("click", () => {
  void invoke("clear_songs_folder_override").then(() => refresh());
});

pluginRefreshBtn.addEventListener("click", () => {
  void refreshPlugins();
});

ingestModeSelect.addEventListener("change", () => {
  ingestStemInspection = null;
  const mode = currentIngestMode();
  setIngestSourcePlaceholder(mode);
  syncImportKindFromMode();
});

for (const card of importChoiceCards) {
  card.addEventListener("click", () => {
    setImportKind((card.dataset.importKind ?? "suno") as ImportKind);
  });
}

importOpenModelsBtn.addEventListener("click", () => setRoute("config"));

setImportKind("suno");
ingestSourcePathInput.addEventListener("change", () => {
  if (currentIngestMode() === "stem-dir") {
    ingestStemInspection = null;
    const folderPath = ingestSourcePathInput.value.trim();
    if (!folderPath) return;
    void inspectIngestStemFolder(folderPath).catch((e) => setIngestStatus(String(e)));
    return;
  }
  inferIngestMetadataFromSelectedSource();
});

ingestBrowseSourceBtn.addEventListener("click", () => {
  void ingestBrowseSource().catch((e) => setIngestStatus(String(e)));
});

ingestRunBtn.addEventListener("click", () => {
  void runIngestImport();
});

stemMidiPickFolderBtn.addEventListener("click", () => {
  void (async () => {
    const folder = await pickFolder();
    if (!folder) return;
    stemMidiFolderPath = folder;
    stemMidiInspection = null;
    renderStemMidiSelection();
    await inspectStemMidiFolder(folder);
  })().catch((e) => setStemMidiStatus(String(e)));
});

stemMidiImportBtn.addEventListener("click", () => {
  void stemMidiCreateAuralSong().catch((e) => setStemMidiStatus(String(e)));
});

setIngestSourcePlaceholder(currentIngestMode());

renderStemMidiSelection();
setStemMidiStatus("(not imported)");

// Ensure we stop background threads on window close.
window.addEventListener("beforeunload", () => {
  void shutdownMidiOutputService();
  // Best-effort: stop native audio thread if it was initialized.
  try {
    void invoke("native_audio_shutdown");
  } catch {
    // ignore
  }
});

setRoute("make");

void refresh();
