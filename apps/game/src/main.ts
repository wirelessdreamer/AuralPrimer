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

root.innerHTML = `
  <div class="appShell">
    <div id="runtimeBanner" class="runtimeBanner" aria-live="polite"></div>
    <header class="appHeader">
      <button id="navHome" class="brandBtn" aria-label="AuralPrimer Home">
        <span class="logoMark" aria-hidden="true"></span>
        <span class="brandText">
          <span class="brandName">AuralPrimer</span>
          <span class="brandTag">play | configure | exit</span>
        </span>
      </button>

      <nav class="topNav" aria-label="Primary">
        <button id="navPlay" class="navBtn">Play Songs</button>
        <button id="navConfig" class="navBtn">Configure</button>
      </nav>
    </header>

    <main class="appMain">
      <section class="route isActive" data-route="home">
        <div class="hero">
          <div class="heroLogo">
            <span class="logoMark logoMark--xl" aria-hidden="true"></span>
            <div>
              <h1 class="heroTitle">AuralPrimer</h1>
              <div class="meta heroMeta">Pick a mode to jump in quickly.</div>
            </div>
          </div>
          <div class="menuGrid" role="list">
            <button class="menuCard" id="homePlay" role="listitem">
              <div class="menuTitle">Play Songs</div>
              <div class="meta">Open your song library, choose a track, set up players, then start.</div>
            </button>
            <button class="menuCard" id="homeConfig" role="listitem">
              <div class="menuTitle">Configure</div>
              <div class="meta">Song folders, models, MIDI, audio, and runtime settings.</div>
            </button>
            <div class="menuCard menuCard--info" role="listitem" aria-label="Import lives in AuralStudio">
              <div class="menuTitle">Import / Create</div>
              <div class="meta">Importing songs and creating SongPacks lives in <strong>AuralStudio</strong>. Open AuralStudio to bring in Suno exports, analyzed audio, pre-split stems, or proprietary_archive_import content.</div>
            </div>
            <button class="menuCard menuCard--danger" id="homeExit" role="listitem">
              <div class="menuTitle">Exit</div>
              <div class="meta">Close AuralPrimer.</div>
            </button>
          </div>
        </div>
      </section>

      <section class="route" data-route="play">
        <div class="twoCol playLayout" id="playLayout">
          <section class="panel">
            <div class="panelHeader">
              <h2>Play Songs</h2>
              <div class="row" style="margin:0">
                <button id="refresh">Refresh</button>
              </div>
            </div>

            <pre id="status">(not loaded)</pre>
            <div class="twoCol" style="grid-template-columns: 1fr; gap: 10px;">
              <div id="list"></div>
              <div id="details" class="details"></div>
            </div>
          </section>

          <section class="panel bandSetupPanel">
            <div class="panelHeader bandSetupHeader">
              <h2>Band Setup</h2>
              <div class="row" style="margin:0">
                <span class="meta">proprietary_rhythm_archive style player setup</span>
                <button id="toggleFocus" class="ghostBtn" title="Back to song library">Back to Library</button>
              </div>
            </div>

            <div class="bandSetupBody">
              <aside class="bandSetupRail" aria-label="Band setup controls">
                <div class="hud" id="globalHud">
                  <div class="hudLabel">Key / Mode</div>
                  <div class="hudValue" id="hudKeyMode">C major</div>
                </div>

                <div class="songSetupMeta">
                  <div id="selectedSongLabel" class="setupSongLabel">(select a song from the library)</div>
                  <div id="selectedSongPath" class="meta setupSongPath"></div>
                </div>

                <div class="row">
                  <label class="meta">Visualizer</label>
                  <select id="pluginSelect"></select>
                  <button id="pluginRefresh">Refresh</button>
                </div>

                <div class="row">
                  <label class="meta">Players</label>
                  <div class="grow" id="players"></div>
                  <button id="addPlayer">Add</button>
                </div>

                <div class="row" id="scrollSpeedRow">
                  <label class="meta" for="scrollSpeedSlider">Note spacing</label>
                  <input
                    id="scrollSpeedSlider"
                    type="range"
                    min="0.5"
                    max="3"
                    step="0.05"
                    value="1"
                    aria-label="Scroll speed multiplier"
                    style="flex:1"
                  />
                  <span id="scrollSpeedValue" class="meta" style="min-width:3.5em;text-align:right">1.00x</span>
                  <button id="scrollSpeedReset" title="Reset to 1.00x">Reset</button>
                </div>

                <div class="row">
                  <button id="vizStart">Start visualizer</button>
                  <button id="vizStop" disabled>Stop</button>
                </div>

                <h3>Transport</h3>
                <div class="row">
                  <button id="audioLoad" disabled>Reload audio</button>
                  <button id="audioPlay" disabled>Play</button>
                  <button id="audioPause" disabled>Pause</button>
                  <button id="audioStop" disabled>Stop</button>
                </div>
                <div class="row">
                  <label class="meta">Backend</label>
                  <select id="audioBackend" disabled>
                    <option value="native">Native (Rust)</option>
                  </select>
                </div>
                <div class="row">
                  <label class="meta">Output host</label>
                  <select id="audioOutputHost"></select>
                  <button id="audioOutputHostRefresh">Refresh</button>
                  <button id="audioOutputHostApply">Apply</button>
                </div>
                <div class="row">
                  <label class="meta">Output device</label>
                  <select id="audioOutputDevice"></select>
                  <button id="audioOutputDeviceRefresh">Refresh</button>
                  <button id="audioOutputDeviceApply">Apply</button>
                </div>
                <div class="row">
                  <label class="meta">Slowdown</label>
                  <input id="playbackRate" type="number" min="0.25" max="2" step="0.05" value="1" />
                  <button id="playbackRateApply">Set rate</button>
                </div>
                <div class="row">
                  <label class="meta">Metronome</label>
                  <label><input id="metronomeEnabled" type="checkbox" /> enabled</label>
                  <label class="meta">vol</label>
                  <input id="metronomeVolume" type="range" min="0" max="1" step="0.05" value="0.25" />
                </div>
                <div class="row">
                  <label class="meta">Seek (sec)</label>
                  <input id="audioSeek" type="number" min="0" step="0.25" value="0" />
                  <button id="audioSeekGo" disabled>Go</button>
                </div>
                <div class="row">
                  <label class="meta">Loop</label>
                  <input id="loopT0" type="number" min="0" step="0.25" value="0" />
                  <input id="loopT1" type="number" min="0" step="0.25" value="4" />
                  <button id="loopSet" disabled>Set</button>
                  <button id="loopClear" disabled>Clear</button>
                </div>
                <pre id="audioStatus">(no audio)</pre>
              </aside>

              <div class="bandSetupStage">
                <div id="playerStages" class="playerStages" data-player-count="1">
                  <canvas id="viz" width="800" height="240" data-stage-index="0"></canvas>
                </div>
                <div id="playLyrics" class="playLyrics" hidden aria-live="polite" aria-atomic="true">
                  <div id="playLyricsCurrent" class="playLyricsCurrent"></div>
                  <div id="playLyricsNext" class="playLyricsNext"></div>
                </div>
                <pre id="vizStatus">(not running)</pre>

                <div id="instrumentSelector" class="instrumentSelector" style="display:none">
                  <span class="meta">Instrument:</span>
                </div>
                <div id="tabContainer" class="tabContainer" style="display:none"></div>

                <div class="startRow">
                  <button id="playStart" class="playStartBtn" disabled>Start</button>
                </div>
              </div>
            </div>
          </section>
        </div>
      </section>

      <section class="route" data-route="learn">
        <section class="panel">
          <div class="panelHeader">
            <h2>Learn Songs</h2>
            <div class="meta">Practice mode</div>
          </div>
          <p class="meta">
            This section is evolving. For now, use <strong>Play Songs</strong> for selection + playback.
            Next weâ€™ll add practice-first defaults (loop presets, beat-aligned looping, section navigation, and guided exercises).
          </p>
          <div class="row">
            <button id="learnGoPlay">Go to Play Songs</button>
          </div>
        </section>
      </section>

      <section class="route" data-route="config">
        <div class="twoCol">
          <section class="panel">
            <div class="panelHeader">
              <h2>Library & Models</h2>
              <div class="meta">Song folders + model packs</div>
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

          </section>

          <section class="panel">
            <div class="panelHeader">
              <h2>MIDI</h2>
              <div class="meta">Clock + full I/O</div>
            </div>

            <h3>MIDI Input (keyboard + clock follow)</h3>
            <div class="row">
              <label><input id="midiFollowEnabled" type="checkbox" checked /> follow external clock</label>
            </div>
            <div class="row">
              <label class="meta">MIDI input port</label>
              <select id="midiInPort"></select>
              <button id="midiInRefresh">Refresh</button>
              <button id="midiInConnect">Connect</button>
              <button id="midiInDisconnect">Disconnect</button>
            </div>
            <div class="row">
              <label class="meta">tempo scale</label>
              <input id="midiTempoScale" type="number" min="0.25" max="4" step="0.05" value="1" />
              <span class="meta">(device bpm Ã— scale = song bpm)</span>
            </div>
            <div class="row">
              <label><input id="midiInSysexEnabled" type="checkbox" /> allow SysEx input</label>
            </div>
            <div class="row">
              <button id="midiInPanic">Clear active notes</button>
              <span class="meta">Use this if a keyboard disconnect leaves a held note in the monitor.</span>
            </div>
            <pre id="midiStatus" class="meta">(midi input: not connected)</pre>
            <pre id="midiInActiveNotes" class="meta">(no active notes)</pre>
            <pre id="midiInEvents" class="meta">(midi input events)</pre>

            <h3>MIDI Sync (clock out)</h3>
            <div class="row">
              <label><input id="midiOutEnabled" type="checkbox" /> send MIDI clock</label>
            </div>
            <div class="row">
              <label class="meta">MIDI clock output port</label>
              <select id="midiOutPort"></select>
              <button id="midiOutRefresh">Refresh</button>
              <button id="midiOutSelect">Select</button>
            </div>
            <div class="row">
              <label><input id="midiOutSysexEnabled" type="checkbox" /> allow SysEx output</label>
            </div>
            <div class="row">
              <button id="midiOutStart">Start</button>
              <button id="midiOutContinue">Continue</button>
              <button id="midiOutStop">Stop</button>
            </div>

            <h3>MIDI Output (messages)</h3>
            <div class="row">
              <label class="meta">channel</label>
              <input id="midiMsgChannel" type="number" min="1" max="16" step="1" value="1" />
              <label class="meta">note</label>
              <input id="midiMsgNote" type="number" min="0" max="127" step="1" value="60" />
              <label class="meta">velocity</label>
              <input id="midiMsgVelocity" type="number" min="0" max="127" step="1" value="100" />
            </div>
            <div class="row">
              <button id="midiMsgNoteOn">Note On</button>
              <button id="midiMsgNoteOff">Note Off</button>
              <button id="midiMsgAllNotesOff">All Notes Off</button>
            </div>
            <div class="row">
              <label class="meta">cc</label>
              <input id="midiMsgCc" type="number" min="0" max="127" step="1" value="1" />
              <label class="meta">value</label>
              <input id="midiMsgCcValue" type="number" min="0" max="127" step="1" value="64" />
              <button id="midiMsgCcSend">Send CC</button>
            </div>
            <div class="row">
              <label class="meta">raw hex bytes</label>
              <input id="midiOutRawHex" class="grow" type="text" placeholder="90 3C 64" />
              <button id="midiOutRawSend">Send Raw</button>
            </div>
            <pre id="midiOutStatus" class="meta">(midi clock out: disabled)</pre>
          </section>
        </div>
      </section>
    </main>
    <div id="pauseMenuOverlay" class="pauseMenuOverlay" hidden aria-hidden="true">
      <section
        class="pauseMenuDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pauseMenuTitle"
        aria-describedby="pauseMenuCopy"
      >
        <div class="pauseMenuKicker">Paused</div>
        <h2 id="pauseMenuTitle" class="pauseMenuTitle">Pause Menu</h2>
        <p id="pauseMenuCopy" class="pauseMenuCopy">
          Keep your place and resume, or head back to song selection.
        </p>
        <div class="pauseMenuActions">
          <button id="pauseMenuBack" class="pauseMenuBackBtn">Back to Song Selection</button>
          <button id="pauseMenuResume" class="pauseMenuResumeBtn">Resume</button>
        </div>
        <div class="pauseMenuHint">Press Esc again to resume instantly.</div>
      </section>
    </div>
  </div>
`;

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

type Route = "home" | "play" | "learn" | "config";
let currentRoute: Route = "home";

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

function setRoute(route: Route) {
  currentRoute = route;
  const routes = Array.from(document.querySelectorAll<HTMLElement>(".route"));
  for (const el of routes) {
    const r = el.dataset.route as Route | undefined;
    el.classList.toggle("isActive", r === route);
  }

  const navMap: Record<Route, string> = {
    home: "navHome",
    play: "navPlay",
    learn: "navLearn",
    config: "navConfig"
  };

  for (const [r, id] of Object.entries(navMap) as Array<[Route, string]>) {
    document.getElementById(id)?.classList.toggle("isActive", r === route);
  }

  // Keep the experience tidy: stop visuals/audio when leaving Play.
  if (route !== "play") {
    pauseMenu.close({ restoreFocus: false });
    try {
      stopVisualizer();
      transportController.pause();
    } catch {
      // ignore
    }
  }

  syncPlaySurfaceMode();

  // Always scroll to top of content on navigation.
  document.documentElement.scrollTop = 0;
  logConsole("gamestate", `route -> ${route}`);
}

function openPlaySongFlow() {
  logConsole("gamestate", "open play flow");
  setRoute("play");
  // showSongLibraryStep() now calls refresh() internally on every show.
  showSongLibraryStep();
}

async function exitApplication() {
  if (!haveTauri()) {
    window.close();
    return;
  }
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().close();
}

document.getElementById("navHome")?.addEventListener("click", () => setRoute("home"));
document.getElementById("navPlay")?.addEventListener("click", () => openPlaySongFlow());
document.getElementById("navConfig")?.addEventListener("click", () => setRoute("config"));
document.getElementById("homePlay")?.addEventListener("click", () => openPlaySongFlow());
document.getElementById("homeConfig")?.addEventListener("click", () => setRoute("config"));
document.getElementById("homeExit")?.addEventListener("click", () => {
  void exitApplication().catch((e) => {
    errorConsole("debugging", "failed to exit app", e);
  });
});

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

// proprietary_rhythm_archive-ish: once a song is loaded, make the Now Playing panel the focus.
let playFocusMode = false;
function setPlayFocusMode(enabled: boolean) {
  playFocusMode = enabled;
  playLayoutEl.classList.toggle("isFocus", enabled);
  // Canvas size may change; ensure we resize so the visualizer fills the space.
  resizeVizCanvas();
  logConsole("gamestate", `play focus mode -> ${enabled ? "focus" : "normal"}`);
}


function showSongLibraryStep() {
  pauseMenu.close({ restoreFocus: false });
  logConsole("gamestate", "show song library step");
  playLayoutEl.classList.add("isLibraryOnly");
  setPlayFocusMode(false);
  syncPlaySurfaceMode();
  try {
    stopVisualizer();
    transportController.pause();
  } catch {
    // ignore
  }
  // Always rescan when the library panel is shown. Covers app-start, returning
  // from focus mode / pause menu, and navigating in from Home or the nav bar.
  // Previously the initial DOM left statusEl reading "(not loaded)" until the
  // user clicked Refresh or navigated away and back; new SongPacks dropped
  // into the songs folder by aural_ingest while the app was open also stayed
  // invisible until that manual refresh.
  void songLibraryPanel.refresh();
}

function canOpenLoadedSongBackOutPrompt(): boolean {
  return currentRoute === "play" && Boolean(selectedSongPackPath) && lastLoadedSongPackPath === selectedSongPackPath;
}

function showBandSetupStep() {
  pauseMenu.close({ restoreFocus: false });
  logConsole("gamestate", "show band setup step");
  playLayoutEl.classList.remove("isLibraryOnly");
  setPlayFocusMode(true);
  syncPlaySurfaceMode();
}

toggleFocusBtn.addEventListener("click", () => {
  showSongLibraryStep();
});

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
  getCurrentRoute: () => currentRoute,
});

const metronome = new Metronome({ enabled: false, volume: 0.25 });


let lastLoadedAudio: { blob: Blob; mime: string } | null = null;
let lastLoadedSongPackPath: string | null = null;

function setAudioStatus(msg: string) {
  audioStatusEl.textContent = msg;
  logConsole("play", msg);
}

// Ensure the UI reflects the desktop-only backend.
audioBackendSelect.value = "native";

// audio host/device helpers + refresh/apply functions live in audioOutputPanel.ts

function setVizStatus(msg: string) {
  vizStatusEl.textContent = msg;
  logConsole("debugging", msg);
}

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
    audioLoadBtn.disabled = false;
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
  audioLoadBtn.disabled = true;

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

    audioPlayBtn.disabled = false;
    audioPauseBtn.disabled = false;
    audioStopBtn.disabled = false;
    audioSeekGoBtn.disabled = false;
    loopSetBtn.disabled = false;
    loopClearBtn.disabled = false;

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
    audioLoadBtn.disabled = false;
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

audioLoadBtn.addEventListener("click", () => {
  void loadAudioFromSelectedSongPack().catch((e) => setAudioStatus(String(e)));
});
playStartBtn.addEventListener("click", () => {
  void startSelectedSongSession();
});

audioPlayBtn.addEventListener("click", () => {
  logConsole("play", "play requested");
  void transportController.play()
    .then(() => {
      logConsole("play", "play started");
      return midiPanel.outStartOrContinue();
    })
    .catch((e) => setAudioStatus(String(e)));
});

audioPauseBtn.addEventListener("click", () => {
  transportController.pause();
  transport = transportController.getState();
  void midiPanel.outStop();
  setAudioStatus("paused");
});

audioStopBtn.addEventListener("click", () => {
  pauseMenu.close({ restoreFocus: false });
  stopAudio();
  void midiPanel.outStop();
  void midiPanel.outSeek(0);
  setAudioStatus("stopped");
});

// Resume + back buttons inside the pause-menu overlay are wired by pauseMenu.ts internally.

window.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape" || ev.repeat) return;

  if (pauseMenu.isVisible()) {
    ev.preventDefault();
    void pauseMenu.resume();
    return;
  }

  if (currentRoute !== "play") return;
  if (transportController.getState().isPlaying) {
    ev.preventDefault();
    pauseMenu.openPaused();
    return;
  }
  if (!canOpenLoadedSongBackOutPrompt()) return;

  ev.preventDefault();
  pauseMenu.show("loaded");
});

audioSeekGoBtn.addEventListener("click", () => {
  const t = Number(audioSeekInput.value);
  if (!Number.isFinite(t)) {
    warnConsole("play", "seek ignored: invalid value", { value: audioSeekInput.value });
    return;
  }
  transportController.seek(t);
  void midiPanel.outSeek(t);
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
