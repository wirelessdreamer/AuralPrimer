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
      .refineRoute { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #07090f; }

      /* TOP BAR ----------------------------------------------------------- */
      .refineRoute .rfTopbar {
        display: flex; align-items: center; gap: 12px;
        padding: 9px 16px; background: #0a0e16; border-bottom: 1px solid #1f2a44;
        flex-shrink: 0;
      }
      .refineRoute .rfBack {
        color: #64748b; cursor: pointer; flex-shrink: 0; white-space: nowrap;
        padding: 5px 10px; border-radius: 6px; border: 1px solid #1f2a44; background: #131b29;
      }
      .refineRoute .rfBack:hover { color: #cbd5e1; background: #19223a; }
      /* Title must be able to shrink + ellipsis so a long song name never pushes
         the right-hand controls (Reload / Save) off the edge of the bar. */
      .refineRoute .rfTitle {
        color: #e2e8f0; font-weight: 600;
        flex: 0 1 auto; min-width: 40px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .refineRoute .rfInst { color: #3EE6A8; flex-shrink: 0; white-space: nowrap; }
      .refineRoute .rfSpacer { flex: 1 1 0; min-width: 8px; }
      .refineRoute .rfPill {
        display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 8px;
        background: #0f1620; border: 1px solid #1f2a44; font-size: 12px; color: #e2e8f0; flex-shrink: 0;
      }
      .refineRoute .rfPill > span { color: #94a3b8; }
      .refineRoute .rfPill select {
        background: #0f1620; border: 0; color: #e2e8f0; font-size: 12px; cursor: pointer; outline: none;
      }
      .refineRoute .rfBtn {
        padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer;
        border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0; flex-shrink: 0; white-space: nowrap;
      }
      .refineRoute .rfBtn:hover { background: #19223a; }
      .refineRoute .rfBtn:disabled { opacity: 0.4; cursor: default; }
      .refineRoute .rfBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .refineRoute .rfBtn.isPrimary:hover { background: #58f0b6; }

      /* TRANSPORT --------------------------------------------------------- */
      .refineRoute .rfTransport {
        display: flex; align-items: center; gap: 12px;
        padding: 7px 16px; background: #0a0e16; border-bottom: 1px solid #1f2a44;
        flex-shrink: 0;
      }
      .refineRoute .rfTransBtn {
        width: 34px; height: 30px; display: inline-flex; align-items: center; justify-content: center;
        border-radius: 6px; border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0;
        cursor: pointer; font-size: 13px;
      }
      .refineRoute .rfTransBtn:hover { background: #19223a; }
      .refineRoute .rfTransBtn:disabled { opacity: 0.4; cursor: default; }
      .refineRoute .rfTransBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .refineRoute .rfTime {
        font-variant-numeric: tabular-nums; font-size: 12px; color: #94a3b8;
        min-width: 132px; text-align: center;
      }
      .refineRoute .rfScrub { flex: 1; min-width: 120px; cursor: pointer; }
      .refineRoute .rfAuditionToggle {
        display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #94a3b8; cursor: pointer;
      }

      /* MAIN STAGE (full-width editor) ------------------------------------ */
      .refineRoute .rfStage { flex: 1 1 auto; min-height: 0; position: relative; }
      .refineRoute .rfStage .spectro-editor { height: 100%; }

      /* BOTTOM INSPECTOR -------------------------------------------------- */
      .refineRoute .rfInspector {
        display: flex; align-items: center; gap: 16px;
        padding: 8px 16px; background: #0a0e16; border-top: 1px solid #1f2a44;
        flex-shrink: 0; font-size: 12px; color: #94a3b8; min-height: 44px;
      }
      .refineRoute .rfSelInfo { color: #e2e8f0; min-width: 180px; }
      .refineRoute .rfSelInfo .rfSelPitch { color: #3EE6A8; font-weight: 600; }
      .refineRoute .rfCandChips { display: flex; align-items: center; gap: 6px; flex: 1; flex-wrap: wrap; }
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
        <span class="rfBack" id="refineBack" title="Back to song selection">‹ Songs</span>
        <span class="rfTitle" id="refineSongTitle">(no song)</span>
        <span class="rfInst">— <span id="refineInstLabel">Keys</span></span>
        <div class="rfSpacer"></div>
        <label class="rfPill">
          <span>Instrument</span>
          <select id="refineInstrumentSelect">
            <option value="keys">Keys</option>
            <option value="bass">Bass</option>
            <option value="lead_guitar">Lead Guitar</option>
            <option value="rhythm_guitar">Rhythm Guitar</option>
            <option value="drums">Drums</option>
            <option value="melodic">Melodic</option>
          </select>
        </label>
        <button class="rfBtn" id="refineReloadBtn">Reload</button>
        <button class="rfBtn isPrimary" id="refineSaveBtn">Save</button>
      </header>

      <div class="rfTransport" id="refineTransport">
        <button class="rfTransBtn isPrimary" id="refinePlayBtn" title="Play / pause (Space)" disabled>▶</button>
        <button class="rfTransBtn" id="refineStopBtn" title="Stop" disabled>■</button>
        <span class="rfTime" id="refineTimeReadout">0:00.00 / 0:00.00</span>
        <input type="range" class="rfScrub" id="refineScrub" min="0" max="1000" value="0" step="1" disabled />
        <label class="rfPill">
          <span>Jump</span>
          <select id="refineSectionSelect"><option value="">—</option></select>
        </label>
        <label class="rfPill" title="Solo the current instrument's stem, or play the full mix">
          <span>Audio</span>
          <select id="refineSoloSelect">
            <option value="solo" selected>Solo instrument</option>
            <option value="all">All (mix)</option>
          </select>
        </label>
        <label class="rfPill" title="Playback speed">
          <span>Speed</span>
          <select id="refineSpeedSelect">
            <option value="0.5">0.5x</option>
            <option value="0.75">0.75x</option>
            <option value="1" selected>1x</option>
            <option value="1.5">1.5x</option>
            <option value="2">2x</option>
          </select>
        </label>
        <label class="rfAuditionToggle" title="Also synthesize the notes while playing">
          <input type="checkbox" id="refineAuditionToggle" checked /> Hear notes
        </label>
        <label class="rfAuditionToggle" title="Play a note when you place or edit it">
          <input type="checkbox" id="refineEditAuditionToggle" checked /> Audition edits
        </label>
        <button class="rfTransBtn" id="refineUndoBtn" title="Undo (Ctrl+Z)" disabled>↶</button>
        <button class="rfTransBtn" id="refineRedoBtn" title="Redo (Ctrl+Shift+Z)" disabled>↷</button>
      </div>

      <div class="rfStage" id="refineStage"></div>

      <div class="rfInspector" id="refineInspector">
        <div class="rfSelInfo" id="refineSelInfo">No note selected</div>
        <div class="rfCandChips" id="refineCandChips"></div>
        <div class="rfInsHint">drag = move · edges = resize · click empty = add · Del = remove · scroll = zoom · Alt-drag = pan</div>
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
