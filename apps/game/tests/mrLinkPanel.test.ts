import { describe, expect, it, vi } from "vitest";

import { buildChart, pickMrTrack } from "../src/mrLinkPanel";
import type { MelodicTrackSelection } from "../src/chartLoader";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const keys: MelodicTrackSelection = {
  role: "keys",
  trackName: "Keys",
  channel: 3,
  notes: [{ t_on: 0, t_off: 1, pitch: 60, velocity: 0.8 }],
};

const rhythm: MelodicTrackSelection = {
  role: "rhythm_guitar",
  trackName: "Rhythm Guitar",
  channel: 1,
  notes: [{ t_on: 0, t_off: 1, pitch: 40, velocity: 0.8 }],
};

const lead: MelodicTrackSelection = {
  role: "lead_guitar",
  trackName: "Lead Guitar",
  channel: 2,
  tuning: [40, 45, 50, 55, 59, 64],
  anchors: [{ t: 0, fret: 5, width: 4 }],
  notes: [
    { t_on: 2, t_off: 2.5, pitch: 69, velocity: 0.7, string: 4, fret: 10 },
    {
      t_on: 1,
      t_off: 1.5,
      pitch: 45,
      velocity: 0.7,
      string: 1,
      fret: 0,
      chord: "A5",
      finger: 1,
      tech: { pm: 1, sl: 7 },
    },
    // Never placed by the fingering: no position to send.
    { t_on: 3, t_off: 3.5, pitch: 64, velocity: 0.7 },
  ],
};

type Chart = {
  role: string;
  tuning?: number[];
  anchors?: unknown[];
  notes: Array<Record<string, unknown>>;
};

describe("pickMrTrack", () => {
  it("keeps sending keys first to a headset that never asked", () => {
    expect(pickMrTrack([lead, keys], null)).toBe(keys);
    expect(pickMrTrack([lead], null)).toBe(lead);
    expect(pickMrTrack([], null)).toBeNull();
  });

  it("sends the part the headset asked for, lead before rhythm for 'guitar'", () => {
    expect(pickMrTrack([keys, rhythm, lead], "guitar")).toBe(lead);
    expect(pickMrTrack([keys, rhythm], "guitar")).toBe(rhythm);
    expect(pickMrTrack([keys, rhythm, lead], "rhythm_guitar")).toBe(rhythm);
  });

  it("falls back rather than sending nothing when the song lacks the part", () => {
    // Nothing would leave the headset drawing the previous song; the chart's
    // role says what was sent instead.
    expect(pickMrTrack([keys, lead], "bass")).toBe(keys);
  });
});

describe("buildChart for a fretted part", () => {
  it("adds strings, frets, tuning and hand positions when the headset asked", () => {
    const chart = buildChart("id", "Song", lead, 120, 4, null, false, "both", true, true) as Chart;

    expect(chart.role).toBe("lead_guitar");
    expect(chart.tuning).toEqual([40, 45, 50, 55, 59, 64]);
    expect(chart.anchors).toEqual([{ t: 0, fret: 5, width: 4 }]);
    // Still sorted by onset, with the tab's extras riding along.
    expect(chart.notes[0]).toMatchObject({
      on: 1,
      pitch: 45,
      s: 1,
      f: 0,
      finger: 1,
      chord: "A5",
      tech: ["pm", "sl"],
    });
    expect(chart.notes[1]).toMatchObject({ on: 2, s: 4, f: 10 });
    expect(chart.notes[1]).not.toHaveProperty("tech");
    expect(chart.notes[2]).not.toHaveProperty("s");
  });

  it("adds the key, chord shapes and bar lines the headset's numbered views need", () => {
    const withShapes: MelodicTrackSelection = {
      ...lead,
      handshapes: [{ t: 1, t_end: 1.5, name: "A5", frets: [-1, 0, 2, 2, -1, -1], fingers: [-1, -1, 1, 3, -1, -1] }],
    };
    const chart = buildChart("id", "Song", withShapes, 120, 4, null, false, "both", true, {
      key: { pitchClass: -3, mode: "minor", inferred: true },
      bars: [0, 2, 4],
    }) as Chart & { key?: unknown; handshapes?: unknown[]; bars?: number[] };

    expect(chart.key).toEqual({ pitchClass: 9, mode: "minor", inferred: true });
    expect(chart.handshapes).toHaveLength(1);
    expect(chart.bars).toEqual([0, 2, 4]);
    expect(chart.notes[0]).toMatchObject({ s: 1, f: 0 });
  });

  it("leaves the key out when the desktop has none, rather than guessing one", () => {
    const chart = buildChart("id", "Song", lead, 120, 4, null, false, "both", true, { key: null }) as Chart;
    expect(chart).not.toHaveProperty("key");
    // A keys part never carries them, asked or not.
    const keysChart = buildChart("id", "Song", keys, 120, 4, null, false, "both", true, {
      key: { pitchClass: 0, mode: "major" },
      bars: [0],
    }) as Chart;
    expect(keysChart).not.toHaveProperty("key");
    expect(keysChart).not.toHaveProperty("bars");
  });

  it("sends exactly the old chart to a headset that never asked", () => {
    const chart = buildChart("id", "Song", lead, 120, 4, null, false, "both", true) as Chart;

    expect(chart).not.toHaveProperty("tuning");
    expect(chart).not.toHaveProperty("anchors");
    for (const note of chart.notes) {
      expect(Object.keys(note).sort()).toEqual(["off", "on", "pitch", "vel"]);
    }
  });

  it("does not put fret fields on a keys chart", () => {
    const chart = buildChart("id", "Song", keys, 120, 4, null, false, "both", true, true) as Chart;
    expect(chart).not.toHaveProperty("tuning");
    expect(chart.notes[0]).not.toHaveProperty("s");
  });
});
