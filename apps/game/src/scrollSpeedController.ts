/**
 * Scroll-speed controller (Note-spacing slider).
 *
 * Owns the `#scrollSpeedSlider` / `#scrollSpeedValue` / `#scrollSpeedReset`
 * DOM elements + the per-webview localStorage persistence + the live wiring
 * into the host TransportController. Applies uniformly across every
 * instrument visualizer (drums highway, beats, nashville, keys piano-roll);
 * tempo-lock is preserved — the multiplier only changes how many pixels
 * each second of song time occupies on the canvas, never note onset times.
 *
 * Extracted from main.ts as the first step of the Phase 2 decomposition;
 * establishes the `init(deps)` pattern the rest of the refactor follows.
 */

import { clampScrollSpeedMultiplier } from "@auralprimer/viz-sdk";
import type { TransportController } from "./transportController";

const STORAGE_KEY = "auralprimer.scrollSpeedMultiplier";

export type ScrollSpeedDeps = {
  transportController: TransportController;
  /**
   * Called every time the multiplier changes (slider drag, reset, boot
   * restore). main.ts uses this to refresh its cached transport state so
   * downstream consumers (tabRenderer.render, etc.) see the new value on
   * the next frame without polling.
   */
  onChange?: (multiplier: number) => void;
};

export type ScrollSpeedController = {
  /** Programmatically apply a multiplier; `persist: false` skips localStorage. */
  apply: (value: number, opts?: { persist?: boolean }) => void;
};

function readPersisted(): number {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return 1;
    const n = Number(raw);
    return clampScrollSpeedMultiplier(n);
  } catch {
    // localStorage may be unavailable in some embedded webviews; default safely.
    return 1;
  }
}

function persist(value: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // Best-effort -- session-only is acceptable.
  }
}

/**
 * Wire the scroll-speed controls. Must be called after the DOM has been
 * staged AND after a `TransportController` instance exists.
 */
export function initScrollSpeedController(deps: ScrollSpeedDeps): ScrollSpeedController {
  const slider = document.getElementById("scrollSpeedSlider") as HTMLInputElement | null;
  const valueEl = document.getElementById("scrollSpeedValue") as HTMLSpanElement | null;
  const resetBtn = document.getElementById("scrollSpeedReset") as HTMLButtonElement | null;
  if (!slider || !valueEl || !resetBtn) {
    throw new Error(
      "initScrollSpeedController: required DOM (#scrollSpeedSlider/#scrollSpeedValue/#scrollSpeedReset) missing",
    );
  }

  function applyScrollSpeed(value: number, opts: { persist?: boolean } = { persist: true }): void {
    const clamped = clampScrollSpeedMultiplier(value);
    deps.transportController.setScrollSpeedMultiplier(clamped);
    slider!.value = String(clamped);
    valueEl!.textContent = `${clamped.toFixed(2)}x`;
    if (opts.persist) persist(clamped);
    deps.onChange?.(clamped);
  }

  // Restore persisted value on boot.
  applyScrollSpeed(readPersisted(), { persist: false });

  // Live-update on slider input (every drag tick) so the visualizer responds
  // immediately as the user drags. "change" would only fire on release.
  slider.addEventListener("input", () => {
    applyScrollSpeed(Number(slider.value));
  });

  resetBtn.addEventListener("click", () => {
    applyScrollSpeed(1);
  });

  return { apply: applyScrollSpeed };
}
