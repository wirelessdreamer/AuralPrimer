/**
 * Lyric-timing workspace controller — a DAW-style editor for feedpak lyrics,
 * ported from AuraWave's "Waveform timing editor" UX into AuralStudio.
 *
 * Data model (feedpak `lyrics.json`, schema packages/feedpak/schemas):
 *   a FLAT array of syllable clips `{ t, d, w }` — onset (sec), duration (sec),
 *   and the syllable/word text. Read via `read_auralsong_json` from
 *   `<aural|features>/lyrics.json`; written back with
 *   `write_auralsong_features_json` as a schema-valid `[{t,d,w}]` array.
 *
 * Interaction model (mirrors AuraWave + Studio's refine workspace):
 *   - The SpectrogramEditor renders the vocal/melodic spectrogram as the time
 *     background and owns the time-axis zoom/pan + playhead. A transparent
 *     overlay canvas on top draws each syllable as a clip at `t` with width `d`.
 *   - Drag a clip body to retime (move `t`); drag its left/right edge to resize
 *     (`d`), AuraWave-style, with edge-grip hit-testing. Click empty timeline to
 *     seek the playhead.
 *   - Wheel zooms around the cursor; Alt/middle-drag pans; an overview minimap
 *     below the stage shows the viewport + clips and click/drag scrubs+navigates.
 *   - Split-at-playhead, Add, Delete actions; a line/clip list + a selected-clip
 *     inspector with editable numeric t/d + text.
 *   - Transport (play/pause/stop/scrub) runs off a wall-clock playhead, the same
 *     pattern as refineWorkspace. (Stem audio under the timeline is a follow-up;
 *     streaming multi-MB WAVs over IPC is out of scope here.)
 */

import { invoke } from "@tauri-apps/api/core";
import {
  SpectrogramEditor,
  type SpectrogramGeometry,
} from "./spectrogramEditor";

/** A feedpak lyric syllable clip. */
export type LyricSyllable = { t: number; d: number; w: string };

export type LyricTimingDeps = {
  setStatus: (msg: string) => void;
  onBack: () => void;
};

export type LyricTimingHandle = {
  openForAuralSong: (containerPath: string) => Promise<void>;
  getCurrentContainerPath: () => string | null;
};

// ---- AuraWave-style timeline tunables ----
const EDGE_GRIP_PX = 8; // px from a clip edge that grabs the resize handle
const MIN_SYLLABLE_SEC = 0.03; // floor on a clip's duration
const ROW_TOP_FRAC = 0.62; // clip lane sits in the lower part of the stage
const ROW_HEIGHT_FRAC = 0.3;

type DragState = {
  mode: "move" | "start" | "end";
  index: number;
  pointerX: number; // canvas-space x at pointerdown
  origT: number;
  origD: number;
};

/** feedpak relocates feature artifacts under aural/ (legacy: features/). */
function featureDir(containerPath: string): "aural" | "features" {
  return containerPath.endsWith(".feedpak") ? "aural" : "features";
}

function roundTime(v: number): number {
  return Math.round(v * 100) / 100;
}

function formatTime(t: number): string {
  if (!Number.isFinite(t)) return "—";
  const mins = Math.floor(t / 60);
  const secs = (t - mins * 60).toFixed(2);
  return `${mins}:${secs.padStart(5, "0")}`;
}

/** Normalise an arbitrary parsed JSON value into a clean syllable array. */
export function normalizeLyrics(raw: unknown): LyricSyllable[] {
  if (!Array.isArray(raw)) return [];
  const out: LyricSyllable[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    const t = Number(rec.t);
    const d = Number(rec.d);
    const w = typeof rec.w === "string" ? rec.w : String(rec.w ?? "");
    if (!Number.isFinite(t) || !Number.isFinite(d)) continue;
    out.push({ t: Math.max(0, t), d: Math.max(MIN_SYLLABLE_SEC, d), w });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

/** Validate + serialise to a schema-valid `[{t,d,w}]` array for writing. */
export function toLyricsJson(clips: LyricSyllable[]): LyricSyllable[] {
  return clips
    .slice()
    .sort((a, b) => a.t - b.t)
    .map((c) => ({
      t: roundTime(Math.max(0, c.t)),
      d: roundTime(Math.max(MIN_SYLLABLE_SEC, c.d)),
      w: c.w,
    }));
}

export function initLyricTimingWorkspace(deps: LyricTimingDeps): LyricTimingHandle {
  const $ = <T extends HTMLElement>(id: string): T | null =>
    document.getElementById(id) as T | null;

  const root = $<HTMLElement>("lyricRoute");
  const songTitleEl = $<HTMLElement>("lyricSongTitle");
  const reloadBtn = $<HTMLButtonElement>("lyricReloadBtn");
  const saveBtn = $<HTMLButtonElement>("lyricSaveBtn");
  const backBtn = $<HTMLElement>("lyricBack");
  const transportEl = $<HTMLElement>("lyricTransport");
  const playBtn = $<HTMLButtonElement>("lyricPlayBtn");
  const stopBtn = $<HTMLButtonElement>("lyricStopBtn");
  const timeReadoutEl = $<HTMLElement>("lyricTimeReadout");
  const scrubEl = $<HTMLInputElement>("lyricScrub");
  const splitBtn = $<HTMLButtonElement>("lyricSplitBtn");
  const addBtn = $<HTMLButtonElement>("lyricAddBtn");
  const deleteBtn = $<HTMLButtonElement>("lyricDeleteBtn");
  const jumpSelectEl = $<HTMLSelectElement>("lyricJumpSelect");
  const speedSelectEl = $<HTMLSelectElement>("lyricSpeedSelect");
  const stageEl = $<HTMLElement>("lyricStage");
  const overlayEl = $<HTMLCanvasElement>("lyricOverlay");
  const overviewEl = $<HTMLCanvasElement>("lyricOverview");
  const bodyEl = $<HTMLElement>("lyricBody");
  const listEl = $<HTMLElement>("lyricList");
  const inspectorEl = $<HTMLElement>("lyricInspector");
  const emptyEl = $<HTMLElement>("lyricEmpty");

  if (
    !root || !songTitleEl || !reloadBtn || !saveBtn || !backBtn || !transportEl
    || !playBtn || !stopBtn || !timeReadoutEl || !scrubEl || !splitBtn || !addBtn
    || !deleteBtn || !jumpSelectEl || !speedSelectEl || !stageEl || !overlayEl
    || !overviewEl || !bodyEl || !listEl || !inspectorEl || !emptyEl
  ) {
    throw new Error("initLyricTimingWorkspace: required DOM missing -- lyric timing template not in place");
  }

  // ---- state ----
  let containerPath: string | null = null;
  let clips: LyricSyllable[] = [];
  let durationSec = 0;
  let selectedIndex: number | null = null;
  let isDirty = false;

  // transport (wall-clock playhead)
  let isPlaying = false;
  let playStartOffset = 0;
  let playStartWall = 0;
  let playheadSec = 0;
  let playbackRate = 1;
  let rafId: number | null = null;

  // overlay drawing/interaction
  let drag: DragState | null = null;

  const spectroTileUrls: string[] = [];
  function revokeSpectroUrls(): void {
    for (const u of spectroTileUrls) {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
    spectroTileUrls.length = 0;
  }

  // The spectrogram editor: time background + view transform + zoom/pan.
  // We hide its own note overlay (no notes loaded) and forward wheel/pan from
  // our overlay canvas so the lyric clips line up with the time axis.
  const editor = new SpectrogramEditor(stageEl, { showControls: false });

  function setDirty(flag: boolean): void {
    isDirty = flag;
    saveBtn!.textContent = flag ? "Save *" : "Save";
  }
  function setStatus(msg: string): void {
    deps.setStatus(msg);
  }

  // -------------------------------------------------------------------------
  // Time <-> overlay-x mapping (driven by the editor's visible time window)
  // -------------------------------------------------------------------------

  function viewWindow(): { start: number; end: number } {
    const w = editor.getViewTimeWindow();
    const start = Number.isFinite(w.start) ? w.start : 0;
    const end = Number.isFinite(w.end) && w.end > start ? w.end : start + Math.max(1, durationSec);
    return { start, end };
  }

  function overlayWidth(): number {
    return overlayEl!.width || overlayEl!.clientWidth || 1;
  }
  function overlayHeight(): number {
    return overlayEl!.height || overlayEl!.clientHeight || 1;
  }

  function timeToX(time: number): number {
    const { start, end } = viewWindow();
    const span = Math.max(1e-6, end - start);
    return ((time - start) / span) * overlayWidth();
  }
  function xToTime(x: number): number {
    const { start, end } = viewWindow();
    const span = Math.max(1e-6, end - start);
    return start + (x / Math.max(1, overlayWidth())) * span;
  }

  /** Pointer event -> canvas-space x (accounts for CSS-vs-canvas scaling). */
  function pointerX(ev: PointerEvent | MouseEvent): number {
    const rect = overlayEl!.getBoundingClientRect();
    return ((ev.clientX - rect.left) / Math.max(1, rect.width)) * overlayWidth();
  }

  // -------------------------------------------------------------------------
  // Overlay sizing + render loop (kept synced to the editor's view each frame)
  // -------------------------------------------------------------------------

  function syncOverlaySize(): void {
    const rect = stageEl!.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (overlayEl!.width !== w) overlayEl!.width = w;
    if (overlayEl!.height !== h) overlayEl!.height = h;
  }

  function laneRect(): { top: number; height: number } {
    const h = overlayHeight();
    return { top: h * ROW_TOP_FRAC, height: h * ROW_HEIGHT_FRAC };
  }

  function drawOverlay(): void {
    syncOverlaySize();
    const ctx = overlayEl!.getContext("2d");
    if (!ctx) return;
    const W = overlayWidth();
    const H = overlayHeight();
    ctx.clearRect(0, 0, W, H);

    const { top, height } = laneRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    // lane backdrop
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(0, top, W, height);

    for (let i = 0; i < clips.length; i++) {
      const c = clips[i]!;
      const x0 = timeToX(c.t);
      const x1 = timeToX(c.t + c.d);
      if (x1 < -40 || x0 > W + 40) continue; // cull offscreen
      const w = Math.max(4 * dpr, x1 - x0);
      const sel = i === selectedIndex;

      ctx.fillStyle = sel ? "rgba(56,213,255,0.30)" : "rgba(99,102,241,0.22)";
      ctx.fillRect(x0, top, w, height);
      ctx.lineWidth = (sel ? 2 : 1) * dpr;
      ctx.strokeStyle = sel ? "rgba(56,213,255,0.95)" : "rgba(165,180,252,0.55)";
      ctx.strokeRect(x0 + ctx.lineWidth / 2, top + ctx.lineWidth / 2, w - ctx.lineWidth, height - ctx.lineWidth);

      // edge handle bars
      ctx.fillStyle = sel ? "rgba(56,213,255,0.95)" : "rgba(226,232,240,0.78)";
      const barW = Math.min(4 * dpr, w);
      ctx.fillRect(x0, top, barW, height);
      ctx.fillRect(x1 - barW, top, barW, height);

      // label, clipped to the clip rect
      ctx.save();
      ctx.beginPath();
      ctx.rect(x0 + 2, top, Math.max(0, w - 4), height);
      ctx.clip();
      ctx.fillStyle = sel ? "rgba(248,250,252,0.96)" : "rgba(226,232,240,0.86)";
      ctx.font = `${700} ${12 * dpr}px Inter, system-ui, sans-serif`;
      ctx.textBaseline = "middle";
      ctx.fillText(c.w || "·", x0 + 6 * dpr, top + height / 2);
      ctx.restore();
    }

    // playhead
    const px = timeToX(playheadSec);
    if (px >= -2 && px <= W + 2) {
      ctx.fillStyle = "rgba(255,238,120,0.92)";
      ctx.fillRect(px - dpr, 0, 2 * dpr, H);
    }

    drawOverview();
  }

  let renderQueued = false;
  function requestRender(): void {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      drawOverlay();
    });
  }

  // -------------------------------------------------------------------------
  // Overview minimap (whole-duration scale; viewport rect; click/drag scrub)
  // -------------------------------------------------------------------------

  function drawOverview(): void {
    const ctx = overviewEl!.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const rect = overviewEl!.getBoundingClientRect();
    const W = Math.max(1, Math.round(rect.width * dpr));
    const H = Math.max(1, Math.round(rect.height * dpr));
    if (overviewEl!.width !== W) overviewEl!.width = W;
    if (overviewEl!.height !== H) overviewEl!.height = H;
    ctx.clearRect(0, 0, W, H);

    const dur = Math.max(1, durationSec);
    const tToX = (t: number): number => Math.max(0, Math.min(W, (t / dur) * W));

    // clip lane
    const laneTop = H * 0.55;
    const laneH = Math.max(6 * dpr, H * 0.3);
    for (let i = 0; i < clips.length; i++) {
      const c = clips[i]!;
      const x = tToX(c.t);
      const w = Math.max(2 * dpr, tToX(c.t + c.d) - x);
      ctx.fillStyle = i === selectedIndex ? "rgba(56,213,255,0.6)" : "rgba(165,180,252,0.4)";
      ctx.fillRect(x, laneTop, w, laneH);
    }

    // viewport rectangle
    const { start, end } = viewWindow();
    const vx = tToX(start);
    const vw = Math.max(3 * dpr, tToX(end) - vx);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fillRect(vx, 0, vw, H);
    ctx.lineWidth = 1.5 * dpr;
    ctx.strokeStyle = "rgba(56,213,255,0.8)";
    ctx.strokeRect(vx + ctx.lineWidth / 2, ctx.lineWidth / 2, vw - ctx.lineWidth, H - ctx.lineWidth);

    // playhead
    const px = tToX(playheadSec);
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.fillRect(px - dpr, 0, 2 * dpr, H);
  }

  function overviewTime(ev: PointerEvent | MouseEvent): number {
    const rect = overviewEl!.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
    return frac * Math.max(1, durationSec);
  }

  function centerViewOnTime(t: number): void {
    const { start, end } = viewWindow();
    const span = Math.max(0.001, end - start);
    let s = t - span / 2;
    s = Math.max(0, Math.min(Math.max(0, durationSec - span), s));
    editor.setViewTimeWindow(s, s + span);
    requestRender();
  }

  // -------------------------------------------------------------------------
  // Hit-testing (AuraWave-style: edges win over body)
  // -------------------------------------------------------------------------

  function hitTest(x: number, y: number): { index: number; mode: "move" | "start" | "end" } | null {
    const { top, height } = laneRect();
    if (y < top || y > top + height) return null;
    // iterate from topmost (last drawn) so overlapping clips resolve sanely
    for (let i = clips.length - 1; i >= 0; i--) {
      const c = clips[i]!;
      const x0 = timeToX(c.t);
      const x1 = timeToX(c.t + c.d);
      const grip = EDGE_GRIP_PX * Math.min(2, window.devicePixelRatio || 1);
      if (x >= x0 - grip && x <= x0 + grip) return { index: i, mode: "start" };
      if (x >= x1 - grip && x <= x1 + grip) return { index: i, mode: "end" };
      if (x >= x0 && x <= x1) return { index: i, mode: "move" };
    }
    return null;
  }

  // -------------------------------------------------------------------------
  // Selection + list + inspector
  // -------------------------------------------------------------------------

  function selectClip(index: number | null, scroll = false): void {
    selectedIndex = index;
    renderList(scroll);
    renderInspector();
    updateActionButtons();
    requestRender();
  }

  function renderList(scroll = false): void {
    listEl!.innerHTML = "";
    if (clips.length === 0) {
      const muted = document.createElement("div");
      muted.className = "ltListMuted";
      muted.textContent = "No syllables.";
      listEl!.appendChild(muted);
      return;
    }
    for (let i = 0; i < clips.length; i++) {
      const c = clips[i]!;
      const item = document.createElement("div");
      item.className = "ltListItem" + (i === selectedIndex ? " isActive" : "");
      item.dataset.index = String(i);
      const head = document.createElement("div");
      head.className = "ltItemHead";
      head.textContent = `${i + 1}.  ${c.t.toFixed(2)}s – ${(c.t + c.d).toFixed(2)}s`;
      const text = document.createElement("div");
      text.className = "ltItemText";
      text.textContent = c.w || "·";
      item.appendChild(head);
      item.appendChild(text);
      item.addEventListener("click", () => selectClip(i, false));
      listEl!.appendChild(item);
      if (scroll && i === selectedIndex) {
        item.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function renderInspector(): void {
    inspectorEl!.innerHTML = "";
    if (selectedIndex == null || !clips[selectedIndex]) {
      const none = document.createElement("div");
      none.className = "ltInsNone";
      none.textContent = clips.length
        ? "Select a syllable (in the list or on the timeline) to edit its timing."
        : "No lyrics loaded.";
      inspectorEl!.appendChild(none);
      return;
    }
    const c = clips[selectedIndex]!;

    const row = document.createElement("div");
    row.className = "ltInsRow";

    const textField = buildField("Text", "text", c.w);
    const startField = buildField("Start (s)", "number", c.t.toFixed(2));
    const durField = buildField("Duration (s)", "number", c.d.toFixed(2));
    const endField = buildField("End (s)", "number", (c.t + c.d).toFixed(2));

    textField.input.classList.add("ltText");
    startField.input.classList.add("ltNum");
    durField.input.classList.add("ltNum");
    endField.input.classList.add("ltNum");
    startField.input.step = "0.01";
    durField.input.step = "0.01";
    endField.input.step = "0.01";
    startField.input.min = "0";
    durField.input.min = String(MIN_SYLLABLE_SEC);
    endField.input.min = "0";

    textField.input.addEventListener("input", () => {
      const cur = clips[selectedIndex!];
      if (!cur) return;
      cur.w = textField.input.value;
      setDirty(true);
      renderList(false);
      requestRender();
    });
    startField.input.addEventListener("change", () => {
      const cur = clips[selectedIndex!];
      if (!cur) return;
      const v = Number(startField.input.value);
      if (Number.isFinite(v)) { cur.t = Math.max(0, v); commitEditedClip(); }
    });
    durField.input.addEventListener("change", () => {
      const cur = clips[selectedIndex!];
      if (!cur) return;
      const v = Number(durField.input.value);
      if (Number.isFinite(v)) { cur.d = Math.max(MIN_SYLLABLE_SEC, v); commitEditedClip(); }
    });
    endField.input.addEventListener("change", () => {
      const cur = clips[selectedIndex!];
      if (!cur) return;
      const v = Number(endField.input.value);
      if (Number.isFinite(v)) { cur.d = Math.max(MIN_SYLLABLE_SEC, v - cur.t); commitEditedClip(); }
    });

    row.appendChild(textField.wrap);
    row.appendChild(startField.wrap);
    row.appendChild(durField.wrap);
    row.appendChild(endField.wrap);
    inspectorEl!.appendChild(row);

    const hint = document.createElement("div");
    hint.className = "ltInsHint";
    hint.textContent = "Timeline: drag clip = move · drag edges = resize · click empty = seek · scroll = zoom · Alt-drag = pan";
    inspectorEl!.appendChild(hint);
  }

  function buildField(
    label: string,
    type: string,
    value: string,
  ): { wrap: HTMLElement; input: HTMLInputElement } {
    const wrap = document.createElement("div");
    wrap.className = "ltInsField";
    const lab = document.createElement("label");
    lab.textContent = label;
    const input = document.createElement("input");
    input.type = type;
    input.value = value;
    wrap.appendChild(lab);
    wrap.appendChild(input);
    return { wrap, input };
  }

  /** Re-sort after a numeric edit that may reorder clips, keep selection. */
  function commitEditedClip(): void {
    const cur = selectedIndex != null ? clips[selectedIndex] : null;
    clips.sort((a, b) => a.t - b.t);
    selectedIndex = cur ? clips.indexOf(cur) : null;
    setDirty(true);
    renderList(false);
    renderInspector();
    requestRender();
  }

  function updateActionButtons(): void {
    const has = selectedIndex != null && !!clips[selectedIndex];
    splitBtn!.disabled = !has;
    deleteBtn!.disabled = !has;
  }

  // -------------------------------------------------------------------------
  // Clip actions: split / add / delete
  // -------------------------------------------------------------------------

  function splitAtPlayhead(): void {
    if (selectedIndex == null) return;
    const c = clips[selectedIndex];
    if (!c) return;
    const t = playheadSec;
    if (t <= c.t + MIN_SYLLABLE_SEC || t >= c.t + c.d - MIN_SYLLABLE_SEC) {
      setStatus("Move the playhead inside the selected clip to split.");
      return;
    }
    const leftD = roundTime(t - c.t);
    const rightT = roundTime(t);
    const rightD = roundTime(c.t + c.d - t);
    const left: LyricSyllable = { t: roundTime(c.t), d: leftD, w: c.w };
    const right: LyricSyllable = { t: rightT, d: rightD, w: "" };
    clips.splice(selectedIndex, 1, left, right);
    setDirty(true);
    selectClip(selectedIndex + 1, true);
    setStatus(`Split into 2 syllables at ${formatTime(t)}`);
  }

  function addAtPlayhead(): void {
    const t = roundTime(playheadSec);
    const next = clips.find((c) => c.t > t);
    const maxD = next ? Math.max(MIN_SYLLABLE_SEC, next.t - t) : 0.4;
    const clip: LyricSyllable = { t, d: roundTime(Math.min(0.4, maxD)), w: "la" };
    clips.push(clip);
    clips.sort((a, b) => a.t - b.t);
    setDirty(true);
    selectClip(clips.indexOf(clip), true);
    setStatus(`Added a syllable at ${formatTime(t)}`);
  }

  function deleteSelected(): void {
    if (selectedIndex == null) return;
    if (!clips[selectedIndex]) return;
    clips.splice(selectedIndex, 1);
    setDirty(true);
    const next = clips.length ? Math.min(selectedIndex, clips.length - 1) : null;
    selectClip(next, true);
    setStatus("Deleted syllable");
  }

  // -------------------------------------------------------------------------
  // Overlay pointer interactions (drag move/resize, click-to-seek, wheel zoom)
  // -------------------------------------------------------------------------

  function onOverlayPointerDown(ev: PointerEvent): void {
    if (ev.button === 1 || ev.altKey) return; // pan handled via wheel/editor; let through
    const x = pointerX(ev);
    const rect = overlayEl!.getBoundingClientRect();
    const y = ((ev.clientY - rect.top) / Math.max(1, rect.height)) * overlayHeight();
    const hit = hitTest(x, y);
    if (!hit) {
      // click empty timeline -> seek playhead
      seekTo(xToTime(x));
      selectClip(null);
      return;
    }
    selectClip(hit.index);
    const c = clips[hit.index]!;
    drag = { mode: hit.mode, index: hit.index, pointerX: x, origT: c.t, origD: c.d };
    overlayEl!.setPointerCapture(ev.pointerId);
    ev.preventDefault();
  }

  function onOverlayPointerMove(ev: PointerEvent): void {
    if (!drag) return;
    const x = pointerX(ev);
    const { start, end } = viewWindow();
    const span = Math.max(1e-6, end - start);
    const deltaSec = ((x - drag.pointerX) / Math.max(1, overlayWidth())) * span;
    const c = clips[drag.index];
    if (!c) return;
    if (drag.mode === "move") {
      c.t = Math.max(0, drag.origT + deltaSec);
    } else if (drag.mode === "start") {
      const origEnd = drag.origT + drag.origD;
      const newT = Math.max(0, Math.min(origEnd - MIN_SYLLABLE_SEC, drag.origT + deltaSec));
      c.t = newT;
      c.d = origEnd - newT;
    } else {
      c.d = Math.max(MIN_SYLLABLE_SEC, drag.origD + deltaSec);
    }
    setDirty(true);
    renderInspector();
    renderList(false);
    requestRender();
  }

  function onOverlayPointerUp(ev: PointerEvent): void {
    if (!drag) return;
    const c = clips[drag.index];
    if (c) { c.t = roundTime(c.t); c.d = roundTime(c.d); }
    drag = null;
    try { overlayEl!.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
    commitEditedClip();
  }

  /** Wheel = zoom around cursor (AuraWave), via the editor's view window. */
  function onOverlayWheel(ev: WheelEvent): void {
    if (durationSec <= 0) return;
    ev.preventDefault();
    const x = pointerX(ev);
    const cursorTime = xToTime(x);
    const { start, end } = viewWindow();
    const span = Math.max(0.05, end - start);
    const factor = ev.deltaY > 0 ? 1.15 : 1 / 1.15; // down = zoom out
    let newSpan = Math.max(0.25, Math.min(durationSec, span * factor));
    const frac = (cursorTime - start) / span; // keep cursorTime under the cursor
    let newStart = cursorTime - frac * newSpan;
    newStart = Math.max(0, Math.min(Math.max(0, durationSec - newSpan), newStart));
    editor.setViewTimeWindow(newStart, newStart + newSpan);
    requestRender();
  }

  // -------------------------------------------------------------------------
  // Transport (wall-clock playhead)
  // -------------------------------------------------------------------------

  function setPlayhead(t: number, follow: boolean): void {
    playheadSec = Math.max(0, Math.min(durationSec, t));
    editor.setTime(playheadSec);
    if (follow) editor.scrollTimeIntoView(playheadSec);
    scrubEl!.value = String(durationSec > 0 ? Math.round((playheadSec / durationSec) * 1000) : 0);
    timeReadoutEl!.textContent = `${formatTime(playheadSec)} / ${formatTime(durationSec)}`;
    requestRender();
  }

  function tick(): void {
    if (!isPlaying) return;
    const t = playStartOffset + ((performance.now() - playStartWall) / 1000) * playbackRate;
    if (t >= durationSec) {
      setPlayhead(durationSec, false);
      pauseTransport();
      return;
    }
    setPlayhead(t, true);
    rafId = requestAnimationFrame(tick);
  }

  function playTransport(): void {
    if (isPlaying || durationSec <= 0) return;
    if (playheadSec >= durationSec) setPlayhead(0, true);
    playStartOffset = playheadSec;
    playStartWall = performance.now();
    isPlaying = true;
    playBtn!.textContent = "⏸";
    rafId = requestAnimationFrame(tick);
  }

  function pauseTransport(): void {
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    isPlaying = false;
    playBtn!.textContent = "▶";
  }

  function stopTransport(): void {
    pauseTransport();
    setPlayhead(0, false);
  }

  function toggleTransport(): void {
    if (isPlaying) pauseTransport();
    else playTransport();
  }

  function seekTo(t: number): void {
    const wasPlaying = isPlaying;
    if (wasPlaying) pauseTransport();
    setPlayhead(t, true);
    if (wasPlaying) playTransport();
  }

  function setTransportEnabled(enabled: boolean): void {
    playBtn!.disabled = !enabled;
    stopBtn!.disabled = !enabled;
    scrubEl!.disabled = !enabled;
    addBtn!.disabled = !enabled;
  }

  // -------------------------------------------------------------------------
  // Empty / loaded state
  // -------------------------------------------------------------------------

  function showEmpty(empty: boolean): void {
    emptyEl!.style.display = empty ? "flex" : "none";
    stageEl!.style.display = empty ? "none" : "block";
    transportEl!.style.display = empty ? "none" : "flex";
    overviewEl!.style.display = empty ? "none" : "block";
    bodyEl!.style.display = empty ? "none" : "flex";
  }

  // -------------------------------------------------------------------------
  // Jump-to-line select (navigation, AuraWave overview transport flavour)
  // -------------------------------------------------------------------------

  function buildJumpOptions(): void {
    jumpSelectEl!.innerHTML = "";
    const dash = document.createElement("option");
    dash.value = "";
    dash.textContent = "—";
    jumpSelectEl!.appendChild(dash);
    // sample up to ~24 anchor syllables so the dropdown stays usable
    const stride = Math.max(1, Math.ceil(clips.length / 24));
    for (let i = 0; i < clips.length; i += stride) {
      const c = clips[i]!;
      const opt = document.createElement("option");
      opt.value = String(c.t);
      opt.textContent = `${formatTime(c.t)}  ${c.w || "·"}`;
      jumpSelectEl!.appendChild(opt);
    }
  }

  // -------------------------------------------------------------------------
  // Spectrogram background load (prefer a vocal role; fall back to melodic)
  // -------------------------------------------------------------------------

  async function loadSpectrogram(): Promise<boolean> {
    if (!containerPath) return false;
    const fd = featureDir(containerPath);
    const roleCandidates = ["vocals", "vocal", "melodic", "keys"];
    for (const role of roleCandidates) {
      try {
        const geom = (await invoke("read_auralsong_json", {
          containerPath,
          relPath: `${fd}/spectrogram/${role}/spectrogram.json`,
        })) as SpectrogramGeometry;
        if (!geom || !Array.isArray(geom.tiles) || geom.tiles.length === 0) continue;
        revokeSpectroUrls();
        const urls: string[] = [];
        for (const tile of geom.tiles) {
          const bytes = (await invoke("read_auralsong_bytes", {
            containerPath,
            relPath: `${fd}/spectrogram/${role}/${tile.file}`,
          })) as number[];
          const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "image/png" }));
          urls.push(url);
          spectroTileUrls.push(url);
        }
        await editor.load(geom, urls, []);
        durationSec = editor.getDurationSec();
        return true;
      } catch {
        // try the next role
      }
    }
    return false;
  }

  // -------------------------------------------------------------------------
  // Lyrics read / write
  // -------------------------------------------------------------------------

  async function loadLyrics(): Promise<boolean> {
    if (!containerPath) return false;
    const fd = featureDir(containerPath);
    try {
      const raw = await invoke("read_auralsong_json", {
        containerPath,
        relPath: `${fd}/lyrics.json`,
      });
      clips = normalizeLyrics(raw);
      return clips.length > 0;
    } catch {
      clips = [];
      return false;
    }
  }

  async function save(): Promise<void> {
    if (!containerPath) return;
    const fd = featureDir(containerPath);
    const payload = toLyricsJson(clips);
    try {
      await invoke("write_auralsong_features_json", {
        containerPath,
        relPath: `${fd}/lyrics.json`,
        value: payload,
      });
      setDirty(false);
      setStatus(`Saved ${payload.length} syllables`);
    } catch (e) {
      setStatus(`Save failed: ${String(e)}`);
    }
  }

  // -------------------------------------------------------------------------
  // Public entry
  // -------------------------------------------------------------------------

  async function openForAuralSong(path: string): Promise<void> {
    containerPath = path;
    songTitleEl!.textContent = path;
    setDirty(false);
    stopTransport();
    setTransportEnabled(false);
    selectedIndex = null;
    setStatus(`Loading lyrics for ${path}…`);
    try {
      await loadSpectrogram(); // best-effort: background only
      if (!durationSec) durationSec = 0;
      const hasLyrics = await loadLyrics();
      if (!durationSec && clips.length) {
        // no spectrogram: derive a duration from the lyrics themselves
        durationSec = clips.reduce((m, c) => Math.max(m, c.t + c.d), 0) + 2;
      }
      if (!hasLyrics) {
        showEmpty(true);
        setStatus("No timed lyrics yet — generate them from Cleanup & Edit, then Reload.");
        return;
      }
      showEmpty(false);
      buildJumpOptions();
      renderList(false);
      renderInspector();
      updateActionButtons();
      setTransportEnabled(durationSec > 0);
      setPlayhead(0, false);
      requestRender();
      setStatus(`Loaded ${clips.length} syllables for editing`);
    } catch (e) {
      showEmpty(true);
      setStatus(`Failed to load lyrics: ${String(e)}`);
    }
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------

  reloadBtn.addEventListener("click", () => { if (containerPath) void openForAuralSong(containerPath); });
  saveBtn.addEventListener("click", () => void save());
  backBtn.addEventListener("click", () => { stopTransport(); deps.onBack(); });

  playBtn.addEventListener("click", () => toggleTransport());
  stopBtn.addEventListener("click", () => stopTransport());
  splitBtn.addEventListener("click", () => splitAtPlayhead());
  addBtn.addEventListener("click", () => addAtPlayhead());
  deleteBtn.addEventListener("click", () => deleteSelected());

  scrubEl.addEventListener("input", () => {
    const t = (Number(scrubEl.value) / 1000) * durationSec;
    if (isPlaying) pauseTransport();
    setPlayhead(t, true);
  });
  scrubEl.addEventListener("change", () => {
    seekTo((Number(scrubEl.value) / 1000) * durationSec);
  });

  jumpSelectEl.addEventListener("change", () => {
    const v = jumpSelectEl.value;
    if (v === "") { editor.resetView(); requestRender(); return; }
    const t = Number(v);
    if (Number.isFinite(t)) centerViewOnTime(t);
  });
  speedSelectEl.addEventListener("change", () => {
    const r = Number(speedSelectEl.value);
    if (!Number.isFinite(r) || r <= 0) return;
    // re-anchor the wall clock so the playhead doesn't jump on rate change
    if (isPlaying) { playStartOffset = playheadSec; playStartWall = performance.now(); }
    playbackRate = r;
  });

  overlayEl.addEventListener("pointerdown", onOverlayPointerDown);
  overlayEl.addEventListener("pointermove", onOverlayPointerMove);
  overlayEl.addEventListener("pointerup", onOverlayPointerUp);
  overlayEl.addEventListener("pointercancel", onOverlayPointerUp);
  overlayEl.addEventListener("wheel", onOverlayWheel, { passive: false });

  // Overview minimap: click/drag scrubs the playhead + recenters the view.
  let overviewDragging = false;
  overviewEl.addEventListener("pointerdown", (ev) => {
    overviewDragging = true;
    overviewEl.setPointerCapture(ev.pointerId);
    const t = overviewTime(ev);
    seekTo(t);
    centerViewOnTime(t);
  });
  overviewEl.addEventListener("pointermove", (ev) => {
    if (!overviewDragging) return;
    const t = overviewTime(ev);
    setPlayhead(t, false);
    centerViewOnTime(t);
  });
  overviewEl.addEventListener("pointerup", (ev) => {
    overviewDragging = false;
    try { overviewEl.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
  });

  // Space toggles transport when this route is active and not typing in a field.
  window.addEventListener("keydown", (ev) => {
    if (!root.closest(".isActive")) return;
    if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) return;
    if (ev.key === " " || ev.code === "Space") {
      ev.preventDefault();
      toggleTransport();
    } else if (ev.key === "Delete" || ev.key === "Backspace") {
      if (selectedIndex != null) { ev.preventDefault(); deleteSelected(); }
    }
  });

  window.addEventListener("resize", () => requestRender());

  return {
    openForAuralSong,
    getCurrentContainerPath: () => containerPath,
  };
}
