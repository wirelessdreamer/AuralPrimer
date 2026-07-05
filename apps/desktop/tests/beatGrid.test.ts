import { describe, it, expect } from "vitest";
import {
  buildGridTimes,
  snapTimeToGrid,
  downbeatTimes,
  quantLevelByValue,
} from "../src/beatGrid";

describe("buildGridTimes", () => {
  it("subdivides each beat interval (1/8 = 2 per beat)", () => {
    expect(buildGridTimes([0, 1, 2], 2, 2.0)).toEqual([0, 0.5, 1, 1.5, 2]);
  });

  it("follows a tempo change (uneven beat spacing)", () => {
    // beats accelerate 0 -> 1 -> 1.5; each interval is halved independently.
    expect(buildGridTimes([0, 1, 1.5], 2, 1.5)).toEqual([0, 0.5, 1, 1.25, 1.5]);
  });

  it("1/4 (perBeat 1) returns the beats themselves", () => {
    expect(buildGridTimes([0, 1, 2], 1, 2)).toEqual([0, 1, 2]);
  });

  it("extrapolates the last interval out to the duration", () => {
    expect(buildGridTimes([0, 1], 2, 3)).toEqual([0, 0.5, 1, 1.5, 2, 2.5, 3]);
  });

  it("returns [] for no beats", () => {
    expect(buildGridTimes([], 4, 10)).toEqual([]);
  });
});

describe("snapTimeToGrid", () => {
  const grid = [0, 0.5, 1, 1.5, 2];

  it("snaps to the nearest grid point", () => {
    expect(snapTimeToGrid(0.6, grid)).toBe(0.5);
    expect(snapTimeToGrid(0.76, grid)).toBe(1);
    expect(snapTimeToGrid(1.24, grid)).toBe(1);
    expect(snapTimeToGrid(1.26, grid)).toBe(1.5);
  });

  it("clamps outside the grid to the ends", () => {
    expect(snapTimeToGrid(-1, grid)).toBe(0);
    expect(snapTimeToGrid(99, grid)).toBe(2);
  });

  it("returns the input unchanged for an empty grid", () => {
    expect(snapTimeToGrid(1.3, [])).toBe(1.3);
  });
});

describe("downbeatTimes", () => {
  it("returns the first beat of each measure", () => {
    const beats = [
      { time: 0, measure: 1 },
      { time: 1, measure: 1 },
      { time: 2, measure: 2 },
      { time: 3, measure: 2 },
      { time: 4, measure: 3 },
    ];
    expect(downbeatTimes(beats)).toEqual([0, 2, 4]);
  });

  it("skips beats without a numeric time", () => {
    expect(downbeatTimes([{ measure: 1 }, { time: 1, measure: 2 }])).toEqual([1]);
  });
});

describe("quantLevelByValue", () => {
  it("maps values to subdivisions; Off is null", () => {
    expect(quantLevelByValue("1/16")?.perBeat).toBe(4);
    expect(quantLevelByValue("1/8t")?.perBeat).toBe(3);
    expect(quantLevelByValue("1/32")?.perBeat).toBe(8);
    expect(quantLevelByValue("off")?.perBeat).toBeNull();
    expect(quantLevelByValue("bogus")).toBeUndefined();
  });
});
