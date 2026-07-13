import { describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { drumChartFromTab } from "../src/drumTabChart";

describe("desktop drumTabChart", () => {
  it("converts drum_tab hits into a strict drum chart selection", () => {
    const selection = drumChartFromTab({
      hits: [
        { t: 0.5, p: "kick", v: 100 },
        { t: 0.25, p: "snare" },
        { t: 1.0, p: "hihat_closed", v: 80 },
      ],
    });

    expect(selection).not.toBeNull();
    expect(selection?.reason).toBe("drum_tab");
    expect(selection?.mode).toBe("strict");
    expect(selection?.events.map((ev) => ev.t)).toEqual([0.25, 0.5, 1.0]);
    expect(selection?.events.map((ev) => ev.lane)).toEqual(["SD", "BD", "HH"]);
  });

  it("returns null for empty or malformed tab documents", () => {
    expect(drumChartFromTab(null)).toBeNull();
    expect(drumChartFromTab({ hits: [] })).toBeNull();
    expect(drumChartFromTab({ hits: [{ t: "bad", p: "kick" }] })).toBeNull();
  });
});
