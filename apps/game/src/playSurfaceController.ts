/**
 * Play-surface controller — manages the melodic-instrument tab/piano-roll
 * surface that lives below the primary visualizer canvas. Owns the
 * "which melodic track is on screen" decision, the TabRenderer instance,
 * and the layout-class toggles that promote the keys instrument to
 * primary or expand to wide-solo-keys mode.
 *
 * Owns:
 *   - #instrumentSelector, #tabContainer, #fretViewToggle DOM
 *   - playSurfaceEl, appMainEl class toggles (via deps)
 *   - the renderer instance + active instrument role
 *   - each fretted role's view (string lanes / neck highway / tab scroll /
 *     neck coach / sheet music), persisted per role
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
import { TabRenderer, SheetMusicRenderer, GuitarRenderer, isFrettedRole } from "./tabRenderer";
import type { FretView, FrettedRole, GridBeat, GridSection, PianoLiveInputNote } from "./tabRenderer";
import type { PlayersPanelHandle } from "./playersPanel";
import type { ConsoleBridge } from "./consoleBridge";

/** Display mode for the melodic play surface. */
export type DisplayMode = "piano" | "sheet";

/** What a bass or guitar part is drawn as: one of the guitar views, or sheet music. */
export type FrettedView = FretView | "sheet";
export const FRETTED_VIEWS: readonly FrettedView[] = ["lanes", "highway", "tab", "coach", "sheet"];

/**
 * Views that are a whole play surface rather than a strip. For a guitar or bass
 * player on their own part they take the stage, the way the piano roll does
 * for a keys player. Tab and sheet keep the strip under the visualizer.
 */
const FULL_SURFACE_VIEWS = new Set<FrettedView>(["lanes", "highway", "coach"]);

/** Per role, beside the game's other view prefs: auralprimer.fretView.bass etc. */
const FRETTED_VIEW_STORAGE_PREFIX = "auralprimer.fretView.";
/** The old tab lane became the tab view, so that is where a part starts. */
const DEFAULT_FRETTED_VIEW: FrettedView = "tab";

function isFrettedView(value: unknown): value is FrettedView {
  return typeof value === "string" && (FRETTED_VIEWS as readonly string[]).includes(value);
}

/**
 * All renderers expose the same imperative surface, so the controller can
 * hold one behind a single handle and swap implementations on mode change.
 */
type MelodicRenderer = TabRenderer | SheetMusicRenderer | GuitarRenderer;

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
  /** Which hand the player is working on. Piano only; absent means both. */
  handMode?: "both" | "left" | "right";
  /** Draw the other hand dimmed rather than hiding it. */
  showOtherHand?: boolean;
  /** The song's beats, for the guitar views' bar lines. */
  beats?: GridBeat[];
  /** The song's sections, for the guitar views' section marks. */
  sections?: GridSection[];
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
  /** The view a bass or guitar part is drawn as (persisted per role). */
  getFrettedView: (role: FrettedRole) => FrettedView;
  /** Switch a bass or guitar part's view, rebuilding the surface if it is on screen. */
  setFrettedView: (role: FrettedRole, view: FrettedView) => void;
};

export function initPlaySurfaceController(deps: PlaySurfaceControllerDeps): PlaySurfaceControllerHandle {
  const instrumentSelectorEl = document.getElementById("instrumentSelector") as HTMLDivElement | null;
  const tabContainerEl = document.getElementById("tabContainer") as HTMLDivElement | null;
  if (!instrumentSelectorEl || !tabContainerEl) {
    throw new Error("initPlaySurfaceController: required DOM (#instrumentSelector/#tabContainer) missing");
  }

  // Optional: the keys toggle is wired by the host and only hidden from here,
  // and a host without the guitar toggle simply never offers the views.
  const displayModeToggleEl = document.getElementById("displayModeToggle");
  const fretViewToggleEl = document.getElementById("fretViewToggle");

  let tabRenderer: MelodicRenderer | null = null;
  /** Which renderer `tabRenderer` is, so a track switch rebuilds only when it must. */
  let rendererKey: string | null = null;
  let activeTabInstrument: InstrumentRole | null = null;
  let displayMode: DisplayMode = deps.initialDisplayMode ?? "piano";
  const frettedViews = new Map<FrettedRole, FrettedView>();

  function getFrettedView(role: FrettedRole): FrettedView {
    let view = frettedViews.get(role);
    if (!view) {
      view = DEFAULT_FRETTED_VIEW;
      try {
        const raw = window.localStorage.getItem(FRETTED_VIEW_STORAGE_PREFIX + role);
        if (isFrettedView(raw)) view = raw;
      } catch {
        // localStorage may be unavailable in some embedded webviews; default safely.
      }
      frettedViews.set(role, view);
    }
    return view;
  }

  function rendererKeyFor(role: InstrumentRole): string {
    if (isFrettedRole(role)) {
      const view = getFrettedView(role);
      return view === "sheet" ? "sheet" : `guitar:${view}`;
    }
    return displayMode === "sheet" ? "sheet" : "piano";
  }

  function createRenderer(key: string): MelodicRenderer {
    if (key === "sheet") return new SheetMusicRenderer(tabContainerEl!);
    if (key.startsWith("guitar:")) return new GuitarRenderer(tabContainerEl!, key.slice("guitar:".length) as FretView);
    return new TabRenderer(tabContainerEl!);
  }

  /** Offer the view choice that fits the part on screen: guitar views, or piano/sheet. */
  function syncViewToggles(): void {
    const role = activeTabInstrument;
    const fretted = isFrettedRole(role);
    if (displayModeToggleEl) displayModeToggleEl.hidden = fretted;
    if (!fretViewToggleEl) return;
    fretViewToggleEl.hidden = !fretted;
    if (!fretted) return;
    const view = getFrettedView(role);
    for (const btn of Array.from(fretViewToggleEl.querySelectorAll<HTMLButtonElement>("button[data-view]"))) {
      btn.classList.toggle("isActive", btn.dataset.view === view);
    }
  }

  function findMelodicTrack(role: InstrumentRole | null): MelodicTrackSelection | null {
    if (!role) return null;
    return deps.getSelectedMelodicTracks().find((track) => track.role === role) ?? null;
  }

  function shouldPromoteMelodicSurface(): boolean {
    const primary = deps.playersPanel.getPrimaryInstrument();
    if (primary === "keys") return Boolean(findMelodicTrack("keys"));
    return (
      isFrettedRole(primary)
      && activeTabInstrument === primary
      && FULL_SURFACE_VIEWS.has(getFrettedView(primary))
    );
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

    // Create or update the renderer; a part that needs a different one gets it.
    const key = rendererKeyFor(role);
    if (!tabRenderer || rendererKey !== key) {
      tabRenderer?.dispose();
      tabContainerEl!.innerHTML = "";
      tabRenderer = createRenderer(key);
      rendererKey = key;
    }
    tabRenderer.setTrack(track);
    activeTabInstrument = role;
    syncViewToggles();
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
    rendererKey = null;
    activeTabInstrument = null;
    syncViewToggles();

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

  function setFrettedView(role: FrettedRole, view: FrettedView): void {
    if (!isFrettedView(view)) return;
    frettedViews.set(role, view);
    try {
      window.localStorage.setItem(FRETTED_VIEW_STORAGE_PREFIX + role, view);
    } catch {
      // Best-effort -- session-only persistence is acceptable.
    }
    if (activeTabInstrument === role) selectInstrumentTrack(role);
    deps.consoleBridge.log("play", `${role} view: ${view}`);
  }

  fretViewToggleEl?.addEventListener("click", (event) => {
    const btn = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>("button[data-view]");
    const view = btn?.dataset.view;
    if (!btn || !isFrettedRole(activeTabInstrument) || !isFrettedView(view)) return;
    setFrettedView(activeTabInstrument, view);
    // Hand the keyboard back, or Space would press this button again instead
    // of pausing the song.
    btn.blur();
  });

  return {
    syncSurfaceMode,
    syncMelodicTrackSelectionFromPlayers,
    updateInstrumentSelector,
    selectInstrumentTrack,
    renderTabFrame,
    getActiveTabInstrument: () => activeTabInstrument,
    setDisplayMode,
    getDisplayMode: () => displayMode,
    getFrettedView,
    setFrettedView,
  };
}
