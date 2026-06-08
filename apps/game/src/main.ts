import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { TransportController } from "./transportController";
import type { TransportTimebase } from "./audioBackend";
import { HtmlAudioTimebase } from "./htmlAudioTimebase";
import { NativeAudioTimebase } from "./nativeAudioTimebase";
import { Metronome } from "./metronome";
// extractKeyModeFromManifest now consumed inside songDetailsView.ts.
// Modelpack list/install wiring lives in modelsPanel.ts (Phase 2.K).
import { initModelsPanel, type ModelsPanelHandle } from "./modelsPanel";
// BUILTIN_PLUGINS + scanBundledPlugins + scanUserPlugins now live inside pluginsPanel.ts.
import { type PluginDescriptor, loadPlugin } from "./plugins";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { selectDrumChartFromMidiBytes, selectMelodicTracksFromMidiBytes, parseMidiTracksFromBytes, applyRefinementsToMelodicTracks, type DrumChartSelection, type MelodicTrackSelection, type InstrumentRole } from "./chartLoader";
import { loadRefinementsForRoles } from "./refinementLoader";
// TabRenderer + the melodic-surface logic live in playSurfaceController.ts (Phase 2.O).
import { initScrollSpeedController } from "./scrollSpeedController";
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
import { initCapsPanel, type CapsPanelHandle, type SongCapabilities, type SongPackChartsByPath } from "./capsPanel";
import { initPluginsPanel, type PluginsPanelHandle } from "./pluginsPanel";
import { initSongDetailsView, type SongDetailsViewHandle } from "./songDetailsView";
import { initPlaySurfaceController, type PlaySurfaceControllerHandle } from "./playSurfaceController";
import { initRouteController, type RouteControllerHandle, type Route } from "./routeController";
import { initAudioTransportPanel, type AudioTransportPanelHandle } from "./audioTransportPanel";
import { appShellHtml } from "./appShellHtml";
import { initMidiPanel, type MidiPanelHandle } from "./midiPanel";
import type { ManifestSummary } from "./manifestTypes";
// MidiInputStateTracker + format helpers are consumed by midiPanel.ts (Phase 2.F).
import { loadSongPackAudioIntoTransport } from "./songpackAudioLoader";
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

// ManifestSummary lives in ./manifestTypes (shared with songLibraryPanel).
// SongPackScanEntry + isDemoSongPack moved into songLibraryPanel.ts.
type SongPackDetails = {
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
  has_mix_mp3: boolean;
  has_mix_ogg: boolean;
  has_mix_wav?: boolean;
  charts: string[];
  error?: string;
};

// SongPackChartsByPath + SongCapabilities live in capsPanel.ts (re-exported above).

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

type MidiBlob = {
  bytes: number[];
};

// Import-flow types (proprietary_archive_import, raw-song folder, stem+MIDI, ingest sidecar) and the
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
        Desktop-only features (file picker, SongPack scanning, native audio, etc.) are disabled here.
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
const playbackRateInput = document.getElementById("playbackRate") as HTMLInputElement;
const playbackRateApplyBtn = document.getElementById("playbackRateApply") as HTMLButtonElement;
// scrollSpeed slider DOM + wiring lives in scrollSpeedController.ts
const metronomeEnabledInput = document.getElementById("metronomeEnabled") as HTMLInputElement;
const metronomeVolumeInput = document.getElementById("metronomeVolume") as HTMLInputElement;

// All MIDI panel DOM + wiring lives in midiPanel.ts (Phase 2.F).

// Modelpack DOM (#modelsRefresh / #preferredModels / #modelsStatus /
// #modelpackPath / #modelpackImport) lives in modelsPanel.ts. The init call
// is below (needs escapeHtml defined first).

// Import-flow DOM lookups (proprietary_archive_import, sidecar ingest, analysis import, stem+MIDI
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
  selectedSongPackPath: () => selectedSongPackPath,
  onSongSelected: selectSongPack,
  haveTauri,
  escapeHtml,
});

// Disable desktop-only actions when running without the Tauri runtime.
if (!haveTauri()) {
  songLibraryPanel.disableFolderControls();
  playStartBtn.disabled = true;

  midiPanel.disableAll();

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
type SecondaryStage = {
  playerId: string;
  canvas: HTMLCanvasElement;
  ctx2d: CanvasRenderingContext2D;
  viz: Visualizer | null;
  dispose: (() => void) | null;
  pluginId: string;
};
let secondaryStages: SecondaryStage[] = [];
let vizRaf: number | null = null;
let lastFrameMs: number | null = null;
let selectedSongPackPath: string | null = null;
let selectedSongPackDetails: SongPackDetails | null = null;
let selectedDrumChartSelection: DrumChartSelection | null = null;
let selectedMelodicTracks: MelodicTrackSelection[] = [];
// tabRenderer + activeTabInstrument now live inside playSurfaceController.
let selectedSongPackCharts: SongPackChartsByPath | null = null;
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

// Pause-menu overlay (DOM + state + behavior in pauseMenu.ts). The deps'
// callbacks are arrow lambdas, so the `let`s they reference (transport,
// selectedSongPackPath, lastLoadedSongPackPath) only need to exist at
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
    if (!selectedSongPackPath || lastLoadedSongPackPath !== selectedSongPackPath) {
      setAudioStatus("pause menu closed");
      logConsole("gamestate", "pause menu -> close without resume");
      return;
    }
    try {
      await transportController.play();
      transport = transportController.getState();
      await midiPanel.outStartOrContinue();
      setAudioStatus(selectedSongPackPath ? `playing: ${selectedSongPackPath}` : "resumed");
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
// further down (loadAudioFromSelectedSongPack / stopAudio).
const audioTransportPanel: AudioTransportPanelHandle = initAudioTransportPanel({
  transportController,
  midiPanel,
  pauseMenu,
  consoleBridge,
  onLoadAudio: () => loadAudioFromSelectedSongPack(),
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
  if (transport.loop) {
    transportController.setLoop(transport.loop);
  }
  transport = transportController.getState();
}

async function tryFallbackToHtmlPlayback(songpackPath: string): Promise<boolean> {
  if (!(currentTimebase instanceof NativeAudioTimebase)) {
    return false;
  }
  warnConsole("play", "native output callback inactive; switching to HTML fallback playback");
  setAudioStatus("native output stalled; switching to fallback playback...");

  resetTransportController(new HtmlAudioTimebase(htmlFallbackAudioEl));
  // Force blob-path reload on the fallback backend.
  lastLoadedSongPackPath = null;
  await loadAudioFromSelectedSongPack();
  if (!viz) {
    await startVisualizer();
  }
  await transportController.play();
  await midiPanel.outStartOrContinue();
  setAudioStatus(`playing (fallback): ${songpackPath}`);
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

async function readDrumChartSelection(containerPath: string, details: SongPackDetails): Promise<DrumChartSelection | null> {
  selectedMelodicTracks = [];
  if (!details.has_notes_mid) {
    return null;
  }

  try {
    const midi = await invoke<MidiBlob>("read_songpack_mid", { containerPath, relPath: "features/notes.mid" });
    if (!midi.bytes.length) {
      return null;
    }
    const midiBytes = new Uint8Array(midi.bytes);

    // Extract melodic instrument tracks alongside drums.
    const baseMelodicTracks = selectMelodicTracksFromMidiBytes(midiBytes);
    // Apply per-instrument refinement overlays from features/refinement.<role>.json
    // if present. Best-effort: missing or invalid refinement files are
    // logged and skipped; the base notes.mid track is rendered unchanged.
    const refinements = await loadRefinementsForRoles(
      containerPath,
      baseMelodicTracks.map((t) => t.role),
      { warn: warnConsole },
    );
    selectedMelodicTracks = applyRefinementsToMelodicTracks(baseMelodicTracks, refinements);
    if (selectedMelodicTracks.length > 0) {
      const refRoles = refinements.map((r) => r.instrument);
      const suffix = refRoles.length > 0 ? ` (refinement: ${refRoles.join(", ")})` : "";
      logConsole("play", `found ${selectedMelodicTracks.length} melodic track(s): ${selectedMelodicTracks.map(t => t.role).join(", ")}${suffix}`);
    }

    return selectDrumChartFromMidiBytes(midiBytes);
  } catch (e) {
    selectedMelodicTracks = [];
    warnConsole("debugging", `failed to load/parse features/notes.mid from ${containerPath}`, e);
    return null;
  }
}

// loadRefinementsForRoles lives in refinementLoader.ts (Phase 2.E).
// Instrument-hint helpers (asObjectRecord + 4 applyInstrumentHintsFrom*)
// live in instrumentHints.ts (Phase 2.L) — imported at top of file.

// computeSongCapabilities + renderCaps + applyInstrumentAvailability live in
// capsPanel.ts (Phase 2.L). Use capsPanel.render() / .applyAvailability() / .compute().

// pluginRequirements (per-plugin SongPack data gating) lives in pluginsPanel.ts.

function buildVizSongContext(): {
  lyrics?: LyricsFile;
  charts?: SongPackChartsByPath;
  notes?: Array<{
    t_on: number;
    t_off?: number;
    pitch: number;
    velocity?: number;
    channel?: number;
    trackName?: string;
  }>;
} {
  const drumNotes =
    selectedDrumChartSelection?.events.map((ev) => ({
      t_on: ev.t,
      t_off: ev.t + 0.08,
      pitch: ev.midi,
      velocity: 100,
      channel: 9,
      trackName: ev.trackName
    })) ?? [];

  // Include melodic instrument notes for visualizer plugins.
  const melodicNotes = selectedMelodicTracks.flatMap((track) =>
    track.notes.map((n) => ({
      t_on: n.t_on,
      t_off: n.t_off,
      pitch: n.pitch,
      velocity: n.velocity,
      channel: track.channel,
      trackName: track.trackName,
    }))
  );

  const allNotes = [...drumNotes, ...melodicNotes];
  allNotes.sort((a, b) => a.t_on - b.t_on);

  return {
    lyrics: currentLyrics ?? undefined,
    charts: selectedSongPackCharts ?? undefined,
    notes: allNotes.length > 0 ? allNotes : undefined
  };
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
  getSelectedSongPackDetails: () => selectedSongPackDetails,
  getPreferredPluginIdForPlayers: () => _playersPanelRef?.getPreferredPluginIdForPlayers() ?? null,
  onPluginSelectionChange: () => restartVisualizerForPluginSelection(),
});

const playersPanel: PlayersPanelHandle = initPlayersPanel({
  escapeHtml,
  setPluginSelectionModeAuto: () => pluginsPanel.setSelectionModeAuto(),
  applyInstrumentAvailability: () => {
    capsPanel.applyAvailability(selectedSongPackDetails, selectedDrumChartSelection, selectedSongPackCharts);
  },
  syncPreferredPluginSelection: () => pluginsPanel.syncPreferred(),
  syncMelodicTrackSelectionFromPlayers,
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
    Boolean(selectedSongPackPath) && lastLoadedSongPackPath === selectedSongPackPath,
  playLayoutEl,
});

const metronome = new Metronome({ enabled: false, volume: 0.25 });


let lastLoadedAudio: { blob: Blob; mime: string } | null = null;
let lastLoadedSongPackPath: string | null = null;

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

// Import-flow logic (proprietary_archive_import, Suno stem+MIDI creator, analysis import,
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

  // Secondary stages share the grid layout so they get the same CSS-driven
  // size as Player 1's canvas. Mirror the DPR scaling + onResize call.
  for (const stage of secondaryStages) {
    const sw = stage.canvas.clientWidth || cssWidth;
    const sh = stage.canvas.clientHeight || cssHeight;
    stage.canvas.width = Math.floor(sw * dpr);
    stage.canvas.height = Math.floor(sh * dpr);
    stage.ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    stage.viz?.onResize(sw, sh, dpr);
  }
}

// Tear down every secondary stage's visualizer + remove its canvas from
// the DOM. Player 1's static `#viz` canvas is never removed.
function disposeSecondaryStages(): void {
  for (const stage of secondaryStages) {
    try {
      stage.viz?.dispose();
    } catch {
      /* swallow */
    }
    if (stage.dispose) {
      try {
        stage.dispose();
      } catch {
        /* swallow */
      }
    }
    stage.canvas.remove();
  }
  secondaryStages = [];
  playerStagesEl.setAttribute("data-player-count", "1");
}

// Build a Visualizer for each player past Player 1. Each gets its own
// canvas appended to `#playerStages`, its own plugin instance, and an
// init context where `players` is just that one player (so the plugin
// renders a single-player lane). The transport state is shared every
// frame from the host tick loop.
async function buildSecondaryStages(): Promise<void> {
  disposeSecondaryStages();
  const extras = playersPanel.getPlayers().slice(1);
  if (extras.length === 0) return;

  for (const player of extras) {
    const canvas = document.createElement("canvas");
    canvas.className = "playerStage__canvas";
    canvas.dataset.playerId = player.id;
    canvas.width = 800;
    canvas.height = 240;
    playerStagesEl.appendChild(canvas);

    const ctx2d = canvas.getContext("2d");
    if (!ctx2d) {
      canvas.remove();
      logConsole("debugging", `secondary stage ${player.id}: missing 2d context, skipping`);
      continue;
    }

    const pluginId = defaultPluginIdForInstrument(player.instrument);
    const plugins = pluginsPanel.getAvailable();
    const descriptor = plugins.find((p) => p.id === pluginId)
      ?? plugins.find((p) => p.id === DEFAULT_PLUGIN_ID)
      ?? plugins[0];
    if (!descriptor) {
      canvas.remove();
      continue;
    }

    let loaded;
    try {
      loaded = await loadPlugin(descriptor);
    } catch (e) {
      logConsole("debugging", `secondary stage ${player.id}: plugin load failed: ${String(e)}`);
      canvas.remove();
      continue;
    }

    const pviz = loaded.module.createVisualizer();
    try {
      await pviz.init({
        canvas,
        ctx2d,
        song: buildVizSongContext(),
        players: [{ id: player.id, name: player.name, instrument: player.instrument }],
      });
    } catch (e) {
      logConsole("debugging", `secondary stage ${player.id}: init failed: ${String(e)}`);
      try { pviz.dispose(); } catch { /* swallow */ }
      loaded.dispose?.();
      canvas.remove();
      continue;
    }

    secondaryStages.push({
      playerId: player.id,
      canvas,
      ctx2d,
      viz: pviz,
      dispose: loaded.dispose ?? null,
      pluginId: descriptor.id,
    });
  }

  playerStagesEl.setAttribute("data-player-count", String(1 + secondaryStages.length));
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

async function selectSongPack(containerPath: string) {
  const songChanged = selectedSongPackPath !== containerPath;
  selectedDrumChartSelection = null;
  selectedSongPackCharts = null;
  setSelectedSongCard(containerPath);
  songLibraryPanel.setDetailsHTML("Loading details...");
  try {
    const details = await invoke<SongPackDetails>("get_songpack_details", {
      containerPath,
    });
    songDetailsView.renderDetails(details);
    selectedSongPackDetails = details;
    songDetailsView.setHudKeyMode(details.manifest_raw);
    if (details.charts.length > 0) {
      try {
        selectedSongPackCharts = await safeInvoke<SongPackChartsByPath>("read_songpack_charts", { containerPath });
      } catch (e) {
        selectedSongPackCharts = null;
        warnConsole("debugging", `failed to read charts for ${containerPath}`, e);
      }
    }
    selectedDrumChartSelection = await readDrumChartSelection(containerPath, details);

    // Populate instrument selector with available melodic tracks.
    updateInstrumentSelector();

    // Show per-song data availability so users know what’s actually present.
    capsPanel.render(details, selectedDrumChartSelection, selectedSongPackCharts);
    capsPanel.applyAvailability(details, selectedDrumChartSelection, selectedSongPackCharts);
    pluginsPanel.render();

    // Load lyrics (best-effort)
    try {
      const lyr = await invoke<unknown>("read_songpack_json", { containerPath, relPath: "features/lyrics.json" });
      currentLyrics = (lyr ?? null) as LyricsFile | null;
    } catch {
      currentLyrics = null;
    }
    renderPlaybackLyrics(transport.t);

    // Selecting a SongPack enables audio load.
    selectedSongPackPath = containerPath;
    if (songChanged) {
      lastLoadedSongPackPath = null;
    }
    audioTransportPanel.loadBtn.disabled = false;
    songDetailsView.setSelectedSongSetupLabel(details, containerPath);
    toggleFocusBtn.disabled = false;
    playersPanel.resetForSongSetup();
    showBandSetupStep();
    if (songChanged || lastLoadedSongPackPath !== containerPath) {
      playStartBtn.disabled = true;
      setAudioStatus(`selected songpack: ${containerPath}\npreparing audio...`);
      const preload = loadAudioFromSelectedSongPack(containerPath)
        .catch((e) => {
          if (selectedSongPackPath === containerPath) {
            setAudioStatus(String(e));
          }
        })
        .finally(() => {
          if (selectedSongPreloadPromise === preload) {
            selectedSongPreloadPromise = null;
            selectedSongPreloadPath = null;
          }
          if (selectedSongPackPath === containerPath) {
            playStartBtn.disabled = false;
          }
        });
      selectedSongPreloadPromise = preload;
      selectedSongPreloadPath = containerPath;
      void preload;
    } else {
      setAudioStatus(`selected songpack: ${containerPath}\naudio ready`);
    }
  } catch (e) {
    songLibraryPanel.setDetailsHTML(`<pre class="error">${escapeHtml(String(e))}</pre>`);
    setSelectedSongCard(selectedSongPackPath);
  }
}

async function loadAudioFromSelectedSongPack(containerPath?: string) {
  const targetSongPackPath = containerPath ?? selectedSongPackPath;
  if (!targetSongPackPath) {
    setAudioStatus("Select a song first from the library");
    return;
  }

  setAudioStatus("Loading audioâ€¦");
  audioTransportPanel.loadBtn.disabled = true;

  try {
    const loadResult = await loadSongPackAudioIntoTransport({
      containerPath: targetSongPackPath,
      timebase: currentTimebase,
      transport: transportController,
      playbackRate: currentPlaybackRate,
      readSongPackAudio: async (containerPath) => {
        return invoke<AudioBlob>("read_songpack_audio", { containerPath });
      }
    });

    if (loadResult.mode === "direct") {
      // We no longer have the raw bytes in JS (by design).
      lastLoadedAudio = null;
      lastLoadedSongPackPath = targetSongPackPath;
      setAudioStatus(`loaded: ${targetSongPackPath}`);
    } else {
      lastLoadedAudio = loadResult.loadedAudio;
      lastLoadedSongPackPath = targetSongPackPath;
      setAudioStatus(`loaded: ${loadResult.mime} (${loadResult.byteLength} bytes)`);
    }

    audioTransportPanel.playBtn.disabled = false;
    audioTransportPanel.pauseBtn.disabled = false;
    audioTransportPanel.stopBtn.disabled = false;
    audioTransportPanel.seekGoBtn.disabled = false;
    audioTransportPanel.loopSetBtn.disabled = false;
    audioTransportPanel.loopClearBtn.disabled = false;

    // If user hasnâ€™t started a visualizer yet, auto-start the selected one.
    if (!viz && targetSongPackPath === selectedSongPackPath) {
      void startVisualizer().catch((e) => {
        stopVisualizer({ keepStatus: true });
        setVizStatus(String(e));
      });
    }
  } catch (e) {
    if (targetSongPackPath === selectedSongPackPath) {
      lastLoadedSongPackPath = null;
    }
    setAudioStatus(String(e));
    throw e;
  } finally {
    audioTransportPanel.loadBtn.disabled = false;
  }
}

async function startSelectedSongSession() {
  if (selectedSongPreloadPromise && selectedSongPackPath && selectedSongPreloadPath === selectedSongPackPath) {
    try {
      await selectedSongPreloadPromise;
    } catch {
      // Let the normal start path retry load and surface the real error.
    }
  }
  await startSelectedSongSessionFlow(
    {
      selectedSongPackPath,
      lastLoadedSongPackPath,
      hasVisualizer: Boolean(viz)
    },
    {
      setPlayStartDisabled: (disabled) => {
        playStartBtn.disabled = disabled;
      },
      setAudioStatus,
      setVizStatus,
      showSongLibraryStep,
      loadAudioFromSelectedSongPack,
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

  if (plugin.id === "viz-lyrics" && !currentLyrics) {
    setVizStatus(
      "viz-lyrics: features/lyrics.json missing. Generate it in AuralStudio, then reopen this SongPack."
    );
  }

  const loaded = await loadPlugin(plugin);
  loadedPluginDispose = loaded.dispose ?? null;

  viz = loaded.module.createVisualizer();

  await viz.init({
    canvas: vizCanvas,
    ctx2d: vizCtx2d,
    song: buildVizSongContext(),
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
    // stay tempo-locked to Player 1. Each runs its own Visualizer
    // instance against its own canvas.
    const dpr = window.devicePixelRatio || 1;
    for (const stage of secondaryStages) {
      if (!stage.viz) continue;
      try {
        stage.viz.update(dt, transport);
        stage.viz.render({
          canvas: stage.canvas,
          ctx2d: stage.ctx2d,
          width: stage.canvas.width / dpr,
          height: stage.canvas.height / dpr,
          dpr,
          state: transport,
        });
      } catch (e) {
        // Individual stage failures shouldn't kill the whole RAF loop.
        logConsole("debugging", `secondary stage ${stage.playerId} render failed: ${String(e)}`);
      }
    }

    // Render the melodic instrument tab/piano-roll below the main visualizer.
    if (transport.t !== undefined) {
      playSurfaceController.renderTabFrame(transport.t, {
        bpm: transport.bpm,
        timeSignature: transport.timeSignature,
        liveInputNotes: midiPanel.inputActiveNotes().activeNotes,
        scrollSpeedMultiplier: transport.scrollSpeedMultiplier,
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

// Playback rate controls

playbackRateApplyBtn.addEventListener("click", () => {
  const r = Number(playbackRateInput.value);
  if (!Number.isFinite(r) || r <= 0) return;
  currentPlaybackRate = r;
  transportController.setPlaybackRate(r);
  transport = transportController.getState();
  setAudioStatus(`playbackRate set: ${r.toFixed(2)}x`);
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

// proprietary_archive_import and ingest progress events are emitted by AuralStudio's import flows;
// the gameplay app does not subscribe to them.

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

// audioSeekGoBtn / loopSetBtn / loopClearBtn handlers all live inside audioTransportPanel.ts.

// renderPreferredModelPacks + refreshModels + the modelpack DOM grabs +
// listeners all live in modelsPanel.ts (Phase 2.K).
const modelsPanel: ModelsPanelHandle = initModelsPanel({ escapeHtml });

// Initialize sizing for first paint.
resizeVizCanvas();
modelsPanel.renderPreferred();
void modelsPanel.refresh();

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
