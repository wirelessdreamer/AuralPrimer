/**
 * App shell HTML — extracted from main.ts as Phase 2.R so the bootstrap module
 * is no longer dominated by a 350-line template literal. Pure markup; no
 * interpolation. main.ts assigns the return value into the #app container at boot.
 */

export function appShellHtml(): string {
  return `
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
}
