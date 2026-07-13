// @vitest-environment jsdom
/**
 * Execution tests for the Players panel (Band Setup "PLAYERS" row).
 *
 * Imports + runs initPlayersPanel, drives the add/remove/instrument-change
 * handlers, exercises the localStorage primary-instrument persistence, the
 * derived getters, and the deferred-microtask initial render.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  initPlayersPanel,
  defaultPluginIdForInstrument,
  DEFAULT_PLUGIN_ID,
  DRUM_HIGHWAY_PLUGIN_ID,
  NASHVILLE_PLUGIN_ID,
  LYRICS_PLUGIN_ID,
  type PlayersPanelDeps,
} from "../src/playersPanel";

const STORAGE_KEY = "auralprimer.primaryPlayerInstrument";

function stageDom(): void {
  document.body.innerHTML = '<div id="players"></div><button id="addPlayer"></button>';
}

function makeDeps(overrides: Partial<PlayersPanelDeps> = {}): PlayersPanelDeps {
  return {
    escapeHtml: (s: string) => s,
    setPluginSelectionModeAuto: vi.fn(),
    applyInstrumentAvailability: vi.fn(),
    syncPreferredPluginSelection: vi.fn().mockReturnValue(false),
    syncMelodicTrackSelectionFromPlayers: vi.fn(),
    restartVisualizerForPluginSelection: vi.fn(),
    rebuildSecondaryStagesIfRunning: vi.fn(),
    getSelectedPluginId: vi.fn().mockReturnValue("viz-beats"),
    ...overrides,
  };
}

const flush = () => new Promise<void>((r) => queueMicrotask(() => r()));
const playersEl = () => document.getElementById("players") as HTMLDivElement;
const chips = () => Array.from(playersEl().querySelectorAll(".playerChip"));
const addBtn = () => document.getElementById("addPlayer") as HTMLButtonElement;

describe("playersPanel", () => {
  beforeEach(() => {
    stageDom();
    window.localStorage.clear();
  });

  it("defaultPluginIdForInstrument maps each instrument", () => {
    expect(defaultPluginIdForInstrument("drums")).toBe(DRUM_HIGHWAY_PLUGIN_ID);
    expect(defaultPluginIdForInstrument("vocals")).toBe(LYRICS_PLUGIN_ID);
    expect(defaultPluginIdForInstrument("bass")).toBe(NASHVILLE_PLUGIN_ID);
    expect(defaultPluginIdForInstrument("lead_guitar")).toBe(NASHVILLE_PLUGIN_ID);
    expect(defaultPluginIdForInstrument("rhythm_guitar")).toBe(NASHVILLE_PLUGIN_ID);
    expect(defaultPluginIdForInstrument("keys")).toBe(NASHVILLE_PLUGIN_ID);
    expect(defaultPluginIdForInstrument(undefined)).toBe(DEFAULT_PLUGIN_ID);
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initPlayersPanel(makeDeps())).toThrow(/required DOM/);
  });

  it("renders the default single drums player after the deferred microtask", async () => {
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    expect(panel.getPlayers().length).toBe(1);
    expect(panel.getPrimaryInstrument()).toBe("drums");
    // Render is deferred to a microtask.
    expect(chips().length).toBe(0);
    await flush();
    expect(chips().length).toBe(1);
    expect(deps.applyInstrumentAvailability).toHaveBeenCalled();
    // Remove button disabled with a single player.
    const removeBtn = playersEl().querySelector("button.removePlayer") as HTMLButtonElement;
    expect(removeBtn.disabled).toBe(true);
  });

  it("restores a persisted primary instrument on boot", async () => {
    window.localStorage.setItem(STORAGE_KEY, "keys");
    const panel = initPlayersPanel(makeDeps());
    await flush();
    expect(panel.getPrimaryInstrument()).toBe("keys");
  });

  it("ignores an invalid persisted instrument and defaults to drums", async () => {
    window.localStorage.setItem(STORAGE_KEY, "not-an-instrument");
    const panel = initPlayersPanel(makeDeps());
    await flush();
    expect(panel.getPrimaryInstrument()).toBe("drums");
  });

  it("getPreferredMelodicRole maps the primary instrument", async () => {
    const cases: Array<[string, string | null]> = [
      ["bass", "bass"],
      ["rhythm_guitar", "rhythm_guitar"],
      ["lead_guitar", "lead_guitar"],
      ["keys", "keys"],
      ["vocals", "vocals"],
      ["drums", null],
    ];
    for (const [inst, expected] of cases) {
      window.localStorage.setItem(STORAGE_KEY, inst);
      stageDom();
      const panel = initPlayersPanel(makeDeps());
      await flush();
      expect(panel.getPreferredMelodicRole()).toBe(expected);
    }
  });

  it("getPreferredPluginIdForPlayers reflects drums vs melodic primary", async () => {
    window.localStorage.setItem(STORAGE_KEY, "drums");
    const drumsPanel = initPlayersPanel(makeDeps());
    await flush();
    expect(drumsPanel.getPreferredPluginIdForPlayers()).toBe(DRUM_HIGHWAY_PLUGIN_ID);

    stageDom();
    window.localStorage.setItem(STORAGE_KEY, "keys");
    const keysPanel = initPlayersPanel(makeDeps());
    await flush();
    expect(keysPanel.getPreferredPluginIdForPlayers()).toBe(DEFAULT_PLUGIN_ID);
  });

  it("Add button appends players with role defaults (2..5)", async () => {
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    await flush();
    addBtn().dispatchEvent(new MouseEvent("click")); // p2 lead_guitar
    addBtn().dispatchEvent(new MouseEvent("click")); // p3 bass
    addBtn().dispatchEvent(new MouseEvent("click")); // p4 rhythm_guitar
    addBtn().dispatchEvent(new MouseEvent("click")); // p5 keys
    const players = panel.getPlayers();
    expect(players.map((p) => p.instrument)).toEqual([
      "drums",
      "lead_guitar",
      "bass",
      "rhythm_guitar",
      "keys",
    ]);
    expect(chips().length).toBe(5);
    expect(deps.rebuildSecondaryStagesIfRunning).toHaveBeenCalled();
  });

  it("changing Player 1's instrument persists + restarts viz when plugin changes", async () => {
    const deps = makeDeps({ syncPreferredPluginSelection: vi.fn().mockReturnValue(true) });
    const panel = initPlayersPanel(deps);
    await flush();
    const sel = playersEl().querySelector("select.playerInstrument") as HTMLSelectElement;
    const events: CustomEvent[] = [];
    window.addEventListener("auralprimer:players-updated", (e) => events.push(e as CustomEvent));
    sel.value = "keys";
    sel.dispatchEvent(new Event("change"));
    expect(panel.getPrimaryInstrument()).toBe("keys");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("keys");
    expect(deps.setPluginSelectionModeAuto).toHaveBeenCalled();
    expect(deps.restartVisualizerForPluginSelection).toHaveBeenCalled();
    expect(events.length).toBe(1);
  });

  it("changing a non-primary player does not persist primary, no restart when plugin unchanged", async () => {
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    await flush();
    addBtn().dispatchEvent(new MouseEvent("click")); // adds p2
    const sels = playersEl().querySelectorAll("select.playerInstrument");
    const p2sel = sels[1] as HTMLSelectElement;
    p2sel.value = "vocals";
    p2sel.dispatchEvent(new Event("change"));
    expect(panel.getPlayers()[1].instrument).toBe("vocals");
    // Player 1 instrument key untouched.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(deps.restartVisualizerForPluginSelection).not.toHaveBeenCalled();
  });

  it("remove button drops a player and restarts viz when selected plugin changes", async () => {
    const getSelectedPluginId = vi
      .fn()
      .mockReturnValueOnce("viz-a") // captured before removal
      .mockReturnValue("viz-b"); // after rerender
    const deps = makeDeps({ getSelectedPluginId });
    const panel = initPlayersPanel(deps);
    await flush();
    addBtn().dispatchEvent(new MouseEvent("click")); // p2
    const removeButtons = playersEl().querySelectorAll("button.removePlayer");
    (removeButtons[1] as HTMLButtonElement).dispatchEvent(new MouseEvent("click"));
    expect(panel.getPlayers().length).toBe(1);
    expect(deps.restartVisualizerForPluginSelection).toHaveBeenCalled();
  });

  it("remove button no-ops on the last remaining player", async () => {
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    await flush();
    const removeBtn = playersEl().querySelector("button.removePlayer") as HTMLButtonElement;
    removeBtn.dispatchEvent(new MouseEvent("click"));
    expect(panel.getPlayers().length).toBe(1);
  });

  it("resetForSongSetup rebuilds the single-player config from persisted instrument", async () => {
    window.localStorage.setItem(STORAGE_KEY, "bass");
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    await flush();
    addBtn().dispatchEvent(new MouseEvent("click"));
    expect(panel.getPlayers().length).toBe(2);
    panel.resetForSongSetup();
    expect(panel.getPlayers().length).toBe(1);
    expect(panel.getPrimaryInstrument()).toBe("bass");
    expect(deps.setPluginSelectionModeAuto).toHaveBeenCalled();
  });

  it("setPlayerInstrument switches a specific player by id", async () => {
    const panel = initPlayersPanel(makeDeps());
    await flush();
    panel.setPlayerInstrument("p1", "vocals");
    expect(panel.getPrimaryInstrument()).toBe("vocals");
    // Unknown id is a no-op.
    panel.setPlayerInstrument("nope", "keys");
    expect(panel.getPrimaryInstrument()).toBe("vocals");
  });

  it("rerenderAndApplyAvailability re-runs the dep callbacks", async () => {
    const deps = makeDeps();
    const panel = initPlayersPanel(deps);
    await flush();
    (deps.applyInstrumentAvailability as ReturnType<typeof vi.fn>).mockClear();
    panel.rerenderAndApplyAvailability();
    expect(deps.applyInstrumentAvailability).toHaveBeenCalledTimes(1);
    expect(deps.syncMelodicTrackSelectionFromPlayers).toHaveBeenCalled();
  });

  it("tolerates localStorage throwing on read + write", async () => {
    const getItem = vi.spyOn(window.localStorage.__proto__, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const setItem = vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const panel = initPlayersPanel(makeDeps());
    await flush();
    expect(panel.getPrimaryInstrument()).toBe("drums");
    const sel = playersEl().querySelector("select.playerInstrument") as HTMLSelectElement;
    sel.value = "keys";
    // Should not throw despite setItem blowing up.
    expect(() => sel.dispatchEvent(new Event("change"))).not.toThrow();
    getItem.mockRestore();
    setItem.mockRestore();
  });
});
