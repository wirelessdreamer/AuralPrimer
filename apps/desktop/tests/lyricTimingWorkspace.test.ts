// @vitest-environment jsdom
/**
 * Execution tests for the lyric-timing workspace controller (AuraWave-style
 * DAW lyric editor). Covers the pure helpers (normalizeLyrics/toLyricsJson) and
 * the controller's open/load/empty/save/add/delete/split/select/edit/transport/
 * navigation paths. SpectrogramEditor + Tauri invoke are mocked; no WebGL,
 * network, or real timers.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invokeMock(...a) }));

const spectroInstance = {
  load: vi.fn().mockResolvedValue(undefined),
  setTime: vi.fn(),
  getDurationSec: vi.fn(() => 30),
  getViewTimeWindow: vi.fn(() => ({ start: 0, end: 30 })),
  setViewTimeWindow: vi.fn(),
  scrollTimeIntoView: vi.fn(),
  resetView: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
};
vi.mock("../src/spectrogramEditor", () => ({
  SpectrogramEditor: vi.fn(function (this: unknown, _el: HTMLElement, _opts: unknown) {
    return spectroInstance;
  }),
}));

import {
  initLyricTimingWorkspace,
  normalizeLyrics,
  toLyricsJson,
  type LyricTimingDeps,
} from "../src/lyricTimingWorkspace";

const REQUIRED: ReadonlyArray<readonly [string, string]> = [
  ["lyricSongTitle", "div"], ["lyricReloadBtn", "button"], ["lyricSaveBtn", "button"],
  ["lyricBack", "div"], ["lyricTransport", "div"], ["lyricPlayBtn", "button"],
  ["lyricStopBtn", "button"], ["lyricTimeReadout", "div"], ["lyricScrub", "input"],
  ["lyricSplitBtn", "button"], ["lyricAddBtn", "button"], ["lyricDeleteBtn", "button"],
  ["lyricJumpSelect", "select"], ["lyricSpeedSelect", "select"], ["lyricStage", "div"],
  ["lyricOverlay", "canvas"], ["lyricOverview", "canvas"], ["lyricBody", "div"],
  ["lyricList", "div"], ["lyricInspector", "div"], ["lyricEmpty", "div"],
];

function stageDom(): void {
  document.body.innerHTML = "";
  const active = document.createElement("div");
  active.className = "isActive";
  document.body.appendChild(active);
  const route = document.createElement("div");
  route.id = "lyricRoute";
  active.appendChild(route);
  for (const [id, tag] of REQUIRED) {
    const el = document.createElement(tag);
    el.id = id;
    route.appendChild(el);
  }
  const scrub = document.getElementById("lyricScrub") as HTMLInputElement;
  scrub.type = "range"; scrub.min = "0"; scrub.max = "1000"; scrub.value = "0";
  const speed = document.getElementById("lyricSpeedSelect") as HTMLSelectElement;
  for (const v of ["0.5", "1", "2"]) {
    const o = document.createElement("option"); o.value = v; o.textContent = `${v}x`; speed.appendChild(o);
  }
  (document.getElementById("lyricSpeedSelect") as HTMLSelectElement).value = "1";
}

const el = (id: string) => document.getElementById(id)!;
const flush = () => new Promise<void>((r) => setTimeout(r, 0));
const makeDeps = (): LyricTimingDeps => ({ setStatus: vi.fn(), onBack: vi.fn() });

const SAMPLE = [
  { t: 0.5, d: 0.3, w: "hel" },
  { t: 0.8, d: 0.4, w: "lo" },
  { t: 2.0, d: 0.5, w: "world" },
];

const GEOM = {
  fmin_midi: 24, bins_per_octave: 12, bins_per_semitone: 1, n_bins: 84,
  n_frames: 100, frames_per_sec: 10, duration_sec: 30, db_floor: -80, db_ceil: 0,
  bit_depth: 16, packing: "rg16", row_0_is: "lowest_pitch", tile_width: 4096,
  tiles: [{ file: "t0.png", col_start: 0, col_end: 100 }],
};

function mockInvoke(opts: { lyrics?: unknown; spectro?: boolean; relPaths?: string[] } = {}): void {
  const { lyrics = SAMPLE, spectro = true, relPaths } = opts;
  invokeMock.mockImplementation((cmd: string, args: { relPath?: string }) => {
    const rel = args?.relPath ?? "";
    if (relPaths) relPaths.push(`${cmd}:${rel}`);
    if (cmd === "read_auralsong_json" && rel.endsWith("spectrogram.json")) {
      return spectro ? Promise.resolve(GEOM) : Promise.reject(new Error("no spectrogram"));
    }
    if (cmd === "read_auralsong_bytes") return Promise.resolve([1, 2, 3]);
    if (cmd === "read_auralsong_json" && rel.endsWith("lyrics.json")) {
      return lyrics ? Promise.resolve(lyrics) : Promise.reject(new Error("no lyrics"));
    }
    if (cmd === "write_auralsong_features_json") return Promise.resolve(undefined);
    return Promise.reject(new Error(`unexpected ${cmd} ${rel}`));
  });
}

async function open(container = "/songs/x.feedpak", opts?: Parameters<typeof mockInvoke>[0]) {
  mockInvoke(opts);
  const deps = makeDeps();
  const h = initLyricTimingWorkspace(deps);
  await h.openForAuralSong(container);
  await flush();
  return { h, deps };
}

beforeEach(() => {
  vi.clearAllMocks();
  invokeMock.mockReset();
  spectroInstance.getDurationSec.mockReturnValue(30);
  spectroInstance.getViewTimeWindow.mockReturnValue({ start: 0, end: 30 });
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
  vi.stubGlobal("Blob", class { constructor(public p: unknown[], public o: unknown) {} });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  // jsdom doesn't implement scrollIntoView; selectClip(_, scroll=true) uses it.
  (HTMLElement.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
  stageDom();
});

describe("pure helpers", () => {
  it("normalizeLyrics filters junk, clamps, and sorts by onset", () => {
    const out = normalizeLyrics([
      { t: 2, d: 0.5, w: "b" },
      { t: 0, d: 0.001, w: "a" }, // d clamped up to the floor
      { t: -1, d: 0.2, w: "neg" }, // t clamped to 0
      { t: "x", d: 1, w: "bad" }, // non-finite t -> dropped
      null, 42, "nope", // non-objects -> dropped
    ]);
    expect(out.map((c) => c.w)).toEqual(["a", "neg", "b"]);
    expect(out[0]!.d).toBeGreaterThan(0.001);
    expect(out[1]!.t).toBe(0);
  });

  it("normalizeLyrics returns [] for non-arrays", () => {
    expect(normalizeLyrics(null)).toEqual([]);
    expect(normalizeLyrics({ t: 1 })).toEqual([]);
  });

  it("toLyricsJson sorts, rounds to 2dp, and clamps duration", () => {
    const out = toLyricsJson([
      { t: 1.005, d: 0.001, w: "x" },
      { t: 0.123, d: 0.4567, w: "y" },
    ]);
    expect(out[0]!.w).toBe("y");
    expect(out[0]!.t).toBe(0.12);
    expect(out[1]!.d).toBeGreaterThanOrEqual(0.03);
    expect(out[1]!.t).toBe(1.0);
  });
});

describe("initLyricTimingWorkspace", () => {
  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initLyricTimingWorkspace(makeDeps())).toThrow(/required DOM/);
  });

  it("exposes a handle with null container path", () => {
    const h = initLyricTimingWorkspace(makeDeps());
    expect(h.getCurrentContainerPath()).toBeNull();
  });

  it("renders the empty state when the pack has no lyrics", async () => {
    const { deps } = await open("/songs/x.feedpak", { lyrics: null });
    expect(el("lyricEmpty").style.display).toBe("flex");
    expect(el("lyricStage").style.display).toBe("none");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("No timed lyrics"));
  });

  it("loads lyrics: renders the clip list, enables transport, builds jump options", async () => {
    const { deps } = await open();
    expect(el("lyricEmpty").style.display).toBe("none");
    expect(el("lyricList").querySelectorAll(".ltListItem").length).toBe(3);
    expect((el("lyricPlayBtn") as HTMLButtonElement).disabled).toBe(false);
    expect((el("lyricJumpSelect") as HTMLSelectElement).options.length).toBeGreaterThan(1);
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Loaded 3 syllables"));
  });

  it("reads spectrogram + lyrics from aural/ for a feedpak container", async () => {
    const relPaths: string[] = [];
    await open("/songs/x.feedpak", { relPaths });
    expect(relPaths.some((p) => p.includes("aural/spectrogram/"))).toBe(true);
    expect(relPaths).toContain("read_auralsong_json:aural/lyrics.json");
  });

  it("derives a duration from the lyrics when no spectrogram exists", async () => {
    spectroInstance.getDurationSec.mockReturnValue(0);
    const { deps } = await open("/songs/x.feedpak", { spectro: false });
    expect(el("lyricEmpty").style.display).toBe("none");
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Loaded 3 syllables"));
  });
});

describe("editing", () => {
  it("selecting a clip from the list populates the inspector + enables actions", async () => {
    await open();
    (el("lyricList").querySelectorAll<HTMLElement>(".ltListItem")[2]!).dispatchEvent(new MouseEvent("click"));
    expect(el("lyricInspector").querySelectorAll("input").length).toBe(4); // text/start/dur/end
    expect((el("lyricSplitBtn") as HTMLButtonElement).disabled).toBe(false);
    expect((el("lyricDeleteBtn") as HTMLButtonElement).disabled).toBe(false);
  });

  it("editing the text field marks dirty + updates the list", async () => {
    await open();
    el("lyricList").querySelector<HTMLElement>(".ltListItem")!.dispatchEvent(new MouseEvent("click"));
    const textInput = el("lyricInspector").querySelector<HTMLInputElement>("input.ltText")!;
    textInput.value = "HEY";
    textInput.dispatchEvent(new Event("input"));
    expect(el("lyricSaveBtn").textContent).toBe("Save *");
    expect(el("lyricList").innerHTML).toContain("HEY");
  });

  it("editing the start field re-sorts + marks dirty", async () => {
    await open();
    el("lyricList").querySelector<HTMLElement>(".ltListItem")!.dispatchEvent(new MouseEvent("click"));
    const startInput = el("lyricInspector").querySelectorAll<HTMLInputElement>("input.ltNum")[0]!;
    startInput.value = "5.0"; // push first clip past the others
    startInput.dispatchEvent(new Event("change"));
    expect(el("lyricSaveBtn").textContent).toBe("Save *");
  });

  it("Add inserts a syllable at the playhead", async () => {
    await open();
    el("lyricAddBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("lyricList").querySelectorAll(".ltListItem").length).toBe(4);
    expect(el("lyricSaveBtn").textContent).toBe("Save *");
  });

  it("Delete removes the selected syllable", async () => {
    await open();
    el("lyricList").querySelector<HTMLElement>(".ltListItem")!.dispatchEvent(new MouseEvent("click"));
    el("lyricDeleteBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("lyricList").querySelectorAll(".ltListItem").length).toBe(2);
  });

  it("Split divides the selected clip at the playhead", async () => {
    const { deps } = await open();
    // select the "world" clip (t=2.0,d=0.5) and move the playhead inside it.
    el("lyricList").querySelectorAll<HTMLElement>(".ltListItem")[2]!.dispatchEvent(new MouseEvent("click"));
    const scrub = el("lyricScrub") as HTMLInputElement;
    scrub.value = String(Math.round((2.2 / 30) * 1000));
    scrub.dispatchEvent(new Event("input"));
    el("lyricSplitBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("lyricList").querySelectorAll(".ltListItem").length).toBe(4);
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Split into 2"));
  });
});

describe("save", () => {
  it("writes a schema-valid sorted [{t,d,w}] array + clears dirty", async () => {
    await open();
    el("lyricAddBtn").dispatchEvent(new MouseEvent("click")); // make dirty
    el("lyricSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    const call = invokeMock.mock.calls.find((c) => c[0] === "write_auralsong_features_json")!;
    expect(call).toBeTruthy();
    const payload = (call[1] as { relPath: string; value: Array<{ t: number; d: number; w: string }> });
    expect(payload.relPath).toBe("aural/lyrics.json");
    expect(Array.isArray(payload.value)).toBe(true);
    for (const item of payload.value) {
      expect(item).toHaveProperty("t");
      expect(item).toHaveProperty("d");
      expect(item).toHaveProperty("w");
    }
    // sorted by onset
    const ts = payload.value.map((v) => v.t);
    expect(ts).toEqual([...ts].sort((a, b) => a - b));
    expect(el("lyricSaveBtn").textContent).toBe("Save");
  });

  it("surfaces a save error", async () => {
    const { deps } = await open();
    invokeMock.mockImplementation((cmd: string) =>
      cmd === "write_auralsong_features_json"
        ? Promise.reject(new Error("disk full"))
        : Promise.resolve(undefined));
    el("lyricSaveBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(deps.setStatus).toHaveBeenCalledWith(expect.stringContaining("Save failed"));
  });
});

describe("transport + navigation", () => {
  it("play flips the button; stop resets it", async () => {
    await open();
    el("lyricPlayBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("lyricPlayBtn").textContent).toBe("⏸");
    el("lyricStopBtn").dispatchEvent(new MouseEvent("click"));
    expect(el("lyricPlayBtn").textContent).toBe("▶");
  });

  it("Space toggles transport; Delete removes the selected clip", async () => {
    await open();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    expect(el("lyricPlayBtn").textContent).toBe("⏸");
    el("lyricList").querySelector<HTMLElement>(".ltListItem")!.dispatchEvent(new MouseEvent("click"));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    expect(el("lyricList").querySelectorAll(".ltListItem").length).toBe(2);
  });

  it("scrubbing seeks the playhead", async () => {
    await open();
    const scrub = el("lyricScrub") as HTMLInputElement;
    scrub.value = "500"; // 15s of 30
    scrub.dispatchEvent(new Event("change"));
    expect(spectroInstance.setTime).toHaveBeenCalledWith(15);
  });

  it("jump select recenters the view; '—' resets it", async () => {
    await open();
    const jump = el("lyricJumpSelect") as HTMLSelectElement;
    jump.value = "2"; // a syllable onset
    jump.dispatchEvent(new Event("change"));
    expect(spectroInstance.setViewTimeWindow).toHaveBeenCalled();
    jump.value = "";
    jump.dispatchEvent(new Event("change"));
    expect(spectroInstance.resetView).toHaveBeenCalled();
  });

  it("speed change does not throw and keeps playing", async () => {
    await open();
    el("lyricPlayBtn").dispatchEvent(new MouseEvent("click"));
    const speed = el("lyricSpeedSelect") as HTMLSelectElement;
    speed.value = "2";
    expect(() => speed.dispatchEvent(new Event("change"))).not.toThrow();
  });

  it("reload re-opens the current container; back calls onBack", async () => {
    const { h, deps } = await open();
    invokeMock.mockClear();
    el("lyricReloadBtn").dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(invokeMock).toHaveBeenCalled();
    el("lyricBack").dispatchEvent(new MouseEvent("click"));
    expect(deps.onBack).toHaveBeenCalled();
    expect(h.getCurrentContainerPath()).toBe("/songs/x.feedpak");
  });
});
