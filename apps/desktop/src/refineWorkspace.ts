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
 *  - Transport plays the ISOLATED STEM audio for the current instrument,
 *    streamed off disk via Tauri's asset protocol (convertFileSrc -> an
 *    <audio> element), so the user hears the real recording while editing.
 *    When a stem is loaded, audio.currentTime drives the playhead clock;
 *    when none loads (zip container, missing file, decode error) the
 *    transport falls back to the wall-clock playhead. The "Hear notes"
 *    synth (refineAudition) plays ALONGSIDE the stem when checked, so the
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
import { getAvOffsetSec } from "./avOffset";
import { detectMelodicStems } from "./cleanupReadiness";
import type { RefinementNote } from "./refineCandidatesIo";
import { invoke, convertFileSrc } from "@tauri-apps/api/core";
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
  const backBtn = $<HTMLElement>("refineBack");
  const transportEl = $<HTMLElement>("refineTransport");
  const playBtn = $<HTMLButtonElement>("refinePlayBtn");
  const stopBtn = $<HTMLButtonElement>("refineStopBtn");
  const timeReadoutEl = $<HTMLElement>("refineTimeReadout");
  const scrubEl = $<HTMLInputElement>("refineScrub");
  const sectionSelectEl = $<HTMLSelectElement>("refineSectionSelect");
  const auditionToggle = $<HTMLInputElement>("refineAuditionToggle");
  const editAuditionToggle = $<HTMLInputElement>("refineEditAuditionToggle");
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
    || !sectionSelectEl || !auditionToggle || !editAuditionToggle || !stageEl || !inspectorEl || !selInfoEl
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

  // transport
  //  - When a stem is loaded, `audio` plays the real recording and its
  //    `currentTime` is the playhead clock (driven via rAF).
  //  - When no stem loads, we fall back to the wall-clock playhead below.
  //  - refineAudition (the "Hear notes" synth) plays alongside either path.
  let isPlaying = false;
  let playStartOffset = 0; // playhead position (sec) when play began (wall-clock path)
  let playStartWall = 0; // performance.now() when play began (wall-clock path)
  let playheadSec = 0;
  let rafId: number | null = null;
  let playbackRate = 1; // transport speed multiplier
  let soloMode = true; // true = play the current instrument's isolated stem; false = full mix

  // Isolated-stem playback. The element lives outside the DOM template — it
  // never needs to be visible, just to decode + play the asset:// stream.
  const audio = new Audio();
  audio.preload = "auto";
  let stemLoaded = false; // true once a stem src has loaded (metadata) for the current instrument
  audio.addEventListener("ended", () => stopTransport());
  audio.addEventListener("error", () => {
    // The stem failed to decode/stream: drop back to the wall-clock + synth
    // path so the editor stays usable, and tell the user why there's no audio.
    if (stemLoaded) setStatus(`Stem audio failed to load for ${instrument} — playing notes only`);
    stemLoaded = false;
  });

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
    // they're putting down (gated by the "Audition edits" toggle).
    onNoteAuditioned: (note) => {
      if (editAuditionToggle!.checked) audition.playNote(note.pitch, note.velocity);
    },
    // Sonic-Visualiser-style cursor readout: show the pitch + time under the
    // cursor in the inspector whenever no note is actively selected.
    onHover: (info) => {
      if (selectedIndex != null) return;
      if (!info) {
        selInfoEl!.textContent = "No note selected";
        return;
      }
      selInfoEl!.innerHTML =
        `<span class="rfSelPitch">${pitchLabel(info.pitch)}</span> · ${formatTime(info.time)}`
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
    // Clock source: the stem's own playback position when a stem is loaded,
    // otherwise the wall clock. Keeping the playhead on audio.currentTime
    // keeps the spectrogram glued to what the user actually hears.
    const raw = stemLoaded
      ? audio.currentTime
      : playStartOffset + ((performance.now() - playStartWall) / 1000) * playbackRate;
    if (raw >= durationSec) {
      setPlayhead(durationSec, false);
      pauseTransport();
      return;
    }
    // A/V sync offset: the user hears the output `avOffset` after the clock, so
    // draw the cursor at the position that's actually audible right now (same
    // calibration the game applies to its falling notes). End-of-song uses the
    // raw clock above so a positive offset doesn't cut playback short.
    setPlayhead(raw - getAvOffsetSec(), true);
    rafId = requestAnimationFrame(tick);
  }

  function playTransport(): void {
    if (isPlaying || durationSec <= 0) return;
    if (playheadSec >= durationSec) setPlayhead(0, true);
    playStartOffset = playheadSec;
    playStartWall = performance.now();
    isPlaying = true;
    playBtn!.textContent = "⏸";
    if (stemLoaded) {
      // Seek the stem to the playhead and start it; if play() rejects (autoplay
      // policy, transient decode failure) fall back to the wall-clock path so
      // the synth + playhead still run.
      audio.currentTime = playheadSec;
      audio.playbackRate = playbackRate;
      audio.play().catch(() => {
        stemLoaded = false;
        setStatus(`Stem audio couldn't start for ${instrument} — playing notes only`);
      });
    }
    if (auditionToggle!.checked) audition.playRegion(toRefinementNotes(editor.getNotes()), playheadSec, durationSec);
    rafId = requestAnimationFrame(tick);
  }

  function pauseTransport(): void {
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    try { audio.pause(); } catch { /* ignore */ }
    audition.stop();
    isPlaying = false;
    playBtn!.textContent = "▶";
  }

  function stopTransport(): void {
    pauseTransport();
    if (stemLoaded) { try { audio.currentTime = 0; } catch { /* ignore */ } }
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
    if (stemLoaded) { try { audio.currentTime = playheadSec; } catch { /* ignore */ } }
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

  function showEmpty(empty: boolean, reason?: "candidates" | "spectrogram" | "error", detail?: string): void {
    emptyEl!.style.display = empty ? "flex" : "none";
    stageEl!.style.display = empty ? "none" : "block";
    transportEl!.style.display = empty ? "none" : "flex";
    inspectorEl!.style.display = empty ? "none" : "flex";
    if (!empty) return;
    const h3 = emptyEl!.querySelector("h3");
    if (reason === "spectrogram") {
      if (h3) h3.textContent = "No spectrogram for this stem";
      emptyCmdEl!.textContent = `aural_ingest spectrogram "${containerPath ?? "<song>"}" --instrument ${instrument}`;
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

  async function loadSpectrogramIntoEditor(): Promise<boolean> {
    if (!containerPath || !session) return false;
    const role = instrument;
    // feedpak relocates spectrogram artifacts under aural/ (legacy: features/).
    const fd = containerPath.endsWith(".feedpak") ? "aural" : "features";
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
      await editor.load(geom, urls, assembleFullNotes(session));
      durationSec = editor.getDurationSec();
      return true;
    } catch {
      return false;
    }
  }

  // -------------------------------------------------------------------------
  // Stem audio (asset-protocol stream of the current instrument's stem)
  // -------------------------------------------------------------------------

  type ManifestStem = { id?: string; file?: string; default?: boolean | string };

  /** Truthy "default" per the manifest schema (boolean or true/on/yes string). */
  function isDefaultStem(s: ManifestStem): boolean {
    const d = s.default;
    if (typeof d === "boolean") return d;
    if (typeof d === "string") return /^(true|on|yes)$/i.test(d.trim());
    return false;
  }

  /** Join the container path with a POSIX-relative stem file (forward slashes). */
  function joinContainer(container: string, relFile: string): string {
    const sep = container.includes("\\") ? "\\" : "/";
    const base = container.replace(/[\\/]+$/, "");
    return `${base}${sep}${relFile.replace(/\//g, sep)}`;
  }

  /**
   * Resolve the current instrument's stem to a convertFileSrc URL. Reads the
   * manifest's `stems[]`, matches `id === role`, and falls back to the default
   * stem (or the first) when the role has none. Returns null when the manifest
   * has no usable stem (e.g. a zip container the asset protocol can't reach).
   */
  async function resolveStemUrl(solo: boolean): Promise<string | null> {
    if (!containerPath) return null;
    try {
      const details = await invoke<{ manifest_raw?: { stems?: ManifestStem[] } | null }>(
        "get_auralsong_details",
        { containerPath },
      );
      const stems = details.manifest_raw?.stems ?? [];
      if (!solo) {
        // "All (mix)": prefer an explicit mix/full stem, else the conventional
        // audio/mix.wav. If neither exists the audio element errors and
        // loadStemAudio falls back to the instrument stem.
        const mix = stems.find((s) => /^(mix|mixture|mixdown|full|all)$/i.test(s.id ?? ""));
        if (mix?.file) return convertFileSrc(joinContainer(containerPath, mix.file));
        return convertFileSrc(joinContainer(containerPath, "audio/mix.wav"));
      }
      if (stems.length === 0) return null;
      const match =
        stems.find((s) => s.id === instrument)
        ?? stems.find((s) => isDefaultStem(s))
        ?? stems[0];
      if (!match?.file) return null;
      return convertFileSrc(joinContainer(containerPath, match.file));
    } catch {
      return null;
    }
  }

  /**
   * Point the <audio> element at the current instrument's stem. Resets the
   * loaded flag; `stemLoaded` flips true on the first `loadedmetadata` and the
   * transport then drives the playhead off `audio.currentTime`. A resolve miss
   * or decode error leaves `stemLoaded` false -> wall-clock fallback.
   */
  async function loadStemAudio(): Promise<void> {
    const tryLoad = async (url: string | null): Promise<boolean> => {
      stemLoaded = false;
      try { audio.pause(); } catch { /* ignore */ }
      audio.removeAttribute("src");
      if (!url) return false;
      return new Promise<boolean>((resolve) => {
        const onMeta = (): void => { stemLoaded = true; cleanup(); resolve(true); };
        const onErr = (): void => { stemLoaded = false; cleanup(); resolve(false); };
        const cleanup = (): void => {
          audio.removeEventListener("loadedmetadata", onMeta);
          audio.removeEventListener("error", onErr);
        };
        audio.addEventListener("loadedmetadata", onMeta, { once: true });
        audio.addEventListener("error", onErr, { once: true });
        audio.src = url;
        audio.load();
      });
    };
    const ok = await tryLoad(await resolveStemUrl(soloMode));
    if (!ok && !soloMode) {
      // "All (mix)" had no usable mix — fall back to the instrument's stem.
      const ok2 = await tryLoad(await resolveStemUrl(true));
      if (ok2) setStatus(`No mix for this song — playing the ${instrument} stem`);
    }
  }

  /** Reload the audio after a Solo/All switch, resuming playback if it was on. */
  async function switchAudioSource(): Promise<void> {
    const wasPlaying = isPlaying;
    if (wasPlaying) pauseTransport();
    await loadStemAudio();
    setTransportEnabled(durationSec > 0);
    if (wasPlaying) playTransport();
  }

  // -------------------------------------------------------------------------
  // Save (partition the edited set back into per-region manual decisions)
  // -------------------------------------------------------------------------

  async function save(): Promise<void> {
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
    // melodic roles that actually have candidates. That keeps absent / non-
    // melodic instruments (Drums lives in the drum tab, not candidates) out of
    // the picker, and defaults to a role that will actually load.
    let available: string[] = [];
    let primary = "keys";
    try {
      const det = await detectMelodicStems(path);
      primary = det.primary;
      available = MELODIC_PICK_ORDER.filter((r) => det.readiness.get(r)?.candidates);
    } catch {
      // Detection failed — leave the static option set + current instrument.
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

      const ok = await loadSpectrogramIntoEditor();
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
      // Stream the isolated stem for this instrument (asset protocol). Best
      // effort: a miss leaves stemLoaded false and the transport runs on the
      // wall clock + synth.
      await loadStemAudio();
      const noteCount = editor.getNotes().length;
      const audioNote = stemLoaded ? "" : " (no stem audio — notes only)";
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
  backBtn.addEventListener("click", () => { stopTransport(); deps.onBack(); });

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
    if (stemLoaded) audio.playbackRate = r;
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
 * and the `.feedpak`/`.auralsong` extension, strip the `ingest_` prefix, and
 * turn underscores into spaces. The full path is kept as a hover title.
 */
function songDisplayName(path: string): string {
  const base = path.split(/[\\/]/).pop() ?? path;
  return base
    .replace(/\.(feedpak|auralsong)$/i, "")
    .replace(/^ingest_/, "")
    .replace(/_/g, " ")
    .trim() || base;
}
