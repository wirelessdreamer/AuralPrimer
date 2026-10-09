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
const guitarInstances: Array<Record<string, ReturnType<typeof vi.fn>> & { view: string }> = [];
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
  class GuitarRenderer {
    setTrack = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
    setNumbering = vi.fn();
    constructor(public container: HTMLElement, public view: string) {
      guitarInstances.push(this as unknown as Record<string, ReturnType<typeof vi.fn>> & { view: string });
    }
  }
  const isFrettedRole = (role: unknown) => role === "bass" || role === "rhythm_guitar" || role === "lead_guitar";
  const DEFAULT_FRET_NUMBERING = { labels: "frets", chords: "numbers", count: "song" };
  return { TabRenderer, SheetMusicRenderer, GuitarRenderer, isFrettedRole, DEFAULT_FRET_NUMBERING };
});

import { initPlaySurfaceController, type PlaySurfaceControllerDeps } from "../src/playSurfaceController";
import type { MelodicTrackSelection } from "../src/chartLoader";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="instrumentSelector"><span class="label">x</span>' +
    '<div id="displayModeToggle"></div>' +
    '<div id="fretViewToggle" hidden>' +
    ["lanes", "highway", "tab", "coach", "nashville", "sheet"].map((v) => `<button data-view="${v}"></button>`).join("") +
    "</div>" +
    '<div id="fretNumbersRow" hidden>' +
    '<button data-labels="frets"></button><button data-labels="numbers"></button>' +
    '<span class="numbersOnly" id="chordsLabel"></span>' +
    '<button data-chords="numbers"></button><button data-chords="tones"></button><button data-chords="both"></button>' +
    '<button data-count="song"></button><button data-count="relative"></button>' +
    "</div></div>" +
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
    guitarInstances.length = 0;
    window.localStorage.clear();
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
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]) });
    const ctl = initPlaySurfaceController(deps);
    const frame = { bpm: 120, timeSignature: [4, 4] as [number, number], liveInputNotes: [] };
    ctl.renderTabFrame(1, frame); // no renderer yet
    ctl.selectInstrumentTrack("keys");
    ctl.renderTabFrame(2, frame);
    expect(tabInstances[0].render).toHaveBeenCalledWith(2, frame);
  });

  it("updateInstrumentSelector disposes the previous renderer", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    expect(tabInstances.length).toBe(1);
    ctl.updateInstrumentSelector();
    expect(tabInstances[0].dispose).toHaveBeenCalled();
  });

  it("setDisplayMode swaps to the sheet renderer, preserving the track", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]),
      initialDisplayMode: "piano",
    });
    const ctl = initPlaySurfaceController(deps);
    expect(ctl.getDisplayMode()).toBe("piano");
    ctl.selectInstrumentTrack("keys");
    expect(tabInstances.length).toBe(1);
    ctl.setDisplayMode("sheet");
    expect(ctl.getDisplayMode()).toBe("sheet");
    expect(tabInstances[0].dispose).toHaveBeenCalled();
    expect(sheetInstances.length).toBe(1);
    expect(sheetInstances[0].setTrack).toHaveBeenCalled();
    expect(ctl.getActiveTabInstrument()).toBe("keys");
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
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("keys")]),
      initialDisplayMode: "sheet",
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    expect(sheetInstances.length).toBe(1);
    expect(tabInstances.length).toBe(0);
  });
});

describe("playSurfaceController guitar views", () => {
  const fretToggle = () => document.getElementById("fretViewToggle") as HTMLDivElement;
  const keysToggle = () => document.getElementById("displayModeToggle") as HTMLDivElement;
  const viewBtn = (view: string) => fretToggle().querySelector<HTMLButtonElement>(`button[data-view="${view}"]`)!;

  beforeEach(() => {
    stageDom();
    tabInstances.length = 0;
    sheetInstances.length = 0;
    guitarInstances.length = 0;
    window.localStorage.clear();
  });

  it("draws a bass or guitar part with the guitar views, starting on tab scroll", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("lead_guitar")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("lead_guitar");

    expect(tabInstances.length).toBe(0);
    expect(guitarInstances.map((g) => g.view)).toEqual(["tab"]);
    expect(guitarInstances[0].setTrack).toHaveBeenCalled();
    // The guitar views are offered, piano/sheet for keys is not.
    expect(fretToggle().hidden).toBe(false);
    expect(keysToggle().hidden).toBe(true);
    expect(viewBtn("tab").classList.contains("isActive")).toBe(true);
  });

  it("remembers each role's view on its own", () => {
    window.localStorage.setItem("auralprimer.fretView.bass", "coach");
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass"), track("rhythm_guitar")]),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    expect(guitarInstances.at(-1)!.view).toBe("coach");

    ctl.selectInstrumentTrack("rhythm_guitar");
    viewBtn("highway").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(guitarInstances.at(-1)!.view).toBe("highway");
    expect(window.localStorage.getItem("auralprimer.fretView.rhythm_guitar")).toBe("highway");
    expect(window.localStorage.getItem("auralprimer.fretView.bass")).toBe("coach");
    expect(ctl.getFrettedView("bass")).toBe("coach");
  });

  it("switching view rebuilds the surface with the same part", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    viewBtn("lanes").dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(guitarInstances[0].dispose).toHaveBeenCalled();
    expect(guitarInstances[1].view).toBe("lanes");
    expect(guitarInstances[1].setTrack).toHaveBeenCalled();
    expect(viewBtn("lanes").classList.contains("isActive")).toBe(true);
    expect(viewBtn("tab").classList.contains("isActive")).toBe(false);
    expect(ctl.getActiveTabInstrument()).toBe("bass");
  });

  it("offers sheet music for a fretted part too", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    ctl.setFrettedView("bass", "sheet");
    expect(sheetInstances.length).toBe(1);
    expect(guitarInstances[0].dispose).toHaveBeenCalled();
  });

  it("swaps renderer when the part on screen changes kind, and the keys toggle comes back", () => {
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass"), track("keys")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("keys");
    ctl.selectInstrumentTrack("bass");
    expect(tabInstances[0].dispose).toHaveBeenCalled();
    expect(guitarInstances.length).toBe(1);
    ctl.selectInstrumentTrack("keys");
    expect(guitarInstances[0].dispose).toHaveBeenCalled();
    expect(tabInstances.length).toBe(2);
    expect(keysToggle().hidden).toBe(false);
    expect(fretToggle().hidden).toBe(true);
  });

  it("gives a guitar player's whole-surface view the stage, and keeps tab as the strip", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("lead_guitar")]),
      playersPanel: makePlayersPanel({ primary: "lead_guitar", preferred: "lead_guitar", playerCount: 1 }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("lead_guitar");
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(false);

    ctl.setFrettedView("lead_guitar", "highway");
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(true);
    expect(deps.appMainEl.classList.contains("isWideSoloKeys")).toBe(true);

    ctl.setFrettedView("lead_guitar", "tab");
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(false);
  });

  it("offers frets or Nashville numbers per part, and hands the choice to the surface", () => {
    const row = () => document.getElementById("fretNumbersRow") as HTMLDivElement;
    const btn = (attr: string, v: string) => row().querySelector<HTMLButtonElement>(`button[data-${attr}="${v}"]`)!;
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass"), track("lead_guitar")]) });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");

    // Frets by default; Chords and Count from wait until numbers are on.
    expect(row().hidden).toBe(false);
    expect(guitarInstances[0].setNumbering).toHaveBeenLastCalledWith({ labels: "frets", chords: "numbers", count: "song" });
    expect(btn("labels", "frets").classList.contains("isActive")).toBe(true);
    expect((document.getElementById("chordsLabel") as HTMLElement).hidden).toBe(true);

    btn("labels", "numbers").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    btn("chords", "both").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    btn("count", "relative").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(guitarInstances[0].setNumbering).toHaveBeenLastCalledWith({ labels: "numbers", chords: "both", count: "relative" });
    expect((document.getElementById("chordsLabel") as HTMLElement).hidden).toBe(false);
    expect(btn("chords", "both").classList.contains("isActive")).toBe(true);
    expect(JSON.parse(window.localStorage.getItem("auralprimer.fretNumbers.bass")!)).toEqual({
      labels: "numbers",
      chords: "both",
      count: "relative",
    });

    // Another part keeps its own.
    ctl.selectInstrumentTrack("lead_guitar");
    expect(ctl.getFretNumbering("lead_guitar").labels).toBe("frets");
    expect(btn("labels", "frets").classList.contains("isActive")).toBe(true);
  });

  it("reads a stored numbering field by field, defaulting anything unknown", () => {
    window.localStorage.setItem("auralprimer.fretNumbers.bass", JSON.stringify({ labels: "numbers", chords: "nonsense" }));
    const deps = makeDeps({ getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]) });
    const ctl = initPlaySurfaceController(deps);
    expect(ctl.getFretNumbering("bass")).toEqual({ labels: "numbers", chords: "numbers", count: "song" });
  });

  it("hides the labels row for sheet music, and gives the Nashville neck the stage", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass")]),
      playersPanel: makePlayersPanel({ primary: "bass", preferred: "bass" }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.selectInstrumentTrack("bass");
    ctl.setFrettedView("bass", "sheet");
    expect((document.getElementById("fretNumbersRow") as HTMLDivElement).hidden).toBe(true);
    ctl.setFrettedView("bass", "nashville");
    expect(guitarInstances.at(-1)!.view).toBe("nashville");
    expect((document.getElementById("fretNumbersRow") as HTMLDivElement).hidden).toBe(false);
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(true);
  });

  it("does not hand the stage to someone else's part", () => {
    const deps = makeDeps({
      getSelectedMelodicTracks: vi.fn().mockReturnValue([track("bass"), track("lead_guitar")]),
      playersPanel: makePlayersPanel({ primary: "bass", preferred: "bass" }),
    });
    const ctl = initPlaySurfaceController(deps);
    ctl.setFrettedView("lead_guitar", "coach");
    ctl.selectInstrumentTrack("lead_guitar");
    expect(deps.playSurfaceEl.classList.contains("isPianoPrimary")).toBe(false);
  });
});
