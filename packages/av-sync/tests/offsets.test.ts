/**
 * Unit tests for the shared A/V offset math. These lock the sign convention
 * both apps depend on (visual shift = audio - video) plus clamping/rounding.
 */

import { describe, it, expect } from "vitest";
import {
  AV_OFFSET_MAX_MS,
  audiblePlayheadSec,
  clampOffsetMs,
  effectiveAvOffsetMs,
  effectiveAvOffsetSec,
} from "../src/offsets";

describe("clampOffsetMs", () => {
  it("rounds to whole milliseconds", () => {
    expect(clampOffsetMs(42.6)).toBe(43);
    expect(clampOffsetMs(-42.6)).toBe(-43);
  });

  it("clamps to ±max", () => {
    expect(clampOffsetMs(9000)).toBe(AV_OFFSET_MAX_MS);
    expect(clampOffsetMs(-9000)).toBe(-AV_OFFSET_MAX_MS);
  });

  it("maps non-finite to 0", () => {
    expect(clampOffsetMs(NaN)).toBe(0);
    expect(clampOffsetMs(Infinity)).toBe(0);
  });
});

describe("effectiveAvOffsetMs", () => {
  it("is audio minus video", () => {
    expect(effectiveAvOffsetMs(200, 50)).toBe(150);
    expect(effectiveAvOffsetMs(50, 200)).toBe(-150);
  });

  it("is zero when both latencies match", () => {
    expect(effectiveAvOffsetMs(120, 120)).toBe(0);
  });

  it("clamps the inputs and the result", () => {
    // audio clamps to 500, video clamps to -500 -> diff 1000 clamps to 500.
    expect(effectiveAvOffsetMs(9000, -9000)).toBe(AV_OFFSET_MAX_MS);
  });

  it("rounds fractional inputs before differencing", () => {
    expect(effectiveAvOffsetMs(100.4, 30.4)).toBe(70);
  });
});

describe("effectiveAvOffsetSec", () => {
  it("converts the effective offset to seconds", () => {
    expect(effectiveAvOffsetSec(200, 50)).toBeCloseTo(0.15, 6);
    expect(effectiveAvOffsetSec(0, 0)).toBe(0);
  });
});

describe("audiblePlayheadSec (the alignment formula both apps draw with)", () => {
  const base = { nativePosSec: 20, outputLatencySec: 0.02, playbackRate: 1, avOffsetSec: 0.1 };

  it("subtracts output latency AND the A/V offset from the native position", () => {
    // 20 − 0.02·1 − 0.1 = 19.88
    expect(audiblePlayheadSec(base)).toBeCloseTo(19.88, 9);
  });

  it("with zero latency and zero offset is the identity (playhead == native pos)", () => {
    expect(
      audiblePlayheadSec({ nativePosSec: 12.5, outputLatencySec: 0, playbackRate: 1, avOffsetSec: 0 }),
    ).toBe(12.5);
  });

  it("scales output latency by the playback rate (buffer latency is wall-clock)", () => {
    // 20 − 0.02·2 − 0.1 = 19.86
    expect(audiblePlayheadSec({ ...base, playbackRate: 2 })).toBeCloseTo(19.86, 9);
  });

  it("does NOT rate-scale the A/V offset (guards the reverted regression)", () => {
    // Only the latency term reacts to rate; the 0.1 offset stays 0.1 at 2x.
    const atRate2 = audiblePlayheadSec({ ...base, playbackRate: 2 });
    const latencyDelta = (2 - 1) * base.outputLatencySec; // extra latency from 1x→2x
    expect(atRate2).toBeCloseTo(audiblePlayheadSec(base) - latencyDelta, 9);
  });

  it("applies the offset with the correct sign (negative offset pushes LATER)", () => {
    // A laggy display gives a negative effective offset → playhead drawn ahead.
    expect(audiblePlayheadSec({ ...base, avOffsetSec: -0.1 })).toBeCloseTo(20.08, 9);
  });

  it("clamps to zero near the start instead of going negative", () => {
    expect(
      audiblePlayheadSec({ nativePosSec: 0.05, outputLatencySec: 0.02, playbackRate: 1, avOffsetSec: 0.1 }),
    ).toBe(0);
  });

  it("treats a non-positive/non-finite rate as 1x and negative/non-finite latency as 0", () => {
    expect(audiblePlayheadSec({ ...base, playbackRate: 0 })).toBeCloseTo(19.88, 9);
    expect(audiblePlayheadSec({ ...base, playbackRate: Number.NaN })).toBeCloseTo(19.88, 9);
    expect(audiblePlayheadSec({ ...base, outputLatencySec: Number.NaN })).toBeCloseTo(19.9, 9);
  });
});
