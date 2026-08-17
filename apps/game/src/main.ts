import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import { isManifestPack } from "@auralprimer/auralsong/packKind";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { TransportController } from "./transportController";
import { initAvCalibration } from "@auralprimer/av-sync";
import {
  getAudioOffsetMs,
  getVideoOffsetMs,
  getEffectiveOffsetMs,
  getEffectiveOffsetSec,
  loadAvCalibration,
  setAvCalibration,
} from "./avOffset";
import type { TransportTimebase } from "./audioBackend";
import { HtmlAudioTimebase } from "./htmlAudioTimebase";
import { NativeAudioTimebase } from "./nativeAudioTimebase";
import { Metronome } from "./metronome";
// extractKeyModeFromManifest now consumed inside songDetailsView.ts.
// Modelpack list/install wiring lives in modelsPanel.ts (Phase 2.K).
import { initModelsPanel, type ModelsPanelHandle } from "./modelsPanel";
import { initPendingModelInstallBanner } from "./pendingModelInstalls";
// BUILTIN_PLUGINS + scanBundledPlugins + scanUserPlugins now live inside pluginsPanel.ts.
import { type PluginDescriptor, loadPlugin } from "./plugins";
import { listen } from "@tauri-apps/api/event";
// @tauri-apps/plugin-dialog import removed -- pickFolder/pickFiles no longer
// needed (file/folder pickers moved into respective panel modules).
// MIDI parsing helpers (selectDrumChartFromMidiBytes / selectMelodicTracksFromMidiBytes
// / parseMidiTracksFromBytes / applyRefinementsToMelodicTracks) are consumed by
// songChartLoader.ts (Phase 2.T). loadRefinementsForRoles likewise.
import type { DrumChartSelection, MelodicTrackSelection, InstrumentRole } from "./chartLoader";
// TabRenderer + the melodic-surface logic live in playSurfaceController.ts (Phase 2.O).
import { initScrollSpeedController } from "./scrollSpeedController";
import { initTransportHotkeys } from "./transportHotkeys";
import { initAudioOutputPanel, type AudioOutputPanelHandle } from "./audioOutputPanel";
import { initSongLibraryPanel, type SongLibraryPanelHandle } from "./songLibraryPanel";
import {
  initPlayersPanel,
  defaultPluginIdForInstrument,
  DEFAULT_PLUGIN_ID,
  DRUM_HIGHWAY_PLUGIN_ID,
  type Player,
  type PlayersPanelHandle,
} from "./playersPanel";
import { INSTRUMENT_LABELS, type Instrument } from "./instrumentTypes";
import { initLyricsPanel, type LyricsPanelHandle } from "./lyricsPanel";
import { createConsoleBridge, type ConsoleLogCategory } from "./consoleBridge";
import { initPauseMenu, type PauseMenuHandle } from "./pauseMenu";
// instrumentHints helpers are consumed by capsPanel.ts (Phase 2.L); main.ts
// no longer calls them directly.
import { initCapsPanel, type CapsPanelHandle, type SongCapabilities, type AuralSongChartsByPath } from "./capsPanel";
import { initPluginsPanel, type PluginsPanelHandle } from "./pluginsPanel";
import { initSongDetailsView, type SongDetailsViewHandle } from "./songDetailsView";
import { initPlaySurfaceController, type PlaySurfaceControllerHandle, type DisplayMode } from "./playSurfaceController";
import { initRouteController, type RouteControllerHandle, type Route } from "./routeController";
import { initAudioTransportPanel, type AudioTransportPanelHandle } from "./audioTransportPanel";
import { appShellHtml } from "./appShellHtml";
import { buildVizSongContext } from "./vizSongContext";
import { readSongChartSelection } from "./songChartLoader";
import { initSecondaryStagesController, type SecondaryStagesControllerHandle } from "./secondaryStagesController";
import { initPlaybackRateAndMetronomePanel } from "./playbackRateAndMetronomePanel";
import { initMidiPanel, type MidiPanelHandle } from "./midiPanel";
import type { ManifestSummary } from "./manifestTypes";
import type { AuralSongDetails } from "./auralsong";
// MidiInputStateTracker + format helpers are consumed by midiPanel.ts (Phase 2.F).
import { loadAuralSongAudioIntoTransport } from "./auralSongAudioLoader";
import { initStemMixerPanel } from "./stemMixerPanel";
import { startSelectedSongSessionFlow } from "./sessionStart";

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

// pickFolder + pickFiles were orphaned by earlier panel extractions
// (songLibraryPanel, ingest flows moved out). Removed.

// ManifestSummary lives in ./manifestTypes (shared with songLibraryPanel).
// AuralSongScanEntry + isDemoAuralSong moved into songLibraryPanel.ts.
// AuralSongChartsByPath + SongCapabilities live in capsPanel.ts (re-exported above).

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

type AudioBlob = {
  mime: string;
  bytes: number[];
};

// MidiBlob type moved into songChartLoader.ts -- main.ts no longer invokes
// `read_auralsong_mid` directly.


// Import-flow types (raw-song folder, stem+MIDI, ingest sidecar) and the
// melodic-method dropdown options live in AuralStudio (apps/desktop). See
// `spec.md §1.1` and `wip.md` for the boundary rule: AuralPrimer (this app)
// does not include raw audio/chart import or song-creation flows.

const root = document.getElementById("app");
if (!root) throw new Error("missing #app");
root.innerHTML = appShellHtml();


// In browser-only mode, make it explicit and disable desktop-only actions.
{
  const banner = document.getElementById("runtimeBanner") as HTMLDivElement | null;
  if (banner && !haveTauri()) {
    banner.innerHTML = `
      <div class="runtimeBannerInner">
        <strong>Browser mode</strong> â€” you opened the web build (no Tauri runtime detected).<br />
        Desktop-only features (file picker, AuralSong scanning, native audio, etc.) are disabled here.
        <div class="meta">Run <code>npm run game:dev</code> or launch the installed app to use these features.</div>
      </div>
    `;
  }
}

// Route type + currentRoute state live in routeController.ts (Phase 2.P).

// Console bridge (mirror frontend logs to Rust via frontend_log) lives in
// consoleBridge.ts. The init below returns a {log, warn, error} handle; the
// thin wrapper functions below preserve the existing logConsole/warnConsole/
// errorConsole call shape so the rest of main.ts doesn't change.
const consoleBridge = createConsoleBridge({ haveTauri });
function logConsole(category: ConsoleLogCategory, message: string, details?: unknown): void {
  consoleBridge.log(category, message, details);
}
function warnConsole(category: ConsoleLogCategory, message: string, details?: unknown): void {
  consoleBridge.warn(category, message, details);
}
function errorConsole(category: ConsoleLogCategory, message: string, details?: unknown): void {
  consoleBridge.error(category, message, details);
}

// setRoute / openPlaySongFlow / exitApplication + the 6 nav button listeners
// live in routeController.ts (Phase 2.P). The route controller is constructed
// further down (needs pauseMenu / songLibraryPanel handles which are built
// later in the boot sequence).

// #hudKeyMode lives in songDetailsView.ts (Phase 2.N).

const vizCanvas = document.getElementById("viz") as HTMLCanvasElement;
const playerStagesEl = document.getElementById("playerStages") as HTMLDivElement;
const playLyricsEl = document.getElementById("playLyrics") as HTMLDivElement;
const playLyricsCurrentEl = document.getElementById("playLyricsCurrent") as HTMLDivElement;
const playLyricsNextEl = document.getElementById("playLyricsNext") as HTMLDivElement;
const vizStatusEl = document.getElementById("vizStatus") as HTMLPreElement;
const instrumentSelectorEl = document.getElementById("instrumentSelector") as HTMLDivElement;
const tabContainerEl = document.getElementById("tabContainer") as HTMLDivElement;
const displayModeToggleEl = document.getElementById("displayModeToggle") as HTMLDivElement | null;

// Persisted "Piano roll ↔ Sheet music" display mode for the melodic surface.
const DISPLAY_MODE_STORAGE_KEY = "auralprimer.displayMode";
function readPersistedDisplayMode(): DisplayMode {
  try {
    const raw = window.localStorage.getItem(DISPLAY_MODE_STORAGE_KEY);
    if (raw === "sheet" || raw === "piano") return raw;
  } catch {
    // localStorage may be unavailable in some embedded webviews; default safely.
  }
  return "piano";
}
function persistDisplayMode(mode: DisplayMode): void {
  try {
    window.localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, mode);
  } catch {
    // Best-effort.
  }
}
const appMainEl = document.querySelector(".appMain") as HTMLDivElement;
const pluginSelect = document.getElementById("pluginSelect") as HTMLSelectElement;
const pluginRefreshBtn = document.getElementById("pluginRefresh") as HTMLButtonElement;
const vizStartBtn = document.getElementById("vizStart") as HTMLButtonElement;
const vizStopBtn = document.getElementById("vizStop") as HTMLButtonElement;

const playLayoutEl = document.getElementById("playLayout") as HTMLDivElement;
const toggleFocusBtn = document.getElementById("toggleFocus") as HTMLButtonElement;
// #players list is rendered by playersPanel.ts but applyInstrumentAvailability
// still queries .playerChip across it, so keep the reference handle here.
const playersEl = document.getElementById("players") as HTMLDivElement;
// #addPlayer button is owned by playersPanel.ts (click handler lives there).

const capsEl = document.createElement("div");
capsEl.id = "songCaps";
capsEl.className = "caps";
// Live in the left rail (just below the song-info block) rather than the
// stage. Above the canvas it steals vertical space from the gameplay
// highway; in the rail it fills the otherwise-empty lower area. Fall back
// to the stage if the rail markup ever changes.
const railSongMetaEl = document.querySelector(".bandSetupRail .songSetupMeta");
if (railSongMetaEl) {
  railSongMetaEl.insertAdjacentElement("afterend", capsEl);
} else {
  playerStagesEl.insertAdjacentElement("beforebegin", capsEl);
}

const playSurfaceEl = document.createElement("div");
playSurfaceEl.id = "playSurface";
playSurfaceEl.className = "playSurface";
playerStagesEl.insertAdjacentElement("beforebegin", playSurfaceEl);
playSurfaceEl.append(playerStagesEl, playLyricsEl, vizStatusEl, instrumentSelectorEl, tabContainerEl);

// Audio transport DOM (Load/Play/Pause/Stop/Seek + loop set/clear) and
// #audioStatus / #vizStatus live in audioTransportPanel.ts (Phase 2.Q).
const audioBackendSelect = document.getElementById("audioBackend") as HTMLSelectElement;
// audio output host + device picker DOM + wiring lives in audioOutputPanel.ts
// #playbackRate / #playbackRateApply / #metronomeEnabled / #metronomeVolume
// DOM + listeners all live in playbackRateAndMetronomePanel.ts (Phase 2.V).
// scrollSpeed slider DOM + wiring lives in scrollSpeedController.ts
// #metronomeEnabled / #metronomeVolume DOM lives in playbackRateAndMetronomePanel.ts.

// All MIDI panel DOM + wiring lives in midiPanel.ts (Phase 2.F).

// Modelpack DOM (#modelsRefresh / #preferredModels / #modelsStatus /
// #modelpackPath / #modelpackImport) lives in modelsPanel.ts. The init call
// is below (needs escapeHtml defined first).

// Import-flow DOM lookups (sidecar ingest, analysis import, stem+MIDI
// creator) intentionally do not exist in this app. They live in AuralStudio
// (apps/desktop). See `spec.md §1.1`.

// status/list/details DOM lives in songLibraryPanel.ts; selected-song
// label + path DOM lives in songDetailsView.ts (Phase 2.N).
// refresh button lives in songLibraryPanel.ts
const playStartBtn = document.getElementById("playStart") as HTMLButtonElement;
// Pause-menu DOM + state + behavior live in pauseMenu.ts (Phase 2.J).
// The init call lives further down (after deps are ready).
// songsFolder + override DOM lives in songLibraryPanel.ts

// Song library panel (left column). Init early so the !haveTauri() disable
// block below can call disableFolderControls(), and so showSongLibraryStep
// — invoked at boot via queueMicrotask — has a panel to refresh.
const songLibraryPanel: SongLibraryPanelHandle = initSongLibraryPanel({
  selectedAuralSongPath: () => selectedAuralSongPath,
  onSongSelected: selectAuralSong,
  haveTauri,
  escapeHtml,
});

// Disable desktop-only actions when running without the Tauri runtime.
if (!haveTauri()) {
  songLibraryPanel.disableFolderControls();
  playStartBtn.disabled = true;

  // audio output panel disables itself at boot-time refreshAll() when
  // !haveTauri(), so no explicit call needed here.
}

// renderPlugins + refreshPlugins live in pluginsPanel.ts (Phase 2.M).

function escapeHtml(s: string): string {
  // Avoid hardcoding HTML entity strings here (some tooling auto-decodes them).
  // Using DOM encoding keeps this correct and simple.
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// Lyrics rendering lives in lyricsPanel.ts. Host calls
// lyricsPanel.render(t, currentLyrics, currentPluginId) in the tick loop
// and lyricsPanel.clear() when the visualizer stops.
const lyricsPanel: LyricsPanelHandle = initLyricsPanel();
function renderPlaybackLyrics(t: number): void {
  lyricsPanel.render(t, currentLyrics, currentSelectedPlugin()?.id ?? null);
}
function clearPlaybackLyrics(): void {
  lyricsPanel.clear();
}

// yesNo + setHudKeyMode + renderDetails live in songDetailsView.ts (Phase 2.N).
// formatModelPackLicense + formatInstalledModelPacks live in modelsPanel.ts.

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
// Player 1 always uses the static `#viz` canvas (so single-canvas refs +
// integration tests keep working). Players 2..N get dynamically-created
// sibling canvases appended to `#playerStages`, each with its own
// Visualizer instance keyed off that player's instrument. The primary
// stage's viz lifecycle lives in the existing `viz` field above; secondary
// stages live in this array. See per-player stage logic in
// `startVisualizer` / `stopVisualizer` / `resizeVizCanvas` / tick loop.
// SecondaryStage type + secondaryStages array live in secondaryStagesController.ts (Phase 2.U).
let vizRaf: number | null = null;
let lastFrameMs: number | null = null;
let selectedAuralSongPath: string | null = null;
let selectedAuralSongDetails: AuralSongDetails | null = null;
let selectedDrumChartSelection: DrumChartSelection | null = null;
let selectedMelodicTracks: MelodicTrackSelection[] = [];
// tabRenderer + activeTabInstrument now live inside playSurfaceController.
let selectedAuralSongCharts: AuralSongChartsByPath | null = null;
let selectedSongPreloadPromise: Promise<void> | null = null;
let selectedSongPreloadPath: string | null = null;

// setSelectedSongSetupLabel lives in songDetailsView.ts (Phase 2.N).

function setSelectedSongCard(containerPath: string | null): void {
  songLibraryPanel.setSelectedSongCard(containerPath);
}

const songDetailsView: SongDetailsViewHandle = initSongDetailsView({
  songLibraryPanel,
  consoleBridge,
  escapeHtml,
});
songDetailsView.setSelectedSongSetupLabel(null, null);

// availablePlugins + pluginSelectionMode now live inside pluginsPanel.ts.
// Plugin id constants + defaultPluginIdForInstrument live in playersPanel.ts.
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

// Desktop default: use Rust native audio engine.
let currentTimebase: TransportTimebase = new NativeAudioTimebase({ sampleRateHz: 48_000, channels: 2 });
let transportController = new TransportController(currentTimebase, {
  bpm: 120,
  timeSignature: [4, 4]
});
const nativeTimebase = currentTimebase instanceof NativeAudioTimebase ? currentTimebase : null;
// audio output host + device picker (DOM + wiring + state in audioOutputPanel.ts)
const audioOutputPanel: AudioOutputPanelHandle = initAudioOutputPanel({
  nativeTimebase,
  haveTauri,
  setAudioStatus,
  escapeHtml,
});

const midiPanel: MidiPanelHandle = initMidiPanel({
  transportController,
  haveTauri,
  escapeHtml,
  // Keep the cached transport state's bpm in sync when the external clock
  // publishes a new tempo, so consumers reading `transport.bpm` (e.g. the
  // tick loop, lyric renderer) see it on the next frame.
  onExternalBpmChange: (bpm) => {
    transport = { ...transport, bpm };
  },
});
if (!haveTauri()) {
  midiPanel.disableAll();
}

// Pause-menu overlay (DOM + state + behavior in pauseMenu.ts). The deps'
// callbacks are arrow lambdas, so the `let`s they reference (transport,
// selectedAuralSongPath, lastLoadedAuralSongPath) only need to exist at
// CALL time, not init time -- and the function references (transportController,
// midiPanel, stopAudio, showSongLibraryStep, setAudioStatus, setVizStatus,
// errorConsole) hoist or are already initialized above.
const pauseMenu: PauseMenuHandle = initPauseMenu({
  consoleBridge,
  onPauseRequest: () => {
    transportController.pause();
    transport = transportController.getState();
    void midiPanel.outStop();
    setAudioStatus("paused");
  },
  onResumeRequest: async () => {
    if (!selectedAuralSongPath || lastLoadedAuralSongPath !== selectedAuralSongPath) {
      setAudioStatus("pause menu closed");
      logConsole("gamestate", "pause menu -> close without resume");
      return;
    }
    try {
      await transportController.play();
      transport = transportController.getState();
      await midiPanel.outStartOrContinue();
      setAudioStatus(selectedAuralSongPath ? `playing: ${selectedAuralSongPath}` : "resumed");
      logConsole("gamestate", "pause menu -> resume");
    } catch (e) {
      const err = String(e);
      setAudioStatus(err);
      setVizStatus(`resume failed: ${err}`);
      errorConsole("play", "resume from pause menu failed", e);
    }
  },
  onBackToLibrary: () => {
    stopAudio();
    void midiPanel.outStop();
    void midiPanel.outSeek(0);
    showSongLibraryStep();
    setAudioStatus("returned to song selection");
    logConsole("gamestate", "pause menu -> back to song selection");
  },
});

// Audio transport panel — owns Load/Play/Pause/Stop/Seek + loop set/clear,
// plus the #audioStatus / #vizStatus elements. Host's load/stop callbacks
// are passed lazily via lambdas so they can capture function refs declared
// further down (loadAudioFromSelectedAuralSong / stopAudio).
const audioTransportPanel: AudioTransportPanelHandle = initAudioTransportPanel({
  transportController,
  midiPanel,
  pauseMenu,
  consoleBridge,
  onLoadAudio: () => loadAudioFromSelectedAuralSong(),
  onStopAudio: () => stopAudio(),
  refreshCachedTransport: () => {
    transport = transportController.getState();
  },
});

let currentPlaybackRate = 1;
const htmlFallbackAudioEl = document.createElement("audio");
htmlFallbackAudioEl.preload = "auto";
htmlFallbackAudioEl.style.display = "none";
document.body.appendChild(htmlFallbackAudioEl);

function isNativePlaybackInactiveError(err: string): boolean {
  return err.includes("native playback did not start (output callback inactive");
}

function resetTransportController(timebase: TransportTimebase): void {
  transportController.dispose();
  currentTimebase = timebase;
  transportController = new TransportController(currentTimebase, {
    bpm: 120,
    timeSignature: [4, 4]
  });
  transportController.setPlaybackRate(currentPlaybackRate);
  // Re-apply the persisted A/V calibration — a fresh controller starts at
  // offset 0, so without this a native->HTML fallback silently drops the
  // user's calibration until they next touch the slider.
  transportController.setAudioVisualOffsetSec(getEffectiveOffsetSec());
  if (transport.loop) {
    transportController.setLoop(transport.loop);
  }
  transport = transportController.getState();
}

async function tryFallbackToHtmlPlayback(auralsongPath: string): Promise<boolean> {
  if (!(currentTimebase instanceof NativeAudioTimebase)) {
    return false;
  }
  warnConsole("play", "native output callback inactive; switching to HTML fallback playback");
  setAudioStatus("native output stalled; switching to fallback playback...");

  resetTransportController(new HtmlAudioTimebase(htmlFallbackAudioEl));
  // Force blob-path reload on the fallback backend.
  lastLoadedAuralSongPath = null;
  await loadAudioFromSelectedAuralSong();
  if (!viz) {
    await startVisualizer();
  }
  await transportController.play();
  await midiPanel.outStartOrContinue();
  setAudioStatus(`playing (fallback): ${auralsongPath}`);
  return true;
}

// setPlayFocusMode + showSongLibraryStep + showBandSetupStep +
// canOpenLoadedSongBackOutPrompt + #toggleFocus click handler all live in
// routeController.ts (Phase 2.P). Thin wrappers below preserve the existing
// call shape so the rest of main.ts didn't need to change name-by-name.
function showSongLibraryStep(): void {
  routeController.showSongLibraryStep();
}
function showBandSetupStep(): void {
  routeController.showBandSetupStep();
}

// Defer the boot-time library-step call to a microtask so module evaluation
// finishes initializing every `let`/`const` declared further down the file
// (notably `let players`, which `syncPlaySurfaceMode` reaches via
// `shouldUseWideSoloKeysLayout` -> `players.length`). Calling
// showSongLibraryStep() synchronously here triggered a silent TDZ
// `ReferenceError: Cannot access 'players' before initialization` that
// halted the rest of module init -- so the Refresh button handler was
// never wired up, the `listen("songs_folder_changed", ...)` block at the
// bottom never registered, and the Play Songs panel stayed stuck at the
// initial "(not loaded)" status forever. Deferring with queueMicrotask
// lets the rest of this file run to completion before the call fires.
queueMicrotask(() => {
  showSongLibraryStep();
});
toggleFocusBtn.disabled = true;

// Player/track selection scaffold (multi-lane-ready)
// Instrument type + INSTRUMENT_LABELS live in instrumentTypes.ts.

// readDrumChartSelection lives in songChartLoader.ts (Phase 2.T) as
// `readSongChartSelection`, which returns BOTH the drum selection and the
// melodic tracks instead of mutating selectedMelodicTracks as a side
// effect. selectAuralSong writes both states from the returned object.

// loadRefinementsForRoles lives in refinementLoader.ts (Phase 2.E).
// Instrument-hint helpers (asObjectRecord + 4 applyInstrumentHintsFrom*)
// live in instrumentHints.ts (Phase 2.L) — imported at top of file.

// computeSongCapabilities + renderCaps + applyInstrumentAvailability live in
// capsPanel.ts (Phase 2.L). Use capsPanel.render() / .applyAvailability() / .compute().

// pluginRequirements (per-plugin AuralSong data gating) lives in pluginsPanel.ts.

// buildVizSongContext lives in vizSongContext.ts (Phase 2.S). Thin wrapper
// below feeds the four live module-state pieces into the pure compute so
// the existing call sites (no-arg buildVizSongContextLocal()) didn't need
// to change shape.
function buildVizSongContextLocal() {
  return buildVizSongContext({
    drumSelection: selectedDrumChartSelection,
    melodicTracks: selectedMelodicTracks,
    lyrics: currentLyrics,
    vocalPitch: currentVocalPitch,
    vocalPitchContour: currentVocalPitchContour,
    songTimeline: currentSongTimeline,
    keys: currentKeys,
    harmony: currentHarmony,
    charts: selectedAuralSongCharts,
  });
}

// renderPluginsWithAvailability lives in pluginsPanel.ts (.render()).

// Player state + render + handlers + plugin-id-per-instrument live in
// playersPanel.ts. Cross-cutting fns that read players but touch other
// state (selectedMelodicTracks, pluginSelect, viz lifecycle) stay here
// and reach the players list through the panel handle.

// findMelodicTrack / shouldPromoteMelodicSurface / shouldUseWideSoloKeysLayout
// / syncPlaySurfaceMode / syncMelodicTrackSelectionFromPlayers all live in
// playSurfaceController.ts (Phase 2.O). Wrappers below preserve the existing
// call shape so the rest of main.ts didn't need to change name-by-name.
function syncPlaySurfaceMode(): void {
  playSurfaceController.syncSurfaceMode();
}
function syncMelodicTrackSelectionFromPlayers(): void {
  playSurfaceController.syncMelodicTrackSelectionFromPlayers();
}

// selectedPluginId / setPluginSelectionById / syncPreferredPluginSelection
// live in pluginsPanel.ts as getSelectedPluginId / syncPreferred.

// Mutual-dep break: pluginsPanel needs playersPanel.getPreferredPluginIdForPlayers(),
// and playersPanel needs pluginsPanel.{syncPreferred,getSelectedPluginId,setSelectionModeAuto}.
// We construct pluginsPanel first against a let-ref that the playersPanel
// assignment populates. The deferred callbacks only fire after both exist.
let _playersPanelRef: PlayersPanelHandle | null = null;
const pluginsPanel: PluginsPanelHandle = initPluginsPanel({
  pluginSelect,
  refreshBtn: pluginRefreshBtn,
  setVizStatus,
  escapeHtml,
  getSelectedAuralSongDetails: () => selectedAuralSongDetails,
  getPreferredPluginIdForPlayers: () => _playersPanelRef?.getPreferredPluginIdForPlayers() ?? null,
  onPluginSelectionChange: () => restartVisualizerForPluginSelection(),
});

const playersPanel: PlayersPanelHandle = initPlayersPanel({
  escapeHtml,
  setPluginSelectionModeAuto: () => pluginsPanel.setSelectionModeAuto(),
  applyInstrumentAvailability: () => {
    capsPanel.applyAvailability(selectedAuralSongDetails, selectedDrumChartSelection, selectedAuralSongCharts);
  },
  syncPreferredPluginSelection: () => pluginsPanel.syncPreferred(),
  // Run the full selector update (not just track selection) on player
  // add/remove/instrument-change so the melodic surface shows/hides as the
  // band gains or loses a melodic player. updateInstrumentSelector() calls
  // syncMelodicTrackSelectionFromPlayers() internally, so selection still syncs.
  syncMelodicTrackSelectionFromPlayers: () => updateInstrumentSelector(),
  restartVisualizerForPluginSelection: () => restartVisualizerForPluginSelection(),
  rebuildSecondaryStagesIfRunning: () => {
    if (viz) {
      void buildSecondaryStages().catch((e) => {
        logConsole("debugging", `secondary stages rebuild failed: ${String(e)}`);
      });
    }
  },
  getSelectedPluginId: () => pluginsPanel.getSelectedPluginId(),
});
_playersPanelRef = playersPanel;

// Per-stem mixer (fader + mute/solo). Populated with the loaded stem roles
// after each song's audio loads; hidden for single-stem/legacy packs.
const stemMixer = initStemMixerPanel(document.getElementById("stemMixer") as HTMLElement);

// Caps panel — depends on playersPanel (for instrument writeback) and a
// live getter for selectedMelodicTracks (mutated by readDrumChartSelection).
const capsPanel: CapsPanelHandle = initCapsPanel({
  capsEl,
  playersEl,
  playersPanel,
  getSelectedMelodicTracks: () => selectedMelodicTracks,
  escapeHtml,
});

// Play-surface controller — owns the melodic tab/piano-roll surface, the
// TabRenderer instance, and the pianoPrimary/wideSoloKeys layout toggles.
const playSurfaceController: PlaySurfaceControllerHandle = initPlaySurfaceController({
  playSurfaceEl,
  playLayoutEl,
  appMainEl,
  playersPanel,
  consoleBridge,
  getSelectedMelodicTracks: () => selectedMelodicTracks,
  getCurrentRoute: () => routeController.getCurrentRoute(),
  initialDisplayMode: readPersistedDisplayMode(),
});

// Wire the "Piano roll ↔ Sheet music" display-mode toggle. Reflect the
// persisted initial mode onto the buttons, then switch + persist on click.
function syncDisplayModeButtons(mode: DisplayMode): void {
  if (!displayModeToggleEl) return;
  for (const btn of Array.from(displayModeToggleEl.querySelectorAll<HTMLButtonElement>("button.displayModeBtn"))) {
    btn.classList.toggle("isActive", btn.dataset.mode === mode);
  }
}
if (displayModeToggleEl) {
  syncDisplayModeButtons(playSurfaceController.getDisplayMode());
  displayModeToggleEl.addEventListener("click", (event) => {
    const btn = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>("button.displayModeBtn");
    const mode = btn?.dataset.mode;
    if (mode !== "piano" && mode !== "sheet") return;
    playSurfaceController.setDisplayMode(mode);
    persistDisplayMode(mode);
    syncDisplayModeButtons(mode);
  });
}

// Secondary-stages controller — owns the per-player Visualizer instances
// for Players 2..N. Init AFTER playersPanel + pluginsPanel + playSurfaceController
// because its deps reach those handles.
const secondaryStagesController: SecondaryStagesControllerHandle = initSecondaryStagesController({
  playerStagesEl,
  playersPanel,
  pluginsPanel,
  consoleBridge,
  getSongContext: () => buildVizSongContextLocal(),
});

// Route + step controller. Forward-references: lambdas in the deps invoke
// `routeController` (for the `getCurrentRoute` thunk in playSurfaceController
// above) and a handful of host functions declared further down (stopVisualizer,
// resizeVizCanvas, syncPlaySurfaceMode wrapper). All are called only after
// boot finishes, so the hoisted refs are safe.
const routeController: RouteControllerHandle = initRouteController({
  consoleBridge,
  pauseMenu,
  songLibraryPanel,
  transportController,
  haveTauri,
  stopVisualizer: () => stopVisualizer(),
  resizeVizCanvas: () => resizeVizCanvas(),
  syncPlaySurfaceMode,
  isLoadedSongSelected: () =>
    Boolean(selectedAuralSongPath) && lastLoadedAuralSongPath === selectedAuralSongPath,
  playLayoutEl,
});

const metronome = new Metronome({ enabled: false, volume: 0.25 });


let lastLoadedAudio: { blob: Blob; mime: string } | null = null;
let lastLoadedAuralSongPath: string | null = null;

// setAudioStatus + setVizStatus live in audioTransportPanel.ts (Phase 2.Q).
// Thin wrappers below preserve the existing call shape so the rest of main.ts
// didn't need to change name-by-name.
function setAudioStatus(msg: string): void {
  audioTransportPanel.setAudioStatus(msg);
}
function setVizStatus(msg: string): void {
  audioTransportPanel.setVizStatus(msg);
}

// Ensure the UI reflects the desktop-only backend.
audioBackendSelect.value = "native";

// audio host/device helpers + refresh/apply functions live in audioOutputPanel.ts

// Import-flow logic (Suno stem+MIDI creator, analysis import,
// advanced sidecar ingest) lives in AuralStudio. See `spec.md §1.1`.

function resizeVizCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = vizCanvas.clientWidth || 800;
  const cssHeight = vizCanvas.clientHeight || 240;

  vizCanvas.width = Math.floor(cssWidth * dpr);
  vizCanvas.height = Math.floor(cssHeight * dpr);

  // Reset transform each time.
  vizCtx2d.setTransform(dpr, 0, 0, dpr, 0, 0);

  viz?.onResize(cssWidth, cssHeight, dpr);

  // Secondary stages mirror Player 1's DPR-aware resize.
  secondaryStagesController.resizeAll(cssWidth, cssHeight, dpr);
}

// disposeSecondaryStages + buildSecondaryStages live in secondaryStagesController.ts (Phase 2.U).
// Thin wrappers preserve the call shape for the rest of main.ts.
function disposeSecondaryStages(): void {
  secondaryStagesController.dispose();
}
async function buildSecondaryStages(): Promise<void> {
  await secondaryStagesController.build();
  resizeVizCanvas();
}

function stopVisualizer(opts?: { keepStatus?: boolean; preserveTransport?: boolean }) {
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

  // Tear down every Player 2..N stage too.
  disposeSecondaryStages();

  if (!opts?.preserveTransport) {
    transport = { ...transport, t: 0, isPlaying: false };
  }
  if (!opts?.keepStatus) {
    setVizStatus("(not running)");
  }
  clearPlaybackLyrics();
  vizStartBtn.disabled = false;
  vizStopBtn.disabled = true;
}

// INSTRUMENT_ROLE_LABELS lives inside playSurfaceController.ts.

// updateInstrumentSelector + selectInstrumentTrack live in playSurfaceController.ts.
function updateInstrumentSelector(): void {
  playSurfaceController.updateInstrumentSelector();
}

type ManifestArtifactPointers = {
  lyrics?: unknown;
  keys?: unknown;
  harmony?: unknown;
  vocal_pitch?: unknown;
  vocal_pitch_contour?: unknown;
  song_timeline?: unknown;
};

function manifestArtifactRelPath(
  manifestRaw: ManifestArtifactPointers | undefined,
  key: keyof ManifestArtifactPointers,
): string | null {
  const value = manifestRaw?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

async function readOptionalArtifactJson(
  containerPath: string,
  manifestRaw: ManifestArtifactPointers | undefined,
  key: keyof ManifestArtifactPointers,
  legacyFeatureName: string,
): Promise<unknown | null> {
  const relPath = manifestArtifactRelPath(manifestRaw, key) ?? (isManifestPack(containerPath) ? null : `features/${legacyFeatureName}`);
  if (!relPath) return null;
  try {
    return await invoke<unknown>("read_auralsong_json", { containerPath, relPath });
  } catch {
    return null;
  }
}

async function selectAuralSong(containerPath: string) {
  const songChanged = selectedAuralSongPath !== containerPath;
  selectedDrumChartSelection = null;
  selectedAuralSongCharts = null;
  currentKeys = null;
  currentHarmony = null;
  currentVocalPitch = null;
  currentVocalPitchContour = null;
  currentSongTimeline = null;
  setSelectedSongCard(containerPath);
  songLibraryPanel.setDetailsHTML("Loading details...");
  try {
    const details = await invoke<AuralSongDetails>("get_auralsong_details", {
      containerPath,
    });
    songDetailsView.renderDetails(details);
    selectedAuralSongDetails = details;
    const manifestRaw = details.manifest_raw as ManifestArtifactPointers | undefined;
    currentKeys = await readOptionalArtifactJson(containerPath, manifestRaw, "keys", "keys.json");
    currentHarmony = await readOptionalArtifactJson(containerPath, manifestRaw, "harmony", "harmony.json");
    currentSongTimeline = await readOptionalArtifactJson(containerPath, manifestRaw, "song_timeline", "song_timeline.json");
    if (!currentSongTimeline && !manifestArtifactRelPath(manifestRaw, "song_timeline")) {
      try {
        currentSongTimeline = await invoke<unknown>("read_auralsong_json", {
          containerPath,
          relPath: "song_timeline.json",
        });
      } catch {
        currentSongTimeline = null;
      }
    }
    const keyModeArtifacts = { keys: currentKeys, harmony: currentHarmony };
    songDetailsView.setHudKeyMode(details.manifest_raw, null, keyModeArtifacts);
    if (details.charts.length > 0) {
      try {
        selectedAuralSongCharts = await safeInvoke<AuralSongChartsByPath>("read_auralsong_charts", { containerPath });
      } catch (e) {
        selectedAuralSongCharts = null;
        warnConsole("debugging", `failed to read charts for ${containerPath}`, e);
      }
    }
    const chartSelection = await readSongChartSelection({ containerPath, details, consoleBridge });
    selectedDrumChartSelection = chartSelection.drumSelection;
    selectedMelodicTracks = chartSelection.melodicTracks;

    // Load the song's real meter (initial tempo + time signature) from
    // song_timeline.json so the metronome + visualizer bar grid use it instead
    // of the hardcoded 120 bpm / 4-4. Reset to default per song; a pack without
    // a timeline (e.g. legacy .auralsong) keeps the default. External MIDI clock
    // still overrides bpm at tick time.
    {
      let songBpm = 120;
      let songTimeSig: [number, number] = [4, 4];
      const tl = currentSongTimeline as
        | {
            tempos?: Array<{ bpm?: number }>;
            time_signatures?: Array<{ ts?: number[] }>;
          }
        | null;
      if (tl) {
        const b = tl?.tempos?.[0]?.bpm;
        if (typeof b === "number" && b > 0) songBpm = b;
        const ts = tl?.time_signatures?.[0]?.ts;
        if (Array.isArray(ts) && ts.length >= 2) songTimeSig = [Math.round(ts[0]), Math.round(ts[1])];
      }
      transportController.setSongMeter(songBpm, songTimeSig);
      transport = transportController.getState();
    }

    // Now that the melodic notes are loaded, refresh the HUD key/mode from the
    // primary melodic track (keys/piano when present) so the header shows the
    // data-driven key instead of the manifest default.
    const keyTrack =
      selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
    songDetailsView.setHudKeyMode(details.manifest_raw, keyTrack?.notes ?? null, keyModeArtifacts);
    if (learnMode) buildLearnGroups();

    // Populate instrument selector with available melodic tracks.
    updateInstrumentSelector();

    // Show per-song data availability so users know what’s actually present.
    capsPanel.render(details, selectedDrumChartSelection, selectedAuralSongCharts);
    capsPanel.applyAvailability(details, selectedDrumChartSelection, selectedAuralSongCharts);
    pluginsPanel.render();

    currentLyrics = (await readOptionalArtifactJson(containerPath, manifestRaw, "lyrics", "lyrics.json")) as LyricsFile | null;
    currentVocalPitch = await readOptionalArtifactJson(containerPath, manifestRaw, "vocal_pitch", "vocal_pitch.json");
    currentVocalPitchContour = await readOptionalArtifactJson(
      containerPath,
      manifestRaw,
      "vocal_pitch_contour",
      "vocal_pitch_contour.json",
    );
    if (!currentVocalPitchContour && !manifestArtifactRelPath(manifestRaw, "vocal_pitch_contour") && !isManifestPack(containerPath)) {
      try {
        currentVocalPitchContour = await invoke<unknown>("read_auralsong_json", {
          containerPath,
          relPath: "features/pitch_contour.json",
        });
      } catch {
        currentVocalPitchContour = null;
      }
    }
    renderPlaybackLyrics(transport.t);

    // Selecting an AuralSong enables audio load.
    selectedAuralSongPath = containerPath;
    if (songChanged) {
      lastLoadedAuralSongPath = null;
    }
    audioTransportPanel.loadBtn.disabled = false;
    songDetailsView.setSelectedSongSetupLabel(details, containerPath);
    toggleFocusBtn.disabled = false;
    playersPanel.resetForSongSetup();
    showBandSetupStep();
    if (songChanged || lastLoadedAuralSongPath !== containerPath) {
      playStartBtn.disabled = true;
      setAudioStatus(`selected auralsong: ${containerPath}\npreparing audio...`);
      const preload = loadAudioFromSelectedAuralSong(containerPath)
        .catch((e) => {
          if (selectedAuralSongPath === containerPath) {
            setAudioStatus(String(e));
          }
        })
        .finally(() => {
          if (selectedSongPreloadPromise === preload) {
            selectedSongPreloadPromise = null;
            selectedSongPreloadPath = null;
          }
          if (selectedAuralSongPath === containerPath) {
            playStartBtn.disabled = false;
          }
        });
      selectedSongPreloadPromise = preload;
      selectedSongPreloadPath = containerPath;
      void preload;
    } else {
      setAudioStatus(`selected auralsong: ${containerPath}\naudio ready`);
    }
  } catch (e) {
    songLibraryPanel.setDetailsHTML(`<pre class="error">${escapeHtml(String(e))}</pre>`);
    setSelectedSongCard(selectedAuralSongPath);
  }
}

async function loadAudioFromSelectedAuralSong(containerPath?: string) {
  const targetAuralSongPath = containerPath ?? selectedAuralSongPath;
  if (!targetAuralSongPath) {
    setAudioStatus("Select a song first from the library");
    return;
  }

  setAudioStatus("Loading audioâ€¦");
  audioTransportPanel.loadBtn.disabled = true;

  try {
    const loadResult = await loadAuralSongAudioIntoTransport({
      containerPath: targetAuralSongPath,
      timebase: currentTimebase,
      transport: transportController,
      playbackRate: currentPlaybackRate,
      readAuralSongAudio: async (containerPath) => {
        return invoke<AudioBlob>("read_auralsong_audio", { containerPath });
      }
    });

    if (loadResult.mode === "direct") {
      // We no longer have the raw bytes in JS (by design).
      lastLoadedAudio = null;
      lastLoadedAuralSongPath = targetAuralSongPath;
      setAudioStatus(`loaded: ${targetAuralSongPath}`);
    } else {
      lastLoadedAudio = loadResult.loadedAudio;
      lastLoadedAuralSongPath = targetAuralSongPath;
      setAudioStatus(`loaded: ${loadResult.mime} (${loadResult.byteLength} bytes)`);
    }

    // Populate the per-stem mixer with the loaded stems (empty -> panel hidden).
    stemMixer.setRoles(nativeTimebase?.getLoadedStemRoles() ?? []);

    audioTransportPanel.playBtn.disabled = false;
    audioTransportPanel.pauseBtn.disabled = false;
    audioTransportPanel.stopBtn.disabled = false;
    audioTransportPanel.seekGoBtn.disabled = false;
    audioTransportPanel.loopSetBtn.disabled = false;
    audioTransportPanel.loopClearBtn.disabled = false;

    // If user hasnâ€™t started a visualizer yet, auto-start the selected one.
    if (!viz && targetAuralSongPath === selectedAuralSongPath) {
      void startVisualizer().catch((e) => {
        stopVisualizer({ keepStatus: true });
        setVizStatus(String(e));
      });
    }
  } catch (e) {
    if (targetAuralSongPath === selectedAuralSongPath) {
      lastLoadedAuralSongPath = null;
    }
    setAudioStatus(String(e));
    throw e;
  } finally {
    audioTransportPanel.loadBtn.disabled = false;
  }
}

async function startSelectedSongSession() {
  if (selectedSongPreloadPromise && selectedAuralSongPath && selectedSongPreloadPath === selectedAuralSongPath) {
    try {
      await selectedSongPreloadPromise;
    } catch {
      // Let the normal start path retry load and surface the real error.
    }
  }
  await startSelectedSongSessionFlow(
    {
      selectedAuralSongPath,
      lastLoadedAuralSongPath,
      hasVisualizer: Boolean(viz)
    },
    {
      setPlayStartDisabled: (disabled) => {
        playStartBtn.disabled = disabled;
      },
      setAudioStatus,
      setVizStatus,
      showSongLibraryStep,
      loadAudioFromSelectedAuralSong,
      startVisualizer,
      playTransport: () => transportController.play(),
      startMidiOut: midiPanel.outStartOrContinue,
      isNativePlaybackInactiveError,
      tryFallbackToHtmlPlayback,
      onPrimaryStartError: (err) => errorConsole("play", "start session failed", err),
      onFallbackStartError: (err) => errorConsole("play", "fallback playback start failed", err)
    }
  );
}

function stopAudio() {
  transportController.stop();
  transport = transportController.getState();
}

function currentSelectedPlugin(): PluginDescriptor {
  // Defer to pluginsPanel; the non-null assertion is safe because boot calls
  // pluginsPanel.refresh() (which always seeds at least BUILTIN_PLUGINS).
  return pluginsPanel.getCurrent()!;
}

async function startVisualizer(opts?: { preserveTransport?: boolean }) {
  stopVisualizer({ preserveTransport: opts?.preserveTransport });
  pluginsPanel.syncPreferred();

  const plugin = currentSelectedPlugin();
  setVizStatus(`Loading pluginâ€¦ (${plugin.id})`);

  if (plugin.id === "viz-lyrics" && !currentLyrics && !currentVocalPitch && !currentVocalPitchContour) {
    setVizStatus(
      "viz-lyrics: no lyrics or vocal pitch artifacts in this feedpak. Generate them in AuralStudio, then reopen this song."
    );
  }

  const loaded = await loadPlugin(plugin);
  loadedPluginDispose = loaded.dispose ?? null;

  viz = loaded.module.createVisualizer();

  await viz.init({
    canvas: vizCanvas,
    ctx2d: vizCtx2d,
    song: buildVizSongContextLocal(),
    // Pass only Player 1 to the primary stage so its plugin renders a
    // single-player lane. Players 2..N get their own secondary stages
    // below — see buildSecondaryStages().
    players: playersPanel.getPlayers().slice(0, 1).map((p) => ({
      id: p.id,
      name: p.name,
      instrument: p.instrument
    }))
  });

  // Build a stage per additional player. Failures are non-fatal — the
  // primary stage continues running even if a secondary plugin can't
  // initialize.
  await buildSecondaryStages();

  resizeVizCanvas();

  if (opts?.preserveTransport) {
    transport = transportController.getState();
  } else {
    transport = { ...transport, isPlaying: true, t: 0 };
  }
  vizStartBtn.disabled = true;
  vizStopBtn.disabled = false;
  setVizStatus(`running: ${plugin.id}`);
  renderPlaybackLyrics(transport.t);

  const tick = (ms: number) => {
    if (!viz) return;

    if (lastFrameMs == null) lastFrameMs = ms;
    const dt = (ms - lastFrameMs) / 1000;
    lastFrameMs = ms;

    transport = transportController.tick(dt);
    if (learnMode) {
      learnGateTick();
      transport = transportController.getState();
    }
    renderPlaybackLyrics(transport.t);

    // If MIDI clock out is enabled, keep its BPM tracking the transport.
    // (Transport bpm will be influenced by external clock if follow is enabled.)
    void midiPanel.outSetBpmIfNeeded(transport.bpm);

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

    // Drive every secondary stage with the same transport state so they
    // stay tempo-locked to Player 1.
    const dpr = window.devicePixelRatio || 1;
    secondaryStagesController.renderAll(dt, transport, dpr);

    // Render the melodic instrument tab/piano-roll below the main visualizer.
    if (transport.t !== undefined) {
      playSurfaceController.renderTabFrame(transport.t, {
        bpm: transport.bpm,
        timeSignature: transport.timeSignature,
        liveInputNotes: midiPanel.inputActiveNotes().activeNotes,
        scrollSpeedMultiplier: transport.scrollSpeedMultiplier,
        nashville: nashvilleMode,
      });
    }

    vizRaf = requestAnimationFrame(tick);
  };

  vizRaf = requestAnimationFrame(tick);
}

window.addEventListener("resize", () => resizeVizCanvas());

function restartVisualizerForPluginSelection() {
  if (!viz) return;
  void startVisualizer({ preserveTransport: true }).catch((e) => {
    stopVisualizer({ keepStatus: true, preserveTransport: true });
    setVizStatus(String(e));
  });
}

// #pluginSelect change handler lives inside pluginsPanel.ts (fires
// onPluginSelectionChange which is wired to restartVisualizerForPluginSelection).

vizStartBtn.addEventListener("click", () => {
  void startVisualizer().catch((e) => {
    // Important: stopVisualizer() normally resets the status text.
    // Preserve the error message so users can see what went wrong.
    stopVisualizer({ keepStatus: true, preserveTransport: true });
    setVizStatus(String(e));
  });
});

vizStopBtn.addEventListener("click", () => stopVisualizer());

// Backend switching intentionally removed: desktop build uses Rust native audio engine only.

// audio output click handlers live inside audioOutputPanel.ts

// Playback rate + metronome panel — listeners wired internally; the host
// hands it the metronome instance + a callback to refresh the cached
// transport state after every playback-rate change.
initPlaybackRateAndMetronomePanel({
  transportController,
  metronome,
  setAudioStatus,
  onPlaybackRateApplied: (r) => {
    currentPlaybackRate = r;
    transport = transportController.getState();
  },
});

// Scroll-speed (Note spacing) controller — DOM + persistence + transport
// wiring live in scrollSpeedController.ts. The init call here preserves
// the original module-init ordering (must run after transportController
// exists, before the visualizer starts).
initScrollSpeedController({
  transportController,
  onChange: () => {
    // Refresh the cached transport state so consumers (tabRenderer.render,
    // viz frame state) see the new multiplier on the next frame.
    transport = transportController.getState();
  },
});

// Nashville Number System toggle — labels piano-roll notes by scale degree
// relative to the inferred song key instead of note names. Persisted per
// webview in localStorage. Read each frame by the tab render call below.
const NASHVILLE_STORAGE_KEY = "auralprimer.nashvilleMode";
function readNashvilleMode(): boolean {
  try {
    return window.localStorage.getItem(NASHVILLE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}
let nashvilleMode = readNashvilleMode();
const nashvilleCheckbox = document.getElementById("nashvilleMode") as HTMLInputElement | null;
if (nashvilleCheckbox) {
  nashvilleCheckbox.checked = nashvilleMode;
  nashvilleCheckbox.addEventListener("change", () => {
    nashvilleMode = nashvilleCheckbox.checked;
    try {
      window.localStorage.setItem(NASHVILLE_STORAGE_KEY, nashvilleMode ? "1" : "0");
    } catch {
      // Best-effort — session-only persistence is acceptable.
    }
  });
}

// --- Note-progression "learn" mode --------------------------------------
// Playback waits at each note onset until the correct note(s) are played on
// MIDI input, then advances. Notes are grouped by onset (chords held together).
const LEARN_STORAGE_KEY = "auralprimer.learnMode";
// Playhead jump that means "seeked", not "played on". Comfortably above a
// frame's worth of playback (even a slow frame at 2x rate) and below the
// smallest jog step, so a Shift+arrow nudge still counts as a seek.
const LEARN_SEEK_EPSILON_SEC = 0.35;
type LearnGroup = { t: number; pitches: number[] };
let learnMode = (() => {
  try {
    return window.localStorage.getItem(LEARN_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
})();
let learnGroups: LearnGroup[] = [];
let learnIdx = 0;
const learnHit = new Set<number>();
let learnPrevActive = new Set<number>();
let learnWaiting = false;
// Last playhead we saw in the gate. A jump larger than a frame means someone
// seeked (arrow-key jog, seek box, loop wrap) and our position in the note
// list is stale — see learnGateTick.
let learnLastT = 0;

function learnNoteName(p: number): string {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  return `${names[((p % 12) + 12) % 12]}${Math.floor(p / 12) - 1}`;
}

function buildLearnGroups(): void {
  learnGroups = [];
  const track = selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
  if (track) {
    const sorted = [...track.notes].sort((a, b) => a.t_on - b.t_on);
    let cur: LearnGroup | null = null;
    for (const n of sorted) {
      if (!cur || n.t_on - cur.t > 0.05) {
        cur = { t: n.t_on, pitches: [n.pitch] };
        learnGroups.push(cur);
      } else if (!cur.pitches.includes(n.pitch)) {
        cur.pitches.push(n.pitch);
      }
    }
  }
  resetLearnFromTime(transportController.getState().t);
}

function resetLearnFromTime(t: number): void {
  learnHit.clear();
  learnWaiting = false;
  learnPrevActive = new Set();
  learnLastT = t;
  const idx = learnGroups.findIndex((g) => g.t >= t - 0.02);
  learnIdx = idx < 0 ? learnGroups.length : idx;
}

function learnRegisterPlayed(pitch: number): void {
  if (!learnWaiting || learnIdx >= learnGroups.length) return;
  const g = learnGroups[learnIdx];
  if (g.pitches.includes(pitch)) learnHit.add(pitch);
  if (g.pitches.every((p) => learnHit.has(p))) {
    learnIdx += 1;
    learnHit.clear();
    learnWaiting = false;
    void transportController.play();
  }
}

function learnGateTick(): void {
  if (!learnGroups.length) return;

  // Re-sync after a seek. Playback advances by at most a frame per tick, so a
  // bigger jump means the playhead was moved out from under us (arrow-key jog,
  // seek box, loop wrap). Without this, learnIdx still points at the old spot
  // and the mode has to be toggled off and on to recover.
  const tNow = transportController.getState().t;
  if (Math.abs(tNow - learnLastT) > LEARN_SEEK_EPSILON_SEC) {
    resetLearnFromTime(tNow);
  }
  learnLastT = tNow;

  // Rising-edge MIDI note detection (newly pressed = note-on).
  const snap = midiPanel.inputActiveNotes();
  const active = new Set<number>(
    (snap.activeNotes ?? []).map((n: { pitch: number }) => n.pitch),
  );
  for (const p of active) {
    if (!learnPrevActive.has(p)) learnRegisterPlayed(p);
  }
  learnPrevActive = active;

  if (learnIdx >= learnGroups.length) return;
  const g = learnGroups[learnIdx];
  const st = transportController.getState();
  if (!learnWaiting && st.isPlaying && st.t >= g.t - 0.02) {
    if (g.pitches.every((p) => learnHit.has(p))) {
      learnIdx += 1;
      learnHit.clear();
    } else {
      transportController.pause();
      transportController.seek(g.t);
      learnWaiting = true;
      setAudioStatus(`Wait mode — play: ${g.pitches.map(learnNoteName).join(" + ")}`);
    }
  }
}

const learnCheckbox = document.getElementById("learnMode") as HTMLInputElement | null;
if (learnCheckbox) {
  learnCheckbox.checked = learnMode;
  learnCheckbox.addEventListener("change", () => {
    learnMode = learnCheckbox.checked;
    try {
      window.localStorage.setItem(LEARN_STORAGE_KEY, learnMode ? "1" : "0");
    } catch {
      // best-effort
    }
    if (learnMode) {
      buildLearnGroups();
    } else if (learnWaiting) {
      // Un-stick: if we were holding, let playback continue.
      learnWaiting = false;
      void transportController.play();
    }
  });
}

// Metronome controls live in playbackRateAndMetronomePanel.ts (Phase 2.V).

// MIDI follow defaults to enabled.
transportController.setFollowExternalClock(true);

// Ingest progress events are emitted by AuralStudio's import flows; the
// gameplay app does not subscribe to them.

// Audio controls

// audioLoadBtn / audioPlayBtn / audioPauseBtn / audioStopBtn / audioSeekGoBtn
// / loopSetBtn / loopClearBtn click handlers all live inside audioTransportPanel.ts.
playStartBtn.addEventListener("click", () => {
  void startSelectedSongSession();
});

// Resume + back buttons inside the pause-menu overlay are wired by pauseMenu.ts internally.

window.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape" || ev.repeat) return;

  if (pauseMenu.isVisible()) {
    ev.preventDefault();
    void pauseMenu.resume();
    return;
  }

  if (routeController.getCurrentRoute() !== "play") return;
  if (transportController.getState().isPlaying) {
    ev.preventDefault();
    pauseMenu.openPaused();
    return;
  }
  if (!routeController.canOpenLoadedSongBackOutPrompt()) return;

  ev.preventDefault();
  pauseMenu.show("loaded");
});

// --- Live MIDI input readout -------------------------------------------
// Says out loud what the app is hearing from the keyboard. The 88-key cyan
// highlight only exists in piano-roll mode, so on any other instrument or in
// sheet mode there was previously no sign that MIDI was working at all — and
// no sign when it wasn't (an unconnected port looks identical to silence).
// Wait mode also announces the note it is holding for here.
const liveInputHudEl = document.getElementById("liveInputHud");
const liveInputNotesEl = document.getElementById("liveInputNotes");
const LIVE_INPUT_HUD_INTERVAL_MS = 60;

function renderLiveInputHud(): void {
  if (!liveInputHudEl || !liveInputNotesEl) return;
  liveInputHudEl.hidden = false;

  const held = (midiPanel.inputActiveNotes().activeNotes ?? [])
    .map((n) => n.noteName)
    .join("  ");

  let text: string;
  let state: string;
  if (!midiPanel.inputIsConnected()) {
    text = "no keyboard connected — Configure → MIDI";
    state = "off";
  } else if (learnMode && learnWaiting && learnIdx < learnGroups.length) {
    const want = learnGroups[learnIdx].pitches.map(learnNoteName).join(" + ");
    text = held ? `waiting for ${want}  —  holding ${held}` : `waiting for ${want}`;
    state = "waiting";
  } else if (held) {
    text = held;
    state = "playing";
  } else {
    text = "listening";
    state = "idle";
  }

  // Guard the writes: this runs on a timer and most ticks change nothing.
  if (liveInputNotesEl.textContent !== text) liveInputNotesEl.textContent = text;
  if (liveInputHudEl.dataset.state !== state) liveInputHudEl.dataset.state = state;
}

renderLiveInputHud();
window.setInterval(renderLiveInputHud, LIVE_INPUT_HUD_INTERVAL_MS);

// --- Play-mode transport hotkeys ---------------------------------------
// Space start/pause/resume + Left/Right jog. Logic lives in
// transportHotkeys.ts; the wiring here supplies the host state it needs.
initTransportHotkeys({
  transportController,
  getCurrentRoute: () => routeController.getCurrentRoute(),
  isPauseMenuVisible: () => pauseMenu.isVisible(),
  isSessionRunning: () => Boolean(viz),
  canStartSession: () => !playStartBtn.disabled,
  startSession: () => {
    void startSelectedSongSession();
  },
  onTransportChanged: () => {
    transport = transportController.getState();
  },
  onSeeked: (tSec) => {
    void midiPanel.outSeek(tSec);
  },
});

// --- Audio/visual sync calibration -------------------------------------
// Aligns the falling notes with the audible beat. The backend's auto output-
// latency estimate handles the bulk; the manual calibration covers the
// residual, which is most visible at high Note spacing (a fixed time error
// becomes more pixels as notes fall faster). Two latencies are measured: audio
// (output delay, incl. Bluetooth) and video (display lag); the transport
// applies the effective offset = audio - video. Both persist to the shared
// settings.json so calibrating here ALSO applies to AuralStudio. Nudge audio
// with Ctrl+[ (earlier) / Ctrl+] (later), reset with Ctrl+0.
const AV_OFFSET_STEP_MS = 5;

function applyEffectiveOffsetToTransport(): void {
  transportController.setAudioVisualOffsetSec(getEffectiveOffsetSec());
}

function renderAvReadout(): void {
  const fmt = (ms: number) => `${ms > 0 ? "+" : ""}${ms} ms`;
  const a = document.getElementById("avSyncAudioValue");
  const v = document.getElementById("avSyncVideoValue");
  const eff = document.getElementById("avSyncValue");
  if (a) a.textContent = fmt(getAudioOffsetMs());
  if (v) v.textContent = fmt(getVideoOffsetMs());
  if (eff) eff.textContent = fmt(getEffectiveOffsetMs());
}

async function applyAvCalibration(audioMs: number, videoMs: number): Promise<void> {
  await setAvCalibration(audioMs, videoMs);
  applyEffectiveOffsetToTransport();
  renderAvReadout();
  const eff = getEffectiveOffsetMs();
  const sign = eff > 0 ? "+" : "";
  setAudioStatus(
    `A/V sync: audio ${getAudioOffsetMs()} ms, video ${getVideoOffsetMs()} ms → notes ${eff >= 0 ? "later" : "earlier"} ${sign}${eff} ms`,
  );
  consoleBridge.log("gamestate", "a/v sync calibration changed", {
    audioMs: getAudioOffsetMs(),
    videoMs: getVideoOffsetMs(),
    effectiveMs: eff,
  });
}

// Rock Band-style two-pass calibrator (shared @auralprimer/av-sync): measures
// audio latency (tap to beeps) and video latency (tap to flashes).
const avCalibration = initAvCalibration({
  onApply: ({ audioMs, videoMs }) => void applyAvCalibration(audioMs, videoMs),
  getInitial: () => ({ audioMs: getAudioOffsetMs(), videoMs: getVideoOffsetMs() }),
  log: (message, details) => consoleBridge.log("gamestate", message, details),
});
document.getElementById("avSyncCalibrate")?.addEventListener("click", () => avCalibration.open());
document.getElementById("avSyncReset")?.addEventListener("click", () => void applyAvCalibration(0, 0));

// Load the shared persisted offsets, then apply to the transport + readout.
void loadAvCalibration().then(() => {
  applyEffectiveOffsetToTransport();
  renderAvReadout();
});

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
    void applyAvCalibration(curAudio - AV_OFFSET_STEP_MS, curVideo);
  } else if (ev.key === "]") {
    ev.preventDefault();
    void applyAvCalibration(curAudio + AV_OFFSET_STEP_MS, curVideo);
  } else if (ev.key === "0") {
    ev.preventDefault();
    void applyAvCalibration(0, 0);
  }
});

// audioSeekGoBtn / loopSetBtn / loopClearBtn handlers all live inside audioTransportPanel.ts.

// renderPreferredModelPacks + refreshModels + the modelpack DOM grabs +
// listeners all live in modelsPanel.ts (Phase 2.K).
const modelsPanel: ModelsPanelHandle = initModelsPanel({ escapeHtml });

// Initialize sizing for first paint.
resizeVizCanvas();
modelsPanel.renderPreferred();
void modelsPanel.refresh();

// Attention-grabbing launch notice when preferred model packs are missing,
// leading to the Models install UI. Only meaningful in the desktop app
// (list_installed_modelpacks needs Tauri); stays silent otherwise.
const modelInstallBannerEl = document.getElementById("modelInstallBanner");
if (modelInstallBannerEl && haveTauri()) {
  void initPendingModelInstallBanner({
    container: modelInstallBannerEl,
    onOpenModels: () => {
      const target =
        document.getElementById("preferredModels") ?? document.getElementById("modelsStatus");
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    },
  });
}

// refresh function + refresh button listener + songs_folder_changed listen
// block + setOverride/clearOverride handlers all live inside songLibraryPanel.ts.

// #pluginRefresh click handler lives inside pluginsPanel.ts. Populate plugin
// list on startup (also re-renders #pluginSelect with availability gating).
void pluginsPanel.refresh();

// Initial MIDI active-notes status + port lists handled by midiPanel itself.
void midiPanel.refreshAll();
void audioOutputPanel.refreshAll();

// Ensure we stop background threads on window close.
window.addEventListener("beforeunload", () => {
  void midiPanel.outShutdown();
  // Best-effort: stop native audio thread if it was initialized.
  try {
    void invoke("native_audio_shutdown");
  } catch {
    // ignore
  }
});

void songLibraryPanel.refresh();
