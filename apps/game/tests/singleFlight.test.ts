import { describe, expect, it, vi } from "vitest";
import { singleFlight } from "../src/singleFlight";

describe("singleFlight", () => {
  it("runs one load for any number of callers while it is in flight", async () => {
    let resolve!: (v: boolean) => void;
    const load = vi.fn(() => new Promise<boolean>((r) => (resolve = r)));
    const ensure = singleFlight(load, { retryAfterMs: 5000, now: () => 0 });

    // A strum: a burst of note events before the first load has finished.
    const calls = Array.from({ length: 40 }, () => ensure());
    expect(load).toHaveBeenCalledTimes(1);
    resolve(true);
    expect(await Promise.all(calls)).toEqual(new Array(40).fill(true));
  });

  it("never loads again once it has succeeded", async () => {
    const load = vi.fn(async () => true);
    const ensure = singleFlight(load, { retryAfterMs: 5000, now: () => 0 });
    await ensure();
    await ensure();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does not retry on every call after a failure, only once the wait has passed", async () => {
    let t = 0;
    const load = vi.fn(async () => false);
    const ensure = singleFlight(load, { retryAfterMs: 5000, now: () => t });

    expect(await ensure()).toBe(false);
    t = 1000;
    expect(await ensure()).toBe(false); // too soon: no second load
    expect(load).toHaveBeenCalledTimes(1);
    t = 6000;
    await ensure();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("treats a thrown load as a failure, not a stuck flight", async () => {
    let t = 0;
    const load = vi.fn(async () => {
      throw new Error("no pack");
    });
    const ensure = singleFlight(load, { retryAfterMs: 100, now: () => t });
    expect(await ensure()).toBe(false);
    t = 200;
    await ensure();
    expect(load).toHaveBeenCalledTimes(2);
  });
});
