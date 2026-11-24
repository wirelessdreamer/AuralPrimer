/**
 * Refine workspace controller — owns the Cleanup & Edit route, laid out as a
 * DAW: a full-width spectrogram piano-roll editor (SpectrogramEditor) with a
 * transport bar above and a thin note/candidate inspector below.
 *
 * Interaction model (per the v2 redesign):
 *  - The WHOLE song's transcription is loaded into the editor as one editable
 *    note set. The user edits notes directly — drag to retime/repitch, drag
 *    edges to resize, click empty space to add, Delete to remove, scroll to
 *    zoom, Alt-drag (or middle-drag) to pan. All of that lives in
 *    SpectrogramEditor; this controller wires it to the song data + transport.
 *  - The precomputed per-region candidates become optional "apply" chips in
 *    the inspector: select a note, pick a candidate, and that region's slice of
 *    notes is replaced by the candidate's transcription.
 *  - The 12+ hot-spot regions are navigation only — a "Jump" dropdown scrolls
 *    the editor view to a section start.
 *  - Source and rendered notes share a Web Audio output graph for monitoring.
 *    The streamed source preserves pitch when slowed and drives the note
 *    scheduler. Listening modes can solo either stream, combine them, or route
 *    one to each ear. The existing spectrogram/editor remains mounted.
 *
 * On save, the edited note set is partitioned back into the candidate regions
 * as per-region "manual" decisions and written to refinement.<inst>.json —
 * the same schema the runtime game overlays onto notes.mid.
 */

import {
  loadSession,
  saveDecisions,
  activeNotesForRegion,
  type RefineSession,
  type RefineCandidatesRegion,
  type RefinementInstrument,
} from "./refineCandidatesIo";
import { initRefineAudition, type RefineAuditionHandle } from "./refineAudition";
import { audiblePlayheadSec } from "@auralprimer/av-sync";
import {
  downbeatTimesShifted,
  beatsPerBarFromTimeSignatures,
  quantLevelByValue,
} from "./beatGrid";
import { getAvOffsetSec } from "./avOffset";
import { RefineSourceAudio } from "./refineSourceAudio";
import { RefineGamePreview } from "./refineGamePreview";
import { initRefineMidiMonitor } from "./refineMidiMonitor";
import type { RefineWaveform } from "./refineWaveform";
import { GridMetronome } from "./gridMetronome";
import {
  detectMelodicStems,
  auralsongJsonExists,
  drumTabRelPath as resolveDrumTabRelPath,
} from "./cleanupReadiness";
import {
  laneOrderForTab,
  hitsToNotes,
  notesToTab,
  drumLaneLabel,
  type DrumTabFile,
} from "./drumTabIo";
import type { RefinementNote } from "./refineCandidatesIo";
import { invoke } from "@tauri-apps/api/core";
import { featureDir as packFeatureDir, packDisplayName } from "@auralprimer/auralsong/packKind";
import {
  SpectrogramEditor,
  type SpectrogramGeometry,
  type SpectroNote,
} from "./spectrogramEditor";

/** SpectroNote (velocity optional) -> RefinementNote (velocity required). */
function toRefinementNotes(notes: SpectroNote[]): RefinementNote[] {
  return notes.map((n) => ({
    t_on: n.t_on,
    t_off: n.t_off,
    pitch: n.pitch,
    velocity: typeof n.velocity === "number" ? n.velocity : 100,
  }));
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function pitchLabel(midi: number): string {
  const name = NOTE_NAMES[((midi % 12) + 12) % 12]!;
  const octave = Math.floor(midi / 12) - 1;
  return `${name}${octave}`;
}

function formatTime(t: number): string {
  if (!Number.isFinite(t)) return "—";
  const mins = Math.floor(t / 60);
  const secs = (t - mins * 60).toFixed(2);
  return `${mins}:${secs.padStart(5, "0")}`;
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

export type RefineWorkspaceDeps = {
  setStatus: (msg: string) => void;
  onBack: () => void;
};

export type RefineWorkspaceHandle = {
  openForAuralSong: (containerPath: string, opts?: { instrument?: string }) => Promise<void>;
  getCurrentContainerPath: () => string | null;
};

// Editable melodic roles, in the order we prefer them as the default pick.
// (Drums are charted via the drum tab, not melodic candidates, so they are
// deliberately absent here and never offered in the Refine instrument picker.)
const MELODIC_PICK_ORDER = ["keys", "bass", "guitar", "lead_guitar", "rhythm_guitar", "vocals"];
const INSTRUMENT_LABELS: Record<string, string> = {
  keys: "Keys",
  bass: "Bass",
  guitar: "Guitar",
  lead_guitar: "Lead Guitar",
  rhythm_guitar: "Rhythm Guitar",
  vocals: "Vocals",
  // Drums are the exception to the candidates flow: the picker offers them
  // when the pack has a drum_tab.json, and the workspace switches to the lane
  // editor over the drums spectrogram (see the drumMode branches below).
  drums: "Drums",
};

export function initRefineWorkspace(deps: RefineWorkspaceDeps): RefineWorkspaceHandle {
  const $ = <T extends HTMLElement>(id: string): T | null =>
    document.getElementById(id) as T | null;

  const root = $<HTMLElement>("refineRoute");
  const songTitleEl = $<HTMLElement>("refineSongTitle");
  const instLabelEl = $<HTMLElement>("refineInstLabel");
  const instSelectEl = $<HTMLSelectElement>("refineInstrumentSelect");
  const reloadBtn = $<HTMLButtonElement>("refineReloadBtn");
  const saveBtn = $<HTMLButtonElement>("refineSaveBtn");
  const snapBtn = document.getElementById("refineSnapBtn") as HTMLButtonElement | null;
  const refreshMeterBtn = document.getElementById("refineRefreshMeterBtn") as HTMLButtonElement | null;
  const quantSelect = document.getElementById("refineQuantSelect") as HTMLSelectElement | null;
  const backBtn = $<HTMLElement>("refineBack");
  const transportEl = $<HTMLElement>("refineTransport");
  const playBtn = $<HTMLButtonElement>("refinePlayBtn");
  const stopBtn = $<HTMLButtonElement>("refineStopBtn");
  const timeReadoutEl = $<HTMLElement>("refineTimeReadout");
  const scrubEl = $<HTMLInputElement>("refineScrub");
  const sectionSelectEl = $<HTMLSelectElement>("refineSectionSelect");
  const auditionToggle = $<HTMLInputElement>("refineAuditionToggle");
  const editAuditionToggle = $<HTMLInputElement>("refineEditAuditionToggle");
  const metronomeToggle = $<HTMLInputElement>("refineMetronomeToggle");
  const downbeatMinusBtn = $<HTMLButtonElement>("refineDownbeatMinus");
  const downbeatPlusBtn = $<HTMLButtonElement>("refineDownbeatPlus");
  const speedSelectEl = $<HTMLInputElement>("refineSpeedSelect");
  const soloSelectEl = $<HTMLSelectElement>("refineSoloSelect");
  const stageEl = $<HTMLElement>("refineStage");
  const inspectorEl = $<HTMLElement>("refineInspector");
  const selInfoEl = $<HTMLElement>("refineSelInfo");
  const candChipsEl = $<HTMLElement>("refineCandChips");
  const emptyEl = $<HTMLElement>("refineEmpty");
  const emptyCmdEl = $<HTMLElement>("refineEmptyCmd");
  const undoBtn = $<HTMLButtonElement>("refineUndoBtn");
  const redoBtn = $<HTMLButtonElement>("refineRedoBtn");
  const listenEl = $<HTMLElement>("refineListen");
  const speedOutput = $<HTMLOutputElement>("refineSpeedOutput");
  const loopBtn = $<HTMLButtonElement>("refineLoopBtn");
  const hearSelectionBtn = $<HTMLButtonElement>("refineHearSelection");
  const loopSelectionBtn = $<HTMLButtonElement>("refineLoopSelection");
  const swapBtn = $<HTMLButtonElement>("refineSwapBtn");
  const routeLabel = $<HTMLElement>("refineListenRoute");
  const sourceLevel = $<HTMLInputElement>("refineSourceLevel");
  const renderedLevel = $<HTMLInputElement>("refineRenderedLevel");
  const mainEl = $<HTMLElement>("refineMain")!;
  const splitToggle = $<HTMLButtonElement>("refineSplitToggle")!;
  const splitDivider = $<HTMLElement>("refineSplitDivider")!;
  const gamePane = $<HTMLElement>("refineGamePane")!;
  const gameSurface = $<HTMLElement>("refineGameSurface")!;
  const gameInfo = $<HTMLElement>("refineGameInfo")!;
  type ListenMode = "source" | "rendered" | "together" | "split";
  let listenMode: ListenMode = "split";
  let swapped = false;
  let loopRegion: { t0: number; t1: number } | null = null;

  if (
    !root || !songTitleEl || !instLabelEl || !instSelectEl || !reloadBtn || !saveBtn
    || !backBtn || !transportEl || !playBtn || !stopBtn || !timeReadoutEl || !scrubEl
    || !sectionSelectEl || !auditionToggle || !editAuditionToggle || !metronomeToggle
    || !downbeatMinusBtn || !downbeatPlusBtn || !stageEl || !inspectorEl || !selInfoEl
    || !candChipsEl || !emptyEl || !emptyCmdEl || !undoBtn || !redoBtn
    || !speedSelectEl || !soloSelectEl
  ) {
    throw new Error("initRefineWorkspace: required DOM missing -- refine workspace template not in place");
  }

  const audition: RefineAuditionHandle = initRefineAudition();

  // ---- state ----
  let session: RefineSession | null = null;
  let regionsSorted: RefineCandidatesRegion[] = [];
  let containerPath: string | null = null;
  let instrument: RefinementInstrument = "keys";
  let isDirty = false;
  let selectedIndex: number | null = null;
  let durationSec = 0;
  // Drum-cleanup mode: the editor shows kit lanes over the drums spectrogram
  // and edits the pack's drum_tab artifact directly (no candidates/regions).
let drumMode = false;
let drumLanes: string[] = [];
let drumTabOriginal: DrumTabFile | null = null;
let drumTabRelPath = "drum_tab.json";
let songTimelineRelPath = "song_timeline.json";

  // transport
  //  - The source media clock drives notes; visualization retains the shared
  //    audible-position formula and A/V calibration.
  //  - When no audio loads, we fall back to the wall-clock playhead below.
  //  - refineAudition (the "Hear notes" synth) plays alongside either path.
  let isPlaying = false;
  let playStartOffset = 0; // playhead position (sec) when play began (wall-clock path)
  let playStartWall = 0; // performance.now() when play began (wall-clock path)
  let playheadSec = 0;
  let rafId: number | null = null;
  let transportGeneration = 0;
  let resumePositionSec: number | null = null;
  let playbackRate = 1; // transport speed multiplier
  let soloMode = true; // true = play the current instrument's isolated stem; false = full mix

  // Pitch-preserving media transport. audioLoaded flips true once a
  // stem/mix is loaded; a load miss leaves it false -> wall-clock fallback.
  const sourceAudio = new RefineSourceAudio(audition);
  const gamePreview = new RefineGamePreview(gameSurface);
  // Optional beat-grid metronome (accent on the one), driven off the audible
  // playhead — an on-demand audible test of whether the grid aligns to the song.
  const metronome = new GridMetronome();
  let audioLoaded = false;
  // Monotonic token so a rapid instrument/solo switch can't let a slower,
  // earlier load win the shared engine buffer after a newer one started.
  let loadGeneration = 0;

  const spectroTileUrls: string[] = [];
  function revokeSpectroUrls(): void {
    for (const u of spectroTileUrls) {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
    spectroTileUrls.length = 0;
  }

  function isObject(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }

  function stringRelPath(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }

  async function resolveSongTimelineRelPath(path: string): Promise<string> {
    try {
      const details = await invoke<{
        manifest_raw?: {
          song_timeline?: unknown;
          artifacts?: unknown;
        } | null;
      }>("get_auralsong_details", { containerPath: path });
      const raw = details.manifest_raw;
      const artifacts = isObject(raw?.artifacts) ? raw.artifacts : null;
      return (
        stringRelPath(raw?.song_timeline) ??
        stringRelPath(artifacts?.song_timeline) ??
        "song_timeline.json"
      );
    } catch {
      return "song_timeline.json";
    }
  }

  // ---- the editor (created once; load() swaps geometry + notes) ----
  const editor = new SpectrogramEditor(stageEl, {
    onNotesChanged: () => {
      setDirty(true);
      updateUndoButtons();
      // Times/pitch of the selected note may have changed during a drag.
      if (selectedIndex != null) renderInspector(selectedIndex);
      audition.updateNotes(toRefinementNotes(editor.getNotes()));
      syncGameNotes();
    },
    onSelectionChanged: (index) => {
      selectedIndex = index;
      renderInspector(index);
    },
    // Audition a note as it's placed or edited, so the user hears the pitch
    // they're putting down (gated by the "Audition edits" toggle). Drum-mode
    // "pitches" are lane indices — nothing meaningful to synthesize.
    onNoteAuditioned: (note) => {
      if (!drumMode && editAuditionToggle!.checked) audition.playNote(note.pitch, note.velocity);
    },
    // Sonic-Visualiser-style cursor readout: show the pitch + time under the
    // cursor in the inspector whenever no note is actively selected. In drum
    // mode `pitch` is a lane index and reads as the lane name.
    onHover: (info) => {
      if (selectedIndex != null) return;
      if (!info) {
        selInfoEl!.textContent = drumMode ? "No hit selected" : "No note selected";
        return;
      }
      const label = drumMode ? drumLaneLabel(drumLanes[info.pitch] ?? "?") : pitchLabel(info.pitch);
      selInfoEl!.innerHTML =
        `<span class="rfSelPitch">${label}</span> · ${formatTime(info.time)}`
        + ` <span style="color:#64748b;">· cursor</span>`;
    },
    onSeek: (time) => seekTo(time),
  });
  const midiMonitor = initRefineMidiMonitor({ root, audition, onNotes: (notes) => {
    editor.setLivePitches(notes.map((note) => note.pitch));
    gamePreview.setLiveInputNotes(notes);
  } });

  function setDirty(flag: boolean): void {
    isDirty = flag;
    saveBtn!.textContent = flag ? "Save *" : "Save";
  }
  function setStatus(msg: string): void {
    deps.setStatus(msg);
  }
  function updateUndoButtons(): void {
    undoBtn!.disabled = !editor.canUndo();
    redoBtn!.disabled = !editor.canRedo();
  }

  function syncGameNotes(): void {
    const notes = editor.getNotes();
    gamePreview.setNotes(notes, instrument, drumLanes);
    gameInfo.textContent = `${INSTRUMENT_LABELS[instrument] ?? instrument} · ${notes.length.toLocaleString()} ${drumMode ? "hits" : "notes"}`;
  }

  let splitMode = false;
  let analysisWidth = 50;
  try {
    splitMode = localStorage.getItem("auralprimer.studio.splitGame") === "1";
    const storedWidth = Number(localStorage.getItem("auralprimer.studio.analysisWidth"));
    if (storedWidth >= 30 && storedWidth <= 70) analysisWidth = storedWidth;
  } catch { /* Preferences are optional in restricted webviews. */ }

  function setAnalysisWidth(value: number): void {
    analysisWidth = Math.max(30, Math.min(70, Math.round(value)));
    mainEl.style.setProperty("--rfAnalysisWidth", `${analysisWidth}%`);
    splitDivider.setAttribute("aria-valuenow", String(analysisWidth));
    splitDivider.setAttribute("aria-valuetext", `Transcription ${analysisWidth}%, game ${100 - analysisWidth}%`);
  }

  function applySplitMode(): void {
    mainEl.classList.toggle("isSplit", splitMode);
    gamePane.hidden = !splitMode;
    splitDivider.hidden = !splitMode;
    splitToggle.setAttribute("aria-pressed", String(splitMode));
    gamePreview.setVisible(splitMode && !mainEl.hidden);
    editor.resize();
  }

  setAnalysisWidth(analysisWidth);
  splitToggle.addEventListener("click", () => {
    splitMode = !splitMode;
    applySplitMode();
    try { localStorage.setItem("auralprimer.studio.splitGame", splitMode ? "1" : "0"); } catch { /* optional */ }
  });
  let resizingSplit = false;
  splitDivider.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    resizingSplit = true;
    mainEl.classList.add("isResizing");
    splitDivider.setPointerCapture(ev.pointerId);
  });
  splitDivider.addEventListener("pointermove", (ev) => {
    if (!resizingSplit) return;
    const rect = mainEl.getBoundingClientRect();
    if (rect.width > 0) setAnalysisWidth((ev.clientX - rect.left) / rect.width * 100);
  });
  const finishSplitResize = (ev: PointerEvent): void => {
    if (!resizingSplit) return;
    resizingSplit = false;
    mainEl.classList.remove("isResizing");
    if (splitDivider.hasPointerCapture(ev.pointerId)) splitDivider.releasePointerCapture(ev.pointerId);
    try { localStorage.setItem("auralprimer.studio.analysisWidth", String(analysisWidth)); } catch { /* optional */ }
  };
  splitDivider.addEventListener("pointerup", finishSplitResize);
  splitDivider.addEventListener("pointercancel", finishSplitResize);
  splitDivider.addEventListener("keydown", (ev) => {
    if (!["Home", "+", "-", "="].includes(ev.key)) return;
    ev.preventDefault();
    setAnalysisWidth(ev.key === "Home" ? 50 : analysisWidth + (ev.key === "-" ? -5 : 5));
    try { localStorage.setItem("auralprimer.studio.analysisWidth", String(analysisWidth)); } catch { /* optional */ }
  });
  splitDivider.addEventListener("dblclick", () => {
    setAnalysisWidth(50);
    try { localStorage.setItem("auralprimer.studio.analysisWidth", "50"); } catch { /* optional */ }
  });

  // -------------------------------------------------------------------------
  // Note <-> region helpers
  // -------------------------------------------------------------------------

  /** The region whose span contains time t (largest t_start <= t). */
  function regionForTime(t: number): RefineCandidatesRegion | null {
    let best: RefineCandidatesRegion | null = null;
    for (const r of regionsSorted) {
      if (r.t_start <= t) best = r;
      else break;
    }
    return best ?? regionsSorted[0] ?? null;
  }

  /** Assemble the whole song's transcription (deduped) as the editable set. */
  function assembleFullNotes(s: RefineSession): SpectroNote[] {
    const seen = new Set<string>();
    const out: SpectroNote[] = [];
    for (const region of s.candidates.regions) {
      const active = activeNotesForRegion(s, region.id);
      if (!active) continue;
      for (const n of active.notes) {
        const key = `${n.pitch}|${n.t_on.toFixed(3)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ t_on: n.t_on, t_off: n.t_off, pitch: n.pitch, velocity: n.velocity ?? 100 });
      }
    }
    out.sort((a, b) => a.t_on - b.t_on);
    return out;
  }

  // -------------------------------------------------------------------------
  // Inspector (selected note + candidate "apply" chips)
  // -------------------------------------------------------------------------

  function renderInspector(index: number | null): void {
    const selected = index == null ? undefined : editor.getNotes()[index];
    if (hearSelectionBtn) {
      hearSelectionBtn.disabled = !selected || drumMode;
      hearSelectionBtn.textContent = selected && !drumMode ? `Hear ${pitchLabel(selected.pitch)}` : "Hear selection";
    }
    if (loopSelectionBtn) loopSelectionBtn.disabled = !selected;
    // Drum mode: lane + time readout only — no candidate chips (drums have no
    // candidate system; the drum tab is the single source of truth).
    if (drumMode) {
      candChipsEl!.innerHTML = "";
      const hit = index != null ? editor.getNotes()[index] : undefined;
      if (!hit) {
        selInfoEl!.textContent = "No hit selected";
        return;
      }
      selInfoEl!.innerHTML =
        `<span class="rfSelPitch">${drumLaneLabel(drumLanes[hit.pitch] ?? "?")}</span>`
        + ` · ${formatTime(hit.t_on)}`;
      return;
    }
    if (index == null || !session) {
      selInfoEl!.textContent = "No note selected";
      candChipsEl!.innerHTML = "";
      return;
    }
    const note = editor.getNotes()[index];
    if (!note) {
      selInfoEl!.textContent = "No note selected";
      candChipsEl!.innerHTML = "";
      return;
    }
    const region = regionForTime(note.t_on);
    selInfoEl!.innerHTML =
      `<span class="rfSelPitch">${pitchLabel(note.pitch)}</span> · `
      + `${formatTime(note.t_on)}–${formatTime(note.t_off)}`
      + (region ? ` · ${escapeHtml(region.section_label || labelForHotSpot(region.hot_spot_type))}` : "");
    renderCandChips(region);
  }

  function renderCandChips(region: RefineCandidatesRegion | null): void {
    if (!session || !region) {
      candChipsEl!.innerHTML = "";
      return;
    }
    const cands = Object.entries(session.candidates.candidates);
    const label = escapeHtml(region.section_label || labelForHotSpot(region.hot_spot_type));
    const chips = cands.map(([cid, def], i) => {
      const score = region.candidate_scores[cid] ?? 0;
      const isAuto = region.auto_picked === cid;
      return (
        `<span class="rfCandChip${isAuto ? " isActive" : ""}" data-cid="${cid}" data-region="${region.id}">`
        + `<span class="rfChipKey">${i + 1}</span>`
        + `<span class="rfChipDot" style="background:${def.color}"></span>`
        + `${escapeHtml(def.label)} <span style="color:#94a3b8;">${Math.round(score * 100)}%</span>`
        + `</span>`
      );
    }).join("");
    candChipsEl!.innerHTML = `<span style="color:#64748b;">Apply to ${label}:</span>${chips}`;
    for (const el of Array.from(candChipsEl!.querySelectorAll<HTMLElement>(".rfCandChip"))) {
      el.addEventListener("click", () => applyCandidate(el.dataset.region!, el.dataset.cid!));
    }
  }

  /** Replace a region's slice of the note set with a candidate's transcription. */
  function applyCandidate(regionId: string, cid: string): void {
    if (!session) return;
    const region = session.candidates.regions.find((r) => r.id === regionId);
    if (!region) return;
    const candNotes = region.candidate_notes[cid];
    if (!candNotes) return;
    const kept = editor.getNotes().filter((n) => regionForTime(n.t_on)?.id !== regionId);
    for (const n of candNotes) {
      if (regionForTime(n.t_on)?.id === regionId) {
        kept.push({ t_on: n.t_on, t_off: n.t_off, pitch: n.pitch, velocity: n.velocity ?? 100 });
      }
    }
    kept.sort((a, b) => a.t_on - b.t_on);
    editor.setNotes(kept);
    syncGameNotes();
    setDirty(true);
    setStatus(`Applied “${session.candidates.candidates[cid]?.label ?? cid}” to ${region.section_label || labelForHotSpot(region.hot_spot_type)}`);
    if (selectedIndex != null) renderInspector(selectedIndex);
    if (isPlaying && auditionToggle!.checked) audition.updateNotes(toRefinementNotes(editor.getNotes()));
  }

  // -------------------------------------------------------------------------
  // Section navigation
  // -------------------------------------------------------------------------

  function buildSections(): void {
    sectionSelectEl!.innerHTML = `<option value="">Whole song</option>`;
    const seen = new Set<string>();
    for (const r of regionsSorted) {
      const label = r.section_label;
      if (!label || seen.has(label)) continue;
      seen.add(label);
      const opt = document.createElement("option");
      opt.value = String(r.t_start);
      opt.textContent = `${label} (${formatTime(r.t_start)})`;
      sectionSelectEl!.appendChild(opt);
    }
  }

  // -------------------------------------------------------------------------
  // Transport (wall-clock playhead + synth audition)
  // -------------------------------------------------------------------------

  function setPlayhead(t: number, follow: boolean): void {
    playheadSec = Math.max(0, Math.min(durationSec, t));
    editor.setTime(playheadSec);
    gamePreview.setTime(playheadSec, isPlaying, playbackRate);
    if (follow) editor.scrollTimeIntoView(playheadSec);
    scrubEl!.value = String(durationSec > 0 ? Math.round((playheadSec / durationSec) * 1000) : 0);
    timeReadoutEl!.textContent = `${formatTime(playheadSec)} / ${formatTime(durationSec)}`;
  }

  function transportClock(): number {
    return audioLoaded ? sourceAudio.getCurrentTimeSec()
      : playStartOffset + ((performance.now() - playStartWall) / 1000) * playbackRate;
  }

  function startRenderedNotes(): void {
    if (audioLoaded && !sourceAudio.getIsPlaying()) return;
    if (!drumMode && auditionToggle!.checked && isPlaying) {
      audition.playRegion(toRefinementNotes(editor.getNotes()), transportClock(), loopRegion?.t1 ?? durationSec,
        { getTimeSec: transportClock, playbackRate });
    }
  }

  function applyListening(): void {
    if (drumMode) listenMode = "source";
    auditionToggle!.checked = listenMode !== "source";
    const sourceGain = Number(sourceLevel?.value ?? 80) / 100;
    const renderedGain = Number(renderedLevel?.value ?? 70) / 100;
    const split = listenMode === "split";
    audition.setOutput({
      sourceGain: listenMode === "rendered" ? 0 : sourceGain,
      renderedGain,
      sourcePan: split ? (swapped ? 1 : -1) : 0,
      renderedPan: split ? (swapped ? -1 : 1) : 0,
      renderedEnabled: listenMode !== "source" && !drumMode,
    });
    root!.querySelectorAll<HTMLButtonElement>("[data-listen-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.listenMode === listenMode));
      button.disabled = drumMode && button.dataset.listenMode !== "source";
    });
    if (swapBtn) swapBtn.disabled = !split;
    if (renderedLevel) renderedLevel.disabled = drumMode;
    const sourceEar = root!.querySelector<HTMLElement>("#refineSourceEar");
    const renderedEar = root!.querySelector<HTMLElement>("#refineRenderedEar");
    if (sourceEar) sourceEar.textContent = split ? (swapped ? "R" : "L") : listenMode === "rendered" ? "—" : "L+R";
    if (renderedEar) renderedEar.textContent = split ? (swapped ? "L" : "R") : listenMode === "source" ? "—" : "L+R";
    root!.querySelector("#refineSourceBadge")?.classList.toggle("isMuted", listenMode === "rendered");
    root!.querySelector("#refineRenderedBadge")?.classList.toggle("isMuted", listenMode === "source");
    if (routeLabel) routeLabel.textContent = split
      ? "Both play in sync · one stream per ear"
      : listenMode === "together" ? "Source + rendered notes · both ears"
        : listenMode === "rendered" ? "Rendered notes · both ears" : "Source · both ears";
    for (const level of [sourceLevel, renderedLevel]) {
      if (level) {
        const output = root!.querySelector<HTMLOutputElement>(`output[for="${level.id}"]`);
        if (output) output.value = `${level.value}%`;
      }
    }
  }

  function tick(): void {
    if (!isPlaying) return;
    if (!root!.closest(".isActive")) { pauseTransport(); return; }
    // Clock source: the native engine's playback position when audio is loaded,
    // otherwise the wall clock.
    const raw = transportClock();
    if (loopRegion && raw >= loopRegion.t1) {
      seekTo(loopRegion.t0);
      return;
    }
    if (audioLoaded && sourceAudio.hasEnded()) { pauseTransport(); return; }
    if (raw >= durationSec) {
      setPlayhead(durationSec, false);
      pauseTransport();
      return;
    }
    // Draw the cursor at the position that's actually AUDIBLE right now — the
    // same math the game's transport uses, so the shared calibration lines up:
    //   audible = nativePos − outputLatency·rate − avOffset
    // outputLatency is a wall-clock buffer latency (scaled by rate to song
    // time); avOffset is the user's perceptual calibration (unscaled, as the
    // game applies it). Zero on the wall-clock fallback (no native latency).
    // Only compensate output latency once the engine is actually playing — the
    // native position is pinned during the start-up window, so subtracting
    // latency then would nudge the cursor off. (Matches the game.)
    const outLatency = audioLoaded && sourceAudio.getIsPlaying()
      ? (sourceAudio.getOutputLatencySec() ?? 0)
      : 0;
    // Shared formula (identical to the game's transport) so the playhead lands
    // on what's audible and the two apps can never drift apart.
    const audibleSec = audiblePlayheadSec({
      nativePosSec: raw,
      outputLatencySec: outLatency,
      playbackRate,
      avOffsetSec: getAvOffsetSec(),
    });
    setPlayhead(audibleSec, true);
    // Drive the metronome off the AUDIO-audible position, not the visual playhead.
    // The click and the stem exit the same output, so the perceptual A/V offset
    // (audioMs − videoMs) is common to both and cancels — applying it would shove
    // the click off by the whole calibration. We reuse the SAME av-sync formula
    // (so the engine's output-buffer latency is handled identically) but with
    // avOffset 0; GridMetronome then compensates only its own WebAudio buffer.
    const audibleAudioSec = audiblePlayheadSec({
      nativePosSec: raw,
      outputLatencySec: outLatency,
      playbackRate,
      avOffsetSec: 0,
    });
    metronome.update(audibleAudioSec, true, playbackRate);
    rafId = requestAnimationFrame(tick);
  }

  function playTransport(): void {
    if (isPlaying || durationSec <= 0) return;
    let start = resumePositionSec ?? playheadSec;
    resumePositionSec = null;
    if (start >= durationSec) { start = 0; setPlayhead(0, true); }
    if (loopRegion && (start < loopRegion.t0 || start >= loopRegion.t1)) { start = loopRegion.t0; setPlayhead(start, true); }
    playStartOffset = start;
    playStartWall = performance.now();
    isPlaying = true;
    gamePreview.setTime(playheadSec, isPlaying, playbackRate);
    const generation = ++transportGeneration;
    playBtn!.textContent = "⏸ Pause";
    if (audioLoaded) {
      // Seek to the source position; if source start fails, drop to the
      // wall-clock path so the synth + playhead still run.
      sourceAudio.seek(start);
      sourceAudio.setPlaybackRate(playbackRate);
      void sourceAudio.play().then(() => {
        if (isPlaying && generation === transportGeneration && root!.closest(".isActive")) startRenderedNotes();
        else if (!isPlaying) sourceAudio.pause();
      }).catch(() => {
        if (generation !== transportGeneration) return;
        audioLoaded = false;
        // Media startup can take time; re-anchor the wall clock to
        // NOW (not the stale play-start time) so the playhead resumes smoothly
        // from where it sits instead of lurching forward by the stall, and
        // restart the synth from the current playhead so it stays in sync.
        if (isPlaying) {
          playStartOffset = playheadSec;
          playStartWall = performance.now();
          startRenderedNotes();
        }
        setStatus(`Audio couldn't start for ${instrument} — playing notes only`);
      });
    }
    // Drum-mode "pitches" are lane indices — the melodic synth can't audition
    // them, so drum playback relies on the real drums stem alone.
    if (!audioLoaded) startRenderedNotes();
    rafId = requestAnimationFrame(tick);
  }

  function pauseTransport(): void {
    if (isPlaying) resumePositionSec = transportClock();
    transportGeneration += 1;
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    if (audioLoaded) sourceAudio.pause();
    audition.stop();
    metronome.stop();
    isPlaying = false;
    gamePreview.setTime(playheadSec, isPlaying, playbackRate);
    playBtn!.textContent = "▶ Play";
  }

  function stopTransport(): void {
    pauseTransport();
    resumePositionSec = null;
    if (audioLoaded) sourceAudio.seek(0);
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
    resumePositionSec = playheadSec;
    if (audioLoaded) sourceAudio.seek(playheadSec);
    if (wasPlaying) playTransport();
  }

  function setTransportEnabled(enabled: boolean): void {
    playBtn!.disabled = !enabled;
    stopBtn!.disabled = !enabled;
    scrubEl!.disabled = !enabled;
  }

  // -------------------------------------------------------------------------
  // Empty / loaded state
  // -------------------------------------------------------------------------

  function showEmpty(empty: boolean, reason?: "candidates" | "spectrogram" | "drumtab" | "error", detail?: string): void {
    emptyEl!.style.display = empty ? "flex" : "none";
    stageEl!.style.display = empty ? "none" : "block";
    mainEl.hidden = empty;
    splitToggle.disabled = empty;
    applySplitMode();
    transportEl!.style.display = empty ? "none" : "flex";
    inspectorEl!.style.display = empty ? "none" : "flex";
    if (listenEl) listenEl.hidden = empty;
    const editbar = $<HTMLElement>("refineEditbar");
    if (editbar) editbar.hidden = empty;
    if (!empty) return;
    const h3 = emptyEl!.querySelector("h3");
    if (reason === "spectrogram") {
      if (h3) h3.textContent = "No spectrogram for this stem";
      emptyCmdEl!.textContent = `aural_ingest spectrogram "${containerPath ?? "<song>"}" --instrument ${instrument}`;
    } else if (reason === "drumtab") {
      if (h3) h3.textContent = "No drum chart in this pack";
      emptyCmdEl!.textContent = detail ?? "This pack has no drum_tab.json — reimport with a drums stem to chart drums.";
    } else if (reason === "error") {
      if (h3) h3.textContent = "Failed to load";
      emptyCmdEl!.textContent = detail ?? "";
    } else {
      if (h3) h3.textContent = "No candidates yet";
      emptyCmdEl!.textContent = `aural_ingest refine-candidates "${containerPath ?? "<song>"}" --instrument ${instrument}`;
    }
  }

  // -------------------------------------------------------------------------
  // Spectrogram + notes load
  // -------------------------------------------------------------------------

  async function loadSpectrogramIntoEditor(notes: SpectroNote[]): Promise<boolean> {
    if (!containerPath) return false;
    const role = instrument;
    // feedpak/sloppak relocate spectrogram artifacts under aural/ (legacy: features/).
    const fd = packFeatureDir(containerPath);
    try {
      const geom = (await invoke("read_auralsong_json", {
        containerPath,
        relPath: `${fd}/spectrogram/${role}/spectrogram.json`,
      })) as SpectrogramGeometry;
      if (!geom || !Array.isArray(geom.tiles) || geom.tiles.length === 0) return false;
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
      await editor.load(geom, urls, notes);
      durationSec = editor.getDurationSec();
      await applyBeatGrid();
      syncGameNotes();
      return true;
    } catch {
      return false;
    }
  }

  // Push the current quant-dropdown level into the editor (null = Off).
  function applyQuant(): void {
    const level = quantSelect ? quantLevelByValue(quantSelect.value) : undefined;
    editor.setQuant(level ? level.perBeat : null);
  }

  // The loaded beat grid + the current downbeat-phase nudge. We keep the raw
  // beats so the ◀▶ control can re-derive the accented downbeats without a reload.
  let loadedBeats: Array<{ time?: number; measure?: number }> = [];
  let loadedBeatTimes: number[] = [];
  let beatsPerBar = 4;
  let gameTimeSignature: [number, number] = [4, 4];
  let downbeatOffset = 0;

  // Push the current beats + phase-shifted downbeats to BOTH the editor overlay
  // and the metronome, so the bar lines and the accent always agree.
  function refreshGrid(): void {
    const downbeats = downbeatTimesShifted(loadedBeats, downbeatOffset);
    editor.setBeatGrid(loadedBeatTimes, downbeats);
    gamePreview.setBeatGrid(loadedBeatTimes, downbeats, gameTimeSignature);
    metronome.setBeats(loadedBeatTimes, downbeats);
  }

  // Load the pack's beats into the editor's grid, following manifest pointers,
  // then apply the current quant level. No timeline -> no grid (snap becomes inert).
  async function applyBeatGrid(): Promise<void> {
    if (!containerPath) return;
    try {
      const tl = (await invoke("read_auralsong_json", {
        containerPath,
        relPath: songTimelineRelPath,
      })) as {
        beats?: Array<{ time?: number; measure?: number }>;
        time_signatures?: Array<{ ts?: number[] }>;
      };
      // Use the DETECTED beats directly: measured against drum onsets they track
      // the audio to ~12-23 ms, whereas an even/regularized grid drifts 50-80 ms
      // (and worsens over the song) because real tracks have natural micro-timing
      // a single tempo can't match. Downbeats are the detected measure-starts,
      // rotatable by the ◀▶ nudge when the tracker put "the one" on the wrong beat.
      loadedBeats = Array.isArray(tl?.beats) ? tl.beats : [];
      loadedBeatTimes = loadedBeats
        .map((b) => b?.time)
        .filter((t): t is number => typeof t === "number");
      beatsPerBar = beatsPerBarFromTimeSignatures(tl?.time_signatures);
      const ts = tl?.time_signatures?.[0]?.ts;
      gameTimeSignature = ts?.length === 2 && ts.every((n) => Number.isFinite(n) && n > 0)
        ? [ts[0], ts[1]] : [4, 4];
      downbeatOffset = 0;
      refreshGrid();
    } catch {
      loadedBeats = [];
      loadedBeatTimes = [];
      downbeatOffset = 0;
      editor.setBeatGrid([], []);
      metronome.setBeats([], []);
      gameTimeSignature = [4, 4];
      gamePreview.setBeatGrid([], [], gameTimeSignature);
    }
    applyQuant();
  }

  // Rotate which beat carries the accent + bar line (measure phase) by `delta`
  // beats, wrapped within a bar. Updates the overlay and metronome immediately.
  function nudgeDownbeat(delta: number): void {
    if (loadedBeatTimes.length === 0) return;
    const n = beatsPerBar > 0 ? beatsPerBar : 4;
    downbeatOffset = (((downbeatOffset + delta) % n) + n) % n;
    refreshGrid();
  }

  // -------------------------------------------------------------------------
  // Audio — asset-streamed source with shared note-audition routing.
  // -------------------------------------------------------------------------

  /**
   * Load the current instrument's stem (solo) or the summed base stems ("All")
   * through an asset URL. Rust prepares zip/multi-stem audio in a cache (no
   * multi-MB IPC). A miss leaves audioLoaded false ->
   * wall-clock + synth fallback.
   */
  async function loadAudioForMode(): Promise<void> {
    const gen = ++loadGeneration;
    const stale = (): boolean => gen !== loadGeneration;
    audioLoaded = false;
    editor.setSourceWaveform(null);
    if (!containerPath) return;
    // Capture the mode/role NOW so an in-flight load isn't corrupted by a
    // concurrent instrument/solo switch mutating the module state mid-await.
    const path = containerPath;
    const wantSolo = soloMode;
    const loadWaveform = (role: string | null): void => {
      void invoke<RefineWaveform>("get_refine_waveform", { containerPath: path, role }).then((waveform) => {
        if (!stale() && waveform && Array.isArray(waveform.peaks)) editor.setSourceWaveform(waveform);
      }).catch(() => { /* Source playback and spectrogram remain usable if preview decoding fails. */ });
    };
    const inst = instrument;
    // Drum mode auditions the drums stem; melodic uses the picked role. "All"
    // sums the base stems Rust-side (derived guitar splits are excluded there).
    const primaryRole = wantSolo ? inst : "all";
    // If the primary can't load, try the other: a silent/absent solo stem
    // (e.g. a band song's empty keys) falls to the full mix; a mix that can't
    // build falls to the instrument's own stem — so there's always audio.
    const fallbackRole = wantSolo ? "all" : inst;
    const fallbackMsg = wantSolo
      ? `No isolated ${inst} stem — playing the full mix`
      : `No mix for this song — playing the ${inst} stem`;
    try {
      await sourceAudio.loadPackAudio(path, primaryRole);
      if (!stale()) { audioLoaded = true; sourceAudio.setPlaybackRate(playbackRate); loadWaveform(primaryRole); }
    } catch {
      if (stale()) return;
      try {
        await sourceAudio.loadPackAudio(path, fallbackRole);
        if (stale()) return;
        audioLoaded = true;
        sourceAudio.setPlaybackRate(playbackRate);
        loadWaveform(fallbackRole);
        setStatus(fallbackMsg);
      } catch {
        if (!stale()) audioLoaded = false;
      }
    }
  }

  /** Reload the audio after a Solo/All switch, resuming playback if it was on. */
  async function switchAudioSource(): Promise<void> {
    const wasPlaying = isPlaying;
    if (wasPlaying) pauseTransport();
    const resumeGeneration = transportGeneration;
    setTransportEnabled(false);
    const generation = loadGeneration + 1;
    await loadAudioForMode();
    if (generation !== loadGeneration) return;
    setTransportEnabled(durationSec > 0);
    if (wasPlaying && resumeGeneration === transportGeneration && root!.closest(".isActive")) playTransport();
  }

  // -------------------------------------------------------------------------
  // Save (partition the edited set back into per-region manual decisions)
  // -------------------------------------------------------------------------

  async function save(): Promise<void> {
    // Drum mode: convert the edited lane hits back into the pack's drum_tab
    // artifact (preserving version/name/kit). Runtime charts straight from
    // that file, so the edit lands with no further pipeline step.
    if (drumMode) {
      if (!containerPath || !drumTabOriginal) return;
      const tab = notesToTab(editor.getNotes(), drumLanes, drumTabOriginal);
      try {
        await invoke<void>("write_auralsong_features_json", {
          containerPath,
          relPath: drumTabRelPath,
          value: tab,
        });
        drumTabOriginal = tab;
        setDirty(false);
        setStatus(`Saved ${tab.hits?.length ?? 0} drum hits`);
      } catch (e) {
        setStatus(`Save failed: ${String(e)}`);
      }
      return;
    }
    if (!session) return;
    if (regionsSorted.length === 0) {
      setStatus("Nothing to save (no regions).");
      return;
    }
    const notes = editor.getNotes();
    const now = new Date().toISOString();
    for (const region of session.candidates.regions) {
      session.decisions.set(region.id, {
        region_id: region.id,
        candidate_id: "manual",
        notes: [],
        edited_at: now,
      });
    }
    for (const n of notes) {
      const region = regionForTime(n.t_on);
      if (!region) continue;
      session.decisions.get(region.id)?.notes.push({
        t_on: n.t_on, t_off: n.t_off, pitch: n.pitch,
        velocity: typeof n.velocity === "number" ? n.velocity : 100,
      });
    }
    try {
      await saveDecisions(session);
      setDirty(false);
      setStatus(`Saved ${notes.length} notes for ${instrument}`);
    } catch (e) {
      setStatus(`Save failed: ${String(e)}`);
    }
  }

  // -------------------------------------------------------------------------
  // Public entry
  // -------------------------------------------------------------------------

  /** Reflect the active `instrument` in the dropdown + label. */
  function syncInstrumentControl(): void {
    instSelectEl!.value = instrument;
    instLabelEl!.textContent = instSelectEl!.options[instSelectEl!.selectedIndex]?.text ?? instrument;
  }

  /**
   * Rebuild the instrument dropdown to exactly the given roles.
   *
   * `needsPrecompute` roles are listed but flagged: they are openable (the
   * editor shows a precise "precompute first" empty state), and hiding them
   * outright made a freshly-imported song look like it had transcribed
   * nothing but drums.
   */
  function populateInstrumentOptions(roles: string[], needsPrecompute?: Set<string>): void {
    instSelectEl!.innerHTML = roles
      .map((r) => {
        const label = INSTRUMENT_LABELS[r] ?? r;
        const suffix = needsPrecompute?.has(r) ? " — needs precompute" : "";
        return `<option value="${r}">${label}${suffix}</option>`;
      })
      .join("");
  }

  async function openForAuralSong(path: string, opts?: { instrument?: string }): Promise<void> {
    midiMonitor.reset();
    loopRegion = null;
    loopBtn?.setAttribute("aria-pressed", "false");
    containerPath = path;
    gamePreview.setVisible(false);
    gamePreview.clear();
    songTitleEl!.textContent = songDisplayName(path);
    songTitleEl!.title = path; // full path on hover
    setDirty(false);
    stopTransport();
    setTransportEnabled(false);

    // The dropdown offers this song's editable instruments: every melodic role
    // the pack actually carries, plus Drums when it has a drum chart (which
    // opens the lane editor instead of the candidates flow).
    //
    // A spectrogram alone is enough to list a role. Requiring CANDIDATES here
    // was stricter than detectMelodicStems' own notion of "present"
    // (spectrogram || candidates), and candidates only exist after the
    // separate precompute step — so a freshly imported song offered nothing
    // but Drums and read as "the transcription only produced drums", even
    // with bass and guitar sitting right there in the pack.
    let available: string[] = [];
    const needsPrecompute = new Set<string>();
    let primary = "keys";
    try {
      const det = await detectMelodicStems(path);
      primary = det.primary;
      available = MELODIC_PICK_ORDER.filter((r) => {
        const readiness = det.readiness.get(r);
        if (!readiness?.candidates && !readiness?.spectrogram) return false;
        if (!readiness.candidates) needsPrecompute.add(r);
        return true;
      });
      drumTabRelPath = await resolveDrumTabRelPath(path);
      songTimelineRelPath = await resolveSongTimelineRelPath(path);
    } catch {
      // Detection failed — leave the static option set + current instrument.
      drumTabRelPath = "drum_tab.json";
      songTimelineRelPath = "song_timeline.json";
    }
    try {
      if (await auralsongJsonExists(path, drumTabRelPath)) available.push("drums");
    } catch {
      /* no drum option on probe failure */
    }
    if (available.length) populateInstrumentOptions(available, needsPrecompute);

    if (opts?.instrument && (!available.length || available.includes(opts.instrument))) {
      instrument = opts.instrument as RefinementInstrument;
    } else if (available.includes(primary)) {
      instrument = primary as RefinementInstrument;
    } else if (available.length) {
      instrument = available[0] as RefinementInstrument;
    }
    syncInstrumentControl();
    // "Snap to onsets" only applies to the drum lane editor.
    if (snapBtn) snapBtn.style.display = instrument === "drums" ? "inline-block" : "none";

    // ---- Drum mode: lane editor over the drums spectrogram --------------
    if (instrument === "drums") {
      drumMode = true;
      applyListening();
      session = null;
      regionsSorted = [];
      setStatus(`Loading drum chart from ${path}…`);
      try {
        const tab = (await invoke("read_auralsong_json", {
          containerPath: path,
          relPath: drumTabRelPath,
        })) as DrumTabFile;
        if (!tab || !Array.isArray(tab.hits)) {
          showEmpty(true, "drumtab");
          setStatus("No drum chart in this pack");
          return;
        }
        drumTabOriginal = tab;
        drumLanes = laneOrderForTab(tab);
        editor.setLaneMode(drumLanes);
        const ok = await loadSpectrogramIntoEditor(hitsToNotes(tab, drumLanes));
        if (!ok) {
          showEmpty(true, "spectrogram");
          setStatus("No drums spectrogram — build it first (Prep all unbuilt, or the command below)");
          return;
        }
        showEmpty(false);
        updateUndoButtons();
        buildSections();
        selectedIndex = null;
        renderInspector(null);
        setPlayhead(0, false);
        sectionSelectEl!.value = "";
        await loadAudioForMode();
        setTransportEnabled(durationSec > 0);
        const audioNote = audioLoaded ? "" : " (no stem audio)";
        setStatus(`Loaded ${editor.getNotes().length} drum hits across ${drumLanes.length} lanes${audioNote}`);
      } catch (e) {
        showEmpty(true, "error", String(e));
        setStatus(`Failed to load drum chart: ${String(e)}`);
      }
      return;
    }
    drumMode = false;
    applyListening();
    drumTabOriginal = null;
    drumLanes = [];
    editor.setLaneMode(null);

    setStatus(`Loading ${path}…`);
    try {
      const loaded = await loadSession(path, instrument);
      if (!loaded) {
        session = null;
        regionsSorted = [];
        showEmpty(true, "candidates");
        setStatus(`No refine_candidates.${instrument}.json yet — precompute first`);
        return;
      }
      session = loaded;
      regionsSorted = [...loaded.candidates.regions].sort((a, b) => a.t_start - b.t_start);

      const ok = await loadSpectrogramIntoEditor(assembleFullNotes(session));
      if (!ok) {
        showEmpty(true, "spectrogram");
        setStatus(`No spectrogram for ${instrument} — build it first`);
        return;
      }
      showEmpty(false);
      updateUndoButtons();
      buildSections();
      selectedIndex = null;
      renderInspector(null);
      setPlayhead(0, false);
      sectionSelectEl!.value = "";
      // Load the instrument stem. Best effort: a miss
      // leaves audioLoaded false and the transport runs on the wall clock + synth.
      await loadAudioForMode();
      setTransportEnabled(durationSec > 0);
      const noteCount = editor.getNotes().length;
      const audioNote = audioLoaded ? "" : " (no stem audio — notes only)";
      setStatus(`Loaded ${noteCount} notes across ${loaded.candidates.regions.length} regions for ${instrument}${audioNote}`);
    } catch (e) {
      session = null;
      regionsSorted = [];
      showEmpty(true, "error", String(e));
      setStatus(`Failed to load refine session: ${String(e)}`);
    }
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------

  instSelectEl.addEventListener("change", () => {
    // Explicit user choice — load that instrument, don't auto-redetect.
    if (containerPath) void openForAuralSong(containerPath, { instrument: instSelectEl.value || "keys" });
  });
  reloadBtn.addEventListener("click", () => {
    // Reload keeps whatever instrument is currently selected.
    if (containerPath) void openForAuralSong(containerPath, { instrument });
  });
  saveBtn.addEventListener("click", () => void save());
  snapBtn?.addEventListener("click", () => void snapDrumOnsets());
  refreshMeterBtn?.addEventListener("click", () => void refreshMeter());
  quantSelect?.addEventListener("change", () => applyQuant());
  backBtn.addEventListener("click", () => { stopTransport(); deps.onBack(); });

  // Refine the drum-hit TIMES onto the real drums-stem transients (mr_mt3's
  // onsets wobble by tens of ms). Persists current edits, runs the sidecar's
  // onset alignment, and reloads the result — setNotes snapshots the pre-snap
  // hits so a single Undo reverts it.
  async function snapDrumOnsets(): Promise<void> {
    if (!drumMode || !containerPath || !drumTabOriginal || !snapBtn) return;
    const label = snapBtn.textContent;
    snapBtn.disabled = true;
    snapBtn.textContent = "Snapping…";
    setStatus("Snapping drum hits onto the audio transients…");
    try {
      const cur = notesToTab(editor.getNotes(), drumLanes, drumTabOriginal);
      await invoke<void>("write_auralsong_features_json", {
        containerPath, relPath: drumTabRelPath, value: cur,
      });
      const res = await invoke<{ stdout?: string }>("ingest_align_drum_onsets", {
        req: { container_path: containerPath },
      });
      const aligned = (await invoke("read_auralsong_json", {
        containerPath, relPath: drumTabRelPath,
      })) as DrumTabFile;
      drumTabOriginal = aligned;
      editor.setNotes(hitsToNotes(aligned, drumLanes)); // undoable (snapshots current)
      syncGameNotes();
      setDirty(false);
      updateUndoButtons();
      let moved: number | undefined;
      try {
        moved = JSON.parse((res?.stdout ?? "").trim().split(/\r?\n/).pop() ?? "{}").moved;
      } catch {
        /* status line not parseable — still applied */
      }
      setStatus(
        moved != null
          ? `Snapped ${moved} hit${moved === 1 ? "" : "s"} onto onsets — Undo to revert`
          : "Snapped hits onto onsets — Undo to revert",
      );
    } catch (e) {
      setStatus(`Snap to onsets failed: ${String(e)}`);
    } finally {
      snapBtn.disabled = false;
      snapBtn.textContent = label ?? "Snap to onsets";
    }
  }

  // Re-track beats/downbeats/meter (Beat This!) on this pack and reload the
  // grid from the rewritten song_timeline.json (the sidecar backs the old one
  // up to .bak). Instrument-agnostic — unlike Snap it only touches the timeline,
  // so it's always available. The new grid can move beats, which desyncs edits
  // anchored to the old grid; the returned warning is surfaced to the user.
  async function refreshMeter(): Promise<void> {
    if (!containerPath || !refreshMeterBtn) return;
    const label = refreshMeterBtn.textContent;
    refreshMeterBtn.disabled = true;
    refreshMeterBtn.textContent = "Refreshing…";
    setStatus("Re-tracking the beat grid + meter…");
    try {
      const res = await invoke<{ stdout?: string }>("ingest_refresh_meter", {
        req: { container_path: containerPath },
      });
      let info: {
        ok?: boolean;
        bpm?: number;
        time_signature?: string;
        error?: string;
        meter_denominator_provisional?: boolean;
      } = {};
      try {
        info = JSON.parse((res?.stdout ?? "").trim().split(/\r?\n/).pop() ?? "{}");
      } catch {
        /* status line not parseable — reload anyway, it may have written */
      }
      if (info.ok === false) {
        // Pack left unchanged (e.g. no checkpoint) — nothing to reload.
        setStatus(`Refresh meter: ${info.error ?? "meter model unavailable"}`);
        return;
      }
      await applyBeatGrid(); // re-reads the rewritten song_timeline.json
      // NOTE: do NOT touch the dirty flag here — refresh-meter only re-tracks the
      // grid (song_timeline.json, written by the sidecar). Any unsaved editor
      // note/hit edits are untouched, so clearing dirty would hide the "Save *"
      // indicator and mislead the user into thinking their edits were persisted.
      const ts = info.time_signature ? ` ${info.time_signature}` : "";
      const prov = info.meter_denominator_provisional ? " (denominator provisional)" : "";
      setStatus(
        info.bpm != null
          ? `Meter refreshed — ${info.bpm} bpm${ts}${prov} · old grid saved to .bak`
          : "Meter refreshed · old grid saved to .bak",
      );
    } catch (e) {
      setStatus(`Refresh meter failed: ${String(e)}`);
    } finally {
      refreshMeterBtn.disabled = false;
      refreshMeterBtn.textContent = label ?? "Refresh meter";
    }
  }

  playBtn.addEventListener("click", () => toggleTransport());
  stopBtn.addEventListener("click", () => stopTransport());
  undoBtn.addEventListener("click", () => { editor.undo(); updateUndoButtons(); });
  redoBtn.addEventListener("click", () => { editor.redo(); updateUndoButtons(); });
  speedSelectEl.addEventListener("change", () => {
    const r = Number(speedSelectEl.value);
    if (!Number.isFinite(r) || r <= 0) return;
    // Re-anchor the wall clock so the playhead doesn't jump on a rate change.
    if (isPlaying) { playStartOffset = playheadSec; playStartWall = performance.now(); }
    playbackRate = r;
    if (audioLoaded) sourceAudio.setPlaybackRate(r);
    gamePreview.setTime(playheadSec, isPlaying, playbackRate);
    if (speedOutput) speedOutput.value = `${Math.round(r * 100)}%`;
    root.querySelectorAll<HTMLButtonElement>("[data-refine-speed]").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.refineSpeed) === r));
    });
    if (isPlaying) { audition.stop(); startRenderedNotes(); }
  });
  speedSelectEl.addEventListener("input", () => speedSelectEl.dispatchEvent(new Event("change")));
  root.querySelectorAll<HTMLButtonElement>("[data-refine-speed]").forEach((button) => {
    button.addEventListener("click", () => {
      speedSelectEl.value = button.dataset.refineSpeed!;
      speedSelectEl.dispatchEvent(new Event("change"));
    });
  });
  root.querySelectorAll<HTMLButtonElement>("[data-listen-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      listenMode = button.dataset.listenMode as ListenMode;
      applyListening();
      if (isPlaying) { audition.stop(); startRenderedNotes(); }
    });
  });
  swapBtn?.addEventListener("click", () => { swapped = !swapped; applyListening(); });
  sourceLevel?.addEventListener("input", applyListening);
  renderedLevel?.addEventListener("input", applyListening);
  hearSelectionBtn?.addEventListener("click", auditionSelected);
  function auditionSelected(): void {
    const note = selectedIndex == null ? undefined : editor.getNotes()[selectedIndex];
    if (note && !drumMode) audition.playNote(note.pitch, note.velocity, note.t_off - note.t_on);
  }
  loopSelectionBtn?.addEventListener("click", () => {
    const note = selectedIndex == null ? undefined : editor.getNotes()[selectedIndex];
    if (!note) return;
    loopRegion = { t0: Math.max(0, note.t_on - 0.5), t1: Math.min(durationSec, note.t_off + 0.5) };
    loopBtn?.setAttribute("aria-pressed", "true");
    seekTo(loopRegion.t0);
    setStatus(`Looping ${formatTime(loopRegion.t0)}–${formatTime(loopRegion.t1)}`);
  });
  loopBtn?.addEventListener("click", () => {
    if (loopRegion) loopRegion = null;
    else {
      const view = editor.getViewTimeWindow();
      loopRegion = { t0: Math.max(0, view.start), t1: Math.min(durationSec, view.end) };
      if (loopRegion.t1 <= loopRegion.t0) loopRegion = null;
    }
    loopBtn.setAttribute("aria-pressed", String(loopRegion !== null));
    if (isPlaying) seekTo(loopRegion?.t0 ?? playheadSec);
  });
  applyListening();
  soloSelectEl.addEventListener("change", () => {
    soloMode = soloSelectEl.value !== "all";
    void switchAudioSource();
  });
  // Scrub: move the playhead visually while dragging; commit (and resync synth)
  // on release so we don't restart the audition every input event.
  scrubEl.addEventListener("input", () => {
    const t = (Number(scrubEl.value) / 1000) * durationSec;
    if (isPlaying) pauseTransport();
    setPlayhead(t, true);
  });
  scrubEl.addEventListener("change", () => {
    seekTo((Number(scrubEl.value) / 1000) * durationSec);
  });
  sectionSelectEl.addEventListener("change", () => {
    const v = sectionSelectEl.value;
    if (v === "") { editor.resetView(); return; }
    const t = Number(v);
    editor.setViewTimeWindow(t, Math.min(durationSec || t + 16, t + 16));
  });
  auditionToggle.addEventListener("change", () => {
    if (!isPlaying) return;
    if (auditionToggle.checked) startRenderedNotes();
    else audition.stop();
  });
  metronomeToggle.addEventListener("change", () => {
    metronome.setEnabled(metronomeToggle.checked);
  });
  downbeatMinusBtn.addEventListener("click", () => nudgeDownbeat(-1));
  downbeatPlusBtn.addEventListener("click", () => nudgeDownbeat(1));

  // Cleanup owns transport keys regardless of focus. Capture before a focused
  // button, checkbox, slider, or select can consume/activate it instead.
  window.addEventListener("keydown", (ev) => {
    if (!root.isConnected || !root.closest(".isActive")) return;
    const isSpace = ev.key === " " || ev.code === "Space";
    const direction = ev.key === "ArrowLeft" || ev.key === "ArrowDown" ? -1
      : ev.key === "ArrowRight" || ev.key === "ArrowUp" ? 1 : 0;
    if (!isSpace && direction === 0) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    if (isSpace) {
      if (!ev.repeat) toggleTransport();
    } else if (durationSec > 0) {
      // Use the live clock while playing, and the exact resume point while
      // paused. Held arrows deliberately repeat to scrub through the song.
      const position = isPlaying ? transportClock() : resumePositionSec ?? playheadSec;
      const step = ev.key === "ArrowUp" || ev.key === "ArrowDown" ? 30 : ev.shiftKey ? 1 : 5;
      seekTo(position + direction * step);
    }
  }, { capture: true });

  // Other shortcuts keep their field-editing behavior. Delete/Escape/zoom
  // are handled by the editor stage.
  window.addEventListener("keydown", (ev) => {
    if (!root.isConnected || !root.closest(".isActive")) return;
    if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) return;
    if (ev.key === "Enter" && !(ev.target instanceof HTMLButtonElement)) { ev.preventDefault(); auditionSelected(); }
    if (!ev.ctrlKey && !ev.altKey && !ev.metaKey && ["a", "b", "x"].includes(ev.key.toLowerCase())) {
      listenMode = ({ a: "source", b: "rendered", x: "split" } as const)[ev.key.toLowerCase() as "a" | "b" | "x"];
      applyListening();
      if (isPlaying) { audition.stop(); startRenderedNotes(); }
    }
  });

  return {
    openForAuralSong,
    getCurrentContainerPath: () => containerPath,
  };
}

function escapeHtml(s: string): string {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

/**
 * A readable song name for the top bar from a pack path: drop the directory
 * and the `.feedpak`/`.sloppak`/`.auralsong` extension, strip the `ingest_`
 * prefix, and turn underscores into spaces. Delegates to the shared
 * `packDisplayName` helper (C2) so the extension list stays in one place.
 */
function songDisplayName(path: string): string {
  return packDisplayName(path);
}
