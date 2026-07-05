// @vitest-environment jsdom
/**
 * Execution tests for the Refine workspace controller (DAW redesign). Imports +
 * runs initRefineWorkspace(deps), driving open/load/edit/apply/save/transport/
 * navigation. All collaborators (refineCandidatesIo, SpectrogramEditor, audition
 * engine, Tauri invoke) are mocked so the run is deterministic — no WebGL, no
 * network, no real Tauri, no Web Audio, no timers.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invokeMock = vi.fn();
const convertFileSrcMock = vi.fn((p: string) => `asset://localhost/${encodeURIComponent(p)}`);
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...a: unknown[]) => invokeMock(...a),
  convertFileSrc: (...a: unknown[]) => convertFileSrcMock(...(a as [string])),
}));

// Stateful SpectrogramEditor mock: load/setNotes update an internal note set
// that getNotes returns, mirroring the real editor closely enough to exercise
// the controller's assemble/partition/apply logic.
let editorNotes: Array<{ t_on: number; t_off: number; pitch: number; velocity?: number }> = [];
const spectroInstance = {
  load: vi.fn((_geom: unknown, _urls: unknown, notes: typeof editorNotes) => {
    editorNotes = notes ? [...notes] : [];
    return Promise.resolve();
  }),
  setNotes: vi.fn((n: typeof editorNotes) => { editorNotes = [...n]; }),
  getNotes: vi.fn(() => editorNotes.map((n) => ({ ...n }))),
  setTime: vi.fn(),
  getDurationSec: vi.fn(() => 30),
  getViewTimeWindow: vi.fn(() => ({ start: 0, end: 30 })),
  setViewTimeWindow: vi.fn(),
  scrollTimeIntoView: vi.fn(),
  resetView: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  canUndo: vi.fn(() => false),
  canRedo: vi.fn(() => false),
  undo: vi.fn(),
  redo: vi.fn(),
  setLaneMode: vi.fn(),
  setBeatGrid: vi.fn(),
  setQuant: vi.fn(),
};
let lastSpectroOpts: {
  onNotesChanged?: (notes: unknown[]) => void;
  onSelectionChanged?: (i: number | null) => void;
  onHover?: (info: { time: number; pitch: number } | null) => void;
} | null = null;
vi.mock("../src/spectrogramEditor", () => ({
  SpectrogramEditor: vi.fn(function (this: unknown, _el: HTMLElement, opts: typeof lastSpectroOpts) {
    lastSpectroOpts = opts;
    return spectroInstance;
  }),
}));

const auditionInstance = {
  playRegion: vi.fn(),
  stop: vi.fn(),
  updateNotes: vi.fn(),
  isPlaying: vi.fn().mockReturnValue(false),
};
vi.mock("../src/refineAudition", () => ({
  initRefineAudition: vi.fn(() => auditionInstance),
}));

const loadSessionMock = vi.fn();
const saveDecisionsMock = vi.fn();
vi.mock("../src/refineCandidatesIo", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/refineCandidatesIo")>();
  return {
    ...actual,
    loadSession: (...a: unknown[]) => loadSessionMock(...a),
    saveDecisions: (...a: unknown[]) => saveDecisionsMock(...a),
  };
});

import { initRefineWorkspace, type RefineWorkspaceDeps } from "../src/refineWorkspace";
import type { RefineSession } from "../src/refineCandidatesIo";

const REQUIRED_IDS: ReadonlyArray<readonly [string, string]> = [
  ["refineSongTitle", "div"],
  ["refineInstLabel", "div"],
  ["refineInstrumentSelect", "select"],
  ["refineReloadBtn", "button"],
  ["refineSaveBtn", "button"],
  ["refineBack", "div"],
  ["refineTransport", "div"],
  ["refinePlayBtn", "button"],
  ["refineStopBtn", "button"],
  ["refineTimeReadout", "div"],
  ["refineScrub", "input"],
  ["refineSectionSelect", "select"],
  ["refineAuditionToggle", "input"],
  ["refineEditAuditionToggle", "input"],
  ["refineStage", "div"],
  ["refineInspector", "div"],
  ["refineSelInfo", "div"],
  ["refineCandChips", "div"],
  ["refineUndoBtn", "button"],
  ["refineRedoBtn", "button"],
  ["refineSpeedSelect", "select"],
  ["refineSoloSelect", "select"],
];

function stageDom(): void {
  document.body.innerHTML = "";
  const active = document.createElement("div");
  active.className = "isActive";
  document.body.appendChild(active);

  const route = document.createElement("div");
  route.id = "refineRoute";
  active.appendChild(route);

  for (const [id, tag] of REQUIRED_IDS) {
    const el = document.createElement(tag);
    el.id = id;
    route.appendChild(el);
  }
  // Empty state with the h3 + cmd code the controller mutates.
  const empty = document.createElement("div");
  empty.id = "refineEmpty";
  const h3 = document.createElement("h3");
  h3.textContent = "No candidates yet";
  const cmd = document.createElement("code");
  cmd.id = "refineEmptyCmd";
  empty.append(h3, cmd);
  route.appendChild(empty);

  const select = document.getElementById("refineInstrumentSelect") as HTMLSelectElement;
  for (const [value, text] of [["keys", "Keys"], ["bass", "Bass"]]) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    select.appendChild(opt);
  }
  const scrub = document.getElementById("refineScrub") as HTMLInputElement;
  scrub.type = "range"; scrub.min = "0"; scrub.max = "1000"; scrub.value = "0";
  const toggle = document.getElementById("refineAuditionToggle") as HTMLInputElement;
  toggle.type = "checkbox"; toggle.checked = true;
  const speed = document.getElementById("refineSpeedSelect") as HTMLSelectElement;
  for (const v of ["0.5", "1", "2"]) {
    const o = document.createElement("option"); o.value = v; o.textContent = `${v}x`; speed.appendChild(o);
  }
  speed.value = "1";
  const solo = document.getElementById("refineSoloSelect") as HTMLSelectElement;
  for (const v of ["solo", "all"]) {
    const o = document.createElement("option"); o.value = v; o.textContent = v; solo.appendChild(o);
  }
  solo.value = "solo";
}

const el = (id: string) => document.getElementById(id)!;
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

// Controllable HTMLAudioElement stand-in. The controller streams the stem via
// an <audio> element; we capture the instance so a test can drive currentTime /
// fire loadedmetadata (success) or error (fallback) deterministically — jsdom's
// real Audio never decodes our fake asset:// URLs.
class FakeAudio {
  static instances: FakeAudio[] = [];
  // How load() should resolve: "ok" fires loadedmetadata, "err" fires error,
  // "hang" fires nothing (caller drives events manually).
  static nextResult: "ok" | "err" | "hang" = "ok";
  src = "";
  preload = "";
  currentTime = 0;
  paused = true;
  play = vi.fn(() => { this.paused = false; return Promise.resolve(); });
  pause = vi.fn(() => { this.paused = true; });
  load = vi.fn(() => {
    const mode = FakeAudio.nextResult;
    if (mode === "ok") queueMicrotask(() => this.emit("loadedmetadata"));
    else if (mode === "err") queueMicrotask(() => this.emit("error"));
  });
  private listeners = new Map<string, Set<(ev?: unknown) => void>>();
  constructor() { FakeAudio.instances.push(this); }
  addEventListener(type: string, cb: (ev?: unknown) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(cb);
  }
  removeEventListener(type: string, cb: (ev?: unknown) => void): void {
    this.listeners.get(type)?.delete(cb);
  }
  removeAttribute(_name: string): void { this.src = ""; }
  emit(type: string): void {
    for (const cb of [...(this.listeners.get(type) ?? [])]) cb();
  }
  static latest(): FakeAudio { return FakeAudio.instances[FakeAudio.instances.length - 1]!; }
}

function note(t_on: number, pitch: number, velocity = 90) {
  return { t_on, t_off: t_on + 0.5, pitch, velocity };
}

function makeSession(): RefineSession {
  return {
    containerPath: "/songs/x.auralsong",
    instrument: "keys",
    candidates: {
      version: "0.1.0",
      instrument: "keys",
      song_duration_sec: 30,
      candidates: {
        a: { label: "Stem", color: "#aabbcc" },
        b: { label: "Consensus", color: "#112233" },
      },
      regions: [
        {
          id: "r0", t_start: 1, t_end: 4, section_label: "Verse",
          hot_spot_type: "octave_ghost", confidence: 0.42, chord: "Cmaj7",
          auto_picked: "a",
          candidate_scores: { a: 0.8, b: 0.3 },
          candidate_notes: {
            a: [note(1.5, 60), note(2.0, 67), note(2.5, 72)],
            b: [note(1.5, 64)],
          },
        },
        {
          id: "r1", t_start: 5, t_end: 8,
          hot_spot_type: "clean", confidence: 0.95, auto_picked: "b",
          candidate_scores: { a: 0.2, b: 0.9 },
          candidate_notes: { a: [note(6, 67)], b: [note(6, 67)] },
        },
      ],
    },
    decisions: new Map(),
  };
}

function makeDeps(): RefineWorkspaceDeps {
  return { setStatus: vi.fn(), onBack: vi.fn() };
}

/** Mock invoke so the spectrogram load path succeeds with a 1-tile geometry. */
function mockSpectrogramOk(relPaths?: string[], audioFails = false): void {
  const geom = {
    fmin_midi: 24, bins_per_octave: 12, bins_per_semitone: 1, n_bins: 84,
    n_frames: 100, frames_per_sec: 10, duration_sec: 30, db_floor: -80, db_ceil: 0,
    bit_depth: 16, packing: "rg16", row_0_is: "lowest_pitch", tile_width: 4096,
    tiles: [{ file: "tile0.png", col_start: 0, col_end: 100 }],
  };
  invokeMock.mockImplementation((cmd: string, args: { relPath?: string }) => {
    if (relPaths && args?.relPath) relPaths.push(args.relPath);
    if (cmd === "read_auralsong_json") return Promise.resolve(geom);
    if (cmd === "read_auralsong_bytes") return Promise.resolve([1, 2, 3]);
    if (cmd === "get_auralsong_details") {
      return Promise.resolve({
        manifest_raw: {
          stems: [
            { id: "keys", file: "audio/stems/keys.wav", default: true },
            { id: "bass", file: "audio/stems/bass.wav" },
          ],
        },
      });
    }
    // Native audio engine (same one the game uses).
    if (cmd === "native_audio_load_pack_audio") {
      return audioFails
        ? Promise.reject(new Error("no audio"))
        : Promise.resolve({ mime: "audio/wav", duration_sec: 30, roles: ["keys"] });
    }
    if (cmd === "native_audio_get_state") return Promise.resolve(nativeState());
    if (typeof cmd === "string" && cmd.startsWith("native_audio_")) return Promise.resolve(null);
    return Promise.reject(new Error("unexpected"));
  });
}

/** A minimal NativeAudioState for the get_state poll. */
function nativeState(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    output_host: { id: "default" },
    sample_rate_hz: 48000,
    channels: 2,
    output_device: { name: "dev", channels: 2, sample_rate_hz: 48000 },
    is_playing: true,
    t_sec: 0,
    playback_rate: 1,
    loop_t0_sec: null,
    loop_t1_sec: null,
    has_audio: true,
    output_buffer_frames: null,
    callback_count: 1,
    callback_overrun_count: 0,
    ...over,
  };
}

async function open(container = "/songs/x.auralsong"): Promise<void> {
  loadSessionMock.mockResolvedValue(makeSession());
  mockSpectrogramOk();
  const h = initRefineWorkspace(makeDeps());
  await h.openForAuralSong(container);
  await flush();
}

beforeEach(() => {
  vi.clearAllMocks();
  lastSpectroOpts = null;
  editorNotes = [];
  auditionInstance.isPlaying.mockReturnValue(false);
  invokeMock.mockReset();
  loadSessionMock.mockReset();
  saveDecisionsMock.mockReset();
  spectroInstance.getDurationSec.mockReturnValue(30);
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
  vi.stubGlobal("Blob", class { constructor(public parts: unknown[], public opts: unknown) {} });
  FakeAudio.instances = [];
  FakeAudio.nextResult = "ok";
  convertFileSrcMock.mockClear();
  vi.stubGlobal("Audio", FakeAudio);
  // rAF that does NOT auto-recurse, so transport tick runs once deterministically.
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  stageDom();
});

describe("initRefineWorkspace", () => {
  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initRefineWorkspace(makeDeps())).toThrow(/required DOM/);
  });

  it("initializes and exposes a handle with null container path", () => {
    const h = initRefineWorkspace(makeDeps());
    expect(h.getCurrentContainerPath()).toBeNull();
    expect(typeof h.openForAuralSong).toBe("function");
  });

  it("renders the empty state when no candidates exist", async () => {
    loadSessionMock.mockResolvedValue(null);
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    expect(el("refineEmpty").style.display).toBe("flex");
    expect(el("refineStage").style.display).toBe("none");
    expect(el("refineTransport").style.display).toBe("none");
    expect(el("refineEmptyCmd").textContent).toContain("refine-candidates");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("No refine_candidates"));
    expect(h.getCurrentContainerPath()).toBe("/songs/x.auralsong");
  });

  it("renders the spectrogram empty state when no artifact exists", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === "read_auralsong_json") return Promise.resolve({ tiles: [] });
      return Promise.reject(new Error("nope"));
    });
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    expect(spectroInstance.load).not.toHaveBeenCalled();
    expect(el("refineEmpty").style.display).toBe("flex");
    expect(el("refineEmpty").querySelector("h3")!.textContent).toContain("No spectrogram");
    expect(el("refineEmptyCmd").textContent).toContain("spectrogram");
  });

  it("loads the whole song into the editor + enables transport + builds sections", async () => {
    const deps = makeDeps();
    loadSessionMock.mockResolvedValue(makeSession());
    mockSpectrogramOk();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();

    expect(el("refineEmpty").style.display).toBe("none");
    expect(el("refineStage").style.display).toBe("block");
    // Editor loaded with the assembled full note set (3 from r0.a + 1 from r1.b).
    expect(spectroInstance.load).toHaveBeenCalled();
    expect(editorNotes.length).toBe(4);
    // Transport enabled.
    expect((el("refinePlayBtn") as HTMLButtonElement).disabled).toBe(false);
    // Section jump dropdown built from labelled regions ("Verse" + "Whole song").
    const sectionOpts = (el("refineSectionSelect") as HTMLSelectElement).options;
    expect(sectionOpts.length).toBe(2);
    expect(sectionOpts[1]!.textContent).toContain("Verse");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Loaded 4 notes across 2 regions"));
  });

  it("reads spectrogram artifacts from aural/ for a feedpak container", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    const relPaths: string[] = [];
    mockSpectrogramOk(relPaths);
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.feedpak");
    await flush();
    expect(relPaths).toContain("aural/spectrogram/keys/spectrogram.json");
    expect(relPaths).toContain("aural/spectrogram/keys/tile0.png");
  });

  it("surfaces load errors as the error empty state", async () => {
    loadSessionMock.mockRejectedValue(new Error("boom"));
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    expect(el("refineEmpty").style.display).toBe("flex");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Failed to load"));
  });
});

describe("inspector + candidate apply", () => {
  it("selecting a note shows its pitch + the region's candidate chips", async () => {
    await open();
    lastSpectroOpts!.onSelectionChanged!(0); // first note: pitch 60 = C4, in r0
    expect(el("refineSelInfo").innerHTML).toContain("C4");
    expect(el("refineCandChips").querySelectorAll(".rfCandChip").length).toBe(2);
    expect(el("refineCandChips").innerHTML).toContain("Verse");
  });

  it("cursor hover shows the pitch + time when no note is selected", async () => {
    await open();
    lastSpectroOpts!.onHover!({ time: 7.45, pitch: 60 }); // C4
    expect(el("refineSelInfo").innerHTML).toContain("C4");
    expect(el("refineSelInfo").innerHTML).toContain("cursor");
    lastSpectroOpts!.onHover!(null);
    expect(el("refineSelInfo").textContent).toBe("No note selected");
  });

  it("a selected note takes precedence over the hover readout", async () => {
    await open();
    lastSpectroOpts!.onSelectionChanged!(0);
    lastSpectroOpts!.onHover!({ time: 1, pitch: 72 });
    expect(el("refineSelInfo").innerHTML).not.toContain("cursor");
  });

  it("clearing the selection resets the inspector", async () => {
    await open();
    lastSpectroOpts!.onSelectionChanged!(0);
    lastSpectroOpts!.onSelectionChanged!(null);
    expect(el("refineSelInfo").textContent).toBe("No note selected");
    expect(el("refineCandChips").innerHTML).toBe("");
  });

  it("clicking a candidate chip replaces that region's notes + marks dirty", async () => {
    await open();
    lastSpectroOpts!.onSelectionChanged!(0);
    const chipB = Array.from(el("refineCandChips").querySelectorAll<HTMLElement>(".rfCandChip"))
      .find((c) => c.dataset.cid === "b")!;
    chipB.dispatchEvent(new MouseEvent("click"));
    expect(spectroInstance.setNotes).toHaveBeenCalled();
    // r0's 3 notes replaced by candidate b's 1 note; r1's note kept => 2 total.
    expect(editorNotes.length).toBe(2);
    expect(el("refineSaveBtn").textContent).toBe("Save *");
  });

  it("onNotesChanged marks dirty", async () => {
    await open();
    lastSpectroOpts!.onNotesChanged!(editorNotes);
    expect(el("refineSaveBtn").textContent).toBe("Save *");
  });

  it("undo/redo buttons reflect availability + drive the editor", async () => {
    spectroInstance.canUndo.mockReturnValue(true);
    spectroInstance.canRedo.mockReturnValue(false);
    await open();
    expect((el("refineUndoBtn") as HTMLButtonElement).disabled).toBe(false);
    expect((el("refineRedoBtn") as HTMLButtonElement).disabled).toBe(true);
    el("refineUndoBtn").dispatchEvent(new MouseEvent("click"));
    expect(spectroInstance.undo).toHaveBeenCalled();
    el("refineRedoBtn").dispatchEvent(new MouseEvent("click"));
    expect(spectroInstance.redo).toHaveBeenCalled();
  });
});

describe("save", () => {
  it("partitions the edited notes into per-region manual decisions", async () => {
    const session = makeSession();
    loadSessionMock.mockResolvedValue(session);
    mockSpectrogramOk();
    saveDecisionsMock.mockResolvedValue(undefined);
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();

    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(saveDecisionsMock).toHaveBeenCalled();
    // r0 gets the 3 notes whose onset is in [1,4); r1 gets the 1 note at 6.
    expect(session.decisions.get("r0")?.candidate_id).toBe("manual");
    expect(session.decisions.get("r0")?.notes.length).toBe(3);
    expect(session.decisions.get("r1")?.notes.length).toBe(1);
    expect(el("refineSaveBtn").textContent).toBe("Save");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Saved 4 notes"));
  });

  it("surfaces a save error", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    mockSpectrogramOk();
    saveDecisionsMock.mockRejectedValue(new Error("disk full"));
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Save failed"));
  });

  it("is a no-op when no session is loaded", async () => {
    const h = initRefineWorkspace(makeDeps());
    void h;
    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(saveDecisionsMock).not.toHaveBeenCalled();
  });
});

describe("transport", () => {
  it("play starts the audition + flips the button; stop halts it", async () => {
    await open();
    el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.playRegion).toHaveBeenCalled();
    expect(el("refinePlayBtn").textContent).toBe("⏸");
    el("refineStopBtn").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.stop).toHaveBeenCalled();
    expect(el("refinePlayBtn").textContent).toBe("▶");
  });

  it("Space toggles the transport when the route is active", async () => {
    await open();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    expect(auditionInstance.playRegion).toHaveBeenCalled();
  });

  it("ignores Space when the route is inactive", async () => {
    await open();
    el("refineRoute").parentElement!.classList.remove("isActive");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    expect(auditionInstance.playRegion).not.toHaveBeenCalled();
  });

  it("speed change does not throw", async () => {
    await open();
    const speed = el("refineSpeedSelect") as HTMLSelectElement;
    speed.value = "2";
    expect(() => speed.dispatchEvent(new Event("change"))).not.toThrow();
  });

  it("Solo/All switch reloads the audio through the native engine (role=all)", async () => {
    await open();
    const loadCalls = () => invokeMock.mock.calls.filter((c) => c[0] === "native_audio_load_pack_audio");
    const before = loadCalls().length;
    const solo = el("refineSoloSelect") as HTMLSelectElement;
    solo.value = "all";
    solo.dispatchEvent(new Event("change"));
    await flush();
    const after = loadCalls();
    expect(after.length).toBeGreaterThan(before);
    expect(after.at(-1)![1]).toMatchObject({ role: "all" });
  });

  it("scrubbing seeks the playhead", async () => {
    await open();
    const scrub = el("refineScrub") as HTMLInputElement;
    scrub.value = "500"; // 50% of 30s = 15s
    scrub.dispatchEvent(new Event("change"));
    expect(spectroInstance.setTime).toHaveBeenCalledWith(15);
  });
});

describe("stem audio playback (native engine)", () => {
  const loadCalls = () => invokeMock.mock.calls.filter((c) => c[0] === "native_audio_load_pack_audio");
  const nativeCalls = (cmd: string) => invokeMock.mock.calls.filter((c) => c[0] === cmd);

  it("loads the current instrument's stem into the native engine on open", async () => {
    await open();
    // soloMode defaults true + instrument keys -> load role "keys" (no browser audio).
    expect(loadCalls().at(-1)![1]).toMatchObject({
      containerPath: "/songs/x.auralsong",
      role: "keys",
    });
  });

  it("play() seeks the native engine to the playhead + starts it; the playhead follows the native position", async () => {
    await open();
    const scrub = el("refineScrub") as HTMLInputElement;
    scrub.value = "500"; // 50% of 30s = 15s
    scrub.dispatchEvent(new Event("change"));

    el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
    // seek(playhead) is synchronous in playTransport; play() resolves async.
    expect(nativeCalls("native_audio_seek").some((c) => (c[1] as { tSec: number }).tSec === 15)).toBe(true);
    await flush();
    expect(nativeCalls("native_audio_play").length).toBeGreaterThan(0);

    // The rAF tick reads the native position as the clock. Advance the engine
    // position; the cached position updates after the get_state poll lands.
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === "native_audio_get_state") return Promise.resolve(nativeState({ t_sec: 20 }));
      if (typeof cmd === "string" && cmd.startsWith("native_audio_")) return Promise.resolve(null);
      return Promise.reject(new Error("unexpected"));
    });
    const rafCb = (requestAnimationFrame as unknown as { mock: { calls: Array<[FrameRequestCallback]> } }).mock.calls.at(-1)![0];
    rafCb(0); // triggers a get_state poll (caches position 20)
    await flush();
    rafCb(0); // cached position is now 20 -> playhead 20 (+ sub-ms dead-reckon)
    const lastT = (spectroInstance.setTime as unknown as { mock: { calls: number[][] } }).mock.calls.at(-1)![0];
    expect(lastT).toBeGreaterThanOrEqual(20);
    expect(lastT).toBeLessThan(20.1);
  });

  it("playhead subtracts BOTH the output latency and the A/V offset (formula wiring)", async () => {
    // Drives the refine call-site with a NON-zero calibration + real buffer so a
    // regression that drops/negates the offset or the latency term fails here —
    // every other refine test runs with offset 0 / latency 0.
    const { setAvCalibration } = await import("../src/avOffset");
    await setAvCalibration(200, 0); // effective A/V offset = 0.2s
    try {
      await open();
      el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
      await flush();
      invokeMock.mockImplementation((cmd: string) => {
        if (cmd === "native_audio_get_state")
          return Promise.resolve(nativeState({ t_sec: 20, output_buffer_frames: 480 }));
        if (typeof cmd === "string" && cmd.startsWith("native_audio_")) return Promise.resolve(null);
        return Promise.reject(new Error("unexpected"));
      });
      const rafCb = (requestAnimationFrame as unknown as { mock: { calls: Array<[FrameRequestCallback]> } }).mock.calls.at(-1)![0];
      rafCb(0);
      await flush();
      rafCb(0);
      // audible = 20 − (480*2/48000)·1 − 0.2 = 20 − 0.02 − 0.2 = 19.78
      const lastT = (spectroInstance.setTime as unknown as { mock: { calls: number[][] } }).mock.calls.at(-1)![0];
      expect(lastT).toBeGreaterThanOrEqual(19.78);
      expect(lastT).toBeLessThan(19.81);
    } finally {
      await setAvCalibration(0, 0); // reset the shared module state for other tests
    }
  });

  it("plays the synth alongside the stem when 'Hear notes' is checked", async () => {
    await open();
    el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(nativeCalls("native_audio_play").length).toBeGreaterThan(0);
    expect(auditionInstance.playRegion).toHaveBeenCalled();
  });

  it("falls back to wall-clock + surfaces status when the audio fails to load", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    mockSpectrogramOk(undefined, /* audioFails */ true);
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("no stem audio"));
    // Transport still works: play starts the synth without calling native play.
    const playsBefore = nativeCalls("native_audio_play").length;
    el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(auditionInstance.playRegion).toHaveBeenCalled();
    expect(nativeCalls("native_audio_play").length).toBe(playsBefore);
  });

  it("stop seeks the native engine back to 0", async () => {
    await open();
    el("refinePlayBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    el("refineStopBtn").dispatchEvent(new MouseEvent("click"));
    expect(nativeCalls("native_audio_seek").some((c) => (c[1] as { tSec: number }).tSec === 0)).toBe(true);
  });
});

describe("navigation + wiring", () => {
  it("section jump scrolls the editor view; whole-song resets it", async () => {
    await open();
    const sel = el("refineSectionSelect") as HTMLSelectElement;
    sel.value = "1"; // Verse @ t_start 1
    sel.dispatchEvent(new Event("change"));
    expect(spectroInstance.setViewTimeWindow).toHaveBeenCalledWith(1, expect.any(Number));
    sel.value = "";
    sel.dispatchEvent(new Event("change"));
    expect(spectroInstance.resetView).toHaveBeenCalled();
  });

  it("back stops transport + calls onBack", async () => {
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    void h;
    el("refineBack").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.stop).toHaveBeenCalled();
    expect(deps.onBack).toHaveBeenCalled();
  });

  it("reload re-opens the current container; no-op without one", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    mockSpectrogramOk();
    const h = initRefineWorkspace(makeDeps());
    el("refineReloadBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(loadSessionMock).not.toHaveBeenCalled();
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    loadSessionMock.mockClear();
    el("refineReloadBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(loadSessionMock).toHaveBeenCalled();
  });

  it("instrument change re-opens for the new instrument", async () => {
    await open();
    loadSessionMock.mockClear();
    const select = el("refineInstrumentSelect") as HTMLSelectElement;
    select.value = "bass";
    select.dispatchEvent(new Event("change"));
    await flush();
    expect(el("refineInstLabel").textContent).toBe("Bass");
    expect(loadSessionMock).toHaveBeenCalledWith("/songs/x.auralsong", "bass");
  });
});
