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
import { TabRenderer } from "./tabRenderer";
import type { PlayersPanelHandle } from "./playersPanel";
import type { ConsoleBridge } from "./consoleBridge";

/** Per-frame state required by TabRenderer.render(). */
export type TabRenderFrame = {
  bpm: number;
  timeSignature: [number, number];
  liveInputNotes: Array<{ note: number; channel: number }>;
  scrollSpeedMultiplier?: number;
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
};

export function initPlaySurfaceController(deps: PlaySurfaceControllerDeps): PlaySurfaceControllerHandle {
  const instrumentSelectorEl = document.getElementById("instrumentSelector") as HTMLDivElement | null;
  const tabContainerEl = document.getElementById("tabContainer") as HTMLDivElement | null;
  if (!instrumentSelectorEl || !tabContainerEl) {
    throw new Error("initPlaySurfaceController: required DOM (#instrumentSelector/#tabContainer) missing");
  }

  let tabRenderer: TabRenderer | null = null;
  let activeTabInstrument: InstrumentRole | null = null;

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
      tabRenderer = new TabRenderer(tabContainerEl!);
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

    // Clear old buttons (keep the label span).
    const buttons = instrumentSelectorEl!.querySelectorAll("button");
    buttons.forEach((b) => b.remove());

    const tracks = deps.getSelectedMelodicTracks();
    if (tracks.length === 0) {
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

  return {
    syncSurfaceMode,
    syncMelodicTrackSelectionFromPlayers,
    updateInstrumentSelector,
    selectInstrumentTrack,
    renderTabFrame,
    getActiveTabInstrument: () => activeTabInstrument,
  };
}
