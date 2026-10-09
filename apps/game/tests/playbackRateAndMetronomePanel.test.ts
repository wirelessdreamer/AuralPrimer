// @vitest-environment jsdom
/**
 * Execution tests for the playback-rate + metronome panel. Stages the DOM
 * ids it reads, mocks the transport/metronome deps, and exercises the
 * apply-button, metronome-toggle, and volume-input handlers including the
 * NaN/invalid guards.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  formatSpeedReadout,
  initPlaybackRateAndMetronomePanel,
  speedSliderPercent,
} from "../src/playbackRateAndMetronomePanel";

function stageDom(): void {
  document.body.innerHTML =
    '<input id="playbackRate" type="number" value="1" />' +
    '<button id="playbackRateApply"></button>' +
    '<input id="speedSlider" type="range" min="25" max="100" step="5" value="100" />' +
    '<span id="speedValue"></span>' +
    '<input id="metronomeEnabled" type="checkbox" />' +
    '<input id="metronomeVolume" type="text" value="0.5" />';
}

function makeDeps(songBpm: number | null = null) {
  let enabled = false;
  const setEnabled = vi.fn((v: boolean) => {
    enabled = v;
  });
  const transportController = { setPlaybackRate: vi.fn() };
  return {
    transportController,
    getTransportController: vi.fn(() => transportController as any),
    metronome: {
      setEnabled,
      getEnabled: vi.fn(() => enabled),
      setVolume: vi.fn(),
    } as any,
    setAudioStatus: vi.fn(),
    onPlaybackRateApplied: vi.fn(),
    getSongBpm: vi.fn(() => songBpm),
  };
}

const rateInput = () => document.getElementById("playbackRate") as HTMLInputElement;
const speedSlider = () => document.getElementById("speedSlider") as HTMLInputElement;
const speedValue = () => document.getElementById("speedValue")!;
const press = (key: string, init: KeyboardEventInit = {}) =>
  window.dispatchEvent(new KeyboardEvent("keydown", { key, ...init }));
const applyBtn = () => document.getElementById("playbackRateApply")!;
const metEnabled = () => document.getElementById("metronomeEnabled") as HTMLInputElement;
const metVolume = () => document.getElementById("metronomeVolume") as HTMLInputElement;

describe("initPlaybackRateAndMetronomePanel", () => {
  beforeEach(stageDom);

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initPlaybackRateAndMetronomePanel(makeDeps())).toThrow(/required DOM/);
  });

  it("defaults the reported playback rate to 1", () => {
    const handle = initPlaybackRateAndMetronomePanel(makeDeps());
    expect(handle.getPlaybackRate()).toBe(1);
  });

  it("apply button sets rate, notifies host, and updates status", () => {
    const deps = makeDeps();
    const handle = initPlaybackRateAndMetronomePanel(deps);
    rateInput().value = "1.5";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    expect(deps.transportController.setPlaybackRate).toHaveBeenCalledWith(1.5);
    expect(deps.onPlaybackRateApplied).toHaveBeenCalledWith(1.5);
    expect(deps.setAudioStatus).toHaveBeenCalledWith("playbackRate set: 1.50x");
    expect(handle.getPlaybackRate()).toBe(1.5);
  });

  it("apply button ignores non-positive or non-finite rates", () => {
    const deps = makeDeps();
    const handle = initPlaybackRateAndMetronomePanel(deps);
    rateInput().value = "0";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    rateInput().value = "-2";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    rateInput().value = "abc";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    expect(deps.transportController.setPlaybackRate).not.toHaveBeenCalled();
    expect(handle.getPlaybackRate()).toBe(1);
  });

  it("metronome toggle enables and reports on", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    metEnabled().checked = true;
    metEnabled().dispatchEvent(new Event("change"));
    expect(deps.metronome.setEnabled).toHaveBeenCalledWith(true);
    expect(deps.setAudioStatus).toHaveBeenCalledWith("metronome: on");
  });

  it("metronome toggle disables and reports off", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    metEnabled().checked = false;
    metEnabled().dispatchEvent(new Event("change"));
    expect(deps.metronome.setEnabled).toHaveBeenCalledWith(false);
    expect(deps.setAudioStatus).toHaveBeenCalledWith("metronome: off");
  });

  it("volume input forwards a finite value", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    metVolume().value = "0.8";
    metVolume().dispatchEvent(new Event("input"));
    expect(deps.metronome.setVolume).toHaveBeenCalledWith(0.8);
  });

  it("volume input ignores a non-finite value", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    metVolume().value = "not-a-number";
    metVolume().dispatchEvent(new Event("input"));
    expect(deps.metronome.setVolume).not.toHaveBeenCalled();
  });
});

describe("speed slider", () => {
  beforeEach(stageDom);

  it("sets the speed through the transport while it is dragged, and shows the BPM it makes", () => {
    const deps = makeDeps(120);
    const handle = initPlaybackRateAndMetronomePanel(deps);
    expect(speedValue().textContent).toBe("100% · 120 BPM (song 120)");

    speedSlider().value = "75";
    speedSlider().dispatchEvent(new Event("input"));

    expect(deps.transportController.setPlaybackRate).toHaveBeenCalledWith(0.75);
    expect(deps.onPlaybackRateApplied).toHaveBeenCalledWith(0.75);
    expect(handle.getPlaybackRate()).toBe(0.75);
    expect(speedValue().textContent).toBe("75% · 90 BPM (song 120)");
    // The rail's box follows, so the two controls never disagree.
    expect(rateInput().value).toBe("0.75");
  });

  it("follows the tempo map as the playhead moves", () => {
    let bpm = 120;
    const deps = makeDeps();
    deps.getSongBpm.mockImplementation(() => bpm);
    const handle = initPlaybackRateAndMetronomePanel(deps);
    speedSlider().value = "50";
    speedSlider().dispatchEvent(new Event("input"));
    bpm = 90;
    handle.refresh();
    expect(speedValue().textContent).toBe("50% · 45 BPM (song 90)");
  });

  it("reaches the transport in use now, not the one it started with", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    const replacement = { setPlaybackRate: vi.fn() };
    deps.getTransportController.mockReturnValue(replacement as any);
    speedSlider().value = "60";
    speedSlider().dispatchEvent(new Event("input"));
    expect(replacement.setPlaybackRate).toHaveBeenCalledWith(0.6);
    expect(deps.transportController.setPlaybackRate).not.toHaveBeenCalled();
  });

  it("parks at the top for a rate set past 100% in the rail, and says the real speed", () => {
    const deps = makeDeps(100);
    initPlaybackRateAndMetronomePanel(deps);
    rateInput().value = "1.5";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    expect(speedSlider().value).toBe("100");
    expect(speedValue().textContent).toBe("150% · 150 BPM (song 100)");
  });

  it("- and = step the speed, stopping at 25% and 100%", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    press("-");
    expect(deps.transportController.setPlaybackRate).toHaveBeenLastCalledWith(0.95);
    for (let i = 0; i < 20; i += 1) press("-");
    expect(deps.transportController.setPlaybackRate).toHaveBeenLastCalledWith(0.25);
    expect(speedSlider().value).toBe("25");
    press("=");
    expect(deps.transportController.setPlaybackRate).toHaveBeenLastCalledWith(0.3);
    for (let i = 0; i < 20; i += 1) press("+");
    expect(deps.transportController.setPlaybackRate).toHaveBeenLastCalledWith(1);
    const calls = deps.transportController.setPlaybackRate.mock.calls.length;
    press("=");
    expect(deps.transportController.setPlaybackRate.mock.calls.length).toBe(calls);
  });

  it("leaves modified keys (zoom) and typing alone, and never steps down from 2x with +", () => {
    const deps = makeDeps();
    initPlaybackRateAndMetronomePanel(deps);
    press("-", { ctrlKey: true });
    rateInput().dispatchEvent(new KeyboardEvent("keydown", { key: "-", bubbles: true }));
    expect(deps.transportController.setPlaybackRate).not.toHaveBeenCalled();

    rateInput().value = "2";
    applyBtn().dispatchEvent(new MouseEvent("click"));
    press("=");
    expect(deps.transportController.setPlaybackRate).toHaveBeenLastCalledWith(2);
  });

  it("formats and snaps", () => {
    expect(formatSpeedReadout(0.75, null)).toBe("75%");
    expect(formatSpeedReadout(0.85, 133.4)).toBe("85% · 113 BPM (song 133)");
    expect(speedSliderPercent(0.52)).toBe(50);
    expect(speedSliderPercent(0.1)).toBe(25);
    expect(speedSliderPercent(1.7)).toBe(100);
  });
});
