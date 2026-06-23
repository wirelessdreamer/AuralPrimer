/**
 * Lyric-timing editor HTML template — Studio's DAW-style lyric timing surface,
 * a port of AuraWave's "Waveform timing editor". Mirrors the refine workspace
 * layout (transport bar over a full-width timeline, bottom inspector, dark
 * theme) so the two editors feel native to each other.
 *
 * Layout, top → bottom:
 *   .ltTopbar    — back, title, save
 *   .ltTransport — play/stop, time readout, scrub, split/add/delete, jump,
 *                  playback speed
 *   .ltStage     — the SpectrogramEditor mounts here (#lyricStage) as the time
 *                  background; a transparent overlay canvas (#lyricOverlay)
 *                  draws the draggable syllable clips on top.
 *   .ltOverview  — overview minimap canvas (#lyricOverview) for navigation
 *   .ltBody      — left clip list (#lyricList) + right selected-clip inspector
 *                  (#lyricInspector)
 *   .ltEmpty     — empty state when the pack has no lyrics yet
 *
 * Behaviour lives in `lyricTimingWorkspace.ts`. Styles are inlined + scoped
 * under `.lyricRoute` so they don't bleed into the other routes.
 */

export function lyricTimingHtml(): string {
  return `
  <section class="route" data-route="lyrics">
    <style>
      .lyricRoute { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #07090f; }

      /* TOP BAR ----------------------------------------------------------- */
      .lyricRoute .ltTopbar {
        display: flex; align-items: center; gap: 12px;
        padding: 9px 16px; background: #0a0e16; border-bottom: 1px solid #1f2a44;
        flex-shrink: 0;
      }
      .lyricRoute .ltBack { color: #64748b; cursor: pointer; }
      .lyricRoute .ltBack:hover { color: #94a3b8; }
      .lyricRoute .ltTitle { color: #e2e8f0; font-weight: 600; }
      .lyricRoute .ltSub { color: #3EE6A8; }
      .lyricRoute .ltSpacer { flex: 1; }
      .lyricRoute .ltBtn {
        padding: 7px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer;
        border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0;
      }
      .lyricRoute .ltBtn:hover { background: #19223a; }
      .lyricRoute .ltBtn:disabled { opacity: 0.4; cursor: default; }
      .lyricRoute .ltBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .lyricRoute .ltBtn.isPrimary:hover { background: #58f0b6; }

      /* TRANSPORT --------------------------------------------------------- */
      .lyricRoute .ltTransport {
        display: flex; align-items: center; gap: 12px;
        padding: 7px 16px; background: #0a0e16; border-bottom: 1px solid #1f2a44;
        flex-shrink: 0; flex-wrap: wrap;
      }
      .lyricRoute .ltTransBtn {
        width: 34px; height: 30px; display: inline-flex; align-items: center; justify-content: center;
        border-radius: 6px; border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0;
        cursor: pointer; font-size: 13px;
      }
      .lyricRoute .ltTransBtn:hover { background: #19223a; }
      .lyricRoute .ltTransBtn:disabled { opacity: 0.4; cursor: default; }
      .lyricRoute .ltTransBtn.isPrimary { border-color: #3EE6A8; color: #07090f; background: #3EE6A8; }
      .lyricRoute .ltTime {
        font-variant-numeric: tabular-nums; font-size: 12px; color: #94a3b8;
        min-width: 132px; text-align: center;
      }
      .lyricRoute .ltScrub { flex: 1; min-width: 120px; cursor: pointer; }
      .lyricRoute .ltPill {
        display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 8px;
        background: #0f1620; border: 1px solid #1f2a44; font-size: 12px; color: #e2e8f0;
      }
      .lyricRoute .ltPill > span { color: #94a3b8; }
      .lyricRoute .ltPill select {
        background: #0f1620; border: 0; color: #e2e8f0; font-size: 12px; cursor: pointer; outline: none;
      }

      /* MAIN STAGE (spectrogram background + clip overlay) ----------------- */
      .lyricRoute .ltStage { flex: 1 1 auto; min-height: 0; position: relative; }
      .lyricRoute .ltStage .spectro-editor { height: 100%; }
      .lyricRoute .ltOverlay {
        position: absolute; inset: 0; width: 100%; height: 100%;
        cursor: crosshair; touch-action: none;
      }

      /* OVERVIEW MINIMAP -------------------------------------------------- */
      .lyricRoute .ltOverview {
        flex-shrink: 0; height: 52px; width: 100%; display: block;
        background: #050711; border-top: 1px solid #1f2a44; cursor: pointer; touch-action: none;
      }

      /* BOTTOM BODY: clip list + inspector -------------------------------- */
      .lyricRoute .ltBody {
        display: flex; gap: 0; flex-shrink: 0; height: 210px; min-height: 0;
        background: #0a0e16; border-top: 1px solid #1f2a44;
      }
      .lyricRoute .ltList {
        width: 300px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid #1f2a44; padding: 6px;
      }
      .lyricRoute .ltListItem {
        display: flex; flex-direction: column; gap: 2px; padding: 6px 9px; border-radius: 6px;
        cursor: pointer; border: 1px solid transparent;
      }
      .lyricRoute .ltListItem:hover { background: #131b29; }
      .lyricRoute .ltListItem.isActive { background: rgba(62,230,168,0.08); border-color: #3EE6A8; }
      .lyricRoute .ltListItem .ltItemHead {
        font-size: 11px; color: #94a3b8; font-variant-numeric: tabular-nums;
      }
      .lyricRoute .ltListItem .ltItemText { font-size: 13px; color: #e2e8f0; font-weight: 600; }
      .lyricRoute .ltList .ltListMuted { color: #64748b; font-size: 12px; padding: 10px; }

      .lyricRoute .ltInspector { flex: 1; overflow-y: auto; padding: 12px 16px; }
      .lyricRoute .ltInsRow { display: flex; align-items: flex-end; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
      .lyricRoute .ltInsField { display: flex; flex-direction: column; gap: 3px; }
      .lyricRoute .ltInsField label { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.04em; }
      .lyricRoute .ltInsField input {
        background: #0f1620; border: 1px solid #1f2a44; color: #e2e8f0; border-radius: 6px;
        padding: 5px 8px; font-size: 13px; outline: none;
      }
      .lyricRoute .ltInsField input:focus { border-color: #3EE6A8; }
      .lyricRoute .ltInsField input.ltText { min-width: 200px; }
      .lyricRoute .ltInsField input.ltNum { width: 92px; font-variant-numeric: tabular-nums; }
      .lyricRoute .ltInsActions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .lyricRoute .ltInsHint { color: #64748b; font-size: 11px; margin-top: 4px; }
      .lyricRoute .ltInsNone { color: #64748b; font-size: 13px; padding: 20px 0; }

      /* EMPTY STATE ------------------------------------------------------- */
      .lyricRoute .ltEmpty {
        flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
        gap: 16px; color: #94a3b8; padding: 40px; text-align: center;
      }
      .lyricRoute .ltEmpty h3 { margin: 0; font-size: 18px; color: #e2e8f0; font-weight: 600; }
      .lyricRoute .ltEmpty code { background: #131b29; padding: 2px 6px; border-radius: 4px; color: #cbd5e1; }
    </style>

    <div class="lyricRoute" id="lyricRoute">
      <header class="ltTopbar">
        <span class="ltBack" id="lyricBack">‹ Cleanup &amp; Edit</span>
        <span class="ltTitle" id="lyricSongTitle">(no song)</span>
        <span class="ltSub">— Lyric Timing</span>
        <div class="ltSpacer"></div>
        <button class="ltBtn" id="lyricReloadBtn">Reload</button>
        <button class="ltBtn isPrimary" id="lyricSaveBtn">Save</button>
      </header>

      <div class="ltTransport" id="lyricTransport">
        <button class="ltTransBtn isPrimary" id="lyricPlayBtn" title="Play / pause (Space)" disabled>▶</button>
        <button class="ltTransBtn" id="lyricStopBtn" title="Stop" disabled>■</button>
        <span class="ltTime" id="lyricTimeReadout">0:00.00 / 0:00.00</span>
        <input type="range" class="ltScrub" id="lyricScrub" min="0" max="1000" value="0" step="1" disabled />
        <button class="ltBtn" id="lyricSplitBtn" title="Split selected clip at playhead">Split</button>
        <button class="ltBtn" id="lyricAddBtn" title="Add a syllable at the playhead">Add</button>
        <button class="ltBtn" id="lyricDeleteBtn" title="Delete the selected clip">Delete</button>
        <label class="ltPill">
          <span>Jump</span>
          <select id="lyricJumpSelect"><option value="">—</option></select>
        </label>
        <label class="ltPill">
          <span>Speed</span>
          <select id="lyricSpeedSelect">
            <option value="0.5">0.5x</option>
            <option value="0.75">0.75x</option>
            <option value="1" selected>1x</option>
            <option value="1.5">1.5x</option>
            <option value="2">2x</option>
          </select>
        </label>
      </div>

      <div class="ltStage" id="lyricStage">
        <canvas class="ltOverlay" id="lyricOverlay"></canvas>
      </div>

      <canvas class="ltOverview" id="lyricOverview"></canvas>

      <div class="ltBody" id="lyricBody">
        <div class="ltList" id="lyricList"></div>
        <div class="ltInspector" id="lyricInspector"></div>
      </div>

      <div class="ltEmpty" id="lyricEmpty" style="display:none;">
        <h3>No lyrics yet</h3>
        <div>This song has no timed lyrics to edit. Generate them first from the Cleanup &amp; Edit screen:</div>
        <div><code>Generate lyrics</code> &nbsp;(paste / import a .txt of the lyrics)</div>
        <div style="font-size:12px; color:#64748b;">Then come back here and hit Reload.</div>
      </div>
    </div>
  </section>
  `;
}
