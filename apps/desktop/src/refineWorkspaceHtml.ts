/**
 * Refine workspace HTML template — the static markup for Studio's
 * Cleanup & Edit route, laid out as a DAW: a transport bar over a
 * full-width spectrogram piano-roll editor (SpectrogramEditor mounts into
 * `#refineStage`), with a thin bottom inspector for the selected note +
 * candidate "apply" suggestions.
 *
 * The interactive behaviour lives in `refineWorkspace.ts`. Styles are
 * inlined + scoped under `.refineRoute` so they don't bleed into the other
 * routes.
 */

export function refineWorkspaceHtml(): string {
  return `
  <section class="route" data-route="refine">
    <style>
      .refineRoute { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #07090f; color: #e3eaf5; font: 13px/1.5 "Segoe UI", system-ui, sans-serif; }
      .refineRoute * { box-sizing: border-box; }
      .refineRoute button, .refineRoute input, .refineRoute select { font: inherit; accent-color: #3ee6a8; }

      /* TOP BAR ----------------------------------------------------------- */
      .refineRoute .rfTopbar {
        display: flex; align-items: center; gap: 12px;
        padding: 12px 18px; background: #07090f;
        flex-shrink: 0;
      }
      .refineRoute .rfBack {
        color: #96a6be; cursor: pointer; flex-shrink: 0; white-space: nowrap;
        padding: 5px 9px; border-radius: 6px; border: 1px solid #26334a; background: #0d121d;
      }
      .refineRoute .rfBack:hover { color: #cbd5e1; background: #19223a; }
      /* Title must be able to shrink + ellipsis so a long song name never pushes
         the right-hand controls (Reload / Save) off the edge of the bar. */
      .refineRoute .rfSong { flex: 1 1 200px; min-width: 60px; }
      .refineRoute .rfTitle { color: #e3eaf5; font-size: 18px; font-weight: 500; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .refineRoute .rfSubtitle { color: #96a6be; font-size: 12px; white-space: nowrap; }
      .refineRoute .rfSpacer { flex: 1 1 0; min-width: 8px; }
      .refineRoute .rfPill {
        display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 8px;
        background: #0d121d; border: 1px solid #26334a; font-size: 12px; color: #e3eaf5; flex-shrink: 0;
      }
      .refineRoute .rfPill > span { color: #94a3b8; }
      .refineRoute .rfPill select {
        background: #0f1620; border: 0; color: #e2e8f0; font-size: 12px; cursor: pointer; outline: none;
      }
      .refineRoute .rfBtn {
        padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer;
        border: 1px solid #26334a; background: #161f30; color: #e3eaf5; flex-shrink: 0; white-space: nowrap;
      }
      .refineRoute .rfBtn:hover { background: #19223a; }
      .refineRoute .rfBtn[aria-pressed="true"] { border-color: #3ee6a8; color: #3ee6a8; background: #102d27; }
      .refineRoute .rfBtn:disabled { opacity: 0.4; cursor: default; }
      .refineRoute .rfBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .refineRoute .rfBtn.isPrimary:hover { background: #58f0b6; }

      /* TRANSPORT --------------------------------------------------------- */
      .refineRoute .rfTransport {
        position: relative; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
        padding: 10px 18px 20px; background: #0d121d; border-block: 1px solid #26334a;
        flex-shrink: 0;
      }
      .refineRoute .rfTransportMain, .refineRoute .rfSpeedGroup { display: inline-flex; align-items: center; gap: 8px; }
      .refineRoute .rfSpeedGroup { padding-left: 16px; border-left: 1px solid #26334a; }
      .refineRoute .rfTransBtn {
        width: 34px; height: 32px; display: inline-flex; align-items: center; justify-content: center;
        border-radius: 6px; border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0;
        cursor: pointer; font-size: 13px;
      }
      .refineRoute .rfTransBtn:hover { background: #19223a; }
      .refineRoute .rfTransBtn:disabled { opacity: 0.4; cursor: default; }
      .refineRoute .rfTransBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .refineRoute #refinePlayBtn { width: 80px; gap: 7px; font-weight: 500; }
      .refineRoute .rfTime {
        font: 12px/1.5 Consolas, ui-monospace, monospace; font-variant-numeric: tabular-nums; color: #e3eaf5;
        min-width: 132px; text-align: center;
      }
      .refineRoute .rfScrub { position: absolute; left: 18px; bottom: 0; width: calc(100% - 36px); height: 15px; margin: 0; cursor: pointer; }
      .refineRoute .rfAuditionToggle {
        display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #94a3b8; cursor: pointer;
      }
      .refineRoute [hidden] { display: none !important; }
      .refineRoute .rfSpeed { display: inline-flex; align-items: center; gap: 8px; color: #94a3b8; font-size: 12px; }
      .refineRoute .rfSpeed input { width: 100px; accent-color: #3ee6a8; }
      .refineRoute .rfSpeed output { min-width: 42px; color: #e3eaf5; font-size: 15px; font-weight: 500; font-variant-numeric: tabular-nums; }
      .refineRoute .rfSpeedPreset { border: 0; padding: 5px 7px; background: transparent; color: #94a3b8; border-radius: 5px; cursor: pointer; }
      .refineRoute .rfSpeedPreset[aria-pressed="true"] { background: #102d27; color: #3ee6a8; }
      .refineRoute .rfPitchLock { font-size: 11px; color: #94a3b8; white-space: nowrap; }
      .refineRoute .rfListen { display: flex; align-items: center; gap: 12px 24px; flex-wrap: wrap; padding: 12px 18px; background: #0d121d; border-bottom: 1px solid #26334a; flex-shrink: 0; }
      .refineRoute .rfMonitor { flex: 1 1 520px; min-width: 0; }
      .refineRoute .rfMonitorTop { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
      .refineRoute .rfSectionLabel { color: #96a6be; font-size: 11px; text-transform: uppercase; letter-spacing: .09em; }
      .refineRoute .rfSourceSelect { margin-left: auto; display: inline-flex; align-items: center; gap: 7px; color: #96a6be; font-size: 11px; }
      .refineRoute .rfSourceSelect select { padding: 4px 7px; background: #0d121d; color: #e3eaf5; border: 1px solid #26334a; border-radius: 6px; font-size: 12px; }
      .refineRoute .rfListenModes { display: inline-flex; padding: 3px; gap: 3px; border: 1px solid #26334a; border-radius: 7px; background: #07090f; }
      .refineRoute .rfListenModes button { padding: 6px 9px; border: 0; border-radius: 5px; color: #94a3b8; background: transparent; font-size: 12px; cursor: pointer; white-space: nowrap; }
      .refineRoute .rfListenModes button[aria-pressed="true"] { background: #161f30; color: #e2e8f0; }
      .refineRoute .rfListenModes button:disabled { opacity: 0.4; cursor: default; }
      .refineRoute .rfRoutebar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 9px; }
      .refineRoute .rfEar { display: inline-flex; align-items: center; gap: 7px; padding: 4px 8px; background: #161f30; border-radius: 5px; font-size: 12px; white-space: nowrap; }
      .refineRoute .rfEar b { display: inline-grid; place-content: center; min-width: 23px; height: 22px; padding: 0 3px; background: #0d121d; border-radius: 4px; color: #71d8ef; font-size: 11px; font-weight: 500; }
      .refineRoute .rfEar.isRendered b { color: #3ee6a8; }
      .refineRoute .rfEar.isMuted { opacity: .4; }
      .refineRoute .rfListenRoute { color: #96a6be; font-size: 11px; }
      .refineRoute #refineSwapBtn { border: 0; background: transparent; padding: 4px; color: #96a6be; font-size: 12px; }
      .refineRoute .rfListenLevels { display: flex; gap: 18px; flex: 0 0 300px; }
      .refineRoute .rfListenLevels .rfLevel { flex: 1; min-width: 0; display: grid; grid-template-columns: 1fr auto; gap: 5px; }
      .refineRoute .rfListenLevels .rfLevel input { grid-column: 1 / -1; width: 100%; margin: 0; }
      .refineRoute .rfLevel { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #94a3b8; }
      .refineRoute .rfLevel input { width: 65px; accent-color: #3ee6a8; }
      .refineRoute .rfLevel output { width: 32px; font-variant-numeric: tabular-nums; }
      .refineRoute .rfMidi { display: flex; align-items: center; flex-wrap: wrap; flex-shrink: 0; gap: 10px; padding: 6px 18px; font-size: 11px; color: #96a6be; background: #0d121d; border-top: 1px solid #26334a; }
      .refineRoute .rfMidi .rfBtn { padding: 4px 7px; font-size: 11px; }
      .refineRoute .rfMidiStatus { max-width: 180px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
      .refineRoute .rfMidiStatus[data-connected="true"] { color: #3ee6a8; }
      .refineRoute .rfMidiNotes { flex: 1; min-width: 80px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: #7dd3fc; }
      .refineRoute .rfEditbar { display: flex; gap: 8px 12px; align-items: center; flex-wrap: wrap; padding: 9px 18px; background: #07090f; flex-shrink: 0; }
      .refineRoute .rfAnalysisLabel { margin-left: auto; color: #96a6be; font-size: 11px; }
      .refineRoute .rfDownbeatCtl { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #94a3b8; }
      .refineRoute .rfSelectionActions { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }
      .refineRoute .rfSelectionActions button { padding: 5px 8px; font-size: 12px; }
      .refineRoute .rfSelInfo { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      @media (max-width: 1100px) {
        .refineRoute .rfTopbar { gap: 8px; }
        .refineRoute .rfTopbar .rfBtn { padding: 5px 7px; font-size: 11px; }
        .refineRoute .rfSpeedGroup { padding-left: 12px; gap: 6px; }
        .refineRoute .rfSpeed { gap: 6px; }
        .refineRoute .rfSpeed input { width: 80px; }
        .refineRoute .rfMonitor { flex-basis: 500px; }
        .refineRoute .rfListenLevels { flex-basis: 220px; gap: 12px; }
        .refineRoute .rfListenRoute { display: none; }
        .refineRoute .rfPitchLock { display: none; }
      }
      @media (max-width: 850px) {
        .refineRoute .rfTopbar { flex-wrap: wrap; }
        .refineRoute .rfListenLevels { flex: 1 1 100%; }
        .refineRoute .rfListen { gap: 6px 10px; }
        .refineRoute .rfSelInfo { min-width: 120px; max-width: 180px; }
      }
      @media (max-height: 820px) {
        .refineRoute .rfTopbar { padding-block: 8px; }
        .refineRoute .rfListen { padding-block: 8px; }
        .refineRoute .rfEditbar { padding-block: 6px; }
      }

      /* MAIN STAGE ------------------------------------------------------- */
      .refineRoute .rfMain { display: flex; flex: 1 1 auto; min-height: 0; min-width: 0; overflow: hidden; margin: 0 18px; border: 1px solid #26334a; border-radius: 7px; }
      .refineRoute .rfAnalysisPane { display: flex; flex-direction: column; flex: 1 1 0; min-height: 0; min-width: 0; }
      .refineRoute .rfPaneHeader { display: none; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; background: #0f1620; border-bottom: 1px solid #26334a; color: #e2e8f0; font-size: 12px; flex-shrink: 0; }
      .refineRoute .rfPaneHeader span { color: #94a3b8; font-size: 11px; }
      .refineRoute .rfMain.isSplit .rfPaneHeader { display: flex; }
      .refineRoute .rfMain.isSplit .rfAnalysisPane { flex: 0 0 calc(var(--rfAnalysisWidth, 50%) - 4px); }
      .refineRoute .rfGamePane { display: flex; flex-direction: column; flex: 1 1 0; min-height: 0; min-width: 0; overflow: hidden; }
      .refineRoute .rfGameSurface { position: relative; flex: 1 1 0; min-height: 0; min-width: 0; background: #0a1018; overflow: hidden; }
      .refineRoute .rfGameSurface canvas { display: block; position: absolute; inset: 0; width: 100%; height: 100%; }
      .refineRoute .rfSplitDivider { flex: 0 0 8px; position: relative; background: #151f30; cursor: col-resize; touch-action: none; }
      .refineRoute .rfSplitDivider::after { content: ''; position: absolute; width: 2px; height: 42px; left: 3px; top: calc(50% - 21px); background: #64748b; border-radius: 2px; }
      .refineRoute .rfSplitDivider:hover, .refineRoute .rfSplitDivider:focus-visible { background: #255b62; outline: none; }
      .refineRoute .rfMain.isResizing { cursor: col-resize; user-select: none; }
      .refineRoute .rfMain.isSplit .spectro-controls { gap: 6px; padding: 5px 7px; font-size: 11px; }
      .refineRoute .rfMain.isSplit .spectro-controls input[type="range"] { width: 70px !important; }
      .refineRoute .rfStage { flex: 1 1 auto; min-height: 0; position: relative; }
      .refineRoute .rfStage .spectro-editor { height: 100%; }
      .refineRoute .spectro-controls { background: #0d121d !important; border-color: #26334a !important; color: #96a6be; gap: 8px !important; font-size: 11px; }
      .refineRoute .spectro-controls input[type="range"] { width: 85px !important; }
      .refineRoute .spectro-controls select, .refineRoute .spectro-controls button { background: #161f30 !important; border-color: #26334a !important; color: #e3eaf5 !important; }

      /* BOTTOM INSPECTOR -------------------------------------------------- */
      /* Fixed height (not min-height) + non-wrapping chips so selecting a note
         never grows the bar and reflows/resizes the spectrogram stage above. */
      .refineRoute .rfInspector {
        display: flex; align-items: center; gap: 16px;
        padding: 8px 18px; background: #0d121d; border-block: 1px solid #26334a; margin-top: 10px;
        flex-shrink: 0; font-size: 12px; color: #94a3b8;
        height: 48px; box-sizing: border-box;
      }
      .refineRoute .rfSelInfo { color: #e2e8f0; min-width: 180px; flex-shrink: 0; }
      .refineRoute .rfSelInfo .rfSelPitch { color: #3EE6A8; font-size: 16px; font-weight: 500; }
      .refineRoute .rfCandChips {
        display: flex; align-items: center; gap: 6px; flex: 1 1 0; min-width: 0;
        flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden;
      }
      .refineRoute .rfCandChip { flex-shrink: 0; }
      .refineRoute .rfCandChip {
        display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: 6px;
        background: #131b29; border: 1px solid #1f2a44; color: #e2e8f0; cursor: pointer; font-size: 12px;
      }
      .refineRoute .rfCandChip:hover { background: #19223a; }
      .refineRoute .rfCandChip.isActive { border-color: #3EE6A8; background: rgba(62,230,168,0.08); }
      .refineRoute .rfCandChip .rfChipKey {
        background: #1f2a44; color: #e2e8f0; padding: 0 5px; border-radius: 4px;
        font-size: 10px; font-family: monospace;
      }
      .refineRoute .rfCandChip .rfChipDot { width: 10px; height: 10px; border-radius: 50%; }
      .refineRoute .rfInsHint { color: #64748b; font-size: 11px; text-align: right; min-width: 160px; }

      /* EMPTY STATE ------------------------------------------------------- */
      .refineRoute .rfEmpty {
        flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
        gap: 16px; color: #94a3b8; padding: 40px; text-align: center;
      }
      .refineRoute .rfEmpty h3 { margin: 0; font-size: 18px; color: #e2e8f0; font-weight: 600; }
      .refineRoute .rfEmpty code { background: #131b29; padding: 2px 6px; border-radius: 4px; color: #cbd5e1; }
    </style>

    <div class="refineRoute" id="refineRoute">
      <header class="rfTopbar">
        <button class="rfBack" id="refineBack" title="Back to song selection">‹ Songs</button>
        <div class="rfSong">
          <h1 class="rfTitle" id="refineSongTitle">(no song)</h1>
          <div class="rfSubtitle"><span id="refineInstLabel">Keys</span> · transcription cleanup</div>
        </div>
        <button class="rfBtn" id="refineSplitToggle" aria-pressed="false" aria-controls="refineGamePane" title="Transcription on the left, synchronized game preview on the right" disabled>Split + game</button>
        <label class="rfPill">
          <span>Instrument</span>
          <select id="refineInstrumentSelect">
            <option value="keys">Keys</option>
            <option value="bass">Bass</option>
            <option value="guitar">Guitar</option>
          </select>
        </label>
        <button class="rfBtn" id="refineReloadBtn">Reload</button>
        <button class="rfBtn" id="refineSnapBtn" title="Refine hit times onto the drums-stem audio transients (undoable)" style="display:none">Snap to onsets</button>
        <button class="rfBtn" id="refineRefreshMeterBtn" title="Re-track the beat grid + meter (Beat This!) and rewrite song_timeline.json in place (backs up to .bak)">Refresh meter</button>
        <button class="rfBtn isPrimary" id="refineSaveBtn">Save</button>
      </header>

      <div class="rfTransport" id="refineTransport">
        <div class="rfTransportMain">
        <button class="rfTransBtn isPrimary" id="refinePlayBtn" title="Play / pause (Space)" aria-label="Play / pause" disabled>▶ Play</button>
        <button class="rfTransBtn" id="refineStopBtn" title="Stop" aria-label="Stop" disabled>■</button>
        <span class="rfTime" id="refineTimeReadout">0:00.00 / 0:00.00</span>
        <button class="rfBtn" id="refineLoopBtn" aria-pressed="false" title="Loop the visible section">↻ Loop</button>
        </div>
        <div class="rfSpeedGroup">
        <label class="rfSpeed">
          Playback speed <output id="refineSpeedOutput" for="refineSpeedSelect">100%</output>
          <input type="range" id="refineSpeedSelect" min="0.25" max="2" value="1" step="0.05" aria-label="Playback speed" />
        </label>
        <span class="rfSpeedPresets" role="group" aria-label="Playback speed presets">
          <button class="rfSpeedPreset" data-refine-speed="0.5" aria-pressed="false">50%</button>
          <button class="rfSpeedPreset" data-refine-speed="0.65" aria-pressed="false">65%</button>
          <button class="rfSpeedPreset" data-refine-speed="0.75" aria-pressed="false">75%</button>
          <button class="rfSpeedPreset" data-refine-speed="1" aria-pressed="true">100%</button>
        </span>
        <span class="rfPitchLock">♮ Pitch preserved</span>
        </div>
        <input type="range" class="rfScrub" id="refineScrub" min="0" max="1000" value="0" step="1" title="Space: play/pause · ←/→: seek 5 seconds · Shift+←/→: seek 1 second · ↑/↓: forward/backward 30 seconds (any focus)" disabled />
      </div>

      <div class="rfListen" id="refineListen">
        <div class="rfMonitor">
        <div class="rfMonitorTop">
        <span class="rfSectionLabel">Listen</span>
        <div class="rfListenModes" role="group" aria-label="Listening mode">
          <button data-listen-mode="source" aria-pressed="false" title="Source only (A)">Source</button>
          <button data-listen-mode="rendered" aria-pressed="false" title="Rendered notes only (B)">Rendered</button>
          <button data-listen-mode="together" aria-pressed="false">Together</button>
          <button data-listen-mode="split" aria-pressed="true" title="Source in one ear, rendered notes in the other (X)">L / R split</button>
        </div>
        <label class="rfSourceSelect" title="Listen to the current instrument's isolated stem or the full mix">
          Source
          <select id="refineSoloSelect">
            <option value="solo" selected>Isolated stem</option>
            <option value="all">Full mix</option>
          </select>
        </label>
        </div>
        <div class="rfRoutebar" aria-label="Headphone routing">
        <span class="rfEar" id="refineSourceBadge"><b id="refineSourceEar">L</b> Source</span>
        <span class="rfEar isRendered" id="refineRenderedBadge"><b id="refineRenderedEar">R</b> Rendered notes</span>
        <button class="rfBtn" id="refineSwapBtn" title="Swap source and rendered ears">⇄ Swap</button>
        <span class="rfListenRoute" id="refineListenRoute" aria-live="polite">Both play in sync · one stream per ear</span>
        </div>
        </div>
        <div class="rfListenLevels" aria-label="Listening levels">
          <label class="rfLevel"><span>Source level</span><output for="refineSourceLevel">80%</output><input id="refineSourceLevel" aria-label="Source level" type="range" min="0" max="100" step="5" value="80" /></label>
          <label class="rfLevel"><span>Rendered notes level</span><output for="refineRenderedLevel">70%</output><input id="refineRenderedLevel" aria-label="Rendered notes level" type="range" min="0" max="100" step="5" value="70" /></label>
        </div>
        <input type="checkbox" id="refineAuditionToggle" checked hidden />
      </div>

      <div class="rfEditbar" id="refineEditbar">
        <label class="rfPill">
          <span>Jump</span>
          <select id="refineSectionSelect"><option value="">—</option></select>
        </label>
        <label class="rfPill" title="Snap placed/moved notes to the beat grid. Hold Ctrl while placing to bypass.">
          <span>Quantize</span>
          <select id="refineQuantSelect">
            <option value="off">Off</option>
            <option value="1/4">1/4</option>
            <option value="1/8">1/8</option>
            <option value="1/8t">1/8T</option>
            <option value="1/16" selected>1/16</option>
            <option value="1/16t">1/16T</option>
            <option value="1/32">1/32</option>
          </select>
        </label>
        <label class="rfAuditionToggle" title="Click a note or empty pitch row to hear it. Hold Shift to place, move, or resize notes.">
          <input type="checkbox" id="refineEditAuditionToggle" checked /> Audition clicks
        </label>
        <label class="rfAuditionToggle" title="Click each beat (accent on the one) to hear whether the grid aligns to the audio">
          <input type="checkbox" id="refineMetronomeToggle" /> Metronome
        </label>
        <span class="rfDownbeatCtl" title="Move which beat is 'the one' (accent + bar line) when the detected downbeat is off — flip on the Metronome and nudge until the accent lands on beat 1">
          Downbeat
          <button class="rfTransBtn" id="refineDownbeatMinus" title="Shift the one back one beat">◀</button>
          <button class="rfTransBtn" id="refineDownbeatPlus" title="Shift the one forward one beat">▶</button>
        </span>
        <button class="rfTransBtn" id="refineUndoBtn" title="Undo (Ctrl+Z)" disabled>↶</button>
        <button class="rfTransBtn" id="refineRedoBtn" title="Redo (Ctrl+Shift+Z)" disabled>↷</button>
        <span class="rfAnalysisLabel">Spectrogram + notes</span>
      </div>

      <div class="rfMain" id="refineMain">
        <section class="rfAnalysisPane" aria-label="Transcription and analysis">
          <div class="rfPaneHeader"><strong>Transcription &amp; analysis</strong><span>Shift to edit</span></div>
          <div class="rfStage" id="refineStage"></div>
        </section>
        <div class="rfSplitDivider" id="refineSplitDivider" role="separator" tabindex="0" aria-label="Resize split; drag, plus/minus to adjust, Home to center" aria-orientation="vertical" aria-valuemin="30" aria-valuemax="70" aria-valuenow="50" hidden></div>
        <section class="rfGamePane" id="refineGamePane" aria-label="Game preview" hidden>
          <div class="rfPaneHeader" title="Uses your current edits, including unsaved changes"><strong>Game preview</strong><span id="refineGameInfo">Live preview</span></div>
          <div class="rfGameSurface" id="refineGameSurface" tabindex="0" role="img" aria-label="Synchronized game visualization"></div>
        </section>
      </div>

      <div class="rfInspector" id="refineInspector">
        <div class="rfSelInfo" id="refineSelInfo">No note selected</div>
        <div class="rfSelectionActions">
          <button class="rfBtn" id="refineHearSelection" title="Hear the selected note (Enter)" disabled>Hear selection</button>
          <button class="rfBtn" id="refineLoopSelection" title="Loop the selected note with surrounding context" disabled>Loop selection</button>
        </div>
        <div class="rfCandChips" id="refineCandChips"></div>
        <span class="rfInsHint" title="Click = hear · Shift-click empty = add · Shift-drag = move · Shift-drag edges = resize · Ctrl = bypass snap · Del = remove · scroll = zoom · Alt-drag = pan">Click = hear · Shift = edit</span>
      </div>

      <div class="rfMidi" role="group" aria-label="Live MIDI keyboard">
        <span class="rfMidiStatus" id="refineMidiStatus" role="status">MIDI · checking input…</span>
        <label class="rfAuditionToggle" title="Hear your keyboard through Studio. Uses the shared input selected in Configure → MIDI."><input type="checkbox" id="refineMidiSound" /> Hear keyboard</label>
        <label class="rfLevel" title="Live keyboard volume, independent of source/rendered levels">Live <input type="range" id="refineMidiGain" aria-label="Live keyboard volume" min="0" max="100" value="70" step="5" /></label>
        <span class="rfMidiNotes" id="refineMidiNotes">Play your keyboard</span>
        <button class="rfBtn" id="refineMidiConnect" title="Reconnect the saved shared MIDI input">Reconnect</button>
        <button class="rfBtn" id="refineMidiPanic" title="Release all live keyboard notes and sustain">All notes off</button>
      </div>

      <div class="rfEmpty" id="refineEmpty" style="display:none;">
        <h3>No candidates yet</h3>
        <div>Run the precompute stage to generate transcription candidates for this song:</div>
        <div><code id="refineEmptyCmd">aural_ingest refine-candidates &lt;song&gt; --instrument keys</code></div>
        <div style="font-size:12px; color:#64748b;">Then come back here and hit Reload.</div>
      </div>
    </div>
  </section>
  `;
}
