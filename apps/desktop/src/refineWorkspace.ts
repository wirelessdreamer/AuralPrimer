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
 *  - Transport plays the ISOLATED STEM audio for the current instrument (or
 *    the summed "All" mix) through the SAME native engine the game uses
 *    (NativeAudioTimebase), so the shared A/V calibration is valid and the
 *    playhead is drawn at the audible position. When audio is loaded the native
 *    engine position drives the playhead clock; when none loads (missing file,
 *    decode error) the transport falls back to the wall-clock playhead. The
 *    "Hear notes" synth (refineAudition) plays ALONGSIDE the stem when checked, so the
 *    user can A/B the transcription against the recording.
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
import { NativeAudioTimebase } from "./nativeAudioTimebase";
import { GridMetronome } from "./gridMetronome";
import { detectMelodicStems, auralsongJsonExists } from "./cleanupReadiness";
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
const MELODIC_PICK_ORDER = ["keys", "bass", "guitar", "lead_guitar", "rhythm_guitar"];
const INSTRUMENT_LABELS: Record<string, string> = {
  keys: "Keys",
  bass: "Bass",
  guitar: "Guitar",
  lead_guitar: "Lead Guitar",
  rhythm_guitar: "Rhythm Guitar",
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
  const speedSelectEl = $<HTMLSelectElement>("refineSpeedSelect");
  const soloSelectEl = $<HTMLSelectElement>("refineSoloSelect");
  const stageEl = $<HTMLElement>("refineStage");
  const inspectorEl = $<HTMLElement>("refineInspector");
  const selInfoEl = $<HTMLElement>("refineSelInfo");
  const candChipsEl = $<HTMLElement>("refineCandChips");
  const emptyEl = $<HTMLElement>("refineEmpty");
  const emptyCmdEl = $<HTMLElement>("refineEmptyCmd");
  const undoBtn = $<HTMLButtonElement>("refineUndoBtn");
  const redoBtn = $<HTMLButtonElement>("refineRedoBtn");

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
  // and edits the pack's drum_tab.json directly (no candidates/regions).
  let drumMode = false;
  let drumLanes: string[] = [];
  let drumTabOriginal: DrumTabFile | null = null;

  // transport
  //  - Audio plays through the SAME native engine as the game (via
  //    NativeAudioTimebase), so the shared A/V calibration is valid here too —
  //    the playhead is drawn at the audible position (native pos − output
  //    latency·rate − calibration offset), exactly as the game's transport does.
  //  - When no audio loads, we fall back to the wall-clock playhead below.
  //  - refineAudition (the "Hear notes" synth) plays alongside either path.
  let isPlaying = false;
  let playStartOffset = 0; // playhead position (sec) when play began (wall-clock path)
  let playStartWall = 0; // performance.now() when play began (wall-clock path)
  let playheadSec = 0;
  let rafId: number | null = null;
  let playbackRate = 1; // transport speed multiplier
  let soloMode = true; // true = play the current instrument's isolated stem; false = full mix

  // Native audio engine (same one the game uses). audioLoaded flips true once a
  // stem/mix is loaded; a load miss leaves it false -> wall-clock fallback.
  const nativeAudio = new NativeAudioTimebase();
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

  // ---- the editor (created once; load() swaps geometry + notes) ----
  const editor = new SpectrogramEditor(stageEl, {
    onNotesChanged: () => {
      setDirty(true);
      updateUndoButtons();
      // Times/pitch of the selected note may have changed during a drag.
      if (selectedIndex != null) renderInspector(selectedIndex);
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
  });

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
    if (follow) editor.scrollTimeIntoView(playheadSec);
    scrubEl!.value = String(durationSec > 0 ? Math.round((playheadSec / durationSec) * 1000) : 0);
    timeReadoutEl!.textContent = `${formatTime(playheadSec)} / ${formatTime(durationSec)}`;
  }

  function tick(): void {
    if (!isPlaying) return;
    // Clock source: the native engine's playback position when audio is loaded,
    // otherwise the wall clock.
    const raw = audioLoaded
      ? nativeAudio.getCurrentTimeSec()
      : playStartOffset + ((performance.now() - playStartWall) / 1000) * playbackRate;
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
    const outLatency = audioLoaded && nativeAudio.getIsPlaying()
      ? (nativeAudio.getOutputLatencySec() ?? 0)
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
    if (playheadSec >= durationSec) setPlayhead(0, true);
    playStartOffset = playheadSec;
    playStartWall = performance.now();
    isPlaying = true;
    playBtn!.textContent = "⏸";
    if (audioLoaded) {
      // Seek to the playhead + start; if native start fails, drop to the
      // wall-clock path so the synth + playhead still run.
      nativeAudio.seek(playheadSec);
      nativeAudio.setPlaybackRate(playbackRate);
      void nativeAudio.play().catch(() => {
        audioLoaded = false;
        // The native start-poll can take seconds; re-anchor the wall clock to
        // NOW (not the stale play-start time) so the playhead resumes smoothly
        // from where it sits instead of lurching forward by the stall, and
        // restart the synth from the current playhead so it stays in sync.
        if (isPlaying) {
          playStartOffset = playheadSec;
          playStartWall = performance.now();
          if (!drumMode && auditionToggle!.checked) {
            audition.playRegion(toRefinementNotes(editor.getNotes()), playheadSec, durationSec);
          }
        }
        setStatus(`Audio couldn't start for ${instrument} — playing notes only`);
      });
    }
    // Drum-mode "pitches" are lane indices — the melodic synth can't audition
    // them, so drum playback relies on the real drums stem alone.
    if (!drumMode && auditionToggle!.checked) audition.playRegion(toRefinementNotes(editor.getNotes()), playheadSec, durationSec);
    rafId = requestAnimationFrame(tick);
  }

  function pauseTransport(): void {
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    if (audioLoaded) nativeAudio.pause();
    audition.stop();
    metronome.stop();
    isPlaying = false;
    playBtn!.textContent = "▶";
  }

  function stopTransport(): void {
    pauseTransport();
    if (audioLoaded) nativeAudio.seek(0);
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
    if (audioLoaded) nativeAudio.seek(playheadSec);
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
    transportEl!.style.display = empty ? "none" : "flex";
    inspectorEl!.style.display = empty ? "none" : "flex";
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
  let downbeatOffset = 0;

  // Push the current beats + phase-shifted downbeats to BOTH the editor overlay
  // and the metronome, so the bar lines and the accent always agree.
  function refreshGrid(): void {
    const downbeats = downbeatTimesShifted(loadedBeats, downbeatOffset);
    editor.setBeatGrid(loadedBeatTimes, downbeats);
    metronome.setBeats(loadedBeatTimes, downbeats);
  }

  // Load the pack's beats (song_timeline.json) into the editor's grid, then
  // apply the current quant level. No timeline -> no grid (snap becomes inert).
  async function applyBeatGrid(): Promise<void> {
    if (!containerPath) return;
    try {
      const tl = (await invoke("read_auralsong_json", {
        containerPath,
        relPath: "song_timeline.json",
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
      downbeatOffset = 0;
      refreshGrid();
    } catch {
      loadedBeats = [];
      loadedBeatTimes = [];
      downbeatOffset = 0;
      editor.setBeatGrid([], []);
      metronome.setBeats([], []);
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
  // Audio — native engine (the SAME one the game uses), so the shared A/V
  // calibration is valid and drum/melodic auditioning is sample-accurate.
  // -------------------------------------------------------------------------

  /**
   * Load the current instrument's stem (solo) or the summed base stems ("All")
   * into the native engine. Rust reads + decodes the pack audio directly (no
   * multi-MB IPC of the old browser path). A miss leaves audioLoaded false ->
   * wall-clock + synth fallback.
   */
  async function loadAudioForMode(): Promise<void> {
    const gen = ++loadGeneration;
    const stale = (): boolean => gen !== loadGeneration;
    audioLoaded = false;
    if (!containerPath) return;
    // Capture the mode/role NOW so an in-flight load isn't corrupted by a
    // concurrent instrument/solo switch mutating the module state mid-await.
    const path = containerPath;
    const wantSolo = soloMode;
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
      await nativeAudio.loadPackAudio(path, primaryRole);
      if (!stale()) audioLoaded = true;
    } catch {
      if (stale()) return;
      try {
        await nativeAudio.loadPackAudio(path, fallbackRole);
        if (stale()) return;
        audioLoaded = true;
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
    await loadAudioForMode();
    setTransportEnabled(durationSec > 0);
    if (wasPlaying) playTransport();
  }

  // -------------------------------------------------------------------------
  // Save (partition the edited set back into per-region manual decisions)
  // -------------------------------------------------------------------------

  async function save(): Promise<void> {
    // Drum mode: convert the edited lane hits back into the pack's root
    // drum_tab.json (preserving version/name/kit) — the game charts straight
    // from that file, so the edit lands with no further pipeline step.
    if (drumMode) {
      if (!containerPath || !drumTabOriginal) return;
      const tab = notesToTab(editor.getNotes(), drumLanes, drumTabOriginal);
      try {
        await invoke<void>("write_auralsong_features_json", {
          containerPath,
          relPath: "drum_tab.json",
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

  /** Rebuild the instrument dropdown to exactly the given roles. */
  function populateInstrumentOptions(roles: string[]): void {
    instSelectEl!.innerHTML = roles
      .map((r) => `<option value="${r}">${INSTRUMENT_LABELS[r] ?? r}</option>`)
      .join("");
  }

  async function openForAuralSong(path: string, opts?: { instrument?: string }): Promise<void> {
    containerPath = path;
    songTitleEl!.textContent = songDisplayName(path);
    songTitleEl!.title = path; // full path on hover
    setDirty(false);
    stopTransport();
    setTransportEnabled(false);

    // The dropdown should offer only this song's editable instruments — the
    // melodic roles that actually have candidates, plus Drums when the pack
    // carries a drum chart (drum_tab.json), which opens the lane editor
    // instead of the candidates flow.
    let available: string[] = [];
    let primary = "keys";
    try {
      const det = await detectMelodicStems(path);
      primary = det.primary;
      available = MELODIC_PICK_ORDER.filter((r) => det.readiness.get(r)?.candidates);
    } catch {
      // Detection failed — leave the static option set + current instrument.
    }
    try {
      if (await auralsongJsonExists(path, "drum_tab.json")) available.push("drums");
    } catch {
      /* no drum option on probe failure */
    }
    if (available.length) populateInstrumentOptions(available);

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
      session = null;
      regionsSorted = [];
      setStatus(`Loading drum chart from ${path}…`);
      try {
        const tab = (await invoke("read_auralsong_json", {
          containerPath: path,
          relPath: "drum_tab.json",
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
        setTransportEnabled(durationSec > 0);
        setPlayhead(0, false);
        sectionSelectEl!.value = "";
        await loadAudioForMode();
        const audioNote = audioLoaded ? "" : " (no stem audio)";
        setStatus(`Loaded ${editor.getNotes().length} drum hits across ${drumLanes.length} lanes${audioNote}`);
      } catch (e) {
        showEmpty(true, "error", String(e));
        setStatus(`Failed to load drum chart: ${String(e)}`);
      }
      return;
    }
    drumMode = false;
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
      setTransportEnabled(durationSec > 0);
      setPlayhead(0, false);
      sectionSelectEl!.value = "";
      // Load the instrument stem into the native engine. Best effort: a miss
      // leaves audioLoaded false and the transport runs on the wall clock + synth.
      await loadAudioForMode();
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
        containerPath, relPath: "drum_tab.json", value: cur,
      });
      const res = await invoke<{ stdout?: string }>("ingest_align_drum_onsets", {
        req: { container_path: containerPath },
      });
      const aligned = (await invoke("read_auralsong_json", {
        containerPath, relPath: "drum_tab.json",
      })) as DrumTabFile;
      drumTabOriginal = aligned;
      editor.setNotes(hitsToNotes(aligned, drumLanes)); // undoable (snapshots current)
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
    if (audioLoaded) nativeAudio.setPlaybackRate(r);
  });
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
    if (auditionToggle.checked) audition.playRegion(toRefinementNotes(editor.getNotes()), playheadSec, durationSec);
    else audition.stop();
  });
  metronomeToggle.addEventListener("change", () => {
    metronome.setEnabled(metronomeToggle.checked);
  });
  downbeatMinusBtn.addEventListener("click", () => nudgeDownbeat(-1));
  downbeatPlusBtn.addEventListener("click", () => nudgeDownbeat(1));

  // Space toggles transport when the refine route is active and we're not
  // typing in a field. (Delete/Escape/zoom are handled by the editor stage.)
  window.addEventListener("keydown", (ev) => {
    if (!root.closest(".isActive")) return;
    if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) return;
    if (ev.key === " " || ev.code === "Space") {
      ev.preventDefault();
      toggleTransport();
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
