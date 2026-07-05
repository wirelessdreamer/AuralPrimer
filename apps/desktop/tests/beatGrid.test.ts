import { describe, it, expect } from "vitest";
import {
  buildGridTimes,
  snapTimeToGrid,
  downbeatTimes,
  downbeatTimesShifted,
  beatsPerBarFromTimeSignatures,
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

describe("downbeatTimesShifted", () => {
  // 4/4: beats every 0.5s, detected downbeats on beats 0 and 4 (times 0 and 2).
  const beats = [
    { time: 0, measure: 1 },
    { time: 0.5, measure: 1 },
    { time: 1, measure: 1 },
    { time: 1.5, measure: 1 },
    { time: 2, measure: 2 },
    { time: 2.5, measure: 2 },
    { time: 3, measure: 2 },
    { time: 3.5, measure: 2 },
  ];

  it("offset 0 matches downbeatTimes", () => {
    expect(downbeatTimesShifted(beats, 0)).toEqual(downbeatTimes(beats));
    expect(downbeatTimesShifted(beats, 0)).toEqual([0, 2]);
  });

  it("shifts the accent forward by N beats (moves 'the one')", () => {
    // Detected downbeat on beat 0 was really beat 3 -> shift +2 puts it on the
    // true one (beat 2 of the array).
    expect(downbeatTimesShifted(beats, 2)).toEqual([1, 3]);
    expect(downbeatTimesShifted(beats, 1)).toEqual([0.5, 2.5]);
  });

  it("clamps at the array ends without duplicating", () => {
    // Large positive shift collapses everything toward the last beat, de-duped.
    expect(downbeatTimesShifted(beats, 100)).toEqual([3.5]);
  });

  it("returns [] for no beats", () => {
    expect(downbeatTimesShifted([], 2)).toEqual([]);
  });
});

describe("beatsPerBarFromTimeSignatures", () => {
  it("reads the numerator; defaults to 4", () => {
    expect(beatsPerBarFromTimeSignatures([{ ts: [3, 4] }])).toBe(3);
    expect(beatsPerBarFromTimeSignatures([{ ts: [7, 8] }])).toBe(7);
    expect(beatsPerBarFromTimeSignatures(undefined)).toBe(4);
    expect(beatsPerBarFromTimeSignatures([{}])).toBe(4);
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
