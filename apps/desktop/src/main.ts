import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { TransportController } from "./transportController";
import type { TransportTimebase } from "./audioBackend";
import { NativeAudioTimebase } from "./nativeAudioTimebase";
import { Metronome } from "./metronome";
import { extractKeyModeFromManifest } from "./hud";
import { PREFERRED_MODEL_PACKS } from "./models/preferredModelPacks";
import { installModelPackFromPath, installModelPackFromUrl, listInstalledModelPacks } from "./models/modelManager";
import { BUILTIN_PLUGINS, type PluginDescriptor, loadPlugin, scanBundledPlugins, scanUserPlugins } from "./plugins";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { generateLyricsJsonFromPlainText } from "./lyricsGenerator";

function haveTauri(): boolean {
  // In browser-only Vite dev mode, Tauri globals are not injected.
  // (Tauri WebView provides window.__TAURI__.)
  return typeof (window as unknown as { __TAURI__?: unknown }).__TAURI__ !== "undefined";
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

type SongPackScanEntry = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest?: ManifestSummary;
  error?: string;
};

function isDemoSongPack(e: SongPackScanEntry): boolean {
  // Deterministic id for our built-in first-run song.
  return (e.manifest?.song_id ?? "") === "demo_sine_440hz";
}

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

type SongCapabilities = {
  features: {
    beats: boolean;
    tempo_map: boolean;
    sections: boolean;
    events: boolean;
    lyrics: boolean;
    notes_mid: boolean;
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

type AudioBlob = {
  mime: string;
  bytes: number[];
};

type GhwtSettings = {
  data_root?: string;
  vgmstream_cli_path?: string;
};

type GhwtPreflight = {
  dlc_ok: boolean;
  vgmstream_ok: boolean;
  data_root: string;
  dlc_root: string;
  vgmstream_resolved?: string;
  error?: string;
};

type GhwtSongEntry = {
  checksum: string;
  title: string;
  artist: string;
  year?: number;
  dlc_dir: string;
  preview_fsb_path: string;
  stem_fsb_paths: string[];
  pak_path?: string;
};

type GhwtImportResult = { songpack_path: string; used?: string };

type GhwtImportAllResult = {
  ok: boolean;
  checksum: string;
  songpack_path?: string;
  error?: string;
};

type GhwtImportProgressEvent = {
  song: string;
  type: string;
  id: string;
  progress: number;
  message?: string;
  artifact?: string;
};

type StemMidiCreateRequest = {
  title: string;
  artist: string;
  stemWavPaths: string[];
  midiPath: string;
};

type StemMidiCreateResult = { songpack_path: string };

const root = document.getElementById("app");
if (!root) throw new Error("missing #app");

root.innerHTML = `
  <div class="appShell">
    <header class="appHeader">
      <button id="navHome" class="brandBtn" aria-label="AuralPrimer Home">
        <span class="logoMark" aria-hidden="true"></span>
        <span class="brandText">
          <span class="brandName">AuralPrimer</span>
          <span class="brandTag">learn · play · create</span>
        </span>
      </button>

      <nav class="topNav" aria-label="Primary">
        <button id="navPlay" class="navBtn">Play Songs</button>
        <button id="navLearn" class="navBtn">Learn Songs</button>
        <button id="navMake" class="navBtn">Make Music</button>
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
              <div class="meta heroMeta">Branding/logo artwork coming soon — this spot is reserved front-and-center.</div>
            </div>
          </div>
          <div class="menuGrid" role="list">
            <button class="menuCard" id="homePlay" role="listitem">
              <div class="menuTitle">Play Songs</div>
              <div class="meta">Pick a songpack and play with visuals + sync.</div>
            </button>
            <button class="menuCard" id="homeLearn" role="listitem">
              <div class="menuTitle">Learn Songs</div>
              <div class="meta">Practice mode (loops/slowdown) — evolving.</div>
            </button>
            <button class="menuCard" id="homeMake" role="listitem">
              <div class="menuTitle">Make Music</div>
              <div class="meta">TBD — composition, jam tools, recording.</div>
            </button>
            <button class="menuCard" id="homeConfig" role="listitem">
              <div class="menuTitle">Configure</div>
              <div class="meta">Song folders, plugins, models, MIDI, etc.</div>
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

          <section class="panel">
            <div class="hud" id="globalHud">
              <div class="hudLabel">Key / Mode</div>
              <div class="hudValue" id="hudKeyMode">C major</div>
            </div>

            <div class="panelHeader">
              <h2>Now Playing</h2>
              <div class="row" style="margin:0">
                <span class="meta">Visualizer + transport</span>
                <button id="toggleFocus" class="ghostBtn" title="Toggle play focus mode">Focus</button>
              </div>
            </div>

            <div class="row">
              <label class="meta">Plugin</label>
              <select id="pluginSelect"></select>
              <button id="pluginRefresh">Refresh</button>
            </div>

            <div class="row">
              <label class="meta">Players</label>
              <div class="grow" id="players"></div>
              <button id="addPlayer">Add</button>
            </div>

            <div class="row">
              <button id="vizStart">Start visualizer</button>
              <button id="vizStop" disabled>Stop</button>
            </div>

            <canvas id="viz" width="800" height="240"></canvas>
            <pre id="vizStatus">(not running)</pre>

            <h3>Transport</h3>
            <div class="row">
              <button id="audioLoad" disabled>Load audio</button>
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
            Next we’ll add practice-first defaults (loop presets, beat-aligned looping, section navigation, and guided exercises).
          </p>
          <div class="row">
            <button id="learnGoPlay">Go to Play Songs</button>
          </div>
        </section>
      </section>

      <section class="route" data-route="make">
        <section class="panel">
          <div class="panelHeader">
            <h2>Make Music</h2>
            <div class="meta">TBD</div>
          </div>
          <p class="meta">
            Placeholder for future: jam tools, creation workflows, MIDI routing, generators, recording, etc.
          </p>
        </section>
      </section>

      <section class="route" data-route="config">
        <div class="twoCol">
          <section class="panel">
            <div class="panelHeader">
              <h2>Configure</h2>
              <div class="meta">Folders + plugins + models</div>
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

            <h3>Import GHWT songs (MVP)</h3>
            <p class="meta">
              This importer scans your local <strong>Guitar Hero World Tour Definitive Edition</strong> <code>DATA</code> folder
              and imports each DLC song’s <code>*_preview.fsb.xen</code> into an AuralPrimer SongPack.
              <br />
              Requires <code>vgmstream-cli</code> on PATH (or provide an explicit path).
            </p>

            <div class="row">
              <label class="meta">GHWT DATA root</label>
              <input id="ghwtDataRoot" class="grow" type="text" placeholder="D:\\Guitar Hero World Tour\\DATA" />
              <button id="ghwtBrowse">Browse…</button>
            </div>
            <div class="row">
              <label class="meta">vgmstream-cli path (optional)</label>
              <input id="ghwtVgmstream" type="text" placeholder="C:\\tools\\vgmstream-cli.exe" />
            </div>
            <div class="row">
              <button id="ghwtSave">Save</button>
              <button id="ghwtScan">Scan DLC</button>
              <button id="ghwtImportAll">Import all</button>
            </div>
            <pre id="ghwtStatus" class="meta">(not scanned)</pre>
            <div id="ghwtList"></div>

            <h3>Create SongPack (stems + MIDI)</h3>
            <p class="meta">
              Create a playable SongPack from one or more WAV stems plus a MIDI file.
              This will mix down to <code>audio/mix.wav</code> and store the MIDI as <code>features/notes.mid</code>.
            </p>
            <div class="row">
              <label class="meta">Title</label>
              <input id="stemMidiTitle" class="grow" type="text" placeholder="Song title" />
            </div>
            <div class="row">
              <label class="meta">Artist</label>
              <input id="stemMidiArtist" class="grow" type="text" placeholder="Artist" />
            </div>
            <div class="row">
              <button id="stemMidiPickStems">Pick stem WAVs…</button>
              <div class="meta grow" id="stemMidiStemsLabel">(none)</div>
            </div>
            <div class="row">
              <button id="stemMidiPickMidi">Pick MIDI…</button>
              <div class="meta grow" id="stemMidiMidiLabel">(none)</div>
            </div>
            <div class="row">
              <button id="stemMidiCreate">Create SongPack</button>
            </div>
            <pre id="stemMidiStatus" class="meta">(not created)</pre>
          </section>

          <section class="panel">
            <div class="panelHeader">
              <h2>MIDI</h2>
              <div class="meta">Clock in/out</div>
            </div>

            <h3>MIDI Sync (clock follow)</h3>
            <div class="row">
              <label><input id="midiFollowEnabled" type="checkbox" checked /> follow external clock</label>
            </div>
            <div class="row">
              <label class="meta">MIDI clock input port</label>
              <select id="midiInPort"></select>
              <button id="midiInRefresh">Refresh</button>
              <button id="midiInConnect">Connect</button>
              <button id="midiInDisconnect">Disconnect</button>
            </div>
            <div class="row">
              <label class="meta">tempo scale</label>
              <input id="midiTempoScale" type="number" min="0.25" max="4" step="0.05" value="1" />
              <span class="meta">(device bpm × scale = song bpm)</span>
            </div>
            <pre id="midiStatus" class="meta">(midi clock: not connected)</pre>

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
              <button id="midiOutStart">Start</button>
              <button id="midiOutContinue">Continue</button>
              <button id="midiOutStop">Stop</button>
            </div>
            <pre id="midiOutStatus" class="meta">(midi clock out: disabled)</pre>
          </section>
        </div>
      </section>
    </main>
  </div>
`;

type Route = "home" | "play" | "learn" | "make" | "config";

function setRoute(route: Route) {
  const routes = Array.from(document.querySelectorAll<HTMLElement>(".route"));
  for (const el of routes) {
    const r = el.dataset.route as Route | undefined;
    el.classList.toggle("isActive", r === route);
  }

  const navMap: Record<Route, string> = {
    home: "navHome",
    play: "navPlay",
    learn: "navLearn",
    make: "navMake",
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
}

document.getElementById("navHome")?.addEventListener("click", () => setRoute("home"));
document.getElementById("navPlay")?.addEventListener("click", () => setRoute("play"));
document.getElementById("navLearn")?.addEventListener("click", () => setRoute("learn"));
document.getElementById("navMake")?.addEventListener("click", () => setRoute("make"));
document.getElementById("navConfig")?.addEventListener("click", () => setRoute("config"));

document.getElementById("homePlay")?.addEventListener("click", () => setRoute("play"));
document.getElementById("homeLearn")?.addEventListener("click", () => setRoute("learn"));
document.getElementById("homeMake")?.addEventListener("click", () => setRoute("make"));
document.getElementById("homeConfig")?.addEventListener("click", () => setRoute("config"));
document.getElementById("learnGoPlay")?.addEventListener("click", () => setRoute("play"));

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
const midiStatusEl = document.getElementById("midiStatus") as HTMLPreElement;

const midiOutEnabledInput = document.getElementById("midiOutEnabled") as HTMLInputElement;
const midiOutPortSelect = document.getElementById("midiOutPort") as HTMLSelectElement;
const midiOutRefreshBtn = document.getElementById("midiOutRefresh") as HTMLButtonElement;
const midiOutSelectBtn = document.getElementById("midiOutSelect") as HTMLButtonElement;
const midiOutStartBtn = document.getElementById("midiOutStart") as HTMLButtonElement;
const midiOutContinueBtn = document.getElementById("midiOutContinue") as HTMLButtonElement;
const midiOutStopBtn = document.getElementById("midiOutStop") as HTMLButtonElement;
const midiOutStatusEl = document.getElementById("midiOutStatus") as HTMLPreElement;

const modelsRefreshBtn = document.getElementById("modelsRefresh") as HTMLButtonElement;
const preferredModelsEl = document.getElementById("preferredModels") as HTMLDivElement;
const modelsStatusEl = document.getElementById("modelsStatus") as HTMLPreElement;
const modelpackPathInput = document.getElementById("modelpackPath") as HTMLInputElement;
const modelpackImportBtn = document.getElementById("modelpackImport") as HTMLButtonElement;

const ghwtDataRootInput = document.getElementById("ghwtDataRoot") as HTMLInputElement;
const ghwtVgmstreamInput = document.getElementById("ghwtVgmstream") as HTMLInputElement;
const ghwtSaveBtn = document.getElementById("ghwtSave") as HTMLButtonElement;
const ghwtScanBtn = document.getElementById("ghwtScan") as HTMLButtonElement;
const ghwtImportAllBtn = document.getElementById("ghwtImportAll") as HTMLButtonElement;
const ghwtBrowseBtn = document.getElementById("ghwtBrowse") as HTMLButtonElement;
const ghwtStatusEl = document.getElementById("ghwtStatus") as HTMLPreElement;
const ghwtListEl = document.getElementById("ghwtList") as HTMLDivElement;

const stemMidiTitleInput = document.getElementById("stemMidiTitle") as HTMLInputElement;
const stemMidiArtistInput = document.getElementById("stemMidiArtist") as HTMLInputElement;
const stemMidiPickStemsBtn = document.getElementById("stemMidiPickStems") as HTMLButtonElement;
const stemMidiPickMidiBtn = document.getElementById("stemMidiPickMidi") as HTMLButtonElement;
const stemMidiCreateBtn = document.getElementById("stemMidiCreate") as HTMLButtonElement;
const stemMidiStemsLabel = document.getElementById("stemMidiStemsLabel") as HTMLDivElement;
const stemMidiMidiLabel = document.getElementById("stemMidiMidiLabel") as HTMLDivElement;
const stemMidiStatusEl = document.getElementById("stemMidiStatus") as HTMLPreElement;

const statusEl = document.getElementById("status") as HTMLPreElement;
const listEl = document.getElementById("list") as HTMLDivElement;
const detailsEl = document.getElementById("details") as HTMLDivElement;
const refreshBtn = document.getElementById("refresh") as HTMLButtonElement;
const songsFolderInput = document.getElementById("songsFolder") as HTMLInputElement;
const setOverrideBtn = document.getElementById("setOverride") as HTMLButtonElement;
const clearOverrideBtn = document.getElementById("clearOverride") as HTMLButtonElement;

function renderPlugins() {
  // Base render; actual availability gating happens once we know selected song details.
  renderPluginsWithAvailability(selectedSongPackDetails);
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

function setHudKeyMode(manifestRaw: unknown) {
  const km = extractKeyModeFromManifest(manifestRaw);
  hudKeyModeEl.textContent = `${km.key} ${km.mode}`;
}

function renderDetails(details: SongPackDetails) {
  const title = details.manifest_summary?.title ?? "(missing title)";
  const artist = details.manifest_summary?.artist ?? "";

  const raw = details.manifest_raw ? JSON.stringify(details.manifest_raw, null, 2) : "(no manifest)";

  detailsEl.innerHTML = `
    <h3>Details</h3>
    <div class="meta">${escapeHtml(details.kind)} · ${escapeHtml(details.container_path)}</div>

    <h4>${escapeHtml(title)} ${escapeHtml(artist)}</h4>

    ${details.error ? `<pre class="error">${escapeHtml(details.error)}</pre>` : ""}

    <h4>Features</h4>
    <ul>
      <li>beats: ${escapeHtml(yesNo(details.has_beats))}</li>
      <li>tempo_map: ${escapeHtml(yesNo(details.has_tempo_map))}</li>
      <li>sections: ${escapeHtml(yesNo(details.has_sections))}</li>
      <li>events: ${escapeHtml(yesNo(details.has_events))}</li>
      <li>lyrics: ${escapeHtml(yesNo(Boolean(details.has_lyrics)))}</li>
    </ul>

    <h4>Audio</h4>
    <ul>
      <li>mix.mp3: ${escapeHtml(yesNo(details.has_mix_mp3))}</li>
      <li>mix.ogg: ${escapeHtml(yesNo(details.has_mix_ogg))}</li>
      <li>mix.wav: ${escapeHtml(yesNo(Boolean(details.has_mix_wav)))}</li>
    </ul>

    <h4>Charts</h4>
    ${details.charts.length ? `<ul>${details.charts.map((c) => `<li>${escapeHtml(c)}</li>`).join("\n")}</ul>` : "(none)"}

    <h4>manifest.json</h4>
    <pre>${escapeHtml(raw)}</pre>
  `;
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
let selectedSongPackPath: string | null = null;
let selectedSongPackDetails: SongPackDetails | null = null;

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

// Desktop default: use Rust native audio engine.
let currentTimebase: TransportTimebase = new NativeAudioTimebase({ sampleRateHz: 48_000, channels: 2 });
let transportController = new TransportController(currentTimebase, {
  bpm: 120,
  timeSignature: [4, 4]
});

let currentPlaybackRate = 1;

// Guitar-Hero-ish: once a song is loaded, make the Now Playing panel the focus.
let playFocusMode = false;
function setPlayFocusMode(enabled: boolean) {
  playFocusMode = enabled;
  playLayoutEl.classList.toggle("isFocus", enabled);
  toggleFocusBtn.textContent = enabled ? "Library" : "Focus";
  // Canvas size may change; ensure we resize so the visualizer fills the space.
  resizeVizCanvas();
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

function computeSongCapabilities(details: SongPackDetails | null): SongCapabilities {
  const charts = details?.charts ?? [];
  const byInstrument: SongCapabilities["charts"]["byInstrument"] = {};

  // Heuristic mapping: chart filenames often carry role/instrument hints.
  // We’ll firm this up later with a proper chart manifest, but this gives the UX a useful signal now.
  const anyMatch = (re: RegExp) => charts.some((c) => re.test(c));
  byInstrument.lead_guitar = anyMatch(/lead|guitar(?!_rhythm)|gtr/i);
  byInstrument.rhythm_guitar = anyMatch(/rhythm|guitar_rhythm|rhythm_guitar/i);
  byInstrument.bass = anyMatch(/bass/i);
  byInstrument.drums = anyMatch(/drum|kit/i);
  byInstrument.keys = anyMatch(/keys|piano|synth/i);
  byInstrument.vocals = anyMatch(/vocal|vox|lyrics/i);

  return {
    features: {
      beats: Boolean(details?.has_beats),
      tempo_map: Boolean(details?.has_tempo_map),
      sections: Boolean(details?.has_sections),
      events: Boolean(details?.has_events),
      lyrics: Boolean(details?.has_lyrics),
      notes_mid: Boolean(details?.has_notes_mid),
    },
    audio: {
      wav: Boolean(details?.has_mix_wav),
      mp3: Boolean(details?.has_mix_mp3),
      ogg: Boolean(details?.has_mix_ogg),
    },
    charts: {
      any: charts.length > 0,
      byInstrument,
    },
  };
}

function renderCaps(details: SongPackDetails | null) {
  const caps = computeSongCapabilities(details);

  const pill = (label: string, ok: boolean, hint?: string) => {
    const cls = ok ? "capPill capPill--ok" : "capPill capPill--missing";
    const title = hint ? ` title="${escapeHtml(hint)}"` : "";
    return `<span class="${cls}"${title}>${escapeHtml(label)}</span>`;
  };

  const featurePills = [
    pill("beats", caps.features.beats, "features/beats.json"),
    pill("tempo", caps.features.tempo_map, "features/tempo_map.json"),
    pill("sections", caps.features.sections, "features/sections.json"),
    pill("events", caps.features.events, "features/events.json"),
    pill("lyrics", caps.features.lyrics, "features/lyrics.json"),
    pill("midi", caps.features.notes_mid, "features/notes.mid"),
  ].join("\n");

  const chartPills = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
    .map((inst) => pill(INSTRUMENT_LABELS[inst], Boolean(caps.charts.byInstrument[inst]), "chart availability (heuristic)"))
    .join("\n");

  const audioPills = [
    pill("mix.wav", caps.audio.wav),
    pill("mix.mp3", caps.audio.mp3),
    pill("mix.ogg", caps.audio.ogg),
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

function applyInstrumentAvailability(details: SongPackDetails | null) {
  const caps = computeSongCapabilities(details);
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
      if (firstEnabled) sel.value = firstEnabled.value;
    }
  }
}

function pluginRequirements(id: string): { ok: (d: SongPackDetails | null) => boolean; reason: string } {
  // Minimal v1 mapping (can evolve per plugin manifest later)
  switch (id) {
    case "viz-lyrics":
      return {
        ok: (d) => Boolean(d?.has_lyrics),
        reason: "Requires features/lyrics.json"
      };
    // Placeholder visualizers: they can run with transport only.
    default:
      return { ok: () => true, reason: "" };
  }
}

function renderPluginsWithAvailability(details: SongPackDetails | null) {
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
      // TODO: in the GH-style future, this will switch chart lanes.
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
  applyInstrumentAvailability(selectedSongPackDetails);
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

type MidiPortInfo = { id: number; name: string };

type MidiOutputSelection = { id: number; name: string };

let midiConnected = false;

function setMidiStatus(msg: string) {
  midiStatusEl.textContent = msg;
}

async function generateLyricsForSelectedSongPack(): Promise<void> {
  if (!selectedSongPackPath || !selectedSongPackDetails) {
    setVizStatus("Select a SongPack first");
    return;
  }

  // If it's a zip songpack, offer to convert to a directory songpack so we can write features.
  if (selectedSongPackDetails.kind !== "directory") {
    const ok = confirm(
      "This SongPack is a zipped .songpack file (read-only).\n\nConvert it to a directory SongPack so we can write features/lyrics.json?"
    );
    if (!ok) {
      setVizStatus("Lyrics generation cancelled");
      return;
    }

    try {
      const newPath = await safeInvoke<string>("convert_songpack_to_directory", { containerPath: selectedSongPackPath });
      selectedSongPackPath = newPath;
      // Refresh details for the new directory songpack.
      selectedSongPackDetails = await safeInvoke<SongPackDetails>("get_songpack_details", { containerPath: newPath });
      setVizStatus(`Converted to directory SongPack: ${newPath}`);
    } catch (e) {
      setVizStatus(`Conversion failed: ${String(e)}`);
      return;
    }
  }

  const durationSec = Number(selectedSongPackDetails.manifest_summary?.duration_sec ?? 0);
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

    await safeInvoke("write_songpack_lyrics_json", { containerPath: selectedSongPackPath, lyricsJson });

    // Update local state so viz init sees it without requiring the user to click Details again.
    currentLyrics = lyricsJson as unknown as LyricsFile;

    setVizStatus("Generated features/lyrics.json (MVP line-level timings)");
    await refresh();
  } catch (e) {
    setVizStatus(`Lyrics generation failed: ${String(e)}`);
  }
}

function setMidiOutStatus(msg: string) {
  midiOutStatusEl.textContent = msg;
}

async function refreshMidiInputPorts() {
  try {
    const ports = await invoke<MidiPortInfo[]>("list_midi_input_ports");
    midiInPortSelect.innerHTML = ports
      .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`)
      .join("\n");
  } catch (e) {
    setMidiStatus(`midi input ports error: ${String(e)}`);
  }
}

async function refreshMidiOutputPorts() {
  try {
    const ports = await invoke<MidiPortInfo[]>("list_midi_output_ports");
    midiOutPortSelect.innerHTML = ports
      .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`)
      .join("\n");

    // Best-effort: apply saved selection.
    const saved = await invoke<MidiOutputSelection | null>("midi_clock_output_get_saved_port");
    if (saved) {
      const match = ports.find((p) => p.name === saved.name || p.id === saved.id);
      if (match) midiOutPortSelect.value = String(match.id);
    }
  } catch (e) {
    setMidiOutStatus(`midi output ports error: ${String(e)}`);
  }
}

async function selectMidiOutputPortAndPersist() {
  const portId = Number(midiOutPortSelect.value);
  if (!Number.isFinite(portId)) return;
  await invoke("midi_clock_output_select_port_and_persist", { portId });
  setMidiOutStatus(`midi clock out: selected port=${portId}`);
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

async function connectMidiClockInput() {
  const portId = Number(midiInPortSelect.value);
  const tempoScale = Number(midiTempoScaleInput.value);
  if (!Number.isFinite(portId)) return;
  await invoke("midi_clock_input_start", { portId, tempoScale });
  midiConnected = true;
  setMidiStatus(`midi clock input connected: port=${portId} scale=${tempoScale}`);
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
}

// Ensure the UI reflects the desktop-only backend.
audioBackendSelect.value = "native";

function setVizStatus(msg: string) {
  vizStatusEl.textContent = msg;
}

function setGhwtStatus(msg: string) {
  ghwtStatusEl.textContent = msg;
}

function setStemMidiStatus(msg: string) {
  stemMidiStatusEl.textContent = msg;
}

let stemMidiStemPaths: string[] = [];
let stemMidiPath: string | null = null;

function renderStemMidiSelection() {
  stemMidiStemsLabel.textContent = stemMidiStemPaths.length ? `${stemMidiStemPaths.length} stem(s)` : "(none)";
  stemMidiMidiLabel.textContent = stemMidiPath ? stemMidiPath : "(none)";
}

async function stemMidiCreateSongPack() {
  const title = stemMidiTitleInput.value.trim();
  const artist = stemMidiArtistInput.value.trim();
  if (!title || !artist) {
    setStemMidiStatus("title + artist are required");
    return;
  }
  if (!stemMidiStemPaths.length) {
    setStemMidiStatus("pick at least one stem WAV");
    return;
  }
  if (!stemMidiPath) {
    setStemMidiStatus("pick a MIDI file");
    return;
  }

  setStemMidiStatus("creating…");
  stemMidiCreateBtn.disabled = true;
  try {
    const res = await safeInvoke<StemMidiCreateResult>("stem_midi_create_songpack", {
      req: {
        title,
        artist,
        stemWavPaths: stemMidiStemPaths,
        midiPath: stemMidiPath,
      } satisfies StemMidiCreateRequest,
    });
    setStemMidiStatus(`created: ${res.songpack_path}`);
    void refresh();
  } finally {
    stemMidiCreateBtn.disabled = false;
  }
}

let ghwtSongs: GhwtSongEntry[] = [];

function renderGhwtSongs() {
  if (!ghwtSongs.length) {
    ghwtListEl.innerHTML = "<div class=\"meta\">(no DLC songs found)</div>";
    return;
  }

  ghwtListEl.innerHTML = `
    <ul>
      ${ghwtSongs
        .map((s) => {
          const title = s.title || s.checksum;
          const artist = s.artist || "";
          const stems = s.stem_fsb_paths?.length ?? 0;
          const hasPreview = Boolean(s.preview_fsb_path);
          const audioHint = stems >= 2 ? `${stems} stems` : hasPreview ? "preview" : "(no audio?)";
          return `
            <li>
              <div class="row">
                <div class="grow">
                  <strong>${escapeHtml(title)}</strong> ${escapeHtml(artist)}
                  <div class="meta">${escapeHtml(s.checksum)} · ${escapeHtml(audioHint)} · ${escapeHtml(s.preview_fsb_path)}</div>
                </div>
                <button class="ghwtImportBtn" data-checksum="${escapeHtml(s.checksum)}">Import</button>
              </div>
            </li>
          `;
        })
        .join("\n")}
    </ul>
  `;

  for (const btn of Array.from(ghwtListEl.querySelectorAll<HTMLButtonElement>("button.ghwtImportBtn"))) {
    btn.addEventListener("click", () => {
      const checksum = btn.getAttribute("data-checksum");
      if (!checksum) return;
      void ghwtImportSong(checksum);
    });
  }
}

async function ghwtLoadSettings() {
  try {
    const s = await safeInvoke<GhwtSettings>("get_ghwt_settings");
    ghwtDataRootInput.value = s.data_root ?? "";
    ghwtVgmstreamInput.value = s.vgmstream_cli_path ?? "";
  } catch (e) {
    // Not fatal.
    setGhwtStatus(String(e));
  }
}

async function ghwtSaveSettings() {
  const dataRoot = ghwtDataRootInput.value.trim();
  const vgm = ghwtVgmstreamInput.value.trim();

  await safeInvoke("set_ghwt_settings", {
    dataRoot: dataRoot || null,
    vgmstreamCliPath: vgm || null,
  });
  setGhwtStatus("saved");
}

async function ghwtScanDlc() {
  const dataRoot = ghwtDataRootInput.value.trim();
  if (!dataRoot) {
    setGhwtStatus("Set GHWT DATA root first");
    return;
  }

  // Preflight (gives friendly actionable errors)
  try {
    const pf = await safeInvoke<GhwtPreflight>("ghwt_preflight", {
      dataRoot,
      vgmstreamCliPath: ghwtVgmstreamInput.value.trim() || null,
    });
    if (!pf.dlc_ok) {
      setGhwtStatus(pf.error ?? "Invalid DATA/DLC folder");
      return;
    }
    if (!pf.vgmstream_ok) {
      // Still allow scan; but make it very clear import will fail.
      setGhwtStatus(
        (pf.error ?? "vgmstream-cli not available") +
          " (scan will work; import will fail until vgmstream is installed/configured)"
      );
    }
  } catch (e) {
    // Ignore: preflight isn't required for scan.
  }

  setGhwtStatus("Scanning DLC…");
  ghwtSongs = [];
  renderGhwtSongs();

  try {
    ghwtSongs = await safeInvoke<GhwtSongEntry[]>("ghwt_scan_dlc", { dataRoot });
    setGhwtStatus(`found ${ghwtSongs.length} DLC songs`);
  } catch (e) {
    setGhwtStatus(String(e));
  }
  renderGhwtSongs();
}

async function ghwtImportAll() {
  const dataRoot = ghwtDataRootInput.value.trim();
  const vgm = ghwtVgmstreamInput.value.trim();
  if (!dataRoot) {
    setGhwtStatus("Set GHWT DATA root first");
    return;
  }

  // Preflight required for import-all.
  const pf = await safeInvoke<GhwtPreflight>("ghwt_preflight", {
    dataRoot,
    vgmstreamCliPath: vgm || null,
  });
  if (!pf.dlc_ok) throw new Error(pf.error ?? "Invalid DATA/DLC folder");
  if (!pf.vgmstream_ok) throw new Error(pf.error ?? "vgmstream-cli not available");

  setGhwtStatus("Importing all DLC songs…");
  ghwtImportAllBtn.disabled = true;

  try {
    const res = await safeInvoke<GhwtImportAllResult[]>("ghwt_import_all", {
      dataRoot,
      vgmstreamCliPath: vgm || null,
    });
    const ok = res.filter((r) => r.ok).length;
    const bad = res.length - ok;
    setGhwtStatus(`bulk import done: ok=${ok} failed=${bad}`);
    void refresh();
  } finally {
    ghwtImportAllBtn.disabled = false;
  }
}

async function ghwtImportSong(checksum: string) {
  const dataRoot = ghwtDataRootInput.value.trim();
  const vgm = ghwtVgmstreamInput.value.trim();
  if (!dataRoot) {
    setGhwtStatus("Set GHWT DATA root first");
    return;
  }

  // Preflight required for import.
  const pf = await safeInvoke<GhwtPreflight>("ghwt_preflight", {
    dataRoot,
    vgmstreamCliPath: vgm || null,
  });
  if (!pf.dlc_ok) throw new Error(pf.error ?? "Invalid DATA/DLC folder");
  if (!pf.vgmstream_ok) throw new Error(pf.error ?? "vgmstream-cli not available");

  setGhwtStatus(`Importing ${checksum}…`);

  try {
    const res = await safeInvoke<GhwtImportResult>("ghwt_import_preview", {
      checksum,
      dataRoot,
      vgmstreamCliPath: vgm || null,
    });
    const used = res.used ? ` (${res.used})` : "";
    setGhwtStatus(`imported: ${res.songpack_path}${used}`);
    // Refresh library list so the new song appears.
    void refresh();
  } catch (e) {
    setGhwtStatus(String(e));
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

async function selectSongPack(containerPath: string, opts?: { autoLoadAudio?: boolean }) {
  detailsEl.innerHTML = "Loading details…";
  try {
    const details = await invoke<SongPackDetails>("get_songpack_details", {
      containerPath,
    });
    renderDetails(details);
    selectedSongPackDetails = details;
    setHudKeyMode(details.manifest_raw);

    // Show per-song data availability so users know what’s actually present.
    renderCaps(details);
    applyInstrumentAvailability(details);
    renderPluginsWithAvailability(details);

    // Load lyrics (best-effort)
    try {
      const lyr = await invoke<unknown>("read_songpack_json", { containerPath, relPath: "features/lyrics.json" });
      currentLyrics = (lyr ?? null) as LyricsFile | null;
    } catch {
      currentLyrics = null;
    }

    // Selecting a SongPack enables audio load.
    selectedSongPackPath = containerPath;
    audioLoadBtn.disabled = false;

    if (opts?.autoLoadAudio) {
      // For the desktop app, auto-load audio so the transport becomes usable immediately.
      setAudioStatus(`selected songpack: ${containerPath}\n(auto-loading audio…)`);
      void loadAudioFromSelectedSongPack();
      // Default UX: once a song is selected, shift attention to Now Playing.
      setPlayFocusMode(true);
    } else {
      setAudioStatus(`selected songpack: ${containerPath}`);
    }
  } catch (e) {
    detailsEl.innerHTML = `<pre class="error">${escapeHtml(String(e))}</pre>`;
  }
}

async function loadAudioFromSelectedSongPack() {
  if (!selectedSongPackPath) {
    setAudioStatus("Select a SongPack first (click Details)");
    return;
  }

  setAudioStatus("Loading audio…");
  audioLoadBtn.disabled = true;

  try {
    // Prefer the direct-native path (avoids sending large WAV bytes over IPC).
    if (transportController.loadAudioFromSongPack) {
      await transportController.loadAudioFromSongPack(selectedSongPackPath);
      transportController.setPlaybackRate(currentPlaybackRate);

      // We no longer have the raw bytes in JS (by design).
      lastLoadedAudio = null;
      setAudioStatus(`loaded: ${selectedSongPackPath}`);
    } else {
      // Fallback: read audio into JS, then send back into Rust for decode.
      const blob = await invoke<AudioBlob>("read_songpack_audio", {
        containerPath: selectedSongPackPath
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

  if (plugin.id === "viz-lyrics" && !currentLyrics) {
    const ok = confirm(
      "This songpack has no lyric animation (features/lyrics.json).\n\nGenerate it now from a .txt lyrics file? (directory SongPacks only)"
    );
    if (ok) {
      await generateLyricsForSelectedSongPack();
    }
  }

  const loaded = await loadPlugin(plugin);
  loadedPluginDispose = loaded.dispose ?? null;

  viz = loaded.module.createVisualizer();

  await viz.init({ canvas: vizCanvas, ctx2d: vizCtx2d, song: { lyrics: currentLyrics ?? undefined } });
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

midiOutStartBtn.addEventListener("click", () => {
  midiOutEnabledInput.checked = true;
  midiOutEnabled = true;
  void midiOutStartOrContinue().catch((e) => setMidiOutStatus(String(e)));
});

midiOutContinueBtn.addEventListener("click", () => {
  midiOutEnabledInput.checked = true;
  midiOutEnabled = true;
  midiOutEverStarted = true;
  void invoke("midi_clock_output_continue")
    .then(() => {
      midiOutRunning = true;
      setMidiOutStatus("midi clock out: CONTINUE");
    })
    .catch((e) => setMidiOutStatus(String(e)));
});

midiOutStopBtn.addEventListener("click", () => {
  void midiOutStop().catch((e) => setMidiOutStatus(String(e)));
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

// GHWT importer progress events (from Rust)
void listen<GhwtImportProgressEvent>("ghwt_import_progress", (ev) => {
  const p = ev.payload;
  const pct = Math.round((p.progress ?? 0) * 100);
  const msg = p.message ? ` · ${p.message}` : "";
  setGhwtStatus(`${p.song}: ${pct}% · ${p.id}${msg}`);
});

// Audio controls

audioLoadBtn.addEventListener("click", () => void loadAudioFromSelectedSongPack());

audioPlayBtn.addEventListener("click", () => {
  void transportController.play()
    .then(() => midiOutStartOrContinue())
    .catch((e) => setAudioStatus(String(e)));
});

audioPauseBtn.addEventListener("click", () => {
  transportController.pause();
  void midiOutStop();
});

audioStopBtn.addEventListener("click", () => {
  stopAudio();
  void midiOutStop();
  void midiOutSeek(0);
});

audioSeekGoBtn.addEventListener("click", () => {
  const t = Number(audioSeekInput.value);
  if (!Number.isFinite(t)) return;
  transportController.seek(t);
  void midiOutSeek(t);
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
        .then(() => refreshModels())
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
    modelsStatusEl.textContent = JSON.stringify(installed, null, 2);
  } catch (e) {
    modelsStatusEl.textContent = String(e);
  }
}

modelsRefreshBtn.addEventListener("click", () => {
  void refreshModels();
});

modelpackImportBtn.addEventListener("click", () => {
  const p = modelpackPathInput.value;
  void installModelPackFromPath(p)
    .then(() => refreshModels())
    .catch((e) => {
      modelsStatusEl.textContent = String(e);
    });
});

// Initialize sizing for first paint.
resizeVizCanvas();
renderPreferredModelPacks();
void refreshModels();

async function refresh() {
  statusEl.textContent = "Loading…";
  listEl.innerHTML = "";
  detailsEl.innerHTML = "";

  try {
    const songsFolder = await invoke<string>("get_songs_folder");
    const entries = await invoke<SongPackScanEntry[]>("scan_songpacks");

    // Prefer the built-in demo song on first load so the app is immediately playable.
    // Order: demo first, then the rest alphabetically by title.
    entries.sort((a, b) => {
      const ad = isDemoSongPack(a);
      const bd = isDemoSongPack(b);
      if (ad !== bd) return ad ? -1 : 1;
      const at = (a.manifest?.title ?? "").toLowerCase();
      const bt = (b.manifest?.title ?? "").toLowerCase();
      return at.localeCompare(bt);
    });

    songsFolderInput.value = songsFolder;
    statusEl.textContent = `songsFolder: ${songsFolder}\ncount: ${entries.length}`;

    listEl.innerHTML = `
      <ul>
        ${entries
          .map((e) => {
            const title = e.manifest?.title ?? "(missing title)";
            const artist = e.manifest?.artist ?? "";
            const ok = e.ok ? "OK" : "INVALID";
            const err = e.error ? `<pre class="error">${escapeHtml(e.error)}</pre>` : "";
            const disabled = e.ok ? "" : "disabled";
            return `
              <li>
                <div class="row">
                  <div class="grow">
                    <strong>${escapeHtml(title)}</strong> ${escapeHtml(artist)}
                    <div class="meta">${escapeHtml(ok)} · ${escapeHtml(e.kind)} · ${escapeHtml(e.container_path)}</div>
                  </div>
                  <button class="detailsBtn" data-path="${escapeHtml(e.container_path)}" ${disabled}>Details</button>
                </div>
                ${err}
              </li>
            `;
          })
          .join("\n")}
      </ul>
    `;

    // Wire up Details buttons.
    for (const btn of Array.from(listEl.querySelectorAll("button.detailsBtn"))) {
      btn.addEventListener("click", async (ev) => {
        const el = ev.currentTarget as HTMLButtonElement;
        const containerPath = el.getAttribute("data-path");
        if (!containerPath) return;

        await selectSongPack(containerPath, { autoLoadAudio: true });
      });
    }

    // UX improvement: if no SongPack is selected yet, auto-select the first valid one
    // so the Transport controls become usable immediately.
    if (!selectedSongPackPath) {
      const firstOk = entries.find((e) => e.ok);
      if (firstOk?.container_path) {
        await selectSongPack(firstOk.container_path, { autoLoadAudio: true });
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

// GHWT importer UI
ghwtSaveBtn.addEventListener("click", () => {
  void ghwtSaveSettings().catch((e) => setGhwtStatus(String(e)));
});

ghwtScanBtn.addEventListener("click", () => {
  void ghwtScanDlc();
});

ghwtImportAllBtn.addEventListener("click", () => {
  void ghwtImportAll().catch((e) => setGhwtStatus(String(e)));
});

ghwtBrowseBtn.addEventListener("click", () => {
  void (async () => {
    const dir = await pickFolder();
    if (!dir) return;
    ghwtDataRootInput.value = dir;
    // Save immediately so next app run remembers it.
    await ghwtSaveSettings();
    setGhwtStatus(`selected: ${dir}`);
  })().catch((e) => setGhwtStatus(String(e)));
});

stemMidiPickStemsBtn.addEventListener("click", () => {
  void (async () => {
    const files = await pickFiles(["wav"], true);
    stemMidiStemPaths = files;
    renderStemMidiSelection();
  })().catch((e) => setStemMidiStatus(String(e)));
});

stemMidiPickMidiBtn.addEventListener("click", () => {
  void (async () => {
    const files = await pickFiles(["mid", "midi"], false);
    stemMidiPath = files[0] ?? null;
    renderStemMidiSelection();
  })().catch((e) => setStemMidiStatus(String(e)));
});

stemMidiCreateBtn.addEventListener("click", () => {
  void stemMidiCreateSongPack().catch((e) => setStemMidiStatus(String(e)));
});

// Populate plugin list on startup.
void refreshPlugins();

// Load GHWT settings.
void ghwtLoadSettings();

renderStemMidiSelection();

// Populate MIDI ports.
void refreshMidiInputPorts();
void refreshMidiOutputPorts();

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

void refresh();
