/**
 * Players panel — the "PLAYERS" row in Band Setup.
 *
 * Owns:
 *   - `players` state (array of { id, name, instrument })
 *   - `renderPlayers()` — the player-chip DOM render
 *   - The instrument-select + remove-button event handlers
 *   - The "Add" button click handler
 *   - The plugin-id defaults per instrument (DEFAULT_PLUGIN_ID, etc. +
 *     defaultPluginIdForInstrument + preferredPluginIdForPlayers)
 *   - Auxiliary derived getters (primary instrument, preferred melodic role)
 *
 * Extracted from main.ts as Phase 2.B. The cross-cutting helpers that read
 * players but also touch unrelated state (applyInstrumentAvailability,
 * shouldUseWideSoloKeysLayout, syncPlaySurfaceMode, etc.) stay in main.ts
 * and reach the players list through this panel's handle. The per-player
 * secondary stages (`secondaryStages`, `buildSecondaryStages`,
 * `disposeSecondaryStages`) also stay in main.ts — they're entangled with
 * the visualizer lifecycle and will be cleaned up alongside Phase 2.G's
 * TabRenderer-into-viz-plugin refactor.
 */

import type { InstrumentRole } from "./chartLoader";
import {
  INSTRUMENT_LABELS,
  type Instrument,
} from "./instrumentTypes";

export type Player = { id: string; name: string; instrument: Instrument };

/** Visualizer-plugin id constants. Shared with main.ts (startVisualizer etc.). */
export const DEFAULT_PLUGIN_ID = "viz-beats";
export const DRUM_HIGHWAY_PLUGIN_ID = "viz-drum-highway";
export const NASHVILLE_PLUGIN_ID = "viz-nashville";
export const LYRICS_PLUGIN_ID = "viz-lyrics";

/**
 * Default visualizer for one player's instrument. Player 1 still flows
 * through the rail's plugin select (manual override); players 2..N pick
 * their visualizer from this table based on their instrument. Drums gets
 * the highway, vocals gets lyrics, harmonic instruments get the Nashville
 * chord lane, fallback to beats.
 */
export function defaultPluginIdForInstrument(inst: Instrument | undefined): string {
  switch (inst) {
    case "drums":
      return DRUM_HIGHWAY_PLUGIN_ID;
    case "vocals":
      return LYRICS_PLUGIN_ID;
    case "bass":
    case "lead_guitar":
    case "rhythm_guitar":
    case "keys":
      return NASHVILLE_PLUGIN_ID;
    default:
      return DEFAULT_PLUGIN_ID;
  }
}

export type PlayersPanelDeps = {
  /** HTML escape helper, injected to avoid duplication. */
  escapeHtml: (s: string) => string;
  /** Set the host's plugin-selection-mode back to "auto" on any player edit. */
  setPluginSelectionModeAuto: () => void;
  /**
   * Refresh chart-availability constraints on the player-chip dropdowns and
   * pick the first enabled if the current option got disabled. Called by
   * `rerenderAndApplyAvailability()` after every list change.
   */
  applyInstrumentAvailability: () => void;
  /** Auto-pick the visualizer plugin based on the current Player 1 instrument. Returns true if changed. */
  syncPreferredPluginSelection: () => boolean;
  /** Refresh which melodic instrument track (keys / bass / ...) is the active tab. */
  syncMelodicTrackSelectionFromPlayers: () => void;
  /** Restart the visualizer when the plugin selection changes. */
  restartVisualizerForPluginSelection: () => void;
  /**
   * Rebuild Player 2..N stages mid-session. Called when a player is
   * added / removed / changes instrument while the visualizer is running;
   * the implementation is a no-op when no session is live.
   */
  rebuildSecondaryStagesIfRunning: () => void;
  /** Read the currently-selected plugin id from the host's plugin select. */
  getSelectedPluginId: () => string | null;
};

export type PlayersPanelHandle = {
  /** Current player list (immutable snapshot — callers must not mutate). */
  getPlayers: () => ReadonlyArray<Player>;
  /** Player 1's instrument, or null if no players (impossible in practice). */
  getPrimaryInstrument: () => Instrument | null;
  /** Primary player's instrument as a melodic-track role, or null when drums/none. */
  getPreferredMelodicRole: () => InstrumentRole | null;
  /** Plugin id Player 1's instrument prefers (drums → highway, else viz-beats). */
  getPreferredPluginIdForPlayers: () => string;
  /** Reset to the default single-player config (Drums) at song-setup time. */
  resetForSongSetup: () => void;
  /** Re-render the player chips and re-apply chart availability constraints. */
  rerenderAndApplyAvailability: () => void;
  /** Programmatic instrument switch for a specific player (used by chart-availability re-pick). */
  setPlayerInstrument: (playerId: string, inst: Instrument) => void;
};

export function initPlayersPanel(deps: PlayersPanelDeps): PlayersPanelHandle {
  const playersEl = document.getElementById("players") as HTMLDivElement | null;
  const addPlayerBtn = document.getElementById("addPlayer") as HTMLButtonElement | null;
  if (!playersEl || !addPlayerBtn) {
    throw new Error("initPlayersPanel: required DOM (#players/#addPlayer) missing");
  }

  let players: Player[] = [{ id: "p1", name: "Player 1", instrument: "drums" }];

  function primaryPlayerInstrument(): Instrument | null {
    return players[0]?.instrument ?? null;
  }

  function preferredMelodicRoleForPlayers(): InstrumentRole | null {
    switch (primaryPlayerInstrument()) {
      case "bass":
        return "bass";
      case "rhythm_guitar":
        return "rhythm_guitar";
      case "lead_guitar":
        return "lead_guitar";
      case "keys":
        return "keys";
      case "vocals":
        return "melodic";
      default:
        return null;
    }
  }

  function preferredPluginIdForPlayers(): string {
    return players[0]?.instrument === "drums" ? DRUM_HIGHWAY_PLUGIN_ID : DEFAULT_PLUGIN_ID;
  }

  function renderPlayers(): void {
    playersEl!.innerHTML = `
      <div class="playersGrid">
        ${players
          .map((p) => {
            const options = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
              .map((inst) => `<option value="${inst}" ${p.instrument === inst ? "selected" : ""}>${INSTRUMENT_LABELS[inst]}</option>`)
              .join("\n");
            return `
              <div class="playerChip" data-player-id="${p.id}">
                <span class="playerName">${deps.escapeHtml(p.name)}</span>
                <select class="playerInstrument" aria-label="Instrument for ${deps.escapeHtml(p.name)}">
                  ${options}
                </select>
                <button class="removePlayer" title="Remove player" ${players.length <= 1 ? "disabled" : ""}>×</button>
              </div>
            `;
          })
          .join("\n")}
      </div>
    `;

    for (const chip of Array.from(playersEl!.querySelectorAll<HTMLElement>(".playerChip"))) {
      const id = chip.getAttribute("data-player-id");
      if (!id) continue;

      const sel = chip.querySelector<HTMLSelectElement>("select.playerInstrument");
      sel?.addEventListener("change", () => {
        const inst = sel.value as Instrument;
        deps.setPluginSelectionModeAuto();
        players = players.map((p) => (p.id === id ? { ...p, instrument: inst } : p));
        deps.syncMelodicTrackSelectionFromPlayers();
        const pluginChanged = deps.syncPreferredPluginSelection();
        if (pluginChanged) {
          deps.restartVisualizerForPluginSelection();
        }
        deps.rebuildSecondaryStagesIfRunning();
        window.dispatchEvent(
          new CustomEvent("auralprimer:players-updated", {
            detail: { players: [...players] },
          })
        );
      });

      const remove = chip.querySelector<HTMLButtonElement>("button.removePlayer");
      remove?.addEventListener("click", () => {
        if (players.length <= 1) return;
        const previousPluginId = deps.getSelectedPluginId();
        players = players.filter((p) => p.id !== id);
        rerenderAndApplyAvailability();
        if (deps.getSelectedPluginId() !== previousPluginId) {
          deps.restartVisualizerForPluginSelection();
        }
      });
    }
  }

  function rerenderAndApplyAvailability(): void {
    renderPlayers();
    deps.applyInstrumentAvailability();
    deps.syncPreferredPluginSelection();
    deps.syncMelodicTrackSelectionFromPlayers();
    // If a session is live, rebuild Player 2..N stages so the user sees
    // the added/removed player immediately. No-op when no session.
    deps.rebuildSecondaryStagesIfRunning();
  }

  function resetForSongSetup(): void {
    deps.setPluginSelectionModeAuto();
    players = [{ id: "p1", name: "Player 1", instrument: "drums" }];
    rerenderAndApplyAvailability();
    deps.syncPreferredPluginSelection();
  }

  addPlayerBtn.addEventListener("click", () => {
    const nextIdx = players.length + 1;
    const id = `p${nextIdx}`;
    const defaultInst: Instrument =
      nextIdx === 2 ? "lead_guitar" : nextIdx === 3 ? "bass" : nextIdx === 4 ? "rhythm_guitar" : "keys";
    players = [...players, { id, name: `Player ${nextIdx}`, instrument: defaultInst }];
    rerenderAndApplyAvailability();
  });

  // Defer the initial render to a microtask so the host's `const
  // playersPanel = initPlayersPanel({...})` assignment completes before
  // any dep callback tries to read `playersPanel` (e.g. main.ts's
  // syncPreferredPluginSelection reads playersPanel.getPreferredPluginIdForPlayers,
  // which would throw "Cannot access 'playersPanel' before initialization"
  // if rerender ran synchronously here). Same TDZ workaround as the
  // showSongLibraryStep boot call.
  queueMicrotask(() => {
    rerenderAndApplyAvailability();
  });

  return {
    getPlayers: () => players,
    getPrimaryInstrument: primaryPlayerInstrument,
    getPreferredMelodicRole: preferredMelodicRoleForPlayers,
    getPreferredPluginIdForPlayers: preferredPluginIdForPlayers,
    resetForSongSetup,
    rerenderAndApplyAvailability,
    setPlayerInstrument: (playerId, inst) => {
      players = players.map((p) => (p.id === playerId ? { ...p, instrument: inst } : p));
    },
  };
}
