/**
 * Tests for the pure scheduler helper in refineAudition.
 *
 * The audio-engine internals require a real AudioContext, which jsdom
 * does not implement; those are covered by the runtime click-through in
 * Studio. Here we just pin the loop-scheduling math so a future
 * "scheduler skipped an iteration" or "gap drift" regression surfaces.
 */
import { describe, expect, it } from "vitest";
import { nextLoopStartTime } from "../src/refineAudition";

describe("nextLoopStartTime", () => {
  it("schedules a small gap after the first iteration", () => {
    // No loop running yet -- next iteration starts shortly after `now`.
    const next = nextLoopStartTime(0, 0, 0);
    expect(next).toBeGreaterThan(0);
    expect(next).toBeLessThan(1); // 200 ms gap
  });

  it("advances by region duration + gap each cycle", () => {
    const loopStart = 10;
    const regionDur = 3.5;
    // After one full pass we should land at loopStart + regionDur + gap.
    const next = nextLoopStartTime(loopStart + regionDur, loopStart, regionDur);
    expect(next).toBeGreaterThan(loopStart + regionDur);
    expect(next).toBeLessThan(loopStart + regionDur + 1);
  });

  it("snaps to the next aligned cycle when the scheduler runs late", () => {
    // Simulate the scheduler waking 1.2 s after a loop iteration ended:
    // it should schedule iteration N+1 (or later) at the next aligned
    // boundary, not at "now + gap".
    const loopStart = 0;
    const regionDur = 2.0;
    const lateNow = 2.0 + 0.2 + 1.0; // gap covers 0.2s; we woke 1s late
    const next = nextLoopStartTime(lateNow, loopStart, regionDur);
    // The next boundary is at cycle 2 = loopStart + 2 * (regionDur + 0.2) = 4.4
    expect(next).toBeGreaterThanOrEqual(4.4 - 1e-9);
  });
});
