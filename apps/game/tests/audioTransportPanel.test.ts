// @vitest-environment jsdom
/**
 * Execution tests for the audio transport panel. Stages the transport control
 * row + loop controls in jsdom and asserts each button forwards to the
 * transportController / midiPanel / host callbacks, including error paths.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initAudioTransportPanel } from "../src/audioTransportPanel";

function stageDom(): void {
  document.body.innerHTML =
    '<button id="audioLoad"></button>' +
    '<button id="audioPlay"></button>' +
    '<button id="audioPause"></button>' +
    '<button id="audioStop"></button>' +
    '<input id="audioSeek" />' +
    '<button id="audioSeekGo"></button>' +
    '<pre id="audioStatus"></pre>' +
    '<pre id="vizStatus"></pre>' +
    '<input id="loopT0" />' +
    '<input id="loopT1" />' +
    '<button id="loopSet"></button>' +
    '<button id="loopClear"></button>';
}

function makeDeps(overrides: Partial<Parameters<typeof initAudioTransportPanel>[0]> = {}) {
  const consoleBridge = { log: vi.fn(), warn: vi.fn(), error: vi.fn() } as any;
  const transportController = {
    play: vi.fn(async () => {}),
    pause: vi.fn(),
    seek: vi.fn(),
    setLoop: vi.fn(),
  } as any;
  const midiPanel = {
    outStartOrContinue: vi.fn(async () => {}),
    outStop: vi.fn(async () => {}),
    outSeek: vi.fn(async () => {}),
  } as any;
  const pauseMenu = { close: vi.fn() } as any;
  return {
    transportController,
    midiPanel,
    pauseMenu,
    consoleBridge,
    onLoadAudio: vi.fn(async () => {}),
    onStopAudio: vi.fn(),
    refreshCachedTransport: vi.fn(),
    ...overrides,
  } as Parameters<typeof initAudioTransportPanel>[0];
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const click = (id: string) => document.getElementById(id)!.dispatchEvent(new MouseEvent("click"));
const status = () => document.getElementById("audioStatus")!.textContent;

describe("audioTransportPanel", () => {
  beforeEach(() => stageDom());

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initAudioTransportPanel(makeDeps())).toThrow(/required DOM/);
  });

  it("setAudioStatus / setVizStatus update elements + log; getVizStatus reads back", () => {
    const deps = makeDeps();
    const handle = initAudioTransportPanel(deps);
    handle.setAudioStatus("hello");
    handle.setVizStatus("viz-ok");
    expect(status()).toBe("hello");
    expect(handle.getVizStatus()).toBe("viz-ok");
    expect(deps.consoleBridge.log).toHaveBeenCalled();
    // exposed button handles.
    expect(handle.loadBtn).toBeInstanceOf(HTMLButtonElement);
    expect(handle.playBtn).toBeInstanceOf(HTMLButtonElement);
  });

  it("Load forwards to onLoadAudio and reports its rejection", async () => {
    const deps = makeDeps({ onLoadAudio: vi.fn(async () => { throw new Error("load-fail"); }) });
    initAudioTransportPanel(deps);
    click("audioLoad");
    await flush();
    expect(deps.onLoadAudio).toHaveBeenCalled();
    expect(status()).toContain("load-fail");
  });

  it("Play starts transport then midi continue", async () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    click("audioPlay");
    await flush();
    expect(deps.transportController.play).toHaveBeenCalled();
    expect(deps.midiPanel.outStartOrContinue).toHaveBeenCalled();
  });

  it("Play reports a transport rejection", async () => {
    const deps = makeDeps();
    (deps.transportController.play as any).mockRejectedValue(new Error("play-fail"));
    initAudioTransportPanel(deps);
    click("audioPlay");
    await flush();
    expect(status()).toContain("play-fail");
  });

  it("Pause pauses + refreshes cache + stops midi + status", async () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    click("audioPause");
    await flush();
    expect(deps.transportController.pause).toHaveBeenCalled();
    expect(deps.refreshCachedTransport).toHaveBeenCalled();
    expect(deps.midiPanel.outStop).toHaveBeenCalled();
    expect(status()).toBe("paused");
  });

  it("Stop closes pause menu, calls onStopAudio, resets midi, status", async () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    click("audioStop");
    await flush();
    expect(deps.pauseMenu.close).toHaveBeenCalled();
    expect(deps.onStopAudio).toHaveBeenCalled();
    expect(deps.midiPanel.outSeek).toHaveBeenCalledWith(0);
    expect(status()).toBe("stopped");
  });

  it("Seek with a valid value seeks transport + midi", () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    (document.getElementById("audioSeek") as HTMLInputElement).value = "12.5";
    click("audioSeekGo");
    expect(deps.transportController.seek).toHaveBeenCalledWith(12.5);
    expect(deps.midiPanel.outSeek).toHaveBeenCalledWith(12.5);
    expect(status()).toContain("12.50s");
  });

  it("Seek ignores a non-finite value and warns", () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    (document.getElementById("audioSeek") as HTMLInputElement).value = "abc";
    click("audioSeekGo");
    expect(deps.transportController.seek).not.toHaveBeenCalled();
    expect(deps.consoleBridge.warn).toHaveBeenCalled();
  });

  it("Loop set with valid endpoints sets the loop", () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    (document.getElementById("loopT0") as HTMLInputElement).value = "1";
    (document.getElementById("loopT1") as HTMLInputElement).value = "4";
    click("loopSet");
    expect(deps.transportController.setLoop).toHaveBeenCalledWith({ t0: 1, t1: 4 });
    expect(deps.refreshCachedTransport).toHaveBeenCalled();
    expect(status()).toContain("loop set: 1..4");
  });

  it("Loop set ignores invalid endpoints", () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    (document.getElementById("loopT0") as HTMLInputElement).value = "x";
    (document.getElementById("loopT1") as HTMLInputElement).value = "4";
    click("loopSet");
    expect(deps.transportController.setLoop).not.toHaveBeenCalled();
  });

  it("Loop clear clears the loop", () => {
    const deps = makeDeps();
    initAudioTransportPanel(deps);
    click("loopClear");
    expect(deps.transportController.setLoop).toHaveBeenCalledWith(undefined);
    expect(status()).toBe("loop cleared");
  });
});
