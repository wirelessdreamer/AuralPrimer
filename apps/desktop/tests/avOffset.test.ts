/**
 * @vitest-environment jsdom
 *
 * Unit tests for the Studio A/V offset store. Persistence is the shared
 * settings.json via the Rust get/set_av_calibration commands, so these mock
 * `invoke` and verify: load pulls both offsets, the effective getter is
 * audio-video, set persists with camelCase args, and clamping is applied.
 * The module caches in memory, so each case re-imports it fresh.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invokeMock(...args) }));

async function freshModule() {
  vi.resetModules();
  return import("../src/avOffset");
}

describe("avOffset store", () => {
  beforeEach(() => {
    invokeMock.mockReset();
  });

  it("defaults to 0/0 before load", async () => {
    const m = await freshModule();
    expect(m.getAudioOffsetMs()).toBe(0);
    expect(m.getVideoOffsetMs()).toBe(0);
    expect(m.getEffectiveOffsetMs()).toBe(0);
    expect(m.getAvOffsetSec()).toBe(0);
  });

  it("loads both offsets and exposes effective = audio - video", async () => {
    invokeMock.mockResolvedValueOnce({ audio_offset_ms: 200, video_offset_ms: 50 });
    const m = await freshModule();
    await m.loadAvCalibration();
    expect(invokeMock).toHaveBeenCalledWith("get_av_calibration");
    expect(m.getAudioOffsetMs()).toBe(200);
    expect(m.getVideoOffsetMs()).toBe(50);
    expect(m.getEffectiveOffsetMs()).toBe(150);
    expect(m.getAvOffsetSec()).toBeCloseTo(0.15, 6);
  });

  it("falls back to 0/0 when load rejects", async () => {
    invokeMock.mockRejectedValueOnce(new Error("no backend"));
    const m = await freshModule();
    await m.loadAvCalibration();
    expect(m.getAudioOffsetMs()).toBe(0);
    expect(m.getVideoOffsetMs()).toBe(0);
  });

  it("persists with camelCase args and clamps", async () => {
    invokeMock.mockResolvedValue(undefined);
    const m = await freshModule();
    await m.setAvCalibration(9000, -30);
    expect(m.getAudioOffsetMs()).toBe(500); // clamped
    expect(m.getVideoOffsetMs()).toBe(-30);
    expect(invokeMock).toHaveBeenCalledWith("set_av_calibration", {
      audioOffsetMs: 500,
      videoOffsetMs: -30,
    });
  });

  it("broadcasts a change event on set", async () => {
    invokeMock.mockResolvedValue(undefined);
    const m = await freshModule();
    const handler = vi.fn();
    window.addEventListener(m.AV_OFFSET_CHANGED_EVENT, handler as EventListener);
    await m.setAvCalibration(120, 40);
    expect(handler).toHaveBeenCalledTimes(1);
    const ev = handler.mock.calls[0][0] as CustomEvent<{ audioMs: number; videoMs: number }>;
    expect(ev.detail).toEqual({ audioMs: 120, videoMs: 40 });
    window.removeEventListener(m.AV_OFFSET_CHANGED_EVENT, handler as EventListener);
  });
});
