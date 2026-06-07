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

  it("tabRenderer (keys/bass/guitar piano-roll + tab) scales its window by the multiplier", () => {
    const src = read("apps/game/src/tabRenderer.ts");
    // Reads the clamped multiplier in BOTH render paths.
    expect(src).toMatch(/clampScrollSpeedMultiplier\(opts\.scrollSpeedMultiplier\)/);
    // Tab path shrinks the visible window so higher multiplier = more spacing.
    expect(src).toMatch(/const\s+windowSec\s*=\s*this\.windowSec\s*\/\s*scrollMul/);
    // Piano-roll path shrinks look-ahead/behind the same way.
    expect(src).toMatch(/const\s+lookAheadSec\s*=\s*this\.pianoLookAheadSec\s*\/\s*scrollMul/);
    expect(src).toMatch(/const\s+lookBehindSec\s*=\s*this\.pianoLookBehindSec\s*\/\s*scrollMul/);
  });

  it("game passes transport.scrollSpeedMultiplier into tabRenderer.render", () => {
    const src = read("apps/game/src/main.ts");
    expect(src).toMatch(
      /tabRenderer\.render\([\s\S]*?scrollSpeedMultiplier:\s*transport\.scrollSpeedMultiplier/
    );
  });

  it("transportController initializes scrollSpeedMultiplier to 1.0 and exposes a setter", () => {
    const src = read("apps/game/src/transportController.ts");
    expect(src).toMatch(/scrollSpeedMultiplier:\s*1/);
    expect(src).toMatch(/setScrollSpeedMultiplier\s*\(\s*mul\s*:\s*number\s*\)/);
  });

  it("game UI template contains the slider/value/reset elements with the SDK clamp range", () => {
    // The DOM template is still authored in main.ts even though the
    // wiring lives in scrollSpeedController.ts.
    const src = read("apps/game/src/main.ts");
    expect(src).toMatch(/id="scrollSpeedSlider"/);
    expect(src).toMatch(/id="scrollSpeedValue"/);
    expect(src).toMatch(/id="scrollSpeedReset"/);
    expect(src).toMatch(/min="0\.5"[\s\S]*max="3"/);
  });

  it("main.ts calls initScrollSpeedController with transportController + onChange", () => {
    // Pins the bootstrap step that wires the controller. If a future
    // refactor drops the onChange callback, the cached transport state
    // stops seeing slider changes -- catch that here.
    const src = read("apps/game/src/main.ts");
    expect(src).toMatch(/import\s*\{\s*initScrollSpeedController\s*\}\s*from\s*["']\.\/scrollSpeedController["']/);
    expect(src).toMatch(
      /initScrollSpeedController\(\s*\{[\s\S]*?transportController[\s\S]*?onChange[\s\S]*?\}\s*\)/
    );
  });

  it("scrollSpeedController owns the slider/reset event listeners + localStorage round-trip", () => {
    const src = read("apps/game/src/scrollSpeedController.ts");
    // Slider 'input' event drives the apply function (live update during drag).
    expect(src).toMatch(
      /slider\.addEventListener\(\s*["']input["'][\s\S]*?applyScrollSpeed/
    );
    // Reset button restores 1.0x.
    expect(src).toMatch(
      /resetBtn\.addEventListener\(\s*["']click["'][\s\S]*?applyScrollSpeed\(\s*1\s*\)/
    );
    // localStorage round-trip lives keyed under a namespaced key.
    expect(src).toMatch(/auralprimer\.scrollSpeedMultiplier/);
    // Boot-time restore call so the persisted value is applied before the
    // user touches the slider.
    expect(src).toMatch(/applyScrollSpeed\(\s*readPersisted\(\s*\)/);
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
