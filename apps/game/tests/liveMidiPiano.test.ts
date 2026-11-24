// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initLiveMidiPiano } from "../src/liveMidiPiano";

let timestamp = 0;
let monitor: ReturnType<typeof initLiveMidiPiano> | undefined;
const send = (message_type: string, data1: number, data2 = 0, channel = 0) => window.dispatchEvent(new CustomEvent("auralprimer:midi-input", {
  detail: { message_type, data1, data2, channel, timestamp_us: ++timestamp, status: 0, bytes: [] },
}));
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
beforeEach(() => { timestamp = 0; vi.spyOn(document, "hasFocus").mockReturnValue(true); });
afterEach(() => { monitor?.dispose(); monitor = undefined; vi.restoreAllMocks(); });
function setup(prepare = vi.fn(async () => true), isReady = () => true) {
  const options = { prepare, isReady, noteOn: vi.fn(), noteOff: vi.fn() };
  monitor = initLiveMidiPiano(options);
  return options;
}

describe("foreground live MIDI piano", () => {
  it("preserves velocity and sustain and treats zero velocity as note-off", () => {
    const piano = setup();
    send("note_on", 60, 82);
    send("control_change", 64, 127);
    send("note_on", 60, 0);
    expect(piano.noteOn).toHaveBeenCalledExactlyOnceWith(60, 82);
    expect(piano.noteOff).not.toHaveBeenCalled();
    send("control_change", 64, 0);
    expect(piano.noteOff).toHaveBeenCalledExactlyOnceWith(60);
    send("note_on", 36, 100, 9);
    expect(piano.noteOn).toHaveBeenCalledTimes(1);
  });

  it("silences held keys on blur and ignores background input", () => {
    const piano = setup();
    send("note_on", 60, 100);
    vi.mocked(document.hasFocus).mockReturnValue(false);
    window.dispatchEvent(new Event("blur"));
    expect(piano.noteOff).toHaveBeenCalledExactlyOnceWith(60);
    send("note_on", 64, 100);
    expect(piano.noteOn).toHaveBeenCalledTimes(1);
    vi.mocked(document.hasFocus).mockReturnValue(true);
    send("note_on", 67, 90);
    expect(piano.noteOn).toHaveBeenLastCalledWith(67, 90);
  });

  it("waits for samples before sounding a held first note and never plays an already released key", async () => {
    let ready = false;
    let finish!: (value: boolean) => void;
    const piano = setup(vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; })), () => ready);
    send("note_on", 60, 100);
    send("note_on", 64, 80);
    send("note_off", 60);
    expect(piano.prepare).toHaveBeenCalledOnce();
    expect(piano.noteOn).not.toHaveBeenCalled();
    ready = true;
    finish(true);
    await flush();
    expect(piano.noteOn).toHaveBeenCalledExactlyOnceWith(64, 80);
  });

  it("does not sound a pending note after losing focus or disposing", async () => {
    let finish!: (value: boolean) => void;
    let ready = false;
    const piano = setup(vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; })), () => ready);
    send("note_on", 60, 100);
    monitor!.dispose();
    ready = true;
    finish(true);
    await flush();
    expect(piano.noteOn).not.toHaveBeenCalled();
  });

  it("keeps a shared pitch sounding until all MIDI channels release it and honors panic", () => {
    const piano = setup();
    send("note_on", 60, 100, 0);
    send("note_on", 60, 90, 1);
    send("note_off", 60, 0, 1);
    expect(piano.noteOff).not.toHaveBeenCalled();
    expect(piano.noteOn).toHaveBeenCalledTimes(2);
    send("control_change", 123, 0, 0);
    expect(piano.noteOff).toHaveBeenCalledExactlyOnceWith(60);
    send("note_on", 64, 100);
    window.dispatchEvent(new Event("auralprimer:midi-reset"));
    expect(piano.noteOff).toHaveBeenLastCalledWith(64);
  });
});
