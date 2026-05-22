/**
 * Source-level regression tests for the per-frame scroll-speed multiplier.
 *
 * Goal: every scroll-aware visualizer reads the host-provided
 * `state.scrollSpeedMultiplier` and multiplies its `pxPerSecond` /
 * `scrollPxPerSec` by it, so a single host-side slider can spread or
 * compress notes uniformly across instruments without breaking tempo lock.
 *
 * These are pure source-level pins. The actual rendering effect is
 * exercised by the runtime jsdom integration check
 * (distBundleSongLibrary.integration.test.ts pattern) -- this file just
 * catches a future refactor that drops the multiplier wiring from any one
 * visualizer or from the host's transport controller.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");

function read(rel: string): string {
  return readFileSync(resolve(REPO_ROOT, rel), "utf-8");
}

describe("scroll-speed multiplier contract (TransportState.scrollSpeedMultiplier)", () => {
  it("viz-sdk exports the field on TransportState + a clamp helper", () => {
    const sdk = read("packages/viz-sdk/src/index.ts");
    expect(sdk).toMatch(/scrollSpeedMultiplier\?\s*:\s*number\s*;/);
    expect(sdk).toMatch(/export\s+function\s+clampScrollSpeedMultiplier/);
    expect(sdk).toMatch(/SCROLL_SPEED_MIN/);
    expect(sdk).toMatch(/SCROLL_SPEED_MAX/);
  });

  it("viz-drum-highway multiplies its scrollPxPerSec by the multiplier", () => {
    const src = read("visualizers/viz-drum-highway/src/index.ts");
    expect(src).toMatch(/clampScrollSpeedMultiplier\(state\.scrollSpeedMultiplier\)/);
    expect(src).toMatch(/scrollPxPerSec\s*=\s*[^;]*\*\s*scrollMul/);
  });

  it("viz-beats multiplies its pxPerSecond by the multiplier", () => {
    const src = read("visualizers/viz-beats/src/index.ts");
    expect(src).toMatch(/clampScrollSpeedMultiplier\(frame\.state\.scrollSpeedMultiplier\)/);
    expect(src).toMatch(/pxPerSecond\s*=\s*[^;]*\*\s*scrollMul/);
  });

  it("viz-nashville multiplies its pxPerSecond by the multiplier", () => {
    const src = read("visualizers/viz-nashville/src/index.ts");
    expect(src).toMatch(/clampScrollSpeedMultiplier\(frame\.state\.scrollSpeedMultiplier\)/);
    expect(src).toMatch(/pxPerSecond\s*=\s*[^;]*\*\s*scrollMul/);
  });

  it("transportController initializes scrollSpeedMultiplier to 1.0 and exposes a setter", () => {
    const src = read("apps/game/src/transportController.ts");
    expect(src).toMatch(/scrollSpeedMultiplier:\s*1/);
    expect(src).toMatch(/setScrollSpeedMultiplier\s*\(\s*mul\s*:\s*number\s*\)/);
  });

  it("game UI has a slider that calls applyScrollSpeed + persists via localStorage", () => {
    const src = read("apps/game/src/main.ts");
    // Slider exists in template
    expect(src).toMatch(/id="scrollSpeedSlider"/);
    expect(src).toMatch(/id="scrollSpeedValue"/);
    expect(src).toMatch(/id="scrollSpeedReset"/);
    // Range bounds + step match the SDK clamp range
    expect(src).toMatch(/min="0\.5"[\s\S]*max="3"/);
    // Slider 'input' event drives applyScrollSpeed (live update during drag)
    expect(src).toMatch(
      /scrollSpeedSlider\.addEventListener\(\s*["']input["'][\s\S]*?applyScrollSpeed/
    );
    // Reset button restores 1.0x
    expect(src).toMatch(
      /scrollSpeedResetBtn\.addEventListener\(\s*["']click["'][\s\S]*?applyScrollSpeed\(\s*1\s*\)/
    );
    // localStorage round-trip lives keyed under a namespaced key
    expect(src).toMatch(/auralprimer\.scrollSpeedMultiplier/);
    // Boot-time restore call so the persisted value is applied before the
    // user touches the slider.
    expect(src).toMatch(/applyScrollSpeed\(\s*readPersistedScrollSpeed\(\s*\)/);
  });

  it("clampScrollSpeedMultiplier rejects bad inputs at the type boundary", async () => {
    // Exercise the actual helper, not just its source string. This proves
    // the contract works for whatever the UI sends down -- including
    // NaN (range parser failure), 0 (broken multiplier), negative
    // (no inversion), and silly large/small values.
    const { clampScrollSpeedMultiplier, SCROLL_SPEED_MIN, SCROLL_SPEED_MAX } =
      await import("../../../packages/viz-sdk/src/index");
    expect(clampScrollSpeedMultiplier(undefined)).toBe(1);
    expect(clampScrollSpeedMultiplier(null)).toBe(1);
    expect(clampScrollSpeedMultiplier(NaN)).toBe(1);
    expect(clampScrollSpeedMultiplier(0)).toBe(1);
    expect(clampScrollSpeedMultiplier(-2)).toBe(1);
    expect(clampScrollSpeedMultiplier(0.1)).toBe(SCROLL_SPEED_MIN);
    expect(clampScrollSpeedMultiplier(10)).toBe(SCROLL_SPEED_MAX);
    expect(clampScrollSpeedMultiplier(1.5)).toBe(1.5);
  });
});
