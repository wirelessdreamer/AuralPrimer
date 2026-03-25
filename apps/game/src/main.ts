import "./style.css";
import { invoke } from "@tauri-apps/api/core";
import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import { TransportController } from "./transportController";
import type { TransportTimebase } from "./audioBackend";
import { HtmlAudioTimebase } from "./htmlAudioTimebase";
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
import { buildIngestRequestFromForm, inferIngestTitleArtistFromSourcePath } from "./ingestUi";
import { PREFERRED_MODEL_PACKS } from "./models/preferredModelPacks";
import { installModelPackFromPath, installModelPackFromUrl, listInstalledModelPacks } from "./models/modelManager";
import { BUILTIN_PLUGINS, type PluginDescriptor, loadPlugin, scanBundledPlugins, scanUserPlugins } from "./plugins";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { generateLyricsJsonFromPlainText } from "./lyricsGenerator";
import { selectDrumChartFromMidiBytes, selectMelodicTracksFromMidiBytes, parseMidiTracksFromBytes, type DrumChartSelection, type MelodicTrackSelection, type InstrumentRole } from "./chartLoader";
import { TabRenderer } from "./tabRenderer";
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

type SongPackChartsByPath = Record<string, unknown>;

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

type MidiBlob = {
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

type IngestImportProgressEvent = {
  stream: "stdout" | "stderr";
  line: string;
  parsed?: unknown;
};

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

type StemMidiCreateResult = { songpack_path: string };

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
  warnings: string[];
};

type ImportRawSongFolderRequest = {
  folder_path: string;
  title?: string;
  artist?: string;
};

type ImportRawSongFolderResult = {
  songpack_path: string;
  stems_count: number;
  midi_files_count: number;
  lyrics_included: boolean;
  mapped_game_roles: string[];
  warnings: string[];
};

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
              <div class="meta">Song folders, plugins, models, MIDI, etc.</div>
            </button>
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

          <section class="panel">
            <div class="hud" id="globalHud">
              <div class="hudLabel">Key / Mode</div>
              <div class="hudValue" id="hudKeyMode">C major</div>
            </div>

            <div class="panelHeader">
              <h2>Band Setup</h2>
              <div class="row" style="margin:0">
                <span class="meta">Guitar Hero style player setup</span>
                <button id="toggleFocus" class="ghostBtn" title="Back to song library">Back to Library</button>
              </div>
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

            <div class="row">
              <button id="vizStart">Start visualizer</button>
              <button id="vizStop" disabled>Stop</button>
            </div>

            <canvas id="viz" width="800" height="240"></canvas>
            <div id="playLyrics" class="playLyrics" hidden aria-live="polite" aria-atomic="true">
              <div id="playLyricsCurrent" class="playLyricsCurrent"></div>
              <div id="playLyricsNext" class="playLyricsNext"></div>
            </div>
            <pre id="vizStatus">(not running)</pre>

            <div id="instrumentSelector" class="instrumentSelector" style="display:none">
              <span class="meta">Instrument:</span>
            </div>
            <div id="tabContainer" class="tabContainer" style="display:none"></div>

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
            <div class="startRow">
              <button id="playStart" class="playStartBtn" disabled>Start</button>
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

      <section class="route" data-route="make">
        <section class="panel">
          <div class="panelHeader">
            <h2>Make Music</h2>
            <div class="meta">Creation tools</div>
          </div>
          <p class="meta">
            Build content now from <strong>Configure</strong>:
            Suno stem+MIDI import, sidecar ingest import, GHWT import, and model-pack setup.
          </p>
          <div class="row">
            <button id="makeGoConfig">Open Configure</button>
          </div>
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
              and imports each DLC songâ€™s <code>*_preview.fsb.xen</code> into an AuralPrimer SongPack.
              <br />
              Requires <code>vgmstream-cli</code> on PATH (or provide an explicit path).
            </p>

            <div class="row">
              <label class="meta">GHWT DATA root</label>
              <input id="ghwtDataRoot" class="grow" type="text" placeholder="D:\\Guitar Hero World Tour\\DATA" />
              <button id="ghwtBrowse">Browseâ€¦</button>
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

            <h3>Ingest Audio (sidecar)</h3>
            <p class="meta">
              Run the Python ingest sidecar directly to create SongPacks from audio files, folders, or DTX charts.
            </p>
            <div class="row">
              <label class="meta">Mode</label>
              <select id="ingestMode">
                <option value="import">import (single audio file)</option>
                <option value="import-dir">import-dir (scan folder)</option>
                <option value="import-dtx">import-dtx (DTX chart)</option>
              </select>
            </div>
            <div class="row">
              <label class="meta">Source</label>
              <input id="ingestSourcePath" class="grow" type="text" placeholder="C:\\music\\song.wav" />
              <button id="ingestBrowseSource">Browseâ€¦</button>
            </div>
            <div class="row">
              <label class="meta">Output SongPack (optional)</label>
              <input id="ingestOutPath" class="grow" type="text" placeholder="(leave blank for songs folder default)" />
            </div>
            <div class="row">
              <label class="meta">Profile</label>
              <input id="ingestProfile" type="text" value="full" />
              <label class="meta">Shifts</label>
              <input id="ingestShifts" type="number" min="1" step="1" value="1" />
              <label><input id="ingestMultiFilter" type="checkbox" /> multi-filter</label>
            </div>
            <div class="row">
              <label class="meta">Drum filter</label>
              <select id="ingestDrumFilter">
                <option value="combined_filter">combined_filter</option>
                <option value="dsp_bandpass_improved">dsp_bandpass_improved</option>
                <option value="dsp_spectral_flux">dsp_spectral_flux</option>
                <option value="adaptive_beat_grid">adaptive_beat_grid</option>
                <option value="aural_onset">aural_onset</option>
                <option value="dsp_bandpass">dsp_bandpass</option>
                <option value="librosa_superflux">librosa_superflux</option>
              </select>
              <label class="meta">Melodic</label>
              <select id="ingestMelodicMethod">
                <option value="auto">auto</option>
                <option value="basic_pitch">basic_pitch</option>
                <option value="pyin">pyin</option>
              </select>
            </div>
            <div class="row">
              <label class="meta">Config JSON/path (optional)</label>
              <input id="ingestConfig" class="grow" type="text" placeholder='{"ingest_timestamp":"..."} or C:\\cfg.json' />
            </div>
            <div class="row">
              <label class="meta">Title</label>
              <input id="ingestTitle" class="grow" type="text" placeholder="Optional title override" />
              <label class="meta">Artist</label>
              <input id="ingestArtist" class="grow" type="text" placeholder="Optional artist override" />
              <button id="ingestRun">Run ingest</button>
            </div>
            <pre id="ingestStatus" class="meta">(not started)</pre>

            <h3>Import stem WAV + MIDI (Suno)</h3>
            <p class="meta">
              Pick one Suno export folder. We will validate the root-level WAV and MIDI files,
              prefer a full-mix WAV when present, and create timed lyrics from <code>lyrics.txt</code>
              against the vocals stem when possible.
            </p>
            <div class="row">
              <label class="meta">Title</label>
              <input id="stemMidiTitle" class="grow" type="text" placeholder="Optional title override" />
            </div>
            <div class="row">
              <label class="meta">Artist</label>
              <input id="stemMidiArtist" class="grow" type="text" placeholder="Optional artist override" />
            </div>
            <div class="row">
              <button id="stemMidiPickStems">Pick stem WAVsâ€¦</button>
              <div class="meta grow" id="stemMidiStemsLabel">(none)</div>
            </div>
            <div class="row">
              <button id="stemMidiPickMidi">Pick MIDIâ€¦</button>
              <div class="meta grow" id="stemMidiMidiLabel">(none)</div>
            </div>
            <div id="stemMidiTrackList" class="meta" style="margin:6px 0;max-height:260px;overflow-y:auto"></div>
            <div class="row">
              <button id="stemMidiCreate">Create SongPack</button>
            </div>
            <pre id="stemMidiStatus" class="meta">(not created)</pre>
          </section>

          <section class="panel">
            <div class="panelHeader">
              <h2>MIDI</h2>
              <div class="meta">Clock + full I/O</div>
            </div>

            <h3>MIDI Sync (clock follow)</h3>
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
            <pre id="midiStatus" class="meta">(midi clock: not connected)</pre>
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

type Route = "home" | "play" | "learn" | "make" | "config";

type ConsoleLogCategory = "gamestate" | "play" | "debugging" | "ingest";
type ConsoleLogLevel = "log" | "warn" | "error";
let currentRoute: Route = "home";

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
    make: "navMake",
    config: "navConfig"
  };

  for (const [r, id] of Object.entries(navMap) as Array<[Route, string]>) {
    document.getElementById(id)?.classList.toggle("isActive", r === route);
  }

  // Keep the experience tidy: stop visuals/audio when leaving Play.
  if (route !== "play") {
    closePauseMenu({ restoreFocus: false });
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

function openPlaySongFlow() {
  logConsole("gamestate", "open play flow");
  setRoute("play");
  showSongLibraryStep();
  void refresh();
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

const hudKeyModeEl = document.getElementById("hudKeyMode") as HTMLDivElement;

const vizCanvas = document.getElementById("viz") as HTMLCanvasElement;
const playLyricsEl = document.getElementById("playLyrics") as HTMLDivElement;
const playLyricsCurrentEl = document.getElementById("playLyricsCurrent") as HTMLDivElement;
const playLyricsNextEl = document.getElementById("playLyricsNext") as HTMLDivElement;
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

const ghwtDataRootInput = document.getElementById("ghwtDataRoot") as HTMLInputElement;
const ghwtVgmstreamInput = document.getElementById("ghwtVgmstream") as HTMLInputElement;
const ghwtSaveBtn = document.getElementById("ghwtSave") as HTMLButtonElement;
const ghwtScanBtn = document.getElementById("ghwtScan") as HTMLButtonElement;
const ghwtImportAllBtn = document.getElementById("ghwtImportAll") as HTMLButtonElement;
const ghwtBrowseBtn = document.getElementById("ghwtBrowse") as HTMLButtonElement;
const ghwtStatusEl = document.getElementById("ghwtStatus") as HTMLPreElement;
const ghwtListEl = document.getElementById("ghwtList") as HTMLDivElement;

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
const ingestStatusEl = document.getElementById("ingestStatus") as HTMLPreElement;

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
const selectedSongLabelEl = document.getElementById("selectedSongLabel") as HTMLDivElement;
const selectedSongPathEl = document.getElementById("selectedSongPath") as HTMLDivElement;
const refreshBtn = document.getElementById("refresh") as HTMLButtonElement;
const playStartBtn = document.getElementById("playStart") as HTMLButtonElement;
const pauseMenuOverlayEl = document.getElementById("pauseMenuOverlay") as HTMLDivElement;
const pauseMenuKickerEl = pauseMenuOverlayEl.querySelector(".pauseMenuKicker") as HTMLDivElement;
const pauseMenuTitleEl = document.getElementById("pauseMenuTitle") as HTMLHeadingElement;
const pauseMenuCopyEl = document.getElementById("pauseMenuCopy") as HTMLParagraphElement;
const pauseMenuHintEl = pauseMenuOverlayEl.querySelector(".pauseMenuHint") as HTMLDivElement;
const pauseMenuResumeBtn = document.getElementById("pauseMenuResume") as HTMLButtonElement;
const pauseMenuBackBtn = document.getElementById("pauseMenuBack") as HTMLButtonElement;
const songsFolderInput = document.getElementById("songsFolder") as HTMLInputElement;
const setOverrideBtn = document.getElementById("setOverride") as HTMLButtonElement;
const clearOverrideBtn = document.getElementById("clearOverride") as HTMLButtonElement;

pauseMenuOverlayEl.hidden = true;
pauseMenuOverlayEl.classList.remove("isVisible");
pauseMenuOverlayEl.setAttribute("aria-hidden", "true");

// Disable desktop-only actions when running without the Tauri runtime.
if (!haveTauri()) {
  ghwtBrowseBtn.disabled = true;
  ghwtSaveBtn.disabled = true;
  ghwtScanBtn.disabled = true;
  ghwtImportAllBtn.disabled = true;

  setOverrideBtn.disabled = true;
  clearOverrideBtn.disabled = true;

  stemMidiPickStemsBtn.disabled = true;
  stemMidiPickMidiBtn.disabled = true;
  stemMidiCreateBtn.disabled = true;

  ingestBrowseSourceBtn.disabled = true;
  ingestRunBtn.disabled = true;
  playStartBtn.disabled = true;

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

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function findActiveLyricLineIndex(lines: LyricsFile["lines"], t: number): number {
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (t >= line.start && t <= line.end) return i;
  }
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (t >= lines[i].start) idx = i;
  }
  return idx;
}

function computeLyricHighlightCharIndex(line: LyricsFile["lines"][number], t: number): number {
  const text = line.text ?? "";
  const chunks = line.chunks ?? [];
  if (!chunks.length) {
    const dur = Math.max(0.001, line.end - line.start);
    const p = clamp((t - line.start) / dur, 0, 1);
    return Math.round(p * text.length);
  }

  let idx = -1;
  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (t >= chunk.start && t <= chunk.end) {
      const dur = Math.max(0.001, chunk.end - chunk.start);
      const p = clamp((t - chunk.start) / dur, 0, 1);
      const span = Math.max(0, chunk.char_end - chunk.char_start);
      return chunk.char_start + Math.round(p * span);
    }
    if (t >= chunk.end) idx = i;
  }
  if (idx >= 0) return chunks[idx].char_end;
  return 0;
}

let lastPlaybackLyricsState = "__hidden";

function clearPlaybackLyrics(): void {
  if (lastPlaybackLyricsState === "__hidden") return;
  playLyricsEl.hidden = true;
  playLyricsCurrentEl.innerHTML = "";
  playLyricsNextEl.textContent = "";
  lastPlaybackLyricsState = "__hidden";
}

function renderPlaybackLyrics(t: number): void {
  if (!currentLyrics?.lines?.length) {
    clearPlaybackLyrics();
    return;
  }
  if (currentSelectedPlugin().id === "viz-lyrics") {
    clearPlaybackLyrics();
    return;
  }

  const lines = currentLyrics.lines;
  const previewLeadSec = 3;
  const postLineHoldSec = 1.5;
  const idx = findActiveLyricLineIndex(lines, t);

  if (idx < 0) {
    const firstLine = lines[0];
    if (firstLine.start - t > previewLeadSec) {
      clearPlaybackLyrics();
      return;
    }
    const previewState = `preview|${firstLine.text}`;
    if (lastPlaybackLyricsState === previewState) return;
    playLyricsEl.hidden = false;
    playLyricsCurrentEl.innerHTML = "";
    playLyricsNextEl.textContent = firstLine.text ?? "";
    lastPlaybackLyricsState = previewState;
    return;
  }

  const line = lines[idx];
  if (idx === lines.length - 1 && t > line.end + postLineHoldSec) {
    clearPlaybackLyrics();
    return;
  }

  const text = line.text ?? "";
  const splitAt = clamp(computeLyricHighlightCharIndex(line, t), 0, text.length);
  const currentHtml = [
    `<span class="playLyricsDone">${escapeHtml(text.slice(0, splitAt))}</span>`,
    `<span class="playLyricsRest">${escapeHtml(text.slice(splitAt))}</span>`
  ].join("");
  const nextText = lines[idx + 1]?.text ?? "";
  const renderState = `${idx}|${splitAt}|${currentHtml}|${nextText}`;
  if (lastPlaybackLyricsState === renderState) return;

  playLyricsEl.hidden = false;
  playLyricsCurrentEl.innerHTML = currentHtml;
  playLyricsNextEl.textContent = nextText;
  lastPlaybackLyricsState = renderState;
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
    <div class="meta">${escapeHtml(details.kind)} Â· ${escapeHtml(details.container_path)}</div>

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
let selectedDrumChartSelection: DrumChartSelection | null = null;
let selectedMelodicTracks: MelodicTrackSelection[] = [];
let tabRenderer: TabRenderer | null = null;
let activeTabInstrument: InstrumentRole | null = null;
let selectedSongPackCharts: SongPackChartsByPath | null = null;
let pauseMenuOpen = false;
let pauseMenuRestoreFocusEl: HTMLElement | null = null;
let pauseMenuMode: "paused" | "loaded" = "paused";
let selectedSongPreloadPromise: Promise<void> | null = null;
let selectedSongPreloadPath: string | null = null;

function setSelectedSongSetupLabel(details: SongPackDetails | null, containerPath: string | null) {
  const title = details?.manifest_summary?.title?.trim() || "(no song selected)";
  const artist = details?.manifest_summary?.artist?.trim() || "";
  selectedSongLabelEl.textContent = artist ? `${title}  ·  ${artist}` : title;
  selectedSongPathEl.textContent = containerPath ?? "";
  playStartBtn.disabled = !containerPath;
  logConsole("gamestate", "selected song updated", {
    title,
    artist,
    containerPath: containerPath ?? "",
    playEnabled: Boolean(containerPath),
  });
}

function setSelectedSongCard(containerPath: string | null): void {
  for (const btn of Array.from(listEl.querySelectorAll<HTMLButtonElement>("button.songSelectBtn"))) {
    const isSelected = containerPath !== null && btn.getAttribute("data-path") === containerPath;
    btn.classList.toggle("isSelected", isSelected);
    btn.setAttribute("aria-pressed", isSelected ? "true" : "false");
  }
}

setSelectedSongSetupLabel(null, null);

let availablePlugins: PluginDescriptor[] = [...BUILTIN_PLUGINS];
let loadedPluginDispose: (() => void) | null = null;
const DEFAULT_PLUGIN_ID = "viz-beats";
const DRUM_HIGHWAY_PLUGIN_ID = "viz-drum-highway";
let pluginSelectionMode: "auto" | "user" = "auto";

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
let audioOutputHosts: NativeAudioHostInfo[] = [];
let audioOutputDevices: NativeAudioDeviceInfo[] = [];

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
  await midiOutStartOrContinue();
  setAudioStatus(`playing (fallback): ${songpackPath}`);
  return true;
}

// Guitar-Hero-ish: once a song is loaded, make the Now Playing panel the focus.
let playFocusMode = false;
function setPlayFocusMode(enabled: boolean) {
  playFocusMode = enabled;
  playLayoutEl.classList.toggle("isFocus", enabled);
  // Canvas size may change; ensure we resize so the visualizer fills the space.
  resizeVizCanvas();
  logConsole("gamestate", `play focus mode -> ${enabled ? "focus" : "normal"}`);
}

function isPauseMenuVisible(): boolean {
  return pauseMenuOpen || pauseMenuOverlayEl.classList.contains("isVisible") || !pauseMenuOverlayEl.hidden;
}

function setPauseMenuMode(mode: "paused" | "loaded") {
  pauseMenuMode = mode;
  if (mode === "paused") {
    pauseMenuKickerEl.textContent = "Paused";
    pauseMenuTitleEl.textContent = "Pause Menu";
    pauseMenuCopyEl.textContent = "Keep your place and resume, or head back to song selection.";
    pauseMenuResumeBtn.textContent = "Resume";
    pauseMenuHintEl.textContent = "Press Esc again to resume instantly.";
    return;
  }

  pauseMenuKickerEl.textContent = "Song Ready";
  pauseMenuTitleEl.textContent = "Back Out?";
  pauseMenuCopyEl.textContent = "This song is loaded. Stay here, or head back to song selection.";
  pauseMenuResumeBtn.textContent = "Stay Here";
  pauseMenuHintEl.textContent = "Press Esc again to close this prompt.";
}

function closePauseMenu(opts?: { restoreFocus?: boolean }) {
  const wasVisible = isPauseMenuVisible();
  pauseMenuOpen = false;
  pauseMenuMode = "paused";
  pauseMenuOverlayEl.classList.remove("isVisible");
  pauseMenuOverlayEl.hidden = true;
  pauseMenuOverlayEl.setAttribute("aria-hidden", "true");
  document.body.classList.remove("pauseMenuOpen");

  if (!wasVisible) {
    pauseMenuRestoreFocusEl = null;
    return;
  }

  const restoreFocus = opts?.restoreFocus ?? true;
  const focusTarget = pauseMenuRestoreFocusEl;
  pauseMenuRestoreFocusEl = null;
  if (restoreFocus && focusTarget) {
    focusTarget.focus();
  }
}

function showPauseMenu(mode: "paused" | "loaded" = "paused") {
  if (isPauseMenuVisible()) return;
  setPauseMenuMode(mode);
  pauseMenuOpen = true;
  pauseMenuRestoreFocusEl = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  pauseMenuOverlayEl.hidden = false;
  pauseMenuOverlayEl.classList.add("isVisible");
  pauseMenuOverlayEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("pauseMenuOpen");
  pauseMenuResumeBtn.focus();
  logConsole("gamestate", "pause menu -> open");
}

function pauseForPauseMenu() {
  transportController.pause();
  transport = transportController.getState();
  void midiOutStop();
  setAudioStatus("paused");
  showPauseMenu("paused");
}

async function resumeFromPauseMenu() {
  const mode = pauseMenuMode;
  closePauseMenu({ restoreFocus: false });
  if (mode === "loaded") {
    logConsole("gamestate", "pause menu -> close loaded-song prompt");
    return;
  }
  if (!selectedSongPackPath || lastLoadedSongPackPath !== selectedSongPackPath) {
    setAudioStatus("pause menu closed");
    logConsole("gamestate", "pause menu -> close without resume");
    return;
  }
  try {
    await transportController.play();
    transport = transportController.getState();
    await midiOutStartOrContinue();
    setAudioStatus(selectedSongPackPath ? `playing: ${selectedSongPackPath}` : "resumed");
    logConsole("gamestate", "pause menu -> resume");
  } catch (e) {
    const err = String(e);
    setAudioStatus(err);
    setVizStatus(`resume failed: ${err}`);
    errorConsole("play", "resume from pause menu failed", e);
  }
}

function backToSongSelectionFromPauseMenu() {
  closePauseMenu({ restoreFocus: false });
  stopAudio();
  void midiOutStop();
  void midiOutSeek(0);
  showSongLibraryStep();
  setAudioStatus("returned to song selection");
  logConsole("gamestate", "pause menu -> back to song selection");
}

function showSongLibraryStep() {
  closePauseMenu({ restoreFocus: false });
  logConsole("gamestate", "show song library step");
  playLayoutEl.classList.add("isLibraryOnly");
  setPlayFocusMode(false);
  try {
    stopVisualizer();
    transportController.pause();
  } catch {
    // ignore
  }
}

function canOpenLoadedSongBackOutPrompt(): boolean {
  return currentRoute === "play" && Boolean(selectedSongPackPath) && lastLoadedSongPackPath === selectedSongPackPath;
}

function showBandSetupStep() {
  closePauseMenu({ restoreFocus: false });
  logConsole("gamestate", "show band setup step");
  playLayoutEl.classList.remove("isLibraryOnly");
  setPlayFocusMode(true);
}

toggleFocusBtn.addEventListener("click", () => {
  showSongLibraryStep();
});

showSongLibraryStep();
toggleFocusBtn.disabled = true;

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
    selectedMelodicTracks = selectMelodicTracksFromMidiBytes(midiBytes);
    if (selectedMelodicTracks.length > 0) {
      logConsole("play", `found ${selectedMelodicTracks.length} melodic track(s): ${selectedMelodicTracks.map(t => t.role).join(", ")}`);
    }

    return selectDrumChartFromMidiBytes(midiBytes);
  } catch (e) {
    selectedMelodicTracks = [];
    warnConsole("debugging", `failed to load/parse features/notes.mid from ${containerPath}`, e);
    return null;
  }
}

function asObjectRecord(v: unknown): Record<string, unknown> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  return v as Record<string, unknown>;
}

function applyInstrumentHintsFromToken(
  tokenRaw: string,
  byInstrument: SongCapabilities["charts"]["byInstrument"]
): void {
  const token = tokenRaw.toLowerCase();
  if (!token) return;

  if (/rhythm[_\s-]?guitar|guitar[_\s-]?rhythm|rhythm/.test(token)) byInstrument.rhythm_guitar = true;
  if (/lead[_\s-]?guitar|guitar[_\s-]?lead|lead/.test(token)) byInstrument.lead_guitar = true;
  if (/guitar|gtr/.test(token) && !/rhythm/.test(token)) byInstrument.lead_guitar = true;
  if (/bass/.test(token)) byInstrument.bass = true;
  if (/keys|piano|synth/.test(token)) byInstrument.keys = true;
  if (/vocals?|vox|lyrics?/.test(token)) byInstrument.vocals = true;
  if (
    /drum|kit|percussion|beat|kick|snare|hihat|hat|cym|ride|tom|bd|sd|hh|cy|rd|ht|lt|ft/.test(token)
  ) {
    byInstrument.drums = true;
  }

  // Common five-fret lane naming in some chart formats.
  if (/^(g|r|y|b|o|green|red|yellow|blue|orange)$/.test(token)) {
    byInstrument.lead_guitar = true;
  }
}

function applyInstrumentHintsFromChartJson(
  chartJson: unknown,
  byInstrument: SongCapabilities["charts"]["byInstrument"]
): void {
  const chart = asObjectRecord(chartJson);
  if (!chart) return;

  if (typeof chart.mode === "string") {
    applyInstrumentHintsFromToken(chart.mode, byInstrument);
  }
  if (typeof chart.instrument === "string") {
    applyInstrumentHintsFromToken(chart.instrument, byInstrument);
  }
  if (Array.isArray(chart.instruments)) {
    for (const item of chart.instruments) {
      if (typeof item === "string") {
        applyInstrumentHintsFromToken(item, byInstrument);
      }
    }
  }

  if (!Array.isArray(chart.targets)) return;
  for (const target of chart.targets) {
    const targetObj = asObjectRecord(target);
    if (!targetObj) continue;
    if (typeof targetObj.lane === "string") {
      applyInstrumentHintsFromToken(targetObj.lane, byInstrument);
    }
    if (typeof targetObj.instrument === "string") {
      applyInstrumentHintsFromToken(targetObj.instrument, byInstrument);
    }
  }
}

function applyInstrumentHintsFromMappedRole(
  roleRaw: string,
  byInstrument: SongCapabilities["charts"]["byInstrument"]
): void {
  switch (roleRaw) {
    case "drums":
      byInstrument.drums = true;
      break;
    case "bass":
      byInstrument.bass = true;
      break;
    case "lead_guitar":
      byInstrument.lead_guitar = true;
      break;
    case "rhythm_guitar":
      byInstrument.rhythm_guitar = true;
      break;
    case "keys":
      byInstrument.keys = true;
      break;
    case "vocals":
      byInstrument.vocals = true;
      break;
    default:
      break;
  }
}

function applyInstrumentHintsFromManifestRaw(
  manifestRaw: unknown,
  byInstrument: SongCapabilities["charts"]["byInstrument"]
): void {
  const manifest = asObjectRecord(manifestRaw);
  if (!manifest) return;

  const source = asObjectRecord(manifest.source);
  const parts = source ? asObjectRecord(source.parts) : null;
  const mappedRoles = Array.isArray(parts?.mapped_game_roles) ? parts?.mapped_game_roles : [];
  for (const role of mappedRoles) {
    if (typeof role === "string") {
      applyInstrumentHintsFromMappedRole(role, byInstrument);
    }
  }

  const assets = asObjectRecord(manifest.assets);
  const midi = assets ? asObjectRecord(assets.midi) : null;
  const midiTracks = Array.isArray(midi?.tracks) ? midi?.tracks : [];
  for (const track of midiTracks) {
    const rec = asObjectRecord(track);
    if (rec && typeof rec.role === "string") {
      applyInstrumentHintsFromMappedRole(rec.role, byInstrument);
    }
  }
}

function computeSongCapabilities(
  details: SongPackDetails | null,
  drumSelection: DrumChartSelection | null,
  chartsByPath: SongPackChartsByPath | null
): SongCapabilities {
  const charts = details?.charts ?? [];
  const byInstrument: SongCapabilities["charts"]["byInstrument"] = {};
  const midiDrumsAvailable = Boolean(drumSelection?.events.length);

  // First pass: filename hints.
  for (const chartPath of charts) {
    applyInstrumentHintsFromToken(chartPath, byInstrument);
  }

  // Second pass: chart JSON content hints (mode/targets/instrument fields).
  for (const [chartPath, chartJson] of Object.entries(chartsByPath ?? {})) {
    applyInstrumentHintsFromToken(chartPath, byInstrument);
    applyInstrumentHintsFromChartJson(chartJson, byInstrument);
  }

  applyInstrumentHintsFromManifestRaw(details?.manifest_raw, byInstrument);

  for (const track of selectedMelodicTracks) {
    applyInstrumentHintsFromMappedRole(track.role, byInstrument);
  }

  if (midiDrumsAvailable) {
    byInstrument.drums = true;
  }

  // Safety fallback: if charts exist but cannot be classified, treat as drums.
  const inferredAny = (Object.keys(INSTRUMENT_LABELS) as Instrument[]).some((inst) => Boolean(byInstrument[inst]));
  if (charts.length > 0 && !inferredAny) {
    byInstrument.drums = true;
  }

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
      any: charts.length > 0 || midiDrumsAvailable,
      byInstrument,
    },
  };
}

function renderCaps(
  details: SongPackDetails | null,
  drumSelection: DrumChartSelection | null,
  chartsByPath: SongPackChartsByPath | null
) {
  const caps = computeSongCapabilities(details, drumSelection, chartsByPath);

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
  ].join("\n");

  const drumHint = drumSelection
    ? `features/notes.mid (${drumSelection.mode}, ${drumSelection.reason}, events=${drumSelection.events.length})`
    : "chart availability (heuristic)";
  const chartPills = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
    .map((inst) => {
      const hint = inst === "drums" ? drumHint : "chart availability (heuristic)";
      return pill(INSTRUMENT_LABELS[inst], Boolean(caps.charts.byInstrument[inst]), hint);
    })
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

function applyInstrumentAvailability(
  details: SongPackDetails | null,
  drumSelection: DrumChartSelection | null,
  chartsByPath: SongPackChartsByPath | null
) {
  const caps = computeSongCapabilities(details, drumSelection, chartsByPath);
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
    case "viz-drum-highway":
      return {
        ok: (d) => Boolean(d?.has_notes_mid),
        reason: "Requires features/notes.mid"
      };
    // Placeholder visualizers: they can run with transport only.
    default:
      return { ok: () => true, reason: "" };
  }
}

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

function renderPluginsWithAvailability(details: SongPackDetails | null) {
  const previousSelectedId = selectedPluginId();

  // Re-render options with disabled state + hint.
  pluginSelect.innerHTML = availablePlugins
    .map((p, idx) => {
      const req = pluginRequirements(p.id);
      const ok = req.ok(details);
      const label = `${p.name} (${p.source})${ok ? "" : " â€” missing data"}`;
      const disabled = ok ? "" : "disabled";
      const title = ok || !req.reason ? "" : ` title="${escapeHtml(req.reason)}"`;
      return `<option value="${idx}" ${disabled}${title}>${escapeHtml(label)}</option>`;
    })
    .join("\n");

  setPluginSelectionById(previousSelectedId);
  syncPreferredPluginSelection();

  // If selected plugin became disabled, choose first enabled.
  if (pluginSelect.selectedOptions.length && pluginSelect.selectedOptions[0].disabled) {
    const firstEnabled = Array.from(pluginSelect.options).find((o) => !o.disabled);
    if (firstEnabled) pluginSelect.value = firstEnabled.value;
  }
}

type Player = { id: string; name: string; instrument: Instrument };
let players: Player[] = [{ id: "p1", name: "Player 1", instrument: "drums" }];

function selectedPluginId(): string | null {
  const idx = pluginSelect.selectedIndex;
  if (idx < 0 || idx >= availablePlugins.length) return null;
  return availablePlugins[idx]?.id ?? null;
}

function preferredPluginIdForPlayers(): string {
  return players[0]?.instrument === "drums" ? DRUM_HIGHWAY_PLUGIN_ID : DEFAULT_PLUGIN_ID;
}

function setPluginSelectionById(pluginId: string | null): boolean {
  if (!pluginId) return false;
  const idx = availablePlugins.findIndex((p) => p.id === pluginId);
  if (idx < 0) return false;
  const option = Array.from(pluginSelect.options).find((o) => o.value === String(idx));
  if (!option || option.disabled) return false;
  if (pluginSelect.value === String(idx)) return false;
  pluginSelect.value = String(idx);
  return true;
}

function syncPreferredPluginSelection(): boolean {
  if (pluginSelectionMode !== "auto") return false;
  return setPluginSelectionById(preferredPluginIdForPlayers());
}

function resetPlayersForSongSetup() {
  pluginSelectionMode = "auto";
  players = [{ id: "p1", name: "Player 1", instrument: "drums" }];
  rerenderPlayersAndApplyAvailability();
  syncPreferredPluginSelection();
}

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
              <button class="removePlayer" title="Remove player" ${players.length <= 1 ? "disabled" : ""}>Ã—</button>
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
      pluginSelectionMode = "auto";
      players = players.map((p) => (p.id === id ? { ...p, instrument: inst } : p));
      const pluginChanged = syncPreferredPluginSelection();
      if (pluginChanged) {
        restartVisualizerForPluginSelection();
      }
      window.dispatchEvent(
        new CustomEvent("auralprimer:players-updated", {
          detail: { players },
        })
      );
    });

    const remove = chip.querySelector<HTMLButtonElement>("button.removePlayer");
    remove?.addEventListener("click", () => {
      if (players.length <= 1) return;
      const previousPluginId = selectedPluginId();
      players = players.filter((p) => p.id !== id);
      rerenderPlayersAndApplyAvailability();
      if (selectedPluginId() !== previousPluginId) {
        restartVisualizerForPluginSelection();
      }
    });
  }
}

// Ensure instruments/plugin availability is applied even if players are added after song selection.
function rerenderPlayersAndApplyAvailability() {
  renderPlayers();
  applyInstrumentAvailability(selectedSongPackDetails, selectedDrumChartSelection, selectedSongPackCharts);
  syncPreferredPluginSelection();
}

addPlayerBtn.addEventListener("click", () => {
  const nextIdx = players.length + 1;
  const id = `p${nextIdx}`;
  const defaultInst: Instrument =
    nextIdx === 2 ? "lead_guitar" : nextIdx === 3 ? "bass" : nextIdx === 4 ? "rhythm_guitar" : "keys";
  players = [...players, { id, name: `Player ${nextIdx}`, instrument: defaultInst }];
  rerenderPlayersAndApplyAvailability();
});

rerenderPlayersAndApplyAvailability();

const metronome = new Metronome({ enabled: false, volume: 0.25 });

type MidiPortInfo = { id: number; name: string };

type MidiOutputSelection = { id: number; name: string };
type MidiInputSelection = { id: number; name: string };

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
    setVizStatus("Generating lyricsâ€¦");

    const text = await safeInvoke<string>("read_text_file", { path: lyricPath });
    const lyricsJson = generateLyricsJsonFromPlainText({
      lyricsText: text,
      durationSec,
      jobId: "auralprimer_mvp"
    });

    await safeInvoke("write_songpack_lyrics_json", { containerPath: selectedSongPackPath, lyricsJson });

    // Update local state so viz init sees it without requiring the user to click Details again.
    currentLyrics = lyricsJson as unknown as LyricsFile;
    renderPlaybackLyrics(transport.t);

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

    const saved = await invoke<MidiInputSavedSettings>("midi_clock_input_get_saved_settings");
    midiTempoScaleInput.value = String(saved.tempo_scale ?? 1);
    midiInSysexEnabledInput.checked = Boolean(saved.allow_sysex);

    const selected = saved.port;
    if (selected) {
      const match = ports.find((p) => p.name === selected.name || p.id === selected.id);
      if (match) {
        midiInPortSelect.value = String(match.id);
      }
    }
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
    const [saved, savedSysex] = await Promise.all([
      invoke<MidiOutputSelection | null>("midi_clock_output_get_saved_port"),
      invoke<boolean>("midi_output_get_saved_allow_sysex")
    ]);
    midiOutSysexEnabled = Boolean(savedSysex);
    midiOutSysexEnabledInput.checked = midiOutSysexEnabled;

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
  if (!Number.isFinite(portId)) return;
  await invoke("midi_clock_input_start_and_persist", { portId, tempoScale, allowSysex });
  midiConnected = true;
  setMidiStatus(`midi input connected: port=${portId} scale=${tempoScale} sysex=${allowSysex ? "on" : "off"}`);
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
let lastLoadedSongPackPath: string | null = null;

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
  logConsole("debugging", msg);
}

function setGhwtStatus(msg: string) {
  ghwtStatusEl.textContent = msg;
}

function setStemMidiStatus(msg: string) {
  stemMidiStatusEl.textContent = msg;
}

function setIngestStatus(msg: string) {
  ingestStatusEl.textContent = msg;
  logConsole("ingest", msg);
}

function debugIngestConsole(message: string, details?: unknown) {
  logConsole("ingest", message, details);
}

let ingestInFlight = false;
let ingestLogLines: string[] = [];

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
    const suffix = msg ? ` Â· ${msg}` : "";
    return `${pct}${id}${suffix}`.trim();
  }

  return ev.line;
}

function inferIngestOutPathFromCommand(command: string[]): string | undefined {
  const outIdx = command.findIndex((part) => part === "--out");
  if (outIdx < 0) return undefined;
  const value = (command[outIdx + 1] ?? "").trim();
  return value || undefined;
}

let stemMidiFolderPath: string | null = null;
let stemMidiInspection: RawSongFolderInspection | null = null;

const stemMidiTrackListEl = document.getElementById("stemMidiTrackList") as HTMLDivElement;

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
  stemMidiPickStemsBtn.textContent = "Pick Suno folder...";
  stemMidiPickMidiBtn.textContent = "Validate folder";
  stemMidiCreateBtn.textContent = "Import SongPack";
  stemMidiStemsLabel.textContent = stemMidiFolderPath ?? "(no folder selected)";
  if (!stemMidiInspection) {
    stemMidiMidiLabel.textContent = stemMidiFolderPath ? "(not validated)" : "(not validated)";
  } else {
    const summary = [
      `${stemMidiInspection.stem_wav_paths.length} WAV${stemMidiInspection.stem_wav_paths.length === 1 ? "" : "s"}`,
      `${stemMidiInspection.midi_paths.length} MIDI${stemMidiInspection.midi_paths.length === 1 ? "" : "s"}`,
      stemMidiInspection.lyrics_txt_path ? "lyrics.txt" : "no lyrics.txt",
      stemMidiInspection.mapped_game_roles.length
        ? stemMidiInspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")
        : "no mapped roles",
      stemMidiInspection.karaoke_json_path
        ? "karaoke JSON"
        : stemMidiInspection.vocal_stem_path
          ? "vocals align"
          : undefined,
    ].filter(Boolean);
    stemMidiMidiLabel.textContent = summary.join(" · ");
  }
  renderStemMidiTrackList();
}

function renderStemMidiTrackList() {
  if (!stemMidiInspection) {
    stemMidiTrackListEl.innerHTML = "";
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

  stemMidiTrackListEl.innerHTML = `
    <div style="font-weight:600;margin-bottom:4px">Folder check</div>
    <div class="meta">default title: ${escapeHtml(stemMidiInspection.title_guess)}</div>
    <div class="meta">game mapping: ${escapeHtml(stemMidiInspection.mapped_game_roles.length ? stemMidiInspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ") : "none")}</div>
    <div class="meta">mix audio: ${escapeHtml(stemMidiInspection.mix_wav_path ? stemMidiBaseName(stemMidiInspection.mix_wav_path) : "sum the detected stems")}</div>
    <div class="meta">lyrics: ${lyricSource}</div>
    <div class="meta" style="margin-top:8px;font-weight:600">Track detection</div>
    ${renderStemMidiAuditTable(stemMidiInspection)}
    ${warningItems}
  `;
}

async function inspectStemMidiFolder(folderPath: string): Promise<void> {
  try {
    const inspection = await safeInvoke<RawSongFolderInspection>("inspect_raw_song_folder", { folderPath });
    stemMidiInspection = inspection;
    if (!stemMidiTitleInput.value.trim() && inspection.title_guess) {
      stemMidiTitleInput.value = inspection.title_guess;
    }
    renderStemMidiSelection();
    const warningCount = inspection.warnings.length;
    const warningSuffix = warningCount ? ` · ${warningCount} warning${warningCount === 1 ? "" : "s"}` : "";
    const mappedSuffix = inspection.mapped_game_roles.length
      ? ` · mapped: ${inspection.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")}`
      : "";
    setStemMidiStatus(`validated: ${inspection.stem_wav_paths.length} WAV(s), ${inspection.midi_paths.length} MIDI file(s)${warningSuffix}${mappedSuffix}`);
  } catch (e) {
    stemMidiInspection = null;
    renderStemMidiSelection();
    setStemMidiStatus(`Folder validation failed: ${String(e)}`);
    throw e;
  }
}

async function stemMidiCreateSongPack() {
  if (!stemMidiFolderPath) {
    setStemMidiStatus("pick a Suno folder first");
    return;
  }

  if (!stemMidiInspection) {
    await inspectStemMidiFolder(stemMidiFolderPath);
  }

  const title = stemMidiTitleInput.value.trim();
  const artist = stemMidiArtistInput.value.trim();

  setStemMidiStatus("importing...");
  stemMidiCreateBtn.disabled = true;
  try {
    const res = await safeInvoke<ImportRawSongFolderResult>("import_raw_song_folder", {
      req: {
        folder_path: stemMidiFolderPath,
        title: title || undefined,
        artist: artist || undefined,
      } satisfies ImportRawSongFolderRequest,
    });
    const lines = [
      `imported: ${res.songpack_path}`,
      `detected ${res.stems_count} WAV stem(s), ${res.midi_files_count} MIDI file(s)${res.lyrics_included ? " · lyrics ready" : ""}`,
    ];
    if (res.mapped_game_roles.length) {
      lines.push(`game roles: ${res.mapped_game_roles.map((role) => formatGameRoleLabel(role)).join(", ")}`);
    }
    if (res.warnings.length) {
      lines.push(`warnings:\n- ${res.warnings.join("\n- ")}`);
    }
    setStemMidiStatus(lines.join("\n"));
    void refresh();
  } finally {
    stemMidiCreateBtn.disabled = false;
  }
}
function ingestSourceExtensions(mode: IngestSubcommand): string[] {
  if (mode === "import-dtx") return ["dtx"];
  return ["wav", "mp3", "ogg", "flac", "m4a"];
}

function setIngestSourcePlaceholder(mode: IngestSubcommand) {
  if (mode === "import-dir") {
    ingestSourcePathInput.placeholder = "C:\\music\\folder";
  } else if (mode === "import-dtx") {
    ingestSourcePathInput.placeholder = "C:\\charts\\song.dtx";
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

async function ingestBrowseSource() {
  const mode = ingestModeSelect.value as IngestSubcommand;
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
  inferIngestMetadataFromSelectedSource();

  let req: IngestImportRequest;
  try {
    req = buildIngestRequestFromForm({
      sourcePath: ingestSourcePathInput.value,
      mode: ingestModeSelect.value as IngestSubcommand,
      outSongpackPath: ingestOutPathInput.value,
      profile: ingestProfileInput.value,
      config: ingestConfigInput.value,
      title: ingestTitleInput.value,
      artist: ingestArtistInput.value,
      drumFilter: ingestDrumFilterSelect.value,
      melodicMethod: ingestMelodicMethodSelect.value,
      shiftsText: ingestShiftsInput.value,
      multiFilter: ingestMultiFilterInput.checked
    });
  } catch (e) {
    setIngestStatus(String(e));
    return;
  }

  ingestInFlight = true;
  resetIngestStatusLog("running ingest sidecar...");
  debugIngestConsole("invoke ingest_import", req);
  ingestRunBtn.disabled = true;
  try {
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
      }
      appendIngestStatusLine(`import complete (exit ${res.exit_code})`);
      void refresh();
    } else {
      const stderr = res.stderr.trim() || "(no stderr)";
      appendIngestStatusLine(`import failed (exit ${res.exit_code})`);
      appendIngestStatusLine(stderr);
    }
  } catch (e) {
    errorConsole("ingest", "invoke ingest_import failed", e);
    appendIngestStatusLine(String(e));
  } finally {
    ingestInFlight = false;
    ingestRunBtn.disabled = false;
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
                  <div class="meta">${escapeHtml(s.checksum)} Â· ${escapeHtml(audioHint)} Â· ${escapeHtml(s.preview_fsb_path)}</div>
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

  setGhwtStatus("Scanning DLCâ€¦");
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

  setGhwtStatus("Importing all DLC songsâ€¦");
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

  setGhwtStatus(`Importing ${checksum}â€¦`);

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

const INSTRUMENT_ROLE_LABELS: Record<string, string> = {
  bass: "Bass",
  rhythm_guitar: "Rhythm Guitar",
  lead_guitar: "Lead Guitar",
  keys: "Keys / Synth",
  melodic: "Melodic",
};

function updateInstrumentSelector(): void {
  const selectorEl = document.getElementById("instrumentSelector") as HTMLDivElement | null;
  const containerEl = document.getElementById("tabContainer") as HTMLDivElement | null;
  if (!selectorEl || !containerEl) return;

  // Clean up old tab renderer.
  if (tabRenderer) {
    tabRenderer.dispose();
    tabRenderer = null;
  }
  activeTabInstrument = null;

  // Clear old buttons (keep the label span).
  const buttons = selectorEl.querySelectorAll("button");
  buttons.forEach((b) => b.remove());

  if (selectedMelodicTracks.length === 0) {
    selectorEl.style.display = "none";
    containerEl.style.display = "none";
    return;
  }

  selectorEl.style.display = "flex";
  containerEl.style.display = "block";

  for (const track of selectedMelodicTracks) {
    const btn = document.createElement("button");
    btn.className = "instrumentBtn";
    btn.textContent = INSTRUMENT_ROLE_LABELS[track.role] ?? track.trackName;
    btn.dataset.role = track.role;
    btn.addEventListener("click", () => {
      selectInstrumentTrack(track.role);
    });
    selectorEl.appendChild(btn);
  }

  // Auto-select the first instrument.
  if (selectedMelodicTracks.length > 0) {
    selectInstrumentTrack(selectedMelodicTracks[0].role);
  }
}

function selectInstrumentTrack(role: InstrumentRole): void {
  const containerEl = document.getElementById("tabContainer") as HTMLDivElement | null;
  const selectorEl = document.getElementById("instrumentSelector") as HTMLDivElement | null;
  if (!containerEl) return;

  const track = selectedMelodicTracks.find((t) => t.role === role);
  if (!track) return;

  // Update button states.
  if (selectorEl) {
    for (const btn of Array.from(selectorEl.querySelectorAll<HTMLButtonElement>("button.instrumentBtn"))) {
      btn.classList.toggle("isActive", btn.dataset.role === role);
    }
  }

  // Create or update tab renderer.
  if (!tabRenderer) {
    containerEl.innerHTML = "";
    tabRenderer = new TabRenderer(containerEl);
  }
  tabRenderer.setTrack(track);
  activeTabInstrument = role;

  logConsole("play", `selected instrument: ${role} (${track.notes.length} notes)`);
}

async function selectSongPack(containerPath: string) {
  const songChanged = selectedSongPackPath !== containerPath;
  selectedDrumChartSelection = null;
  selectedSongPackCharts = null;
  setSelectedSongCard(containerPath);
  detailsEl.innerHTML = "Loading details...";
  try {
    const details = await invoke<SongPackDetails>("get_songpack_details", {
      containerPath,
    });
    renderDetails(details);
    selectedSongPackDetails = details;
    setHudKeyMode(details.manifest_raw);
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
    renderCaps(details, selectedDrumChartSelection, selectedSongPackCharts);
    applyInstrumentAvailability(details, selectedDrumChartSelection, selectedSongPackCharts);
    renderPluginsWithAvailability(details);

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
    setSelectedSongSetupLabel(details, containerPath);
    toggleFocusBtn.disabled = false;
    resetPlayersForSongSetup();
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
    detailsEl.innerHTML = `<pre class="error">${escapeHtml(String(e))}</pre>`;
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
      startMidiOut: midiOutStartOrContinue,
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
  const idx = pluginSelect.selectedIndex;
  if (idx < 0 || idx >= availablePlugins.length) return availablePlugins[0];
  return availablePlugins[idx];
}

async function startVisualizer(opts?: { preserveTransport?: boolean }) {
  stopVisualizer({ preserveTransport: opts?.preserveTransport });
  syncPreferredPluginSelection();

  const plugin = currentSelectedPlugin();
  setVizStatus(`Loading pluginâ€¦ (${plugin.id})`);

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

    // Render the melodic instrument tab/piano-roll below the main visualizer.
    if (tabRenderer && transport.t !== undefined) {
      tabRenderer.render(transport.t);
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

pluginSelect.addEventListener("change", () => {
  pluginSelectionMode = "user";
  restartVisualizerForPluginSelection();
});

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

// GHWT importer progress events (from Rust)
void listen<GhwtImportProgressEvent>("ghwt_import_progress", (ev) => {
  const p = ev.payload;
  const pct = Math.round((p.progress ?? 0) * 100);
  const msg = p.message ? ` Â· ${p.message}` : "";
  setGhwtStatus(`${p.song}: ${pct}% Â· ${p.id}${msg}`);
});

void listen<IngestImportProgressEvent>("ingest_import_progress", (ev) => {
  if (!ingestInFlight) return;
  appendIngestStatusLine(formatIngestProgressEvent(ev.payload));
});

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
      return midiOutStartOrContinue();
    })
    .catch((e) => setAudioStatus(String(e)));
});

audioPauseBtn.addEventListener("click", () => {
  transportController.pause();
  transport = transportController.getState();
  void midiOutStop();
  setAudioStatus("paused");
});

audioStopBtn.addEventListener("click", () => {
  closePauseMenu({ restoreFocus: false });
  stopAudio();
  void midiOutStop();
  void midiOutSeek(0);
  setAudioStatus("stopped");
});

pauseMenuResumeBtn.addEventListener("click", () => {
  void resumeFromPauseMenu();
});

pauseMenuBackBtn.addEventListener("click", () => {
  backToSongSelectionFromPauseMenu();
});

window.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape" || ev.repeat) return;

  if (isPauseMenuVisible()) {
    ev.preventDefault();
    void resumeFromPauseMenu();
    return;
  }

  if (currentRoute !== "play") return;
  if (transportController.getState().isPlaying) {
    ev.preventDefault();
    pauseForPauseMenu();
    return;
  }
  if (!canOpenLoadedSongBackOutPrompt()) return;

  ev.preventDefault();
  showPauseMenu("loaded");
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
        .then(() => refreshModels())
        .catch((e) => {
          modelsStatusEl.textContent = String(e);
        });
    });
  }
}

async function refreshModels() {
  modelsStatusEl.textContent = "Loadingâ€¦";
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
  statusEl.textContent = "Loading...";
  listEl.innerHTML = "";
  detailsEl.innerHTML = "";

  try {
    const songsFolder = await invoke<string>("get_songs_folder");
    const entries = await invoke<SongPackScanEntry[]>("scan_songpacks");

    // Prefer the built-in demo song first, then alphabetical.
    entries.sort((a, b) => {
      const ad = isDemoSongPack(a);
      const bd = isDemoSongPack(b);
      if (ad !== bd) return ad ? -1 : 1;
      const at = (a.manifest?.title ?? "").toLowerCase();
      const bt = (b.manifest?.title ?? "").toLowerCase();
      return at.localeCompare(bt);
    });

    songsFolderInput.value = songsFolder;
    statusEl.textContent = `songsFolder: ${songsFolder}\ntracks: ${entries.length}`;

    listEl.innerHTML = `
      <ul class="songLibraryList">
        ${entries
          .map((e) => {
            const title = e.manifest?.title ?? "(missing title)";
            const artist = e.manifest?.artist ?? "";
            const ok = e.ok ? "OK" : "INVALID";
            const err = e.error ? `<pre class="error">${escapeHtml(e.error)}</pre>` : "";
            const disabled = e.ok ? "" : "disabled";
            const selected = selectedSongPackPath === e.container_path ? " isSelected" : "";
            const pressed = selected ? "true" : "false";
            const cta = e.ok ? "Choose" : "Invalid";
            return `
              <li>
                <button class="songSelectBtn${selected}" data-path="${escapeHtml(e.container_path)}" aria-pressed="${pressed}" ${disabled}>
                  <span class="songSelectCopy">
                    <span class="songSelectTitleRow">
                      <strong class="songSelectTitle">${escapeHtml(title)}</strong>
                      ${artist ? `<span class="songSelectArtist">${escapeHtml(artist)}</span>` : ""}
                    </span>
                    <span class="meta songSelectMeta">${escapeHtml(ok)} Â· ${escapeHtml(e.kind)} Â· ${escapeHtml(e.container_path)}</span>
                  </span>
                  <span class="songSelectCta" aria-hidden="true">${escapeHtml(cta)}</span>
                </button>
                ${err}
              </li>
            `;
          })
          .join("\n")}
      </ul>
    `;

    for (const btn of Array.from(listEl.querySelectorAll("button.songSelectBtn"))) {
      btn.addEventListener("click", async (ev) => {
        const el = ev.currentTarget as HTMLButtonElement;
        const containerPath = el.getAttribute("data-path");
        if (!containerPath) return;

        await selectSongPack(containerPath);
      });
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

ingestModeSelect.addEventListener("change", () => {
  const mode = ingestModeSelect.value as IngestSubcommand;
  setIngestSourcePlaceholder(mode);
});
ingestSourcePathInput.addEventListener("change", () => {
  inferIngestMetadataFromSelectedSource();
});

ingestBrowseSourceBtn.addEventListener("click", () => {
  void ingestBrowseSource().catch((e) => setIngestStatus(String(e)));
});

ingestRunBtn.addEventListener("click", () => {
  void runIngestImport();
});

stemMidiPickStemsBtn.addEventListener("click", () => {
  void (async () => {
    const folder = await pickFolder();
    if (!folder) return;
    stemMidiFolderPath = folder;
    stemMidiInspection = null;
    renderStemMidiSelection();
    await inspectStemMidiFolder(folder);
  })().catch((e) => setStemMidiStatus(String(e)));
});

stemMidiPickMidiBtn.addEventListener("click", () => {
  void (async () => {
    if (!stemMidiFolderPath) {
      setStemMidiStatus("pick a Suno folder first");
      return;
    }
    await inspectStemMidiFolder(stemMidiFolderPath);
  })().catch((e) => setStemMidiStatus(String(e)));
});

stemMidiCreateBtn.addEventListener("click", () => {
  void stemMidiCreateSongPack().catch((e) => setStemMidiStatus(String(e)));
});

// Populate plugin list on startup.
void refreshPlugins();

// Load GHWT settings.
void ghwtLoadSettings();

setIngestSourcePlaceholder(ingestModeSelect.value as IngestSubcommand);

renderStemMidiSelection();
setStemMidiStatus("(not imported)");

// Populate MIDI ports.
void refreshMidiInputPorts();
void refreshMidiOutputPorts();
void refreshAudioOutputHosts();
void refreshAudioOutputDevices();

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

