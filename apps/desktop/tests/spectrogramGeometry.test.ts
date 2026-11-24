import { describe, it, expect } from "vitest";
import {
  frameToNorm,
  harmonicMidiPitches,
  laneBandRows,
  laneMarkerXSpan,
  laneRowBand,
  noteBodyXSpan,
  secToFrame,
} from "../src/spectrogramGeometry";

describe("harmonicMidiPitches", () => {
  it("places overtones at frequency multiples without rounding to semitones", () => {
    const guides = harmonicMidiPitches(69); // A4, 440 Hz.
    expect(guides.map((g) => g.harmonic)).toEqual([2, 3, 4, 5, 6, 7, 8]);
    expect(guides[0].pitch).toBe(81);
    expect(guides[2].pitch).toBe(93);
    expect(guides[6].pitch).toBe(105);
    expect(guides[1].pitch).toBeCloseTo(88.01955, 5);
    expect(guides[5].pitch).toBeCloseTo(102.68826, 5);
    for (const { harmonic, pitch } of guides) {
      expect(440 * 2 ** ((pitch - 69) / 12)).toBeCloseTo(440 * harmonic, 6);
    }
  });
});

describe("secToFrame", () => {
  it("maps a hit at time T to frame T*fps (energy column)", () => {
    expect(secToFrame(2, 43.06640625)).toBeCloseTo(86.1328125, 6);
    expect(secToFrame(0, 43)).toBe(0);
  });
});

describe("frameToNorm", () => {
  it("is 0 at the window origin and 1 at origin+span", () => {
    expect(frameToNorm(100, 100, 400)).toBe(0);
    expect(frameToNorm(500, 100, 400)).toBe(1);
    expect(frameToNorm(300, 100, 400)).toBe(0.5);
  });
});

describe("laneBandRows / laneRowBand", () => {
  it("divides the bins evenly among lanes", () => {
    expect(laneBandRows(96, 8)).toBe(12);
    expect(laneBandRows(96, 0)).toBe(1); // guard, no divide-by-zero
  });

  it("gives each lane a contiguous band, bottom lane = index 0", () => {
    expect(laneRowBand(0, 96, 8)).toEqual({ rLo: 0, rHi: 12 });
    expect(laneRowBand(1, 96, 8)).toEqual({ rLo: 12, rHi: 24 });
    expect(laneRowBand(7, 96, 8)).toEqual({ rLo: 84, rHi: 96 });
  });
});

describe("laneMarkerXSpan (drum hit = centered instant)", () => {
  it("is symmetric about the onset pixel, with a fixed width", () => {
    const span = laneMarkerXSpan(100, 5, 2); // half = 5*2/2 = 5
    expect(span).toEqual({ xLeft: 95, xRight: 105 });
    // Center is exactly the onset px (marker never drifts off the transient).
    expect((span.xLeft + span.xRight) / 2).toBe(100);
    // Width depends ONLY on the mark size + dpr, never on note duration.
    expect(span.xRight - span.xLeft).toBe(10);
  });

  it("scales the width with device pixel ratio, staying centered", () => {
    const span = laneMarkerXSpan(200, 5, 1); // half = 2.5
    expect((span.xLeft + span.xRight) / 2).toBe(200);
    expect(span.xRight - span.xLeft).toBe(5);
  });
});

describe("noteBodyXSpan (pitch mode = real duration)", () => {
  it("spans t_on..t_off with a 1px minimum, order-independent", () => {
    expect(noteBodyXSpan(100, 140)).toEqual({ xLeft: 100, xRight: 140 });
    expect(noteBodyXSpan(140, 100)).toEqual({ xLeft: 100, xRight: 140 });
    // Zero-width note still draws at least 1px.
    expect(noteBodyXSpan(100, 100)).toEqual({ xLeft: 100, xRight: 101 });
  });
});
