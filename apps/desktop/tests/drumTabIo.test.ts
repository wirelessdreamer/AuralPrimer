/**
 * Unit tests for the drum-tab <-> lane-note conversion behind the Refine
 * workspace's drum-cleanup mode. Covers the bugs that would silently corrupt a
 * chart: lane-order instability, dropped/duplicated hits on round-trip, and
 * losing the tab's non-hit fields (version/kit/unknown extras) on save.
 */
import { describe, it, expect } from "vitest";

import {
  laneOrderForTab,
  hitsToNotes,
  notesToTab,
  drumLaneLabel,
  DRUM_HIT_DISPLAY_SEC,
  DRUM_HIT_DEFAULT_VELOCITY,
  type DrumTabFile,
} from "../src/drumTabIo";

const TAB: DrumTabFile = {
  version: "1.0",
  name: "Test Song",
  kit: [
    { id: "crash" },
    { id: "hihat_closed" },
    { id: "kick" },
    { id: "ride" },
    { id: "snare" },
    { id: "tom_high" },
    { id: "tom_low" },
    { id: "tom_mid" },
  ],
  hits: [
    { t: 1.5, p: "snare", v: 127 },
    { t: 0.5, p: "kick", v: 127 },
    { t: 0.5, p: "hihat_closed", v: 90 },
    { t: 2.25, p: "crash" }, // no velocity
  ],
};

describe("laneOrderForTab", () => {
  it("orders the kit voices canonically (kick bottom, crash top)", () => {
    expect(laneOrderForTab(TAB)).toEqual([
      "kick", "tom_low", "tom_mid", "tom_high", "snare", "hihat_closed", "ride", "crash",
    ]);
  });

  it("appends unknown voices (from kit or hits) alphabetically after canonical ones", () => {
    const tab: DrumTabFile = {
      kit: [{ id: "kick" }, { id: "cowbell" }],
      hits: [{ t: 0, p: "woodblock" }],
    };
    expect(laneOrderForTab(tab)).toEqual(["kick", "cowbell", "woodblock"]);
  });

  it("includes voices that only appear in hits (kit omission doesn't drop them)", () => {
    const tab: DrumTabFile = { kit: [{ id: "kick" }], hits: [{ t: 0, p: "snare" }] };
    expect(laneOrderForTab(tab)).toEqual(["kick", "snare"]);
  });
});

describe("hitsToNotes", () => {
  const lanes = laneOrderForTab(TAB);

  it("maps hits to lane-indexed notes sorted by time", () => {
    const notes = hitsToNotes(TAB, lanes);
    expect(notes.map((n) => n.t_on)).toEqual([0.5, 0.5, 1.5, 2.25]);
    // kick (lane 0) sorts before hihat_closed (lane 5) at the same time.
    expect(notes[0]).toMatchObject({ t_on: 0.5, pitch: lanes.indexOf("kick"), velocity: 127 });
    expect(notes[1]).toMatchObject({ pitch: lanes.indexOf("hihat_closed"), velocity: 90 });
    expect(notes[2]).toMatchObject({ pitch: lanes.indexOf("snare") });
  });

  it("gives hits the display duration and defaults missing velocity", () => {
    const notes = hitsToNotes(TAB, lanes);
    for (const n of notes) expect(n.t_off - n.t_on).toBeCloseTo(DRUM_HIT_DISPLAY_SEC, 9);
    const crash = notes.find((n) => n.pitch === lanes.indexOf("crash"))!;
    expect(crash.velocity).toBe(DRUM_HIT_DEFAULT_VELOCITY);
  });

  it("skips hits whose voice isn't in the lane list instead of mislabeling them", () => {
    const notes = hitsToNotes({ hits: [{ t: 0, p: "kick" }, { t: 1, p: "gong" }] }, ["kick"]);
    expect(notes).toHaveLength(1);
    expect(notes[0]!.pitch).toBe(0);
  });
});

describe("notesToTab", () => {
  const lanes = laneOrderForTab(TAB);

  it("round-trips hits losslessly (t, voice, velocity)", () => {
    const tab2 = notesToTab(hitsToNotes(TAB, lanes), lanes, TAB);
    const key = (h: { t: number; p: string; v?: number }) => `${h.t}|${h.p}|${h.v ?? DRUM_HIT_DEFAULT_VELOCITY}`;
    expect(new Set((tab2.hits ?? []).map(key))).toEqual(new Set((TAB.hits ?? []).map(key)));
    expect(tab2.hits).toHaveLength(TAB.hits!.length);
  });

  it("preserves version, name, kit, and unknown top-level fields", () => {
    const orig: DrumTabFile = { ...TAB, custom_field: { nested: true } };
    const tab2 = notesToTab([], lanes, orig);
    expect(tab2.version).toBe("1.0");
    expect(tab2.name).toBe("Test Song");
    expect(tab2.kit).toEqual(TAB.kit);
    expect(tab2.custom_field).toEqual({ nested: true });
    expect(tab2.hits).toEqual([]);
  });

  it("sorts hits by time then voice for stable diffs", () => {
    const notes = [
      { t_on: 2, t_off: 2.08, pitch: lanes.indexOf("kick"), velocity: 127 },
      { t_on: 1, t_off: 1.08, pitch: lanes.indexOf("snare"), velocity: 127 },
      { t_on: 1, t_off: 1.08, pitch: lanes.indexOf("kick"), velocity: 127 },
    ];
    const tab2 = notesToTab(notes, lanes, TAB);
    expect((tab2.hits ?? []).map((h) => `${h.t}:${h.p}`)).toEqual(["1:kick", "1:snare", "2:kick"]);
  });

  it("drops notes whose lane index is out of range rather than inventing a voice", () => {
    const tab2 = notesToTab([{ t_on: 0, t_off: 0.08, pitch: 99, velocity: 127 }], lanes, TAB);
    expect(tab2.hits).toEqual([]);
  });
});

describe("drumLaneLabel", () => {
  it("labels known lanes and passes unknown ids through", () => {
    expect(drumLaneLabel("hihat_closed")).toBe("Hi-hat");
    expect(drumLaneLabel("cowbell")).toBe("cowbell");
  });
});
