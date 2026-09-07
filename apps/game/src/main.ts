import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import { isManifestPack } from "@auralprimer/auralsong/packKind";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { inferKeySignature } from "@auralprimer/viz-tab";
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
import { initMidiTransportControl } from "./midiTransportControl";
import { initMrLinkPanel, buildChart } from "./mrLinkPanel";
import type { MrKeyboardLayout } from "./mrLinkPanel";
import { nameChord, chordLabels } from "@auralprimer/core-music";
import { initMidiTransportPanel } from "./midiTransportPanel";
import {
  defaultBindings,
  loadBindings,
  saveBindings,
  type TransportBindings,
} from "./midiTransportBindings";
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

// Uncaught errors reach the log, instead of only the devtools console nobody
// has open.
//
// The bridge only ever mirrored calls someone remembered to make, so an
// exception thrown while wiring the page left the log looking perfectly clean
// while every control registered after the throw silently did nothing. A
// backend log that says nothing is worse than no log, because it reads as
// evidence that nothing is wrong.
window.addEventListener("error", (ev) => {
  // ResizeObserver's "loop completed with undelivered notifications" is a
  // browser notice, not a fault: it means the observer rescheduled, which is
  // exactly what it is meant to do when a layout settles over more than one
  // frame. Reporting it buries the real entries under noise, which is how a
  // clean-looking log stops being read at all.
  if (ev.message?.includes("ResizeObserver loop")) return;
  const where = ev.filename ? ` (${ev.filename}:${ev.lineno}:${ev.colno})` : "";
  errorConsole("debugging", `uncaught: ${ev.message}${where}`, ev.error?.stack);
});
window.addEventListener("unhandledrejection", (ev) => {
  const reason = ev.reason;
  errorConsole(
    "debugging",
    `unhandled rejection: ${reason instanceof Error ? reason.message : String(reason)}`,
    reason instanceof Error ? reason.stack : undefined,
  );
});

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

// True once the start flow has actually run for the current selection.
// `viz` is NOT a proxy for this: the visualizer auto-starts as soon as a song
// is selected, so it is already non-null while the user is still looking at
// the Start button. Play/pause needs to know the difference.
let sessionStarted = false;

/**
 * Chord names for the loaded melodic track, computed once when the song loads.
 * Naming a chord walks a template table; doing that per frame for every visible
 * group would be real work repeated on a value that never changes.
 */
let songChordLabels: { tSec: number; label: string }[] = [];
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

// Song volume. The native engine has no master gain, so this folds into the
// per-track gains the mixer already pushes -- which keeps whatever balance the
// player set between stems while moving the whole song together.
//
// Persisted, unlike the per-stem faders. Those are a balance for one song and
// the engine resets them on load; this is how loud the room is, and having to
// set it again every launch is the kind of small friction that makes a player
// stop touching a control at all.
const SONG_VOLUME_KEY = "auralprimer.songVolume";
const songVolumeEl = document.getElementById("songVolume") as HTMLInputElement | null;
const songVolumeValueEl = document.getElementById("songVolumeValue") as HTMLElement | null;

function applySongVolume(percent: number, persist: boolean): void {
  const clamped = Math.max(0, Math.min(150, Math.round(percent)));
  stemMixer.setMaster(clamped / 100);
  if (songVolumeValueEl) songVolumeValueEl.textContent = `${clamped}%`;
  if (!persist) return;
  try {
    window.localStorage.setItem(SONG_VOLUME_KEY, String(clamped));
  } catch {
    // Private browsing or a locked profile: the fader still works this session.
  }
}

if (songVolumeEl) {
  const stored = (() => {
    try {
      const raw = window.localStorage.getItem(SONG_VOLUME_KEY);
      const n = raw === null ? NaN : Number(raw);
      return Number.isFinite(n) ? n : 100;
    } catch {
      return 100;
    }
  })();
  songVolumeEl.value = String(Math.max(0, Math.min(150, Math.round(stored))));
  applySongVolume(Number(songVolumeEl.value), false);
  songVolumeEl.addEventListener("input", () => {
    applySongVolume(Number(songVolumeEl.value), true);
  });
}

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

    // Chord names for the roll, from the same track the piano surface renders.
    const chordTrack =
      selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
    // Spell chords the way the key does. Without this every chord came out
    // sharp regardless of key, so A minor showed "C/A#" where the note is Bb.
    // Minor counts as flat-side even on a natural tonic (b3/b6/b7 are diatonic).
    const chordKey = chordTrack ? inferKeySignature(chordTrack.notes) : null;
    const chordSpelling =
      chordKey && (chordKey.noteLabelStyle === "flat" || chordKey.mode === "minor")
        ? "flat"
        : "sharp";
    songChordLabels = chordTrack ? chordLabels(chordTrack.notes, chordSpelling) : [];

    // Hand the headset this song's chart. Prefers keys, since that is what the
    // MR client renders against a real keyboard.
    const mrTrack =
      selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
    mrLink.setChart(
      buildChart(
        containerPath,
        containerPath.split(/[\/]/).pop()?.replace(/\.(feedpak|auralsong)$/i, "") ?? "song",
        mrTrack,
        transport.bpm,
        transport.timeSignature?.[0] ?? 4,
      ),
    );

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
    if (selectedAuralSongPath !== containerPath) sessionStarted = false;
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
            // Only now does the audio engine exist. Asking before this point
            // always failed with "not initialized", and since nothing asked
            // again the status sat on "starting audio subsystem" for the rest
            // of the session -- describing a moment that had long passed.
            void sendPianoNotes();
          }
        });
      selectedSongPreloadPromise = preload;
      selectedSongPreloadPath = containerPath;
      void preload;
    } else {
      setAudioStatus(`selected auralsong: ${containerPath}\naudio ready`);
      // Same song, audio already in the engine: the notes still have to follow
      // the selection, and the engine is ready to take them now.
      void sendPianoNotes();
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
  const startResult = await startSelectedSongSessionFlow(
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
  if (startResult.kind === "started" || startResult.kind === "fallback_started") {
    sessionStarted = true;
  }
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
    if (learnMode || graceMode) {
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
        noteColors: noteColorMode,
        chordLabels: songChordLabels,
      });
    }

    // Feed the MR headset. Rate-limited inside the panel; a no-op when off.
    mrLink.publish(
      transport.t,
      transport.isPlaying,
      midiPanel.inputActiveNotes().activeNotes,
    );

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

// Note colours -- pitch class as hue on the falling notes.
//
// Defaults ON, unlike the other practice toggles. The approach spectrum it
// replaces was saying something the lane already says better: a note's
// distance from the hit line IS its timing, continuously and without being
// taught. Hue was the weaker duplicate, so spending it on identity costs
// nothing and the default should reflect that.
const NOTE_COLOR_STORAGE_KEY = "auralprimer.noteColorMode";
function readNoteColorMode(): boolean {
  try {
    // Absent means never set, which is a new install -- and the default is on.
    const raw = window.localStorage.getItem(NOTE_COLOR_STORAGE_KEY);
    return raw === null ? true : raw === "1";
  } catch {
    return true;
  }
}
let noteColorMode = readNoteColorMode();
// MR headset link: serves the chart, playhead and live notes to the Quest app.
// The headset's Songs menu loads through exactly the same path as a click
// in the desktop library, so the two cannot drift apart in what "select a
// song" means.
const mrLink = initMrLinkPanel(
  (containerPath) => {
    void selectAuralSong(containerPath);
  },
  // Wait mode must not hold the song open for a note the headset already
  // decided this keyboard cannot play.
  setMrKeyboardLayout,
);

const noteColorCheckbox = document.getElementById("noteColorMode") as HTMLInputElement | null;
if (noteColorCheckbox) {
  noteColorCheckbox.checked = noteColorMode;
  noteColorCheckbox.addEventListener("change", () => {
    noteColorMode = noteColorCheckbox.checked;
    try {
      window.localStorage.setItem(NOTE_COLOR_STORAGE_KEY, noteColorMode ? "1" : "0");
    } catch {
      // Best-effort -- session-only persistence is acceptable.
    }
  });
}

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
const GRACE_STORAGE_KEY = "auralprimer.graceMode";
// Half-window either side of a note's onset. A hit inside it counts, so the
// full window is twice this — early and late are forgiven equally, because a
// player rushing and a player dragging are the same mistake in opposite
// directions and neither deserves a stutter.
//
// This is what "correct" means, and it is deliberately tight. Widening it does
// not make the player better, it makes the app agree with them more often.
const GRACE_WINDOW_SEC = 0.05;

// How far before a note's onset Wait mode DECIDES to stop.
//
// Deciding is not stopping. Between the two sits a whole latency chain: the
// gate runs once a frame (up to ~17 ms of quantisation), pause() is an async
// IPC call into the Rust engine, the command waits for the next audio callback
// (~11 ms at 512 frames / 48 kHz), and the output buffer already handed to the
// device plays out regardless (the playhead compensates two buffers, ~21 ms).
// Forty to fifty milliseconds of music sounds after the decision is made.
//
// A 20 ms lead was inside that, which is why notes still sounded before the
// player played them: the gate fired in time and the audio did not stop in
// time. This has to clear the whole chain with room to spare.
const GATE_LEAD_SEC = 0.12;

// Where the transport parks once it has stopped.
//
// Close to the note, because this distance is how long the player waits to
// hear the note they just played, and that gap is the whole feel of the mode.
// The elision it causes -- from wherever the pause overrun left the playhead
// up to here -- is covered by the engine's fade: the join happens in silence
// with a ramp either side, so what is skipped is the smooth middle of a decay
// rather than an audible cut.
const PARK_LEAD_SEC = 0.02;

// How far past a note's onset the recording resumes, once the player has
// played that note themselves.
//
// A piano attack is the first few tens of milliseconds; past that the note is
// ringing rather than striking, and resuming there is heard as the player's
// own note continuing instead of a second one starting. Short enough that
// almost nothing musical is skipped, and floored against the next note so a
// fast run never loses one.
const ATTACK_SKIP_SEC = 0.045;

// How long after a note's onset a hit still counts as that note's, when Grace
// is on and the transport is free-running.
//
// Separate from the scoring window, and four times longer, because the two
// answer different questions. Scoring asks "was that on time?" — a judgement
// worth keeping strict. This asks "was that this note at all?" — and refusing
// to recognise a note the player clearly meant is a far heavier penalty than
// not awarding it full marks, so it earns much more patience.
//
// It no longer governs the Wait-mode gate. It used to, and that was the bug:
// holding the gate 200 ms past the onset meant the transport had to PLAY the
// note to find out whether the player was late, which is precisely what Wait
// mode exists to prevent.
const GRACE_HOLDOFF_SEC = 0.2;
// How early a hit still counts as this note's.
//
// Scoring and accepting are different questions, and they were sharing one
// number. A note played before its onset failed the scoring window, so it was
// discarded outright -- then the gate arrived, found nothing registered, and
// stopped the song to ask for the note the player had just played, seeking
// BACKWARD to do it. From the keyboard that reads as the song rejecting a
// correct note and rewinding.
//
// Nothing else the early hit could belong to: it is the current group, and the
// group behind it has already retired. So accepting it costs nothing and
// refusing it costs a false miss. It still scores by GRACE_WINDOW_SEC -- early
// is still early, it is simply no longer treated as never having happened.
//
// Matched to the hold-off so the accepted span is symmetric about the onset:
// the gate forgives 200 ms of drag, and this forgives 200 ms of rush.
const GRACE_ACCEPT_EARLY_SEC = GRACE_HOLDOFF_SEC;
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
let graceMode = (() => {
  try {
    return window.localStorage.getItem(GRACE_STORAGE_KEY) === "1";
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

let mrKeyboardLayout: MrKeyboardLayout | null = null;

/**
 * Put a chart pitch onto a key the player actually has.
 *
 * Mirrors `CalibrationProfile.FoldPitch` on the headset exactly, and has to:
 * the whole point is that what the player SEES and what the host WAITS FOR are
 * the same note. Returns null when there is no key for it, meaning there is
 * nothing to wait for.
 *
 * Whole octaves only. Any other shift changes which note it is.
 */
function playablePitch(pitch: number): number | null {
  const layout = mrKeyboardLayout;
  // No headset, an older client, or one not yet calibrated: assume everything
  // is playable, which is exactly how this behaved before the frame existed.
  if (!layout) return pitch;

  if (pitch >= layout.lowestPitch && pitch <= layout.highestPitch) return pitch;
  if (layout.dropOutOfRange) return null;

  let folded = pitch;
  while (folded < layout.lowestPitch) folded += 12;
  while (folded > layout.highestPitch) folded -= 12;
  return folded >= layout.lowestPitch && folded <= layout.highestPitch ? folded : null;
}

function buildLearnGroups(): void {
  learnGroups = [];
  const track = selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
  if (track) {
    const sorted = [...track.notes].sort((a, b) => a.t_on - b.t_on);
    let cur: LearnGroup | null = null;
    for (const n of sorted) {
      // A note with no key on this instrument is not something to wait for.
      // Waiting for it stopped the song dead: the headset had already dropped
      // it from the display, so the player was being asked for a note that was
      // both invisible and impossible.
      const pitch = playablePitch(n.pitch);
      if (pitch === null) continue;

      if (!cur || n.t_on - cur.t > 0.05) {
        cur = { t: n.t_on, pitches: [pitch] };
        learnGroups.push(cur);
      } else if (!cur.pitches.includes(pitch)) {
        cur.pitches.push(pitch);
      }
    }
    // Folding can empty a group, and an empty one would be waited on forever.
    learnGroups = learnGroups.filter((g) => g.pitches.length > 0);
  }
  resetLearnFromTime(transportController.getState().t);
}

/**
 * Adopt a keyboard layout from the headset and rebuild what we wait for.
 *
 * Rebuilt rather than adjusted in place: the groups are derived from it, and a
 * recalibration mid-song can change which notes are reachable.
 */
function setMrKeyboardLayout(layout: MrKeyboardLayout | null): void {
  const before = JSON.stringify(mrKeyboardLayout);
  if (JSON.stringify(layout) === before) return;
  mrKeyboardLayout = layout;
  console.log("[mr-link] headset keyboard", layout);
  buildLearnGroups();
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
  if (learnIdx >= learnGroups.length) return;
  const g = learnGroups[learnIdx];

  // Count the hit if we are already holding for it, OR if the playhead is
  // inside the grace window around its onset.
  //
  // The second case is what stops Wait mode asking twice. The gate only pauses
  // once the playhead reaches the onset, so a note played a few milliseconds
  // early used to arrive while learnWaiting was still false and was dropped on
  // the floor — then the transport stopped and asked for the note you had just
  // played.
  // Grace widens what counts as "on time"; without it only the gate's own
  // tolerance applies. The two stack rather than exclude: Wait mode still
  // stops when a note is genuinely missed, Grace just stops it stopping for
  // a note you played a few milliseconds off.
  const t = transportController.getState().t;
  // Asymmetric on purpose. Late is bounded by the gate, because past it the
  // song has already stopped and learnWaiting carries the hit instead. Early
  // is bounded by how far ahead of the beat a hand can credibly be.
  const early = graceMode ? GRACE_ACCEPT_EARLY_SEC : 0.02;
  const late = graceMode ? GRACE_HOLDOFF_SEC : 0.02;
  const offset = t - g.t;
  const inWindow = offset >= -early && offset <= late;
  if (!learnWaiting && !inWindow) return;

  if (g.pitches.includes(pitch)) learnHit.add(pitch);
  if (!g.pitches.every((p) => learnHit.has(p))) return;

  learnIdx += 1;
  learnHit.clear();

  // Grace mode never pauses, so there is nothing to resume; calling play() here
  // would fight a transport that is already running.
  if (learnWaiting) {
    learnWaiting = false;
    // Resume PAST the recording's attack of the note just played.
    //
    // The player has played it. Letting the recording strike it again is the
    // doubling -- their note under their finger, then the same pitch again a
    // moment later, which is the flam that reads as a stutter. Their key is
    // the attack; the recording carries on from where that note is already
    // ringing.
    const played = learnGroups[learnIdx - 1];
    if (played) transportController.seek(resumeAfterAttack(played));
    void transportController.play();
  }
}

/**
 * Where to resume so the recording does not re-strike a note already played.
 *
 * Far enough past the onset to clear the attack transient, and never past the
 * next note the player is going to be asked for -- on a fast run the gap
 * between groups can be shorter than an attack, and skipping into the next
 * note would drop it from the recording while still demanding it from the
 * player.
 */
function resumeAfterAttack(played: LearnGroup): number {
  const next = learnGroups[learnIdx];
  const ceiling = next ? next.t - GATE_LEAD_SEC : Number.POSITIVE_INFINITY;
  return Math.min(played.t + ATTACK_SKIP_SEC, Math.max(played.t, ceiling));
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

  // Grace mode scores without ever taking the transport. Once a note's window
  // has closed it is retired, missed or not, and the song carries on — that is
  // the whole point of it, and it is why it cannot share the gate below.
  if (graceMode && !learnMode) {
    // Grace on its own scores without ever taking the transport: once a note's
    // window closes it is retired, missed or not, and the song carries on.
    // Retired when its SCORING window closes, not when the gate would have
    // fired. Past that it cannot be scored either way, so holding it current
    // any longer only delays the next note becoming current -- and on anything
    // quicker than the hold-off (sixteenths at 150 bpm clear it easily) the
    // next note would arrive already too late to score. The longer patience
    // belongs to stopping the music, not to bookkeeping.
    while (
      learnIdx < learnGroups.length &&
      st.t - learnGroups[learnIdx].t > GRACE_WINDOW_SEC
    ) {
      learnIdx += 1;
      learnHit.clear();
    }
    return;
  }

  // Stop BEFORE the note sounds, always — Grace does not move this.
  //
  // In Wait mode the key press is what plays the note, so the transport must
  // never get there on its own. Gating past the onset (which is what Grace
  // used to do, by 200 ms) breaks that twice over: the recording plays the
  // note the player has not played yet, and then, having established they were
  // late, the transport rewinds to the onset and plays that same 200 ms again
  // when they finally hit it. Every waited-on note came with its own attack
  // heard twice.
  //
  // Grace still applies to whether a hit COUNTS -- see the accept window in
  // learnRegisterPlayed. It just no longer decides where the music stops.
  const gateAt = g.t - GATE_LEAD_SEC;
  // Groups closer together than the lead would gate before the previous one
  // was released, so a fast run would stop on a note it had already passed.
  // Never gate earlier than the note behind it.
  const prev = learnIdx > 0 ? learnGroups[learnIdx - 1] : null;
  const gateFloor = prev ? prev.t : Number.NEGATIVE_INFINITY;
  if (!learnWaiting && st.isPlaying && st.t >= Math.max(gateAt, gateFloor)) {
    if (g.pitches.every((p) => learnHit.has(p))) {
      learnIdx += 1;
      learnHit.clear();
    } else {
      transportController.pause();
      // Park close to the note so the recording answers the key press quickly.
      //
      // This elides the few tens of milliseconds between where the sound
      // actually stopped and here. That used to matter; it does not now,
      // because the join happens while the transport is faded out and stopped,
      // and the engine ramps back in rather than switching. What is skipped is
      // the smooth middle of a decay, and 5ms of ramp covers the seam.
      transportController.seek(g.t - PARK_LEAD_SEC);
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
    // In Wait mode the song only moves when the player plays, so the chart
    // playing itself would be answering its own question. Their keys still
    // sound; the chart just stops covering for them.
    void applyPianoSchedule();
    if (learnMode) {
      buildLearnGroups();
    } else if (learnWaiting) {
      // Un-stick: if we were holding, let playback continue.
      learnWaiting = false;
      void transportController.play();
    }
  });
}

function persistGrace(): void {
  try {
    window.localStorage.setItem(GRACE_STORAGE_KEY, graceMode ? "1" : "0");
  } catch {
    // best-effort
  }
}

const graceCheckbox = document.getElementById("graceMode") as HTMLInputElement | null;
if (graceCheckbox) {
  graceCheckbox.checked = graceMode;
  graceCheckbox.addEventListener("change", () => {
    graceMode = graceCheckbox.checked;
    persistGrace();
    if (graceMode) {
      // Turning Grace on while Wait mode is holding releases the transport:
      // the note it stopped for is inside the widened window now.
      if (learnWaiting) {
        learnWaiting = false;
        void transportController.play();
      }
      buildLearnGroups();
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
const liveInputChordEl = document.getElementById("liveInputChord");
const playheadHudEl = document.getElementById("playheadHud");
const pianoEnabledEl = document.getElementById("pianoEnabled") as HTMLInputElement | null;
const pianoGainEl = document.getElementById("pianoGain") as HTMLInputElement | null;
const pianoStatusEl = document.getElementById("pianoStatus");
const playheadClockEl = document.getElementById("playheadClock");
const playheadDetailEl = document.getElementById("playheadDetail");
const LIVE_INPUT_HUD_INTERVAL_MS = 60;

function renderLiveInputHud(): void {
  if (!liveInputHudEl || !liveInputNotesEl) return;
  liveInputHudEl.hidden = false;

  const heldNotes = midiPanel.inputActiveNotes().activeNotes ?? [];
  const held = heldNotes.map((n) => n.noteName).join("  ");

  // Name what is being held, beside the note names rather than instead of them:
  // the names say which keys are down, the chord says what they mean.
  if (liveInputChordEl) {
    const chord = heldNotes.length >= 2 ? nameChord(heldNotes.map((n) => n.pitch)) : null;
    const chordText = chord ?? "";
    if (liveInputChordEl.textContent !== chordText) liveInputChordEl.textContent = chordText;
  }

  let text: string;
  let state: string;
  if (!midiPanel.inputIsConnected()) {
    text = "no keyboard connected — Configure → MIDI";
    state = "off";
  } else if (learnMode && !learnGroups.length) {
    // Wait mode on with nothing to wait for is otherwise a silent no-op.
    text = "wait mode on, but this song has no melodic notes to follow";
    state = "off";
  } else if (learnMode && learnWaiting && learnIdx < learnGroups.length) {
    const want = learnGroups[learnIdx].pitches.map(learnNoteName).join(" + ");
    text = held ? `waiting for ${want}  —  holding ${held}` : `waiting for ${want}`;
    state = "waiting";
  } else if (held) {
    text = held;
    state = "playing";
  } else {
    text = learnMode ? "wait mode armed" : "listening";
    state = "idle";
  }

  // Guard the writes: this runs on a timer and most ticks change nothing.
  if (liveInputNotesEl.textContent !== text) liveInputNotesEl.textContent = text;
  if (liveInputHudEl.dataset.state !== state) liveInputHudEl.dataset.state = state;
}

renderLiveInputHud();
window.setInterval(renderLiveInputHud, LIVE_INPUT_HUD_INTERVAL_MS);

/** m:ss.t — precise enough to report a drift, short enough to read at a glance. */
function formatPlayhead(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00.0";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

/**
 * Where we are in the song, plus the two numbers that decide it.
 *
 * The position exists because "the audio is a few seconds off" is not a
 * reportable bug without a timestamp. The detail beside it exists because the
 * playhead is not one number but three: the engine renders at `pos`, the app
 * subtracts an output-latency estimate and the A/V calibration to get the time
 * it believes is audible, and drift between those is exactly what a
 * sync complaint is about. Showing only the answer hides the disagreement.
 */
// --- Song scrub bar ---------------------------------------------------------
//
// Driven from the same tick as the playhead readout, so the thumb and the
// clock can never disagree. Two rules make a scrubber feel right and both are
// about who owns the value: while the user is dragging, the tick must not
// write over their thumb; and the seek must land on release rather than on
// every intermediate frame, or a drag across a long song issues hundreds of
// seeks and the engine spends the whole gesture catching up.
const songScrubEl = document.getElementById("songScrub") as HTMLElement | null;
const songScrubRangeEl = document.getElementById("songScrubRange") as HTMLInputElement | null;
const songScrubNowEl = document.getElementById("songScrubNow");
const songScrubTotalEl = document.getElementById("songScrubTotal");
const SCRUB_STEPS = 1000;

let scrubbing = false;

function scrubDurationSec(): number {
  // From the timebase, not the transport state: the state carries the playhead
  // and the web timebase has no duration at all, so the bar hides itself
  // rather than scaling against a zero it would have to special-case anyway.
  const d = nativeTimebase?.getDurationSec?.() ?? 0;
  return Number.isFinite(d) && d > 0 ? d : 0;
}

function formatClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function seekFromScrub(): void {
  if (!songScrubRangeEl) return;
  const dur = scrubDurationSec();
  if (dur <= 0) return;
  const t = (Number(songScrubRangeEl.value) / SCRUB_STEPS) * dur;
  transportController.seek(t);
  transportHostDeps.onTransportChanged?.();
  transportHostDeps.onSeeked?.(transportController.getState().t);
}

if (songScrubRangeEl) {
  // pointerdown rather than input: the drag has to be claimed before the first
  // value change arrives, or that first frame is still overwritten by the tick.
  songScrubRangeEl.addEventListener("pointerdown", () => { scrubbing = true; });
  songScrubRangeEl.addEventListener("keydown", () => { scrubbing = true; });
  songScrubRangeEl.addEventListener("input", () => {
    // Live feedback while dragging, without seeking yet.
    if (songScrubNowEl) {
      const dur = scrubDurationSec();
      songScrubNowEl.textContent = formatClock((Number(songScrubRangeEl.value) / SCRUB_STEPS) * dur);
    }
  });
  const commit = () => {
    if (!scrubbing) return;
    scrubbing = false;
    seekFromScrub();
  };
  // Hand focus back after a click.
  //
  // transportHotkeys treats every <input> as typing and stands down, and a
  // range natively eats arrow keys to move its own thumb. So a slider that
  // keeps focus after a click silently disables Space and Left/Right for the
  // rest of the session -- and this is the seek bar, which is exactly what
  // someone reaches for when they want to move around a song. Releasing focus
  // on pointer release restores the hotkeys; deliberately tabbing here still
  // holds focus, so arrow-key adjustment stays available to keyboard users.
  const releaseAfterPointer = () => {
    commit();
    songScrubRangeEl.blur();
  };
  songScrubRangeEl.addEventListener("change", commit);
  songScrubRangeEl.addEventListener("pointerup", releaseAfterPointer);
  songScrubRangeEl.addEventListener("pointercancel", releaseAfterPointer);
  songScrubRangeEl.addEventListener("blur", commit);
}

function renderSongScrub(): void {
  if (!songScrubEl || !songScrubRangeEl) return;
  const dur = scrubDurationSec();
  songScrubEl.hidden = dur <= 0;
  if (dur <= 0) return;

  if (songScrubTotalEl) songScrubTotalEl.textContent = formatClock(dur);
  // The user owns the thumb mid-drag; writing to it here would fight them.
  if (scrubbing) return;

  const t = transportController.getState().t;
  const v = String(Math.round(Math.min(1, Math.max(0, t / dur)) * SCRUB_STEPS));
  if (songScrubRangeEl.value !== v) songScrubRangeEl.value = v;
  if (songScrubNowEl) songScrubNowEl.textContent = formatClock(t);
}

function renderPlayheadHud(): void {
  if (!playheadHudEl || !playheadClockEl) return;

  const st = transportController.getState();
  const hasSong = Number.isFinite(st.t) && (st.isPlaying || st.t > 0);
  playheadHudEl.hidden = !hasSong;
  if (!hasSong) return;

  const text = formatPlayhead(st.t);
  if (playheadClockEl.textContent !== text) playheadClockEl.textContent = text;

  if (playheadDetailEl) {
    // Null whenever the web timebase is in use: the readout is a diagnostic,
    // so it degrades to blanks rather than throwing inside a render tick.
    const enginePos = nativeTimebase?.getCurrentTimeSec?.();
    const latencyMs = (nativeTimebase?.getOutputLatencySec?.() ?? 0) * 1000;
    const avMs = getEffectiveOffsetMs();
    const lag = typeof enginePos === "number" && Number.isFinite(enginePos)
      ? `  ·  engine ${formatPlayhead(enginePos)} (${(enginePos - st.t).toFixed(3)}s ahead)`
      : "";
    const detail = `lat ${latencyMs.toFixed(0)}ms  ·  a/v ${avMs.toFixed(0)}ms${lag}`;
    if (playheadDetailEl.textContent !== detail) playheadDetailEl.textContent = detail;
  }
}

// --- Sampled piano -------------------------------------------------------
//
// The chart already knows every note; this plays it, so a part can be heard
// even when the song has no rendered stem for it -- which is every chart that
// came from MIDI rather than from a recording.

/** Loaded once. The pack is tens of megabytes and does not change per song. */
let pianoPackLoaded = false;

/** True once the pack is in the engine, so key presses can sound. */
let pianoLiveReady = false;

function setPianoStatus(text: string): void {
  if (pianoStatusEl && pianoStatusEl.textContent !== text) pianoStatusEl.textContent = text;
}

async function ensurePianoPack(): Promise<boolean> {
  if (pianoPackLoaded) return true;
  try {
    const info = await invoke<{ name: string; license: string; samples: number }>(
      "piano_load_pack",
      { name: "salamander" },
    );
    pianoPackLoaded = true;
    pianoLiveReady = true;
    // One pitch may only sound once inside this window, whoever played it.
    void invoke("piano_set_grace_ms", { ms: 80 }).catch(() => {});
    setPianoStatus(`${info.name} · ${info.samples} samples · ${info.license}`);
    return true;
  } catch (e) {
    // Absent pack is a normal state, not a failure: the sound pack ships
    // separately, so say what is missing rather than throwing.
    const msg = String(e);
    setPianoStatus(
      msg.includes("not initialized")
        ? "starting audio subsystem…"
        : `no piano pack installed (${msg})`,
    );
    return false;
  }
}

/** Give the engine the current song's keys part. */
async function sendPianoNotes(): Promise<void> {
  const track =
    selectedMelodicTracks.find((t) => t.role === "keys") ?? selectedMelodicTracks[0] ?? null;
  if (!track) {
    void invoke("piano_set_notes", { notes: [] }).catch(() => {});
    return;
  }
  const notes = track.notes.map((n) => ({
    tOn: n.t_on,
    tOff: n.t_off,
    pitch: Math.round(n.pitch),
    velocity: Math.max(1, Math.min(127, Math.round((n.velocity ?? 0.63) * 127))),
  }));
  try {
    const count = await invoke<number>("piano_set_notes", { notes });
    if (pianoEnabledEl?.checked) setPianoStatus(`${count} notes ready`);
  } catch (e) {
    // Before a song's audio loads there is no engine yet, which is a stage of
    // starting up rather than something going wrong. Saying "error" about the
    // normal path teaches the player to ignore the line that will one day
    // carry a real fault.
    const msg = String(e);
    setPianoStatus(
      msg.includes("not initialized") ? "starting audio subsystem…" : `could not send notes: ${msg}`,
    );
  }
}

// Sound the keys the player actually presses.
//
// Driven by the midi-input event rather than by polling the active-note set:
// the poll runs on a timer, so every note would land up to a tick late and all
// the notes in a chord would land on whichever tick caught them. For something
// you play, that is the difference between an instrument and a delay.
//
// A note-on with zero velocity is a note-off -- the running-status form most
// controllers actually send, and treating it as a strike leaves the key stuck
// on forever.
window.addEventListener("auralprimer:midi-input", (ev) => {
  const msg = (ev as CustomEvent<{ message_type: string; data1?: number | null; data2?: number | null }>).detail;
  const pitch = msg?.data1;
  if (typeof pitch !== "number") return;
  if (msg.message_type !== "note_on" && msg.message_type !== "note_off") return;

  // Load on first touch rather than at startup or on a checkbox. Sixty
  // megabytes is too much to read before anyone has asked for a sound, and
  // requiring a tick first means the obvious thing -- press a key, hear a
  // piano -- silently does nothing. The first note is lost while it loads;
  // every one after it plays.
  if (!pianoLiveReady) {
    void ensurePianoPack();
    return;
  }

  if (msg.message_type === "note_on" && (msg.data2 ?? 0) > 0) {
    void invoke("piano_note_on", { pitch, velocity: msg.data2 ?? 80 }).catch((e) => {
      // Surfaced, not swallowed: a piano that cannot sound looked exactly like
      // one that was working, which is how a silent bug survived a build.
      setPianoStatus(`piano: ${String(e)}`);
    });
  } else if (msg.message_type === "note_off" || msg.message_type === "note_on") {
    void invoke("piano_note_off", { pitch }).catch(() => {});
  }
});

/**
 * Whether the chart should play its own part right now.
 *
 * Two conditions, not one: the checkbox asks for it, and Wait mode forbids it.
 * Wait mode advances only when the player plays, so a chart playing itself
 * would be prompting and answering at the same time.
 */
async function applyPianoSchedule(): Promise<void> {
  const on = !!pianoEnabledEl?.checked && !learnMode;
  await invoke("piano_set_enabled", { enabled: on }).catch(() => {});
  if (on) await sendPianoNotes();
}

pianoEnabledEl?.addEventListener("change", () => {
  const on = !!pianoEnabledEl.checked;
  void (async () => {
    if (on && !(await ensurePianoPack())) {
      pianoEnabledEl.checked = false;
      return;
    }
    await applyPianoSchedule();
    if (on) {
      // nothing further: applyPianoSchedule already sent the notes
    } else {
      // The pack stays loaded: the checkbox governs whether the CHART plays
      // itself, not whether the player's own keys make a sound. Report what is
      // actually loaded rather than asserting what will happen.
      setPianoStatus(pianoLiveReady ? "chart part off · your keys still sound" : "no piano loaded");
    }
  })();
});

pianoGainEl?.addEventListener("input", () => {
  const gain = Number(pianoGainEl.value) / 100;
  void invoke("piano_set_gain", { gain }).catch(() => {});
});

renderPlayheadHud();
renderSongScrub();
window.setInterval(() => {
  renderPlayheadHud();
  renderSongScrub();
}, LIVE_INPUT_HUD_INTERVAL_MS);

// --- Play-mode transport hotkeys ---------------------------------------
// Space start/pause/resume + Left/Right jog. Logic lives in
// transportHotkeys.ts; the wiring here supplies the host state it needs.
const transportHostDeps = {
  // A getter, not the controller: resetTransportController() replaces the
  // instance on an audio-backend fallback, and a captured reference would keep
  // driving the disposed one.
  getTransportController: () => transportController,
  getCurrentRoute: () => routeController.getCurrentRoute(),
  isPauseMenuVisible: () => pauseMenu.isVisible(),
  isSessionRunning: () => sessionStarted,
  canStartSession: () => !playStartBtn.disabled,
  startSession: () => {
    void startSelectedSongSession();
  },
  onTransportChanged: () => {
    transport = transportController.getState();
  },
  onSeeked: (tSec: number) => {
    void midiPanel.outSeek(tSec);
  },
};

initTransportHotkeys(transportHostDeps);

// The same transport, driven from the control surface's buttons. Which button
// does what is learned in Configure -> MIDI -> Transport control, because
// controllers disagree about whether they send CC or notes and on what numbers.
// Start on the defaults so the panel can render immediately, then swap in the
// durable set from settings.json once it arrives.
let midiTransportBindings: TransportBindings = defaultBindings();
const midiTransportPanel = initMidiTransportPanel({
  getBindings: () => midiTransportBindings,
  setBindings: (next) => {
    midiTransportBindings = next;
    void saveBindings(next);
  },
});
void loadBindings().then((loaded) => {
  midiTransportBindings = loaded;
  midiTransportPanel.refresh();
});

initMidiTransportControl({
  ...transportHostDeps,
  getBindings: () => midiTransportBindings,
  // Drive the checkbox rather than the flag, so the learned button and the
  // on-screen toggle stay in agreement and the existing change handler does
  // the rebuild / un-stick work.
  toggleWaitMode: () => {
    if (learnCheckbox) learnCheckbox.click();
  },
  // Don't fire the transport while the panel is capturing that same button.
  isSuppressed: () => midiTransportPanel.isLearning(),
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
