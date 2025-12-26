// @vitest-environment jsdom

import { computeNextClickSongT, beatDurationSec } from "../src/metronome";

describe("metronome", () => {
  it("computes beat duration from bpm", () => {
    expect(beatDurationSec(120)).toBeCloseTo(0.5, 6);
    expect(beatDurationSec(60)).toBeCloseTo(1.0, 6);
  });

  it("computes next click time on beat grid", () => {
    // 120 bpm => 0.5s per beat
    expect(computeNextClickSongT(0.0, 120)).toBeCloseTo(0.0, 6);
    expect(computeNextClickSongT(0.01, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.49, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.5, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.51, 120)).toBeCloseTo(1.0, 6);
  });
});
