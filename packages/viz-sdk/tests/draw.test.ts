import { describe, expect, it } from "vitest";
import { beatGridLines, clamp, scrollWindow } from "../src/draw";

describe("clamp", () => {
  it("returns the value when already inside the range", () => {
    expect(clamp(5, 0, 10)).toBe(5);
  });

  it("clamps below the lower bound up to lo", () => {
    expect(clamp(-3, 0, 10)).toBe(0);
  });

  it("clamps above the upper bound down to hi", () => {
    expect(clamp(42, 0, 10)).toBe(10);
  });

  it("returns the bounds when value equals them", () => {
    expect(clamp(0, 0, 10)).toBe(0);
    expect(clamp(10, 0, 10)).toBe(10);
  });
});

describe("scrollWindow", () => {
  it("scales pxPerSec by the multiplier", () => {
    expect(scrollWindow({ heightPx: 600, basePxPerSec: 120, scrollMul: 1 }).pxPerSec).toBe(120);
    expect(scrollWindow({ heightPx: 600, basePxPerSec: 120, scrollMul: 2 }).pxPerSec).toBe(240);
  });

  it("derives windowSec as referencePx / pxPerSec", () => {
    const { windowSec } = scrollWindow({ heightPx: 600, basePxPerSec: 120, scrollMul: 1 });
    expect(windowSec).toBeCloseTo(5, 10); // 600 / 120
  });

  it("higher scrollMul => higher pxPerSec and smaller windowSec", () => {
    const slow = scrollWindow({ heightPx: 600, basePxPerSec: 140, scrollMul: 1 });
    const fast = scrollWindow({ heightPx: 600, basePxPerSec: 140, scrollMul: 2.5 });
    expect(fast.pxPerSec).toBeGreaterThan(slow.pxPerSec);
    expect(fast.windowSec).toBeLessThan(slow.windowSec);
  });

  it("preserves tempo-lock: pxPerSec * windowSec stays equal to the reference span", () => {
    // The window always covers exactly `heightPx` pixels regardless of the
    // multiplier -- only the seconds-per-pixel density changes, never the
    // mapping from song time to position. This is what keeps notes hitting
    // at the correct beat time.
    for (const scrollMul of [0.3, 1, 1.7, 3]) {
      const { pxPerSec, windowSec } = scrollWindow({ heightPx: 600, basePxPerSec: 140, scrollMul });
      expect(pxPerSec * windowSec).toBeCloseTo(600, 6);
    }
  });
});

describe("beatGridLines", () => {
  it("emits one line per beat across the visible window, ordered ascending", () => {
    // 120 bpm => 0.5 s/beat. Window [0, 2] covers beats 0..4 (floor/ceil incl).
    const lines = beatGridLines({ t: 0, windowSec: 2, bpm: 120, beatsPerBar: 4 });
    expect(lines.map((l) => l.tSec)).toEqual([0, 0.5, 1, 1.5, 2]);
    for (let i = 1; i < lines.length; i += 1) {
      expect(lines[i].tSec).toBeGreaterThan(lines[i - 1].tSec);
    }
  });

  it("x01 is monotonic increasing and spans 0..1 across the window", () => {
    const windowSec = 2;
    const lines = beatGridLines({ t: 0, windowSec, bpm: 120, beatsPerBar: 4 });
    for (let i = 1; i < lines.length; i += 1) {
      expect(lines[i].x01).toBeGreaterThan(lines[i - 1].x01);
    }
    expect(lines[0].x01).toBeCloseTo(0, 10);
    expect(lines[lines.length - 1].x01).toBeCloseTo(1, 10);
    // x01 is exactly (tSec - t) / windowSec.
    for (const l of lines) {
      expect(l.x01).toBeCloseTo(l.tSec / windowSec, 10);
    }
  });

  it("flags downbeats at bar starts and assigns the correct barIndex (4/4)", () => {
    // 120 bpm, 4/4 => 0.5 s/beat, 2 s/bar. Window [0, 4] => beats 0..8.
    const lines = beatGridLines({ t: 0, windowSec: 4, bpm: 120, beatsPerBar: 4 });
    const downbeats = lines.filter((l) => l.isDownbeat);
    expect(downbeats.map((l) => l.tSec)).toEqual([0, 2, 4]);
    expect(downbeats.map((l) => l.barIndex)).toEqual([0, 1, 2]);
    // Non-downbeats all share their bar's index.
    const beat3 = lines.find((l) => l.tSec === 0.5);
    expect(beat3?.isDownbeat).toBe(false);
    expect(beat3?.barIndex).toBe(0);
  });

  it("downbeat cadence follows beatsPerBar (3/4)", () => {
    // 120 bpm, 3/4 => 0.5 s/beat, 1.5 s/bar. Window [0, 3] => beats 0..6.
    const lines = beatGridLines({ t: 0, windowSec: 3, bpm: 120, beatsPerBar: 3 });
    const downbeats = lines.filter((l) => l.isDownbeat).map((l) => l.tSec);
    expect(downbeats).toEqual([0, 1.5, 3]);
  });

  it("handles a non-zero window start with correct floor/ceil and negative barIndex", () => {
    // Start mid-stream just below a beat boundary; floor pulls in the prior
    // beat, ceil pulls in the trailing one.
    const lines = beatGridLines({ t: 0.4, windowSec: 1, bpm: 120, beatsPerBar: 4 });
    expect(lines.map((l) => l.tSec)).toEqual([0, 0.5, 1, 1.5]);
    // x01 of the leading (pre-window) beat is negative; trailing is > 1.
    expect(lines[0].x01).toBeLessThan(0);
    expect(lines[lines.length - 1].x01).toBeGreaterThan(1);
  });

  it("guards against zero/NaN bpm without throwing", () => {
    expect(() => beatGridLines({ t: 0, windowSec: 1, bpm: 0, beatsPerBar: 4 })).not.toThrow();
  });
});
