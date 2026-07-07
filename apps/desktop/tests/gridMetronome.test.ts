import { describe, it, expect } from "vitest";
import { buildGridBeats, firstBeatAtOrAfter, selectDueBeats } from "../src/gridMetronome";

describe("buildGridBeats", () => {
  it("sorts beats and accents the downbeats", () => {
    const g = buildGridBeats([2, 0, 1, 3], [0, 2]);
    expect(g.map((b) => b.t)).toEqual([0, 1, 2, 3]);
    expect(g.map((b) => b.accent)).toEqual([true, false, true, false]);
  });

  it("drops non-finite times and accents nothing when no downbeats", () => {
    const g = buildGridBeats([0, NaN, 1], []);
    expect(g.map((b) => b.t)).toEqual([0, 1]);
    expect(g.every((b) => !b.accent)).toBe(true);
  });
});

describe("firstBeatAtOrAfter", () => {
  const g = buildGridBeats([0, 1, 2, 3, 4], []);
  it("finds the first index at or after t", () => {
    expect(firstBeatAtOrAfter(g, 0)).toBe(0);
    expect(firstBeatAtOrAfter(g, 1.5)).toBe(2);
    expect(firstBeatAtOrAfter(g, 2)).toBe(2); // exact match is 'at or after'
    expect(firstBeatAtOrAfter(g, 99)).toBe(5); // past the end
  });
});

describe("selectDueBeats", () => {
  const g = buildGridBeats([0, 0.5, 1, 1.5, 2], [0, 2]);

  it("returns beats within [songT, songT+ahead] and advances the index", () => {
    const { due, nextIdx } = selectDueBeats(g, 0, 0.4, 0.7); // window [0.4, 1.1]
    expect(due.map((b) => b.t)).toEqual([0.5, 1]);
    expect(nextIdx).toBe(3); // 1.5 is the next unscheduled beat
  });

  it("skips beats already passed but still advances past them (no late fire)", () => {
    // fromIdx 0 but songT 1.1: beats 0,0.5,1 are behind and must not fire.
    const { due, nextIdx } = selectDueBeats(g, 0, 1.1, 0.5); // window [1.1, 1.6]
    expect(due.map((b) => b.t)).toEqual([1.5]);
    expect(nextIdx).toBe(4);
  });

  it("carries the accent flag through so the one can be emphasized", () => {
    const { due } = selectDueBeats(g, 0, 1.9, 0.2); // window [1.9, 2.1] -> beat at 2
    expect(due).toEqual([{ t: 2, accent: true }]);
  });

  it("returns nothing when the window is past the last beat", () => {
    const { due, nextIdx } = selectDueBeats(g, 5, 10, 0.5);
    expect(due).toEqual([]);
    expect(nextIdx).toBe(5);
  });
});
