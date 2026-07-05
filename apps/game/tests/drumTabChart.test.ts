// @vitest-environment jsdom
/**
 * Tests for the game-side drum_tab.json reader. Covers the pure conversion
 * (drumChartFromTab) — lane→GM MIDI mapping, defensive skipping of bad hits,
 * null on unusable input — and the Tauri-backed loader (loadDrumChartFromTab)
 * with the `read_auralsong_json` invoke mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import { drumChartFromTab, loadDrumChartFromTab } from "../src/drumTabChart";

function tab(hits: Array<{ t: number; p: string; v?: number }>) {
  return { version: 1, name: "drums", kit: [], hits };
}

describe("drumChartFromTab", () => {
  it("maps canonical drum lanes to the 8-lane GM scheme", () => {
    const sel = drumChartFromTab(
      tab([
        { t: 0, p: "kick" },
        { t: 0.5, p: "snare" },
        { t: 1, p: "hihat_closed" },
        { t: 1.5, p: "hihat_open" },
        { t: 2, p: "crash" },
        { t: 2.5, p: "ride" },
        { t: 3, p: "tom_high" },
        { t: 3.5, p: "tom_mid" },
        { t: 4, p: "tom_low" },
      ]),
    );
    expect(sel).not.toBeNull();
    const lanes = sel!.events.map((e) => e.lane);
    expect(lanes).toEqual(["BD", "SD", "HH", "HH", "CY", "RD", "HT", "LT", "FT"]);
    // reason flags the drum_tab.json provenance
    expect(sel!.reason).toBe("drum_tab");
    // events are time-sorted
    expect(sel!.events.map((e) => e.t)).toEqual([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4]);
  });

  it("routes unknown / perc lanes to a snare so no hit is dropped", () => {
    const sel = drumChartFromTab(tab([
      { t: 0, p: "perc" },
      { t: 1, p: "cowbell_of_mystery" },
    ]));
    expect(sel).not.toBeNull();
    expect(sel!.events).toHaveLength(2);
    expect(sel!.events.map((e) => e.lane)).toEqual(["SD", "SD"]);
  });

  it("skips malformed hits (non-finite t, empty/absent lane) but keeps the rest", () => {
    const sel = drumChartFromTab(
      tab([
        { t: 0, p: "kick" },
        { t: Number.NaN, p: "snare" },
        { t: 1, p: "" },
        { t: 2, p: "snare" },
      ] as Array<{ t: number; p: string }>),
    );
    expect(sel).not.toBeNull();
    expect(sel!.events).toHaveLength(2);
    expect(sel!.events.map((e) => e.lane)).toEqual(["BD", "SD"]);
  });

  it("returns null for unusable documents (no hits / empty hits / not an object)", () => {
    expect(drumChartFromTab(null)).toBeNull();
    expect(drumChartFromTab({})).toBeNull();
    expect(drumChartFromTab(tab([]))).toBeNull();
    expect(drumChartFromTab({ hits: "nope" })).toBeNull();
    // all hits invalid -> no events -> null
    expect(drumChartFromTab(tab([{ t: Number.NaN, p: "" }]))).toBeNull();
  });
});

describe("loadDrumChartFromTab", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("reads drum_tab.json at the pack root and returns the converted selection", async () => {
    invoke.mockResolvedValue(tab([{ t: 0, p: "kick" }, { t: 0.5, p: "snare" }]));
    const sel = await loadDrumChartFromTab("/song.feedpak");
    expect(invoke).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/song.feedpak",
      relPath: "drum_tab.json",
    });
    expect(sel).not.toBeNull();
    expect(sel!.events).toHaveLength(2);
    expect(sel!.reason).toBe("drum_tab");
  });

  it("returns null (no throw) when the read rejects — pack has no drum_tab.json", async () => {
    invoke.mockRejectedValue(new Error("not found"));
    await expect(loadDrumChartFromTab("/song.feedpak")).resolves.toBeNull();
  });

  it("returns null when the read yields null or an unusable document", async () => {
    invoke.mockResolvedValueOnce(null);
    await expect(loadDrumChartFromTab("/song.feedpak")).resolves.toBeNull();
    invoke.mockResolvedValueOnce(tab([]));
    await expect(loadDrumChartFromTab("/song.feedpak")).resolves.toBeNull();
  });
});
