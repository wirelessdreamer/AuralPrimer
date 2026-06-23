// @vitest-environment jsdom
/**
 * Execution tests for the Refine workspace controller. Imports + runs
 * initRefineWorkspace(deps), drives the open/render/focus/pick/accept/skip/
 * save/instrument-change paths, and the spectrogram load + onNotesChanged
 * manual-decision path. All collaborators (refineCandidatesIo, spectrogram
 * editor, audition engine, Tauri invoke) are mocked so the run is fully
 * deterministic — no network, no real Tauri, no Web Audio, no timers.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mocks — declared before importing the SUT so the module graph picks them up.
// ---------------------------------------------------------------------------

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invokeMock(...a) }));

const spectroInstance = {
  load: vi.fn().mockResolvedValue(undefined),
  setNotes: vi.fn(),
  setTime: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
};
let lastSpectroOpts: { onNotesChanged?: (notes: unknown[]) => void } | null = null;
vi.mock("../src/spectrogramEditor", () => ({
  SpectrogramEditor: vi.fn(function (this: unknown, _el: HTMLElement, opts: { onNotesChanged?: (n: unknown[]) => void }) {
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

// refineCandidatesIo: keep the real pure helpers (activeNotesForRegion,
// pickCandidateForRegion) but stub the Tauri-bound loadSession/saveDecisions.
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

// ---------------------------------------------------------------------------
// DOM staging — every id read via getElementById in initRefineWorkspace.
// ---------------------------------------------------------------------------

const REQUIRED_IDS = [
  ["refineRoute", "div"],
  ["refineSongTitle", "div"],
  ["refineInstLabel", "div"],
  ["refineInstrumentSelect", "select"],
  ["refineReloadBtn", "button"],
  ["refineSaveBtn", "button"],
  ["refineBack", "div"],
  ["refineWorkSummary", "div"],
  ["refineWorklist", "div"],
  ["refineFocusedRegion", "div"],
  ["refineFocusedTime", "div"],
  ["refineFocusedChord", "div"],
  ["refinePitchRuler", "div"],
  ["refineTimelineSurface", "div"],
  ["refineTimelineCanvas", "canvas"],
  ["refinePalette", "div"],
  ["refineRegionInfo", "div"],
  ["refineAcceptBtn", "button"],
  ["refineSkipBtn", "button"],
  ["refineEmpty", "div"],
  ["refineGrid", "div"],
  ["refineEmptyCmd", "div"],
  ["refineAuditionPlay", "button"],
  ["refineAuditionStop", "button"],
  ["refineAuditionStatus", "div"],
] as const;

function stageDom(): void {
  document.body.innerHTML = "";
  // Make the route active so the global keydown handler runs (root.closest(".isActive")).
  const active = document.createElement("div");
  active.className = "isActive";
  document.body.appendChild(active);
  for (const [id, tag] of REQUIRED_IDS) {
    const el = document.createElement(tag);
    el.id = id;
    active.appendChild(el);
  }
  // Populate the instrument <select> with options so selectedIndex/text resolve.
  const select = document.getElementById("refineInstrumentSelect") as HTMLSelectElement;
  for (const [value, text] of [["keys", "Keys"], ["bass", "Bass"]]) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    select.appendChild(opt);
  }
}

const el = (id: string) => document.getElementById(id)!;
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

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
          id: "r0",
          t_start: 1,
          t_end: 4,
          section_label: "Verse",
          hot_spot_type: "octave_ghost",
          confidence: 0.42,
          chord: "Cmaj7",
          auto_picked: "a",
          candidate_scores: { a: 0.8, b: 0.3 },
          candidate_notes: {
            a: [note(1.5, 60), note(2.0, 200), note(2.5, 10)], // includes out-of-range pitches
            b: [note(1.5, 64)],
          },
        },
        {
          id: "r1",
          t_start: 5,
          t_end: 8,
          hot_spot_type: "clean",
          confidence: 0.95,
          auto_picked: "b",
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

beforeEach(() => {
  vi.clearAllMocks();
  lastSpectroOpts = null;
  auditionInstance.isPlaying.mockReturnValue(false);
  invokeMock.mockReset();
  loadSessionMock.mockReset();
  saveDecisionsMock.mockReset();
  spectroInstance.load.mockResolvedValue(undefined);
  // jsdom canvas getContext returns null by default — provide a stub 2D ctx so
  // the timeline render path executes instead of bailing on a null context.
  const ctxStub: Partial<CanvasRenderingContext2D> = {
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    setTransform: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
  };
  (HTMLCanvasElement.prototype.getContext as unknown) = vi.fn(() => ctxStub);
  // URL.createObjectURL / revokeObjectURL aren't in jsdom.
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:fake"),
    revokeObjectURL: vi.fn(),
  });
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
    expect(el("refineGrid").style.display).toBe("none");
    expect(el("refineEmptyCmd").textContent).toContain("aural_ingest refine-candidates");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("No refine_candidates"));
    expect(h.getCurrentContainerPath()).toBe("/songs/x.auralsong");
  });

  it("loads a session, renders worklist/timeline/palette, focuses first hot spot", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    // No spectrogram artifact -> invoke rejects -> classic timeline path.
    invokeMock.mockRejectedValue(new Error("not found"));
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();

    expect(el("refineGrid").style.display).toBe("grid");
    // Worklist rendered with group headings + region rows.
    expect(el("refineWorklist").innerHTML).toContain("Octave ghosts");
    expect(el("refineWorklist").querySelectorAll(".rfRegionRow").length).toBe(2);
    // Focused on r0 (first non-clean, unreviewed).
    expect(el("refineFocusedRegion").textContent).toBe("Verse");
    expect(el("refineFocusedTime").textContent).toContain("–");
    expect(el("refineFocusedChord").textContent).toContain("Cmaj7");
    // Palette swatches with keys + AUTO badge.
    expect(el("refinePalette").querySelectorAll(".rfSwatch").length).toBe(2);
    expect(el("refinePalette").innerHTML).toContain("AUTO");
    // Region info populated.
    expect(el("refineRegionInfo").innerHTML).toContain("Confidence");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Loaded 2 regions"));
  });

  it("clicking a worklist row focuses that region", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    const rows = el("refineWorklist").querySelectorAll<HTMLElement>(".rfRegionRow");
    // Click the clean region (r1).
    const r1 = Array.from(rows).find((r) => r.dataset.regionId === "r1")!;
    r1.dispatchEvent(new MouseEvent("click"));
    expect(el("refineFocusedTime").textContent).toContain("0:05");
  });

  it("picking a candidate via palette click marks dirty + updates region info", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    const swatch = Array.from(el("refinePalette").querySelectorAll<HTMLElement>(".rfSwatch"))
      .find((s) => s.dataset.candidateId === "b")!;
    swatch.dispatchEvent(new MouseEvent("click"));
    expect(el("refineSaveBtn").textContent).toBe("Save *");
    expect(el("refineRegionInfo").innerHTML).toContain("Your pick");
    // Worklist shows the region as accepted now.
    expect(el("refineWorklist").innerHTML).toContain("isAccepted");
  });

  it("picking a candidate while audition is playing swaps the notes live", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    auditionInstance.isPlaying.mockReturnValue(true);
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    const swatch = Array.from(el("refinePalette").querySelectorAll<HTMLElement>(".rfSwatch"))
      .find((s) => s.dataset.candidateId === "b")!;
    swatch.dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.updateNotes).toHaveBeenCalled();
  });

  it("accept button accepts auto-pick then advances; skip advances", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    // Accept r0 -> auto-picks "a" and advances to r1.
    el("refineAcceptBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("refineSaveBtn").textContent).toBe("Save *");
    expect(el("refineFocusedTime").textContent).toContain("0:05"); // r1
    // Skip from r1 (last region): loops to first unreviewed -> none left unreviewed
    // among r1 only? r0 reviewed, r1 not -> still focuses r1.
    el("refineSkipBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("refineFocusedTime").textContent).toContain("0:05");
  });

  it("save calls saveDecisions, clears dirty, reports status", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    saveDecisionsMock.mockResolvedValue(undefined);
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    el("refineAcceptBtn").dispatchEvent(new MouseEvent("click")); // make dirty
    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(saveDecisionsMock).toHaveBeenCalled();
    expect(el("refineSaveBtn").textContent).toBe("Save");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Saved"));
  });

  it("save surfaces an error when saveDecisions rejects", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    saveDecisionsMock.mockRejectedValue(new Error("disk full"));
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Save failed"));
  });

  it("save is a no-op when no session loaded", async () => {
    const h = initRefineWorkspace(makeDeps());
    // No openForAuralSong -> session null.
    el("refineSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(saveDecisionsMock).not.toHaveBeenCalled();
  });

  it("back button stops audition and calls onBack", async () => {
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    el("refineBack").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.stop).toHaveBeenCalled();
    expect(deps.onBack).toHaveBeenCalled();
  });

  it("reload re-opens the current container; no-op without a container", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    // Reload before any open -> no-op (loadSession not called).
    el("refineReloadBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(loadSessionMock).not.toHaveBeenCalled();
    // Open then reload.
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    loadSessionMock.mockClear();
    el("refineReloadBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(loadSessionMock).toHaveBeenCalled();
  });

  it("instrument change re-opens for the new instrument", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    loadSessionMock.mockClear();
    const select = el("refineInstrumentSelect") as HTMLSelectElement;
    select.value = "bass";
    select.dispatchEvent(new Event("change"));
    await flush();
    expect(el("refineInstLabel").textContent).toBe("Bass");
    expect(loadSessionMock).toHaveBeenCalledWith("/songs/x.auralsong", "bass");
  });

  it("openForAuralSong surfaces load errors", async () => {
    loadSessionMock.mockRejectedValue(new Error("boom"));
    const deps = makeDeps();
    const h = initRefineWorkspace(deps);
    await h.openForAuralSong("/songs/x.auralsong");
    expect(el("refineEmpty").style.display).toBe("flex");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Failed to load"));
  });

  it("falls back to first region when no non-clean hot spot is unreviewed", async () => {
    const session = makeSession();
    // Mark r0 reviewed so the first-hotspot search skips it; only clean r1 left.
    session.decisions.set("r0", {
      region_id: "r0",
      candidate_id: "a",
      notes: session.candidates.regions[0]!.candidate_notes.a!,
      edited_at: "now",
    });
    loadSessionMock.mockResolvedValue(session);
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    // Falls back to regions[0] = r0.
    expect(el("refineFocusedRegion").textContent).toBe("Verse");
  });
});

describe("keyboard shortcuts", () => {
  async function open() {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    return h;
  }

  it("number keys pick a candidate", async () => {
    await open();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "2" }));
    expect(el("refineSaveBtn").textContent).toBe("Save *");
  });

  it("Enter accepts, ArrowRight/s skip, ArrowLeft goes back", async () => {
    await open();
    // Advance to r1 with ArrowRight.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
    expect(el("refineFocusedTime").textContent).toContain("0:05");
    // ArrowLeft back to r0.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft" }));
    expect(el("refineFocusedTime").textContent).toContain("0:01");
    // "s" skips forward.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "s" }));
    expect(el("refineFocusedTime").textContent).toContain("0:05");
    // Enter accepts current.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    expect(el("refineSaveBtn").textContent).toBe("Save *");
  });

  it("Space toggles audition", async () => {
    await open();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    expect(auditionInstance.playRegion).toHaveBeenCalled();
  });

  it("ignores keys when route is not active", async () => {
    await open();
    el("refineRoute").parentElement!.classList.remove("isActive");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    // No dirty change because handler bailed.
    expect(el("refineSaveBtn").textContent).toBe("Save");
  });

  it("ignores keys when focus is in an input/select", async () => {
    await open();
    const select = el("refineInstrumentSelect");
    select.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(el("refineSaveBtn").textContent).toBe("Save");
  });
});

describe("audition wiring", () => {
  async function open() {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockRejectedValue(new Error("not found"));
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    return h;
  }

  it("play button starts audition + sets status; stop button stops it", async () => {
    await open();
    el("refineAuditionPlay").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.playRegion).toHaveBeenCalled();
    expect(el("refineAuditionStatus").classList.contains("isPlaying")).toBe(true);
    el("refineAuditionStop").dispatchEvent(new MouseEvent("click"));
    expect(auditionInstance.stop).toHaveBeenCalled();
    expect(el("refineAuditionStatus").classList.contains("isPlaying")).toBe(false);
  });

  it("resize redraws the timeline without throwing", async () => {
    await open();
    expect(() => window.dispatchEvent(new Event("resize"))).not.toThrow();
  });
});

describe("spectrogram overlay", () => {
  it("loads a spectrogram, activates the editor, and a note edit becomes a manual decision", async () => {
    const session = makeSession();
    loadSessionMock.mockResolvedValue(session);
    const geom = {
      fmin_midi: 24,
      tiles: [{ file: "tile0.png", col_start: 0, col_end: 10 }],
      n_frames: 10,
    };
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === "read_auralsong_json") return Promise.resolve(geom);
      if (cmd === "read_auralsong_bytes") return Promise.resolve([1, 2, 3]);
      return Promise.reject(new Error("unexpected"));
    });
    vi.stubGlobal("Blob", class {
      constructor(public parts: unknown[], public opts: unknown) {}
    });
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    await flush();
    await flush();

    expect(spectroInstance.load).toHaveBeenCalled();
    expect(lastSpectroOpts?.onNotesChanged).toBeTypeOf("function");

    // Trigger an edit -> manual decision recorded + worklist re-rendered.
    lastSpectroOpts!.onNotesChanged!([
      { t_on: 1.1, t_off: 1.3, pitch: 61, velocity: 80 },
      { t_on: 1.4, t_off: 1.6, pitch: 62 }, // missing velocity -> defaulted to 100
    ]);
    expect(el("refineSaveBtn").textContent).toBe("Save *");
    expect(session.decisions.get("r0")?.candidate_id).toBe("manual");
    expect(session.decisions.get("r0")?.notes[1]?.velocity).toBe(100);
  });

  it("reads spectrogram tiles from aural/ for a feedpak container", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    const geom = {
      fmin_midi: 24,
      tiles: [{ file: "tile0.png", col_start: 0, col_end: 10 }],
      n_frames: 10,
    };
    const relPaths: string[] = [];
    invokeMock.mockImplementation((cmd: string, args: { relPath?: string }) => {
      if (args?.relPath) relPaths.push(args.relPath);
      if (cmd === "read_auralsong_json") return Promise.resolve(geom);
      if (cmd === "read_auralsong_bytes") return Promise.resolve([1, 2, 3]);
      return Promise.reject(new Error("unexpected"));
    });
    vi.stubGlobal("Blob", class {
      constructor(public parts: unknown[], public opts: unknown) {}
    });
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.feedpak");
    await flush();
    await flush();
    await flush();
    expect(relPaths).toContain("aural/spectrogram/keys/spectrogram.json");
    expect(relPaths).toContain("aural/spectrogram/keys/tile0.png");
  });

  it("treats empty/absent tiles as no artifact (classic timeline)", async () => {
    loadSessionMock.mockResolvedValue(makeSession());
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === "read_auralsong_json") return Promise.resolve({ tiles: [] });
      return Promise.reject(new Error("nope"));
    });
    const h = initRefineWorkspace(makeDeps());
    await h.openForAuralSong("/songs/x.auralsong");
    await flush();
    expect(spectroInstance.load).not.toHaveBeenCalled();
    // Classic timeline canvas stays visible.
    expect(el("refineTimelineCanvas").style.display).not.toBe("none");
  });
});
