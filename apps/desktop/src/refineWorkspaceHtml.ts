/**
 * Refine workspace HTML template — the static markup for Studio's
 * Cleanup & Edit route. Ports the layout from
 * docs/refine-prototype/screen-mockup.html into the Studio shell so the
 * existing `setRoute("refine")` machinery can render it.
 *
 * Lives in its own file because it's a 350+ line template literal; the
 * accompanying `refineWorkspace.ts` controller owns the interactive bits.
 *
 * Scoped under `.refineRoute` so the styling doesn't bleed into the
 * other routes (home / play / make / config). v1 inlines styles to keep
 * the workspace self-contained; if it grows we can move them into
 * style.css.
 */

export function refineWorkspaceHtml(): string {
  return `
  <section class="route" data-route="refine">
    <style>
      .refineRoute { display: flex; flex-direction: column; height: 100%; min-height: 0; }
      .refineRoute .rfTopbar {
        display: flex; align-items: center; gap: 14px;
        padding: 10px 18px;
        background: #0a0e16; border-bottom: 1px solid #1f2a44;
        flex-shrink: 0;
      }
      .refineRoute .rfCrumbs { color: #94a3b8; font-size: 13px; flex: 1; }
      .refineRoute .rfCrumbs .rfTitle { color: #e2e8f0; font-weight: 600; margin-left: 6px; }
      .refineRoute .rfCrumbs .rfInst { color: #3EE6A8; margin-left: 6px; }
      .refineRoute .rfCrumbs .rfBack { color: #64748b; cursor: pointer; margin-right: 12px; }
      .refineRoute .rfPill {
        display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
        background: #0f1620; border: 1px solid #1f2a44; font-size: 12px; color: #e2e8f0;
      }
      .refineRoute .rfInstSelect select {
        background: #0f1620; border: 1px solid #1f2a44; color: #e2e8f0; padding: 6px 10px;
        border-radius: 6px; font-size: 12px; cursor: pointer;
      }

      .refineRoute .rfGrid {
        flex: 1; min-height: 0; display: grid;
        grid-template-columns: 280px 1fr 320px;
        grid-template-rows: 1fr;
        gap: 0;
      }

      /* LEFT: worklist ----------------------------------------------------- */
      .refineRoute .rfLeft {
        background: #0f1620; border-right: 1px solid #1f2a44;
        display: flex; flex-direction: column; overflow: hidden;
      }
      .refineRoute .rfLeftHead {
        padding: 12px 14px; border-bottom: 1px solid #1f2a44;
        font-size: 12px; color: #94a3b8;
      }
      .refineRoute .rfWorkSummary { font-size: 12px; color: #e2e8f0; }
      .refineRoute .rfWorkSummary strong { color: #3EE6A8; }
      .refineRoute .rfWorklist { flex: 1; overflow-y: auto; padding: 6px 0; }
      .refineRoute .rfWorkSection { padding: 6px 14px 4px; }
      .refineRoute .rfWorkSection .rfHead {
        font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
        color: #94a3b8; padding: 4px 0;
      }
      .refineRoute .rfWorkSection .rfHead .rfBadge {
        float: right; background: #1f2a44; color: #e2e8f0;
        padding: 1px 6px; border-radius: 10px; font-size: 10px;
      }
      .refineRoute .rfRegionRow {
        display: flex; align-items: center; gap: 8px;
        padding: 7px 10px; border-radius: 6px; margin: 2px 0;
        font-size: 12px; color: #cbd5e1; cursor: pointer;
        border: 1px solid transparent;
      }
      .refineRoute .rfRegionRow:hover { background: #131b29; }
      .refineRoute .rfRegionRow.isFocused {
        background: #131b29; border-color: #3EE6A8;
      }
      .refineRoute .rfRegionRow.isAccepted { color: #64748b; }
      .refineRoute .rfRegionRow.isAccepted .rfRegionTime { color: #64748b; }
      .refineRoute .rfRegionTime {
        font-variant-numeric: tabular-nums; color: #94a3b8; min-width: 64px;
      }
      .refineRoute .rfRegionLabel { flex: 1; }
      .refineRoute .rfRegionConf {
        font-size: 10px; color: #94a3b8;
        background: #1f2a44; padding: 1px 6px; border-radius: 8px;
      }

      /* CENTER: timeline (notes-over-keys) ------------------------------- */
      .refineRoute .rfCenter {
        position: relative; background: #07090f; min-width: 0; overflow: hidden;
        display: flex; flex-direction: column;
      }
      .refineRoute .rfCenterHead {
        padding: 10px 14px; border-bottom: 1px solid #1f2a44; flex-shrink: 0;
        display: flex; align-items: center; gap: 12px;
        font-size: 12px; color: #94a3b8;
      }
      .refineRoute .rfFocusedRegion { color: #e2e8f0; font-weight: 600; }
      .refineRoute .rfFocusedChord { color: #3EE6A8; }
      .refineRoute .rfTimelineWrap {
        flex: 1; min-height: 0; overflow: hidden;
        display: grid;
        grid-template-columns: 60px 1fr;
        grid-template-rows: 1fr;
      }
      .refineRoute .rfPitchRuler {
        background: #0a0e16; border-right: 1px solid #1f2a44;
        font-size: 10px; color: #64748b;
        font-variant-numeric: tabular-nums;
        overflow: hidden; padding-top: 6px;
        text-align: right; padding-right: 6px;
      }
      .refineRoute .rfPitchRow {
        height: var(--rf-row-height, 12px);
        line-height: var(--rf-row-height, 12px);
        white-space: nowrap;
      }
      .refineRoute .rfPitchRow.isC { color: #94a3b8; font-weight: 600; }
      .refineRoute .rfTimelineSurface {
        position: relative; overflow-y: auto;
        background:
          linear-gradient(
            to bottom,
            rgba(255,255,255,0.02) 0,
            rgba(255,255,255,0.02) 11px,
            transparent 11px,
            transparent 12px
          );
        background-size: 100% var(--rf-row-height, 12px);
      }
      .refineRoute .rfTimelineCanvas {
        position: absolute; inset: 0;
        width: 100%; height: 100%;
        display: block;
      }
      .refineRoute .rfTimelineEmpty {
        padding: 60px 30px; text-align: center; color: #64748b; font-size: 13px;
      }

      /* RIGHT: candidate palette + region info -------------------------- */
      .refineRoute .rfRight {
        background: #0f1620; border-left: 1px solid #1f2a44;
        display: flex; flex-direction: column;
        overflow-y: auto;
      }
      .refineRoute .rfCard {
        padding: 14px; border-bottom: 1px solid #1f2a44;
      }
      .refineRoute .rfCard h4 {
        margin: 0 0 10px; font-size: 11px; text-transform: uppercase;
        letter-spacing: 0.8px; color: #94a3b8; font-weight: 600;
      }
      .refineRoute .rfCard h4 .rfHint {
        float: right; color: #64748b; text-transform: none; letter-spacing: 0;
        font-weight: 400;
      }
      .refineRoute .rfCard h4 .rfHint kbd {
        background: #1f2a44; color: #e2e8f0; border-radius: 3px;
        padding: 1px 4px; font-size: 10px; font-family: inherit;
      }
      .refineRoute .rfPalette { display: flex; flex-direction: column; gap: 4px; }
      .refineRoute .rfSwatch {
        display: flex; align-items: center; gap: 10px; padding: 8px 10px;
        background: #131b29; border: 1px solid #1f2a44; border-radius: 6px;
        cursor: pointer; font-size: 12px; color: #e2e8f0;
      }
      .refineRoute .rfSwatch:hover { background: #19223a; }
      .refineRoute .rfSwatch.isActive {
        border-color: #3EE6A8; background: rgba(62,230,168,0.08);
      }
      .refineRoute .rfSwatchKey {
        background: #1f2a44; color: #e2e8f0; padding: 1px 6px; border-radius: 4px;
        font-size: 10px; font-family: monospace; min-width: 18px; text-align: center;
      }
      .refineRoute .rfSwatchDot {
        width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
      }
      .refineRoute .rfSwatchLabel { flex: 1; }
      .refineRoute .rfSwatchScore { color: #94a3b8; font-variant-numeric: tabular-nums; }
      .refineRoute .rfSwatchAuto {
        color: #3EE6A8; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
      }

      .refineRoute .rfMetaRow {
        display: flex; justify-content: space-between; align-items: baseline;
        padding: 4px 0; font-size: 12px; color: #cbd5e1;
      }
      .refineRoute .rfMetaRow strong { color: #e2e8f0; }

      .refineRoute .rfActions {
        padding: 14px; border-top: 1px solid #1f2a44;
        display: flex; gap: 8px; margin-top: auto;
      }
      .refineRoute .rfBtn {
        flex: 1; padding: 10px 12px; border-radius: 6px;
        font-size: 13px; font-weight: 600; cursor: pointer;
        border: 1px solid #1f2a44; background: #131b29; color: #e2e8f0;
      }
      .refineRoute .rfBtn:hover { background: #19223a; }
      .refineRoute .rfBtn.isPrimary {
        border-color: #3EE6A8; color: #07090f; background: #3EE6A8;
      }
      .refineRoute .rfBtn.isPrimary:hover { background: #58f0b6; }

      .refineRoute .rfEmpty {
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        height: 100%; gap: 18px; color: #94a3b8; padding: 40px;
        text-align: center;
      }
      .refineRoute .rfEmpty h3 {
        margin: 0; font-size: 18px; color: #e2e8f0; font-weight: 600;
      }
      .refineRoute .rfEmpty code {
        background: #131b29; padding: 2px 6px; border-radius: 4px; color: #cbd5e1;
      }
    </style>

    <div class="refineRoute" id="refineRoute">
      <header class="rfTopbar">
        <div class="rfCrumbs">
          <span class="rfBack" id="refineBack">‹ Make</span>
          <span class="rfTitle" id="refineSongTitle">(no song)</span>
          <span class="rfInst">— <span id="refineInstLabel">Keys</span></span>
        </div>
        <div class="rfInstSelect">
          <label class="rfPill">
            <span style="color:#94a3b8;">Instrument</span>
            <select id="refineInstrumentSelect">
              <option value="keys">Keys</option>
              <option value="bass">Bass</option>
              <option value="lead_guitar">Lead Guitar</option>
              <option value="rhythm_guitar">Rhythm Guitar</option>
              <option value="drums">Drums</option>
              <option value="melodic">Melodic</option>
            </select>
          </label>
        </div>
        <button class="rfBtn" id="refineReloadBtn" style="flex:0 0 auto;">Reload</button>
        <button class="rfBtn isPrimary" id="refineSaveBtn" style="flex:0 0 auto;">Save</button>
      </header>

      <div class="rfGrid" id="refineGrid">
        <!-- LEFT: WORKLIST -->
        <aside class="rfLeft">
          <div class="rfLeftHead">
            <div class="rfWorkSummary" id="refineWorkSummary">
              <strong>0</strong> / 0 regions reviewed
            </div>
          </div>
          <div class="rfWorklist" id="refineWorklist"></div>
        </aside>

        <!-- CENTER: TIMELINE -->
        <section class="rfCenter">
          <header class="rfCenterHead">
            <span id="refineFocusedRegion" class="rfFocusedRegion">(pick a region)</span>
            <span id="refineFocusedTime" style="font-variant-numeric: tabular-nums;"></span>
            <span id="refineFocusedChord" class="rfFocusedChord"></span>
          </header>
          <div class="rfTimelineWrap">
            <div class="rfPitchRuler" id="refinePitchRuler"></div>
            <div class="rfTimelineSurface" id="refineTimelineSurface">
              <canvas class="rfTimelineCanvas" id="refineTimelineCanvas"></canvas>
            </div>
          </div>
        </section>

        <!-- RIGHT: CANDIDATES + ACCEPT -->
        <aside class="rfRight">
          <div class="rfCard">
            <h4>Candidates <span class="rfHint">press <kbd>1</kbd>–<kbd>4</kbd></span></h4>
            <div class="rfPalette" id="refinePalette">
              <div style="color:#64748b; font-size:12px;">(no region selected)</div>
            </div>
          </div>

          <div class="rfCard">
            <h4>Region info</h4>
            <div id="refineRegionInfo">
              <div class="rfMetaRow"><span>Type</span><strong>—</strong></div>
              <div class="rfMetaRow"><span>Confidence</span><strong>—</strong></div>
              <div class="rfMetaRow"><span>Chord</span><strong>—</strong></div>
              <div class="rfMetaRow"><span>Auto-pick</span><strong>—</strong></div>
            </div>
          </div>

          <div class="rfActions">
            <button class="rfBtn isPrimary" id="refineAcceptBtn" title="Enter">Accept ↵</button>
            <button class="rfBtn" id="refineSkipBtn" title="→">Skip →</button>
          </div>
        </aside>
      </div>

      <div class="rfEmpty" id="refineEmpty" style="display:none;">
        <h3>No candidates yet</h3>
        <div>Run the precompute stage to generate per-region transcription candidates for this SongPack:</div>
        <div>
          <code id="refineEmptyCmd">aural_ingest refine-candidates &lt;songpack&gt; --instrument keys</code>
        </div>
        <div style="font-size:12px; color:#64748b;">
          Then come back here and hit Reload.
        </div>
      </div>
    </div>
  </section>
  `;
}
