// @vitest-environment jsdom
/**
 * Execution tests for the playback-rate + metronome panel. Stages the DOM
 * ids it reads, mocks the transport/metronome deps, and exercises the
 * apply-button, metronome-toggle, and volume-input handlers including the
 * NaN/invalid guards.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { initPlaybackRateAndMetronomePanel } from "../src/playbackRateAndMetronomePanel";

function stageDom(): void {
  document.body.innerHTML =
    '<input id="playbackRate" type="number" value="1" />' +
    '<button id="playbackRateApply"></button>' +
    '<input id="metronomeEnabled" type="checkbox" />' +
    '<input id="metronomeVolume" type="text" value="0.5" />';
}

function makeDeps() {
  let enabled = false;
  const setEnabled = vi.fn((v: boolean) => {
    enabled = v;
  });
  return {
    transportController: { setPlaybackRate: vi.fn() } as any,
    metronome: {
      setEnabled,
      getEnabled: vi.fn(() => enabled),
      setVolume: vi.fn(),
    } as any,
    setAudioStatus: vi.fn(),
    onPlaybackRateApplied: vi.fn(),
  };
}

const rateInput = () => document.getElementById("playbackRate") as HTMLInputElement;
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
