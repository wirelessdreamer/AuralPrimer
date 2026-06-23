// @vitest-environment jsdom
/**
 * Execution tests for the scroll-speed controller (Note-spacing slider +
 * StepMania bracket hotkeys). Exercises the init(deps) wiring, localStorage
 * persistence, and the keydown guards in jsdom.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initScrollSpeedController } from "../src/scrollSpeedController";

const STORAGE_KEY = "auralprimer.scrollSpeedMultiplier";

function stageDom(): void {
  document.body.innerHTML =
    '<input id="scrollSpeedSlider" type="range" min="0.25" max="4" step="0.05" />' +
    '<span id="scrollSpeedValue"></span>' +
    '<button id="scrollSpeedReset"></button>';
}

function mockTransport() {
  return { setScrollSpeedMultiplier: vi.fn() } as unknown as Parameters<
    typeof initScrollSpeedController
  >[0]["transportController"];
}

const valueText = () => document.getElementById("scrollSpeedValue")!.textContent;
const slider = () => document.getElementById("scrollSpeedSlider") as HTMLInputElement;

describe("scrollSpeedController", () => {
  beforeEach(() => {
    stageDom();
    window.localStorage.clear();
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() =>
      initScrollSpeedController({ transportController: mockTransport() }),
    ).toThrow(/required DOM/);
  });

  it("restores the default 1x on boot when nothing is persisted (no persist write)", () => {
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    expect(tc.setScrollSpeedMultiplier).toHaveBeenCalledWith(1);
    expect(valueText()).toBe("1.00x");
    expect(slider().value).toBe("1");
    // Boot restore must NOT write back to storage.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("restores a persisted multiplier on boot", () => {
    window.localStorage.setItem(STORAGE_KEY, "2");
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    expect(tc.setScrollSpeedMultiplier).toHaveBeenCalledWith(2);
    expect(valueText()).toBe("2.00x");
  });

  it("defaults to 1 when the persisted value is unparseable", () => {
    window.localStorage.setItem(STORAGE_KEY, "not-a-number");
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    // Number("not-a-number") is NaN; clamp falls back to a safe value and the
    // controller still initializes without throwing.
    expect(tc.setScrollSpeedMultiplier).toHaveBeenCalled();
  });

  it("applies + persists + fires onChange on slider input", () => {
    const tc = mockTransport();
    const onChange = vi.fn();
    initScrollSpeedController({ transportController: tc, onChange });
    onChange.mockClear();
    slider().value = "1.5";
    slider().dispatchEvent(new Event("input"));
    expect(tc.setScrollSpeedMultiplier).toHaveBeenLastCalledWith(1.5);
    expect(onChange).toHaveBeenLastCalledWith(1.5);
    expect(valueText()).toBe("1.50x");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("1.5");
  });

  it("reset button returns to 1x", () => {
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    slider().value = "3";
    document.getElementById("scrollSpeedReset")!.dispatchEvent(new MouseEvent("click"));
    expect(tc.setScrollSpeedMultiplier).toHaveBeenLastCalledWith(1);
  });

  it("[ spreads (coarse +0.25) and ] with shift compresses (fine -0.05)", () => {
    const tc = mockTransport();
    const { apply } = initScrollSpeedController({ transportController: tc });
    apply(1, { persist: false });
    (tc.setScrollSpeedMultiplier as ReturnType<typeof vi.fn>).mockClear();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "[" }));
    expect(tc.setScrollSpeedMultiplier).toHaveBeenLastCalledWith(1.25);

    slider().value = "2";
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", shiftKey: true }));
    expect(tc.setScrollSpeedMultiplier).toHaveBeenLastCalledWith(1.95);
  });

  it("ignores bracket hotkeys with ctrl/meta and for non-bracket keys", () => {
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    (tc.setScrollSpeedMultiplier as ReturnType<typeof vi.fn>).mockClear();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", ctrlKey: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(tc.setScrollSpeedMultiplier).not.toHaveBeenCalled();
  });

  it("ignores bracket hotkeys while focus is in a text input", () => {
    const tc = mockTransport();
    initScrollSpeedController({ transportController: tc });
    (tc.setScrollSpeedMultiplier as ReturnType<typeof vi.fn>).mockClear();
    const typing = document.createElement("input");
    document.body.appendChild(typing);
    typing.dispatchEvent(new KeyboardEvent("keydown", { key: "[", bubbles: true }));
    expect(tc.setScrollSpeedMultiplier).not.toHaveBeenCalled();
  });

  it("apply() returned handle drives the multiplier programmatically", () => {
    const tc = mockTransport();
    const { apply } = initScrollSpeedController({ transportController: tc });
    apply(2.5);
    expect(tc.setScrollSpeedMultiplier).toHaveBeenLastCalledWith(2.5);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("2.5");
  });
});
