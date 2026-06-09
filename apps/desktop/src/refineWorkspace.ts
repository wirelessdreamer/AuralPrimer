/**
 * Refine workspace controller — owns the Cleanup & Edit route.
 *
 * Responsibilities:
 *  - load + persist refine_candidates / refinement JSON via refineCandidatesIo
 *  - render the worklist (left), timeline (center), candidate palette (right)
 *  - wire keyboard shortcuts (1–4 = pick candidate, Enter = accept, → = skip)
 *  - track focused region, current instrument, and a dirty flag for unsaved
 *    decisions
 *
 * Built so the host (Studio's main.ts) does `initRefineWorkspace({...})` and
 * gets a handle with `openForSongPack(containerPath)`. Internal panels are
 * functions in this module rather than separate files; the workspace is
 * ~600 lines which is fine for v1. If it grows we can split worklist /
 * timeline / palette into siblings, mirroring the Phase 2 Game pattern.
 */

import {
  loadSession,
  saveDecisions,
  pickCandidateForRegion,
  activeNotesForRegion,
  type RefineSession,
  type RefineCandidatesRegion,
  type RefinementInstrument,
  type RefinementNote,
} from "./refineCandidatesIo";
import { initRefineAudition, type RefineAuditionHandle } from "./refineAudition";

const PITCH_LOW = 21; // A0
const PITCH_HIGH = 108; // C8
const PITCH_COUNT = PITCH_HIGH - PITCH_LOW + 1; // 88
const ROW_HEIGHT_PX = 12;

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function pitchLabel(midi: number): string {
  const name = NOTE_NAMES[midi % 12]!;
  const octave = Math.floor(midi / 12) - 1;
  return `${name}${octave}`;
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function formatTime(t: number): string {
  if (!Number.isFinite(t)) return "—";
  const mins = Math.floor(t / 60);
  const secs = (t - mins * 60).toFixed(2);
  const secStr = secs.padStart(5, "0");
  return `${mins}:${secStr}`;
}

export type RefineWorkspaceDeps = {
  /** Host-side error/status reporter — surfaces import errors to user. */
  setStatus: (msg: string) => void;
  /** Called when the user navigates back to the Make route. */
  onBack: () => void;
};

export type RefineWorkspaceHandle = {
  /** Open the workspace for a specific SongPack (called from Make route). */
  openForSongPack: (containerPath: string) => Promise<void>;
  /** Currently-loaded SongPack path or null. */
  getCurrentContainerPath: () => string | null;
};

export function initRefineWorkspace(deps: RefineWorkspaceDeps): RefineWorkspaceHandle {
  // DOM grabs — every required element is asserted up front so a missing
  // template element fails loud at boot instead of silently at first click.
  const root = document.getElementById("refineRoute") as HTMLElement | null;
  const songTitleEl = document.getElementById("refineSongTitle") as HTMLElement | null;
  const instLabelEl = document.getElementById("refineInstLabel") as HTMLElement | null;
  const instSelectEl = document.getElementById("refineInstrumentSelect") as HTMLSelectElement | null;
  const reloadBtn = document.getElementById("refineReloadBtn") as HTMLButtonElement | null;
  const saveBtn = document.getElementById("refineSaveBtn") as HTMLButtonElement | null;
  const backBtn = document.getElementById("refineBack") as HTMLElement | null;
  const workSummaryEl = document.getElementById("refineWorkSummary") as HTMLElement | null;
  const worklistEl = document.getElementById("refineWorklist") as HTMLElement | null;
  const focusedRegionEl = document.getElementById("refineFocusedRegion") as HTMLElement | null;
  const focusedTimeEl = document.getElementById("refineFocusedTime") as HTMLElement | null;
  const focusedChordEl = document.getElementById("refineFocusedChord") as HTMLElement | null;
  const pitchRulerEl = document.getElementById("refinePitchRuler") as HTMLElement | null;
  const timelineSurfaceEl = document.getElementById("refineTimelineSurface") as HTMLElement | null;
  const timelineCanvas = document.getElementById("refineTimelineCanvas") as HTMLCanvasElement | null;
  const paletteEl = document.getElementById("refinePalette") as HTMLElement | null;
  const regionInfoEl = document.getElementById("refineRegionInfo") as HTMLElement | null;
  const acceptBtn = document.getElementById("refineAcceptBtn") as HTMLButtonElement | null;
  const skipBtn = document.getElementById("refineSkipBtn") as HTMLButtonElement | null;
  const emptyEl = document.getElementById("refineEmpty") as HTMLElement | null;
  const gridEl = document.getElementById("refineGrid") as HTMLElement | null;
  const emptyCmdEl = document.getElementById("refineEmptyCmd") as HTMLElement | null;
  const auditionPlayBtn = document.getElementById("refineAuditionPlay") as HTMLButtonElement | null;
  const auditionStopBtn = document.getElementById("refineAuditionStop") as HTMLButtonElement | null;
  const auditionStatusEl = document.getElementById("refineAuditionStatus") as HTMLElement | null;
  if (
    !root || !songTitleEl || !instLabelEl || !instSelectEl
    || !reloadBtn || !saveBtn || !backBtn
    || !workSummaryEl || !worklistEl
    || !focusedRegionEl || !focusedTimeEl || !focusedChordEl
    || !pitchRulerEl || !timelineSurfaceEl || !timelineCanvas
    || !paletteEl || !regionInfoEl
    || !acceptBtn || !skipBtn || !emptyEl || !gridEl || !emptyCmdEl
    || !auditionPlayBtn || !auditionStopBtn || !auditionStatusEl
  ) {
    throw new Error("initRefineWorkspace: required DOM missing -- refine workspace template not in place");
  }

  const audition: RefineAuditionHandle = initRefineAudition();

  // Set the row-height CSS variable for the pitch ruler to match the canvas.
  root.style.setProperty("--rf-row-height", `${ROW_HEIGHT_PX}px`);

  let session: RefineSession | null = null;
  let containerPath: string | null = null;
  let instrument: RefinementInstrument = "keys";
  let focusedRegionId: string | null = null;
  let isDirty = false;
  let ctx2d: CanvasRenderingContext2D | null = timelineCanvas.getContext("2d");

  function setDirty(flag: boolean): void {
    isDirty = flag;
    saveBtn!.textContent = flag ? "Save *" : "Save";
  }

  function setStatus(msg: string): void {
    deps.setStatus(msg);
  }

  function setEmpty(empty: boolean): void {
    gridEl!.style.display = empty ? "none" : "grid";
    emptyEl!.style.display = empty ? "flex" : "none";
    if (empty && containerPath) {
      emptyCmdEl!.textContent = `aural_ingest refine-candidates "${containerPath}" --instrument ${instrument}`;
    }
  }

  // -------------------------------------------------------------------------
  // Worklist (left rail)
  // -------------------------------------------------------------------------
  function renderWorklist(): void {
    if (!session) {
      worklistEl!.innerHTML = "";
      workSummaryEl!.innerHTML = "<strong>0</strong> / 0 regions reviewed";
      return;
    }
    const regions = session.candidates.regions;
    const reviewed = regions.filter((r) => session!.decisions.has(r.id)).length;
    workSummaryEl!.innerHTML =
      `<strong>${reviewed}</strong> / ${regions.length} regions reviewed`;

    // Group by hot_spot_type so the worklist shows hot spots first.
    const groups = new Map<string, RefineCandidatesRegion[]>();
    for (const region of regions) {
      const k = region.hot_spot_type;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k)!.push(region);
    }
    const groupOrder = [
      "octave_ghost",
      "off_chord",
      "density_outlier",
      "bass_gap",
      "low_confidence",
      "manual",
      "clean",
    ];

    const out: string[] = [];
    for (const groupKey of groupOrder) {
      const list = groups.get(groupKey);
      if (!list || list.length === 0) continue;
      const label = labelForHotSpot(groupKey);
      out.push(
        `<div class="rfWorkSection"><div class="rfHead">${label}<span class="rfBadge">${list.length}</span></div>`,
      );
      for (const region of list) {
        const isFocused = region.id === focusedRegionId;
        const accepted = session.decisions.has(region.id);
        const cls =
          "rfRegionRow" + (isFocused ? " isFocused" : "") + (accepted ? " isAccepted" : "");
        out.push(
          `<div class="${cls}" data-region-id="${region.id}">`
          + `<span class="rfRegionTime">${formatTime(region.t_start)}</span>`
          + `<span class="rfRegionLabel">${escapeHtml(region.section_label || labelForHotSpot(region.hot_spot_type))}</span>`
          + `<span class="rfRegionConf">${Math.round(region.confidence * 100)}%</span>`
          + `</div>`,
        );
      }
      out.push("</div>");
    }
    worklistEl!.innerHTML = out.join("");

    // Wire clicks.
    for (const row of Array.from(worklistEl!.querySelectorAll<HTMLElement>(".rfRegionRow"))) {
      row.addEventListener("click", () => {
        focusRegion(row.dataset.regionId ?? null);
      });
    }
  }

  function labelForHotSpot(k: string): string {
    switch (k) {
      case "octave_ghost": return "Octave ghosts";
      case "off_chord": return "Off-chord";
      case "density_outlier": return "Density outliers";
      case "bass_gap": return "Bass gaps";
      case "low_confidence": return "Low confidence";
      case "clean": return "Clean";
      case "manual": return "Manual flags";
      default: return k;
    }
  }

  // -------------------------------------------------------------------------
  // Timeline (center notes-over-keys, simplified piano-roll)
  // -------------------------------------------------------------------------
  function renderPitchRuler(): void {
    const out: string[] = [];
    // Iterate high→low so C8 is at the TOP of the canvas (gameplay metaphor:
    // higher pitches scroll past you from above as time moves down).
    for (let p = PITCH_HIGH; p >= PITCH_LOW; p--) {
      const cls = (p % 12 === 0) ? "rfPitchRow isC" : "rfPitchRow";
      const label = (p % 12 === 0 || p === PITCH_HIGH || p === PITCH_LOW) ? pitchLabel(p) : "";
      out.push(`<div class="${cls}">${label}</div>`);
    }
    pitchRulerEl!.innerHTML = out.join("");
  }

  function renderTimeline(): void {
    if (!session || !focusedRegionId || !ctx2d) {
      if (ctx2d) {
        ctx2d.clearRect(0, 0, timelineCanvas!.width, timelineCanvas!.height);
      }
      return;
    }
    const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
    if (!region) return;

    const active = activeNotesForRegion(session, focusedRegionId);
    if (!active) return;

    // Compute a window around the focused region: 2 s of context before
    // t_start, 2 s after t_end, so the user sees adjacent notes too.
    const windowStart = Math.max(0, region.t_start - 2);
    const windowEnd = region.t_end + 2;
    const windowSec = windowEnd - windowStart;

    // Size canvas to match surface; one row = ROW_HEIGHT_PX, columns based on
    // available width.
    const surfaceRect = timelineSurfaceEl!.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cssW = Math.max(200, Math.floor(surfaceRect.width));
    const cssH = PITCH_COUNT * ROW_HEIGHT_PX;
    timelineCanvas!.style.height = `${cssH}px`;
    timelineCanvas!.style.width = `${cssW}px`;
    timelineCanvas!.width = Math.floor(cssW * dpr);
    timelineCanvas!.height = Math.floor(cssH * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx2d.clearRect(0, 0, cssW, cssH);

    // Draw region highlight (t_start..t_end gets a subtle accent bg).
    const tToX = (t: number) => ((t - windowStart) / windowSec) * cssW;
    const pToY = (p: number) => (PITCH_HIGH - p) * ROW_HEIGHT_PX;

    ctx2d.fillStyle = "rgba(62,230,168,0.06)";
    ctx2d.fillRect(tToX(region.t_start), 0, tToX(region.t_end) - tToX(region.t_start), cssH);

    // Region boundary lines.
    ctx2d.strokeStyle = "rgba(62,230,168,0.35)";
    ctx2d.lineWidth = 1;
    for (const t of [region.t_start, region.t_end]) {
      const x = tToX(t);
      ctx2d.beginPath();
      ctx2d.moveTo(x, 0);
      ctx2d.lineTo(x, cssH);
      ctx2d.stroke();
    }

    // For each candidate, draw its notes inside this region as faint ghosts
    // EXCEPT the active candidate, which is drawn solid on top.
    for (const [candidateId, candidate] of Object.entries(session.candidates.candidates)) {
      const notes = region.candidate_notes[candidateId] ?? [];
      const isActive = candidateId === active.candidate_id;
      drawNotes(notes, candidate.color, isActive ? 1.0 : 0.18, tToX, pToY);
    }
  }

  function drawNotes(
    notes: RefinementNote[],
    color: string,
    opacity: number,
    tToX: (t: number) => number,
    pToY: (p: number) => number,
  ): void {
    if (!ctx2d) return;
    ctx2d.globalAlpha = opacity;
    ctx2d.fillStyle = color;
    ctx2d.strokeStyle = color;
    for (const note of notes) {
      if (note.pitch < PITCH_LOW || note.pitch > PITCH_HIGH) continue;
      const x = tToX(note.t_on);
      const w = Math.max(2, tToX(note.t_off) - x);
      const y = pToY(note.pitch);
      ctx2d.fillRect(x, y, w, ROW_HEIGHT_PX - 1);
    }
    ctx2d.globalAlpha = 1.0;
  }

  // -------------------------------------------------------------------------
  // Candidate palette (right pane)
  // -------------------------------------------------------------------------
  function renderPalette(): void {
    if (!session || !focusedRegionId) {
      paletteEl!.innerHTML = `<div style="color:#64748b; font-size:12px;">(no region selected)</div>`;
      return;
    }
    const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
    if (!region) return;

    const active = activeNotesForRegion(session, focusedRegionId);
    const activeId = active?.candidate_id;

    const candidates = Object.entries(session.candidates.candidates);
    const out: string[] = [];
    candidates.forEach(([cid, def], idx) => {
      const score = region.candidate_scores[cid] ?? 0;
      const isAuto = region.auto_picked === cid;
      const isActive = cid === activeId;
      const cls = "rfSwatch" + (isActive ? " isActive" : "");
      out.push(
        `<div class="${cls}" data-candidate-id="${cid}">`
        + `<span class="rfSwatchKey">${idx + 1}</span>`
        + `<span class="rfSwatchDot" style="background:${def.color}"></span>`
        + `<span class="rfSwatchLabel">${escapeHtml(def.label)}</span>`
        + `<span class="rfSwatchScore">${Math.round(score * 100)}%</span>`
        + (isAuto ? `<span class="rfSwatchAuto">AUTO</span>` : "")
        + `</div>`,
      );
    });
    paletteEl!.innerHTML = out.join("");

    for (const swatch of Array.from(paletteEl!.querySelectorAll<HTMLElement>(".rfSwatch"))) {
      swatch.addEventListener("click", () => {
        const cid = swatch.dataset.candidateId;
        if (cid) pickCandidate(cid);
      });
    }
  }

  function renderRegionInfo(): void {
    if (!session || !focusedRegionId) {
      regionInfoEl!.innerHTML = `<div class="rfMetaRow"><span>Type</span><strong>—</strong></div>`;
      return;
    }
    const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
    if (!region) return;
    const autoLabel =
      session.candidates.candidates[region.auto_picked]?.label ?? region.auto_picked;
    const decisionLabel = session.decisions.get(region.id);
    regionInfoEl!.innerHTML = [
      `<div class="rfMetaRow"><span>Type</span><strong>${labelForHotSpot(region.hot_spot_type)}</strong></div>`,
      `<div class="rfMetaRow"><span>Confidence</span><strong>${Math.round(region.confidence * 100)}%</strong></div>`,
      `<div class="rfMetaRow"><span>Chord</span><strong>${escapeHtml(region.chord ?? "—")}</strong></div>`,
      `<div class="rfMetaRow"><span>Auto-pick</span><strong>${escapeHtml(autoLabel)}</strong></div>`,
      decisionLabel
        ? `<div class="rfMetaRow"><span>Your pick</span><strong style="color:#3EE6A8;">${escapeHtml(session.candidates.candidates[decisionLabel.candidate_id]?.label ?? decisionLabel.candidate_id)}</strong></div>`
        : "",
    ].join("");
  }

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------
  function focusRegion(regionId: string | null): void {
    focusedRegionId = regionId;
    if (!regionId || !session) {
      focusedRegionEl!.textContent = "(pick a region)";
      focusedTimeEl!.textContent = "";
      focusedChordEl!.textContent = "";
    } else {
      const region = session.candidates.regions.find((r) => r.id === regionId);
      if (region) {
        focusedRegionEl!.textContent =
          region.section_label || labelForHotSpot(region.hot_spot_type);
        focusedTimeEl!.textContent =
          `${formatTime(region.t_start)} – ${formatTime(region.t_end)}`;
        focusedChordEl!.textContent = region.chord ? `· ${region.chord}` : "";
      }
    }
    renderWorklist();
    renderTimeline();
    renderPalette();
    renderRegionInfo();
    syncAudition();
  }

  function pickCandidate(candidateId: string): void {
    if (!session || !focusedRegionId) return;
    const ok = pickCandidateForRegion(session, focusedRegionId, candidateId);
    if (!ok) return;
    setDirty(true);
    renderPalette();
    renderRegionInfo();
    renderTimeline();
    renderWorklist();
    // If audition is running, swap to the newly-picked notes immediately
    // so A/B comparisons feel instant rather than requiring stop+play.
    if (audition.isPlaying()) {
      const active = activeNotesForRegion(session, focusedRegionId);
      if (active) audition.updateNotes(active.notes);
    }
  }

  // -------------------------------------------------------------------------
  // Audition
  // -------------------------------------------------------------------------
  function setAuditionStatus(status: "stopped" | "playing"): void {
    auditionStatusEl!.classList.toggle("isPlaying", status === "playing");
    if (status === "playing" && session && focusedRegionId) {
      const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
      if (region) {
        auditionStatusEl!.textContent =
          `Looping ${formatTime(region.t_start)} – ${formatTime(region.t_end)}`;
        return;
      }
    }
    auditionStatusEl!.textContent = "Stopped";
  }

  /**
   * Refresh the audition button availability + status text based on the
   * current focused region. Stops playback when no region is focused so
   * the engine doesn't keep humming after a save/back/etc.
   */
  function syncAudition(): void {
    const canPlay = Boolean(session && focusedRegionId);
    auditionPlayBtn!.disabled = !canPlay;
    auditionStopBtn!.disabled = !canPlay;
    if (!canPlay && audition.isPlaying()) {
      audition.stop();
    }
    setAuditionStatus(audition.isPlaying() ? "playing" : "stopped");
  }

  function startAudition(): void {
    if (!session || !focusedRegionId) return;
    const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
    if (!region) return;
    const active = activeNotesForRegion(session, focusedRegionId);
    if (!active) return;
    audition.playRegion(active.notes, region.t_start, region.t_end);
    setAuditionStatus("playing");
  }

  function stopAudition(): void {
    audition.stop();
    setAuditionStatus("stopped");
  }

  function toggleAudition(): void {
    if (audition.isPlaying()) stopAudition();
    else startAudition();
  }

  function focusNextRegion(): void {
    if (!session) return;
    const regions = session.candidates.regions;
    const idx = regions.findIndex((r) => r.id === focusedRegionId);
    if (idx < 0 || idx >= regions.length - 1) {
      // Loop to first unreviewed region instead of doing nothing.
      const firstUnreviewed = regions.find((r) => !session!.decisions.has(r.id));
      if (firstUnreviewed) focusRegion(firstUnreviewed.id);
      return;
    }
    focusRegion(regions[idx + 1]!.id);
  }

  function acceptRegion(): void {
    if (!session || !focusedRegionId) return;
    // If no explicit pick yet, accept the auto-picked candidate.
    if (!session.decisions.has(focusedRegionId)) {
      const region = session.candidates.regions.find((r) => r.id === focusedRegionId);
      if (region) pickCandidate(region.auto_picked);
    }
    focusNextRegion();
  }

  async function save(): Promise<void> {
    if (!session) return;
    try {
      await saveDecisions(session);
      setDirty(false);
      const count = session.decisions.size;
      setStatus(`Saved ${count} decision(s) for ${instrument}`);
    } catch (e) {
      setStatus(`Save failed: ${String(e)}`);
    }
  }

  async function reload(): Promise<void> {
    if (!containerPath) return;
    await openForSongPack(containerPath);
  }

  // -------------------------------------------------------------------------
  // Public entry: openForSongPack
  // -------------------------------------------------------------------------
  async function openForSongPack(path: string): Promise<void> {
    containerPath = path;
    songTitleEl!.textContent = path;
    setDirty(false);
    setStatus(`Loading refine candidates for ${path}…`);
    try {
      const loaded = await loadSession(path, instrument);
      if (!loaded) {
        session = null;
        focusedRegionId = null;
        renderWorklist();
        renderPalette();
        renderRegionInfo();
        setEmpty(true);
        setStatus(
          `No refine_candidates.${instrument}.json yet -- precompute first`,
        );
        return;
      }
      session = loaded;
      setEmpty(false);
      // Focus the first non-clean region by default so the user lands on
      // something interesting. Fall back to the first region.
      const firstHotSpot = loaded.candidates.regions.find(
        (r) => r.hot_spot_type !== "clean" && !loaded.decisions.has(r.id),
      );
      const first = firstHotSpot ?? loaded.candidates.regions[0] ?? null;
      focusedRegionId = first?.id ?? null;
      renderPitchRuler();
      renderWorklist();
      renderPalette();
      renderRegionInfo();
      renderTimeline();
      if (focusedRegionId) {
        const region = loaded.candidates.regions.find((r) => r.id === focusedRegionId);
        if (region) {
          focusRegion(region.id);
        }
      }
      setStatus(
        `Loaded ${loaded.candidates.regions.length} regions for ${instrument} (${loaded.decisions.size} previously reviewed)`,
      );
    } catch (e) {
      session = null;
      setEmpty(true);
      setStatus(`Failed to load refine session: ${String(e)}`);
    }
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------
  instSelectEl.addEventListener("change", () => {
    instrument = (instSelectEl.value as RefinementInstrument) || "keys";
    instLabelEl!.textContent =
      instSelectEl.options[instSelectEl.selectedIndex]?.text ?? instrument;
    if (containerPath) {
      void openForSongPack(containerPath);
    }
  });

  reloadBtn.addEventListener("click", () => void reload());
  saveBtn.addEventListener("click", () => void save());
  backBtn.addEventListener("click", () => {
    audition.stop();
    deps.onBack();
  });
  acceptBtn.addEventListener("click", () => acceptRegion());
  skipBtn.addEventListener("click", () => focusNextRegion());
  auditionPlayBtn.addEventListener("click", () => startAudition());
  auditionStopBtn.addEventListener("click", () => stopAudition());

  // Keyboard shortcuts (only when refine route is active).
  window.addEventListener("keydown", (ev) => {
    if (!root.closest(".isActive")) return;
    // Don't steal keys when the user is editing a field.
    if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) {
      return;
    }
    if (!session || !focusedRegionId) return;
    if (ev.key === " " || ev.code === "Space") {
      ev.preventDefault();
      toggleAudition();
      return;
    }
    if (ev.key >= "1" && ev.key <= "4") {
      const idx = Number(ev.key) - 1;
      const cid = Object.keys(session.candidates.candidates)[idx];
      if (cid) {
        ev.preventDefault();
        pickCandidate(cid);
      }
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      acceptRegion();
    } else if (ev.key === "ArrowRight" || ev.key === "s" || ev.key === "S") {
      ev.preventDefault();
      focusNextRegion();
    } else if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      if (!session) return;
      const idx = session.candidates.regions.findIndex((r) => r.id === focusedRegionId);
      if (idx > 0) focusRegion(session.candidates.regions[idx - 1]!.id);
    }
  });

  // Resize redraws timeline (canvas resolution depends on surface width).
  window.addEventListener("resize", () => renderTimeline());

  return {
    openForSongPack,
    getCurrentContainerPath: () => containerPath,
  };
}

// HTML-escape for embedded user content.
function escapeHtml(s: string): string {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}
