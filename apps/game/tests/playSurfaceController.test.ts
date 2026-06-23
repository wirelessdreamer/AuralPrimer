// @vitest-environment jsdom
/**
 * Execution tests for the melodic play-surface controller.
 *
 * Imports + runs initPlaySurfaceController with mocked TabRenderer /
 * SheetMusicRenderer (from ./tabRenderer) and a fake playersPanel handle.
 * Drives the instrument-selector build, track selection, layout-class
 * toggles, per-frame render, and the piano/sheet display-mode swap.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the renderer module that re-exports TabRenderer/SheetMusicRenderer.
const tabInstances: Array<Record<string, ReturnType<typeof vi.fn>>> = [];
const sheetInstances: Array<Record<string, ReturnType<typeof vi.fn>>> = [];
vi.mock("../src/tabRenderer", () => {
  class TabRenderer {
    setTrack = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
    constructor(public container: HTMLElement) {
      tabInstances.push(this as unknown as Record<string, ReturnType<typeof vi.fn>>);
    }
  }
  class SheetMusicRenderer {
    setTrack = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
    constructor(public container: HTMLElement) {
      sheetInstances.push(this as unknown as Record<string, ReturnType<typeof vi.fn>>);
    }
  }
  return { TabRenderer, SheetMusicRenderer };
});

import { initPlaySurfaceController, type PlaySurfaceControllerDeps } from "../src/playSurfaceController";
import type { MelodicTrackSelection } from "../src/chartLoader";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="instrumentSelector"><span class="label">x</span></div>' +
    '<div id="tabContainer"></div>';
}

function track(role: string, notes = 4): MelodicTrackSelection {
  return {
    role,
    trackName: `${role} track`,
    notes: new Array(notes).fill(0).map(() => ({})),
  } as unknown as MelodicTrackSelection;
}

function makePlayersPanel(opts: {
  primary?: string;
  preferred?: string | null;
  playerCount?: number;
} = {}) {
  return {
    getPrimaryInstrument: vi.fn().mockReturnValue(opts.primary ?? "keys"),
    getPreferredMelodicRole: vi.fn().mockReturnValue(opts.preferred ?? "keys"),
    getPlayers: vi.fn().mockReturnValue(new Array(opts.playerCount ?? 1).fill({})),
    getPreferredPluginIdForPlayers: vi.fn(),
    resetForSongSetup: vi.fn(),
    rerenderAndApplyAvailability: vi.fn(),
    setPlayerInstrument: vi.fn(),
  } as unknown as PlaySurfaceControllerDeps["playersPanel"];
}

function makeDeps(overrides: Partial<PlaySurfaceControllerDeps> = {}): PlaySurfaceControllerDeps {
  return {
    playSurfaceEl: document.createElement("div"),
    playLayoutEl: document.createElement("div"),
    appMainEl: document.createElement("div"),
    playersPanel: makePlayersPanel(),
    consoleBridge: { log: vi.fn() } as unknown as PlaySurfaceControllerDeps["consoleBridge"],
    getSelectedMelodicTracks: vi.fn().mockReturnValue([]),
    getCurrentRoute: vi.fn().mockReturnValue("play"),
    ...overrides,
  };
}

const selectorEl = () => document.getElementById("instrumentSelector") as HTMLDivElement;
const containerEl = () => document.getElementById("tabContainer") as HTMLDivElement;
const btns = () => Array.from(selectorEl().querySelectorAll("button.instrumentBtn")) as HTMLButtonElement[];

describe("playSurfaceController", () => {
  beforeEach(() => {
    stageDom();
    tabInstances.length = 0;
    sheetInstances.length = 0;
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initPlaySurfaceController(makeDeps())).toThrow(/required DOM/);
  });

  it("hides the selector + container when no melodic tracks", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.updateInstrumentSelector();
    expect(selectorEl().style.display).toBe("none");
    expect(containerEl().style.display).toBe("none");
    expect(btns().length).toBe(0);
  });

  it("builds a button per track and auto-selects the preferred role", () => {
    const tracks = [track("bass"), track("keys")];
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue(tracks),
      playersPanel: makePlayersPanel({ preferred: "keys", primary: "keys" }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.updateInstrumentSelector();
    expect(btns().length).toBe(2);
    expect(selectorEl().style.display).toBe("flex");
    // Preferred "keys" track selected.
    expect(ctl.getActiveTabInstrument()).toBe("keys");
    expect(tabInstances.length).toBe(1);
    expect(tabInstances[0].setTrack).toHaveBeenCalled();
    // Active button highlighted.
    const activeBtn = btns().find((b) => b.dataset.role === "keys")!;
    expect(activeBtn.classList.contains("isActive")).toBe(true);
  });

  it("falls back to the first track when preferred/active are absent", () => {
    const tracks = [track("bass")];
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue(tracks),
      playersPanel: makePlayersPanel({ preferred: null, primary: "bass" }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.updateInstrumentSelector();
    expect(ctl.getActiveTabInstrument()).toBe("bass");
  });

  it("clicking an instrument button selects that track", () => {
    const tracks = [track("bass"), track("keys")];
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue(tracks),
      playersPanel: makePlayersPanel({ preferred: "keys" }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.updateInstrumentSelector();
    const bassBtn = btns().find((b) => b.dataset.role === "bass")!;
    bassBtn.dispatchEvent(new MouseEvent("click"));
    expect(ctl.getActiveTabInstrument()).toBe("bass");
    expect(bassBtn.classList.contains("isActive")).toBe(true);
  });

  it("selectInstrumentTrack no-ops for an unknown role", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    expect(ctl.getActiveTabInstrument()).toBeNull();
  });

  it("syncSurfaceMode toggles pianoPrimary + wideSoloKeys for solo keys", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]),
      playersPanel: makePlayersPanel({ primary: "keys", playerCount: 1 }),
      getCurrentRoute: vi.fn().mockReturnValue("play"),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    ctl.syncSurfaceMode();
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(true);
    expect(deps.playLayoutEl.classList.contains("isWideSoloKeys")).toBe(true);
    expect(deps.appMainEl.classList.contains("isWideSoloKeys")).toBe(true);
  });

  it("wideSoloKeys is off with multiple players or off the play route", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]),
      playersPanel: makePlayersPanel({ primary: "keys", playerCount: 2 }),
      getCurrentRoute: vi.fn().mockReturnValue("library"),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    ctl.syncSurfaceMode();
    expect(deps.playLayoutEl.classList.contains("isWideSoloKeys")).toBe(false);
  });

  it("wideSoloKeys is off when playLayout is library-only", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]),
      playersPanel: makePlayersPanel({ primary: "keys", playerCount: 1 }),
    });
    deps.playLayoutEl.classList.add("isLibraryOnly");
    const ctl = initPlaySurfaceController(deps);
    ctl.syncSurfaceMode();
    expect(deps.playLayoutEl.classList.contains("isWideSoloKeys")).toBe(false);
  });

  it("syncMelodicTrackSelectionFromPlayers no-ops (just syncs surface) when no tracks", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.syncMelodicTrackSelectionFromPlayers();
    expect(ctl.getActiveTabInstrument()).toBeNull();
  });

  it("renderTabFrame is a no-op with no active renderer, then renders once selected", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    const frame = { bpm: 120, timeSignature: [4, 4] as [number, number], liveInputNotes: [] };
    ctl.renderTabFrame(1, frame); // no renderer yet
    ctl.selectInstrumentTrack("bass");
    ctl.renderTabFrame(2, frame);
    expect(tabInstances[0].render).toHaveBeenCalledWith(2, frame);
  });

  it("updateInstrumentSelector disposes the previous renderer", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    expect(tabInstances.length).toBe(1);
    ctl.updateInstrumentSelector();
    expect(tabInstances[0].dispose).toHaveBeenCalled();
  });

  it("setDisplayMode swaps to the sheet renderer, preserving the track", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]),
      initialDisplayMode: "piano",
    });
    const ctl = initPlaySurfaceController(deps);
    expect(ctl.getDisplayMode()).toBe("piano");
    ctl.selectInstrumentTrack("bass");
    expect(tabInstances.length).toBe(1);
    ctl.setDisplayMode("sheet");
    expect(ctl.getDisplayMode()).toBe("sheet");
    expect(tabInstances[0].dispose).toHaveBeenCalled();
    expect(sheetInstances.length).toBe(1);
    expect(sheetInstances[0].setTrack).toHaveBeenCalled();
    expect(ctl.getActiveTabInstrument()).toBe("bass");
  });

  it("setDisplayMode is a no-op for the same mode", () => {
    const deps = makeDeps({ initialDisplayMode: "piano" });
    const ctl = initPlaySurfaceController(deps);
    ctl.setDisplayMode("piano");
    expect(ctl.getDisplayMode()).toBe("piano");
  });

  it("setDisplayMode without an active track just records the mode", () => {
    const deps = makeDeps();
    const ctl = initPlaySurfaceController(deps);
    ctl.setDisplayMode("sheet");
    expect(ctl.getDisplayMode()).toBe("sheet");
    expect(sheetInstances.length).toBe(0);
  });

  it("honors an initial sheet display mode when creating the renderer", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]),
      initialDisplayMode: "sheet",
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    expect(sheetInstances.length).toBe(1);
    expect(tabInstances.length).toBe(0);
  });
});
