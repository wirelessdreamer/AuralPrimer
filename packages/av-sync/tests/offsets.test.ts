/**
 * Unit tests for the shared A/V offset math. These lock the sign convention
 * both apps depend on (visual shift = audio - video) plus clamping/rounding.
 */

import { describe, it, expect } from "vitest";
import {
  AV_OFFSET_MAX_MS,
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
