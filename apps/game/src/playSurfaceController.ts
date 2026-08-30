/**
 * Play-surface controller — manages the melodic-instrument tab/piano-roll
 * surface that lives below the primary visualizer canvas. Owns the
 * "which melodic track is on screen" decision, the TabRenderer instance,
 * and the layout-class toggles that promote the keys instrument to
 * primary or expand to wide-solo-keys mode.
 *
 * Owns:
 *   - #instrumentSelector, #tabContainer DOM
 *   - playSurfaceEl, appMainEl class toggles (via deps)
 *   - the TabRenderer instance + active instrument role
 *   - per-frame tab render (called from the host's RAF tick)
 *
 * Reads (via dep getters):
 *   - playersPanel handle (preferred melodic role / players list / primary)
 *   - selectedMelodicTracks (live, can change between calls)
 *   - currentRoute
 *
 * Writes back to the host via `onInstrumentSelected` so other consumers
 * can react. Extracted from main.ts as Phase 2.O.
 */

import type { InstrumentRole, MelodicTrackSelection } from "./chartLoader";
import { TabRenderer, SheetMusicRenderer } from "./tabRenderer";
import type { PianoLiveInputNote } from "./tabRenderer";
import type { PlayersPanelHandle } from "./playersPanel";
import type { ConsoleBridge } from "./consoleBridge";

/** Display mode for the melodic play surface. */
export type DisplayMode = "piano" | "sheet";

/**
 * Both renderers expose the same imperative surface, so the controller can
 * hold one behind a single handle and swap implementations on mode change.
 */
type MelodicRenderer = TabRenderer | SheetMusicRenderer;

/** Per-frame state required by TabRenderer.render(). */
export type TabRenderFrame = {
  bpm: number;
  timeSignature: [number, number];
  liveInputNotes: PianoLiveInputNote[];
  scrollSpeedMultiplier?: number;
  /** Label falling notes by scale-degree number (Nashville Number System). */
  nashville?: boolean;
  /** Colour falling notes by pitch class rather than by approach. */
  noteColors?: boolean;
  /** Chord names for the chart, one per change; drawn beside the notes. */
  chordLabels?: { tSec: number; label: string }[];
};

const INSTRUMENT_ROLE_LABELS: Record<string, string> = {
  bass: "Bass",
  rhythm_guitar: "Rhythm Guitar",
  lead_guitar: "Lead Guitar",
  keys: "Keys / Synth",
  melodic: "Melodic",
};

export type PlaySurfaceControllerDeps = {
  playSurfaceEl: HTMLElement;
  playLayoutEl: HTMLElement;
  appMainEl: HTMLElement;
  playersPanel: PlayersPanelHandle;
  consoleBridge: ConsoleBridge;
  getSelectedMelodicTracks: () => MelodicTrackSelection[];
  getCurrentRoute: () => string;
  /** Initial display mode (persisted by the host). Defaults to "piano". */
  initialDisplayMode?: DisplayMode;
};

export type PlaySurfaceControllerHandle = {
  /** Apply pianoPrimary / wideSoloKeys layout class toggles. */
  syncSurfaceMode: () => void;
  /**
   * Pick the right melodic track based on players' preferences and re-render
   * the instrument selector. No-op when no melodic tracks loaded.
   */
  syncMelodicTrackSelectionFromPlayers: () => void;
  /** Re-render the instrument-selector chip row from the current track set. */
  updateInstrumentSelector: () => void;
  /** Programmatically select a track by role (button-click equivalent). */
  selectInstrumentTrack: (role: InstrumentRole) => void;
  /** Per-frame tab render (called from the host's RAF tick). No-op if no track. */
  renderTabFrame: (t: number, frame: TabRenderFrame) => void;
  /** Currently-active instrument role (null if none selected). */
  getActiveTabInstrument: () => InstrumentRole | null;
  /** Switch between the piano-roll and sheet-music renderers. */
  setDisplayMode: (mode: DisplayMode) => void;
  /** Currently-active display mode. */
  getDisplayMode: () => DisplayMode;
};

export function initPlaySurfaceController(deps: PlaySurfaceControllerDeps): PlaySurfaceControllerHandle {
  const instrumentSelectorEl = document.getElementById("instrumentSelector") as HTMLDivElement | null;
  const tabContainerEl = document.getElementById("tabContainer") as HTMLDivElement | null;
  if (!instrumentSelectorEl || !tabContainerEl) {
    throw new Error("initPlaySurfaceController: required DOM (#instrumentSelector/#tabContainer) missing");
  }

  let tabRenderer: MelodicRenderer | null = null;
  let activeTabInstrument: InstrumentRole | null = null;
  let displayMode: DisplayMode = deps.initialDisplayMode ?? "piano";

  function createRenderer(): MelodicRenderer {
    return displayMode === "sheet"
      ? new SheetMusicRenderer(tabContainerEl!)
      : new TabRenderer(tabContainerEl!);
  }

  function findMelodicTrack(role: InstrumentRole | null): MelodicTrackSelection | null {
    if (!role) return null;
    return deps.getSelectedMelodicTracks().find((track) => track.role === role) ?? null;
  }

  function shouldPromoteMelodicSurface(): boolean {
    return deps.playersPanel.getPrimaryInstrument() === "keys" && Boolean(findMelodicTrack("keys"));
  }

  function shouldUseWideSoloKeysLayout(): boolean {
    return (
      deps.getCurrentRoute() === "play"
      && !deps.playLayoutEl.classList.contains("isLibraryOnly")
      && deps.playersPanel.getPlayers().length === 1
      && shouldPromoteMelodicSurface()
    );
  }

  function syncSurfaceMode(): void {
    const pianoPrimary = shouldPromoteMelodicSurface();
    const wideSoloKeys = shouldUseWideSoloKeysLayout();
    deps.playSurfaceEl.classList.toggle("isPianoPrimary", pianoPrimary);
    deps.playLayoutEl.classList.toggle("isWideSoloKeys", wideSoloKeys);
    deps.appMainEl.classList.toggle("isWideSoloKeys", wideSoloKeys);
  }

  function selectInstrumentTrack(role: InstrumentRole): void {
    const track = deps.getSelectedMelodicTracks().find((t) => t.role === role);
    if (!track) return;

    // Update button states.
    for (const btn of Array.from(instrumentSelectorEl!.querySelectorAll<HTMLButtonElement>("button.instrumentBtn"))) {
      btn.classList.toggle("isActive", btn.dataset.role === role);
    }

    // Create or update tab renderer.
    if (!tabRenderer) {
      tabContainerEl!.innerHTML = "";
      tabRenderer = createRenderer();
    }
    tabRenderer.setTrack(track);
    activeTabInstrument = role;
    syncSurfaceMode();

    deps.consoleBridge.log("play", `selected instrument: ${role} (${track.notes.length} notes)`);
  }

  function syncMelodicTrackSelectionFromPlayers(): void {
    const tracks = deps.getSelectedMelodicTracks();
    if (tracks.length === 0) {
      syncSurfaceMode();
      return;
    }

    const preferredTrack = findMelodicTrack(deps.playersPanel.getPreferredMelodicRole());
    const activeTrack = findMelodicTrack(activeTabInstrument);
    const nextTrack = preferredTrack ?? activeTrack ?? tracks[0] ?? null;
    if (!nextTrack) {
      syncSurfaceMode();
      return;
    }

    selectInstrumentTrack(nextTrack.role);
  }

  function updateInstrumentSelector(): void {
    // Clean up old tab renderer.
    if (tabRenderer) {
      tabRenderer.dispose();
      tabRenderer = null;
    }
    activeTabInstrument = null;

    // Clear old instrument buttons (keep the label span AND the
    // display-mode toggle, which also lives in this row).
    const buttons = instrumentSelectorEl!.querySelectorAll("button.instrumentBtn");
    buttons.forEach((b) => b.remove());

    const tracks = deps.getSelectedMelodicTracks();
    // Only surface the melodic piano-roll/sheet when a player is actually on a
    // melodic instrument. A drums-only (or vocals-only) band has no melodic
    // part to play, so showing the song's keys/bass/guitar here is just noise.
    const hasMelodicPlayer = deps.playersPanel
      .getPlayers()
      .some((p) => p.instrument !== "drums" && p.instrument !== "vocals");
    if (tracks.length === 0 || !hasMelodicPlayer) {
      instrumentSelectorEl!.style.display = "none";
      tabContainerEl!.style.display = "none";
      syncSurfaceMode();
      return;
    }

    instrumentSelectorEl!.style.display = "flex";
    tabContainerEl!.style.display = "block";

    for (const track of tracks) {
      const btn = document.createElement("button");
      btn.className = "instrumentBtn";
      btn.textContent = INSTRUMENT_ROLE_LABELS[track.role] ?? track.trackName;
      btn.dataset.role = track.role;
      btn.addEventListener("click", () => {
        selectInstrumentTrack(track.role);
      });
      instrumentSelectorEl!.appendChild(btn);
    }

    syncMelodicTrackSelectionFromPlayers();
  }

  function renderTabFrame(t: number, frame: TabRenderFrame): void {
    if (!tabRenderer) return;
    tabRenderer.render(t, frame);
  }

  function setDisplayMode(mode: DisplayMode): void {
    if (mode === displayMode) return;
    displayMode = mode;
    // Rebuild the active renderer in place, preserving the current track.
    if (tabRenderer && activeTabInstrument) {
      const role = activeTabInstrument;
      tabRenderer.dispose();
      tabContainerEl!.innerHTML = "";
      tabRenderer = null;
      selectInstrumentTrack(role);
    }
    deps.consoleBridge.log("play", `display mode: ${mode}`);
  }

  return {
    syncSurfaceMode,
    syncMelodicTrackSelectionFromPlayers,
    updateInstrumentSelector,
    selectInstrumentTrack,
    renderTabFrame,
    getActiveTabInstrument: () => activeTabInstrument,
    setDisplayMode,
    getDisplayMode: () => displayMode,
  };
}
