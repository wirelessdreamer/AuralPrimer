// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initRefineMidiMonitor } from "../src/refineMidiMonitor";
import type { RefineAuditionHandle } from "../src/refineAudition";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
const saved = { port: { id: 0, name: "USB Axiom 61", stable_id: "axiom" }, tempo_scale: 1, allow_sysex: true };
let connected = true;
let timestamp = 0;
let monitor: ReturnType<typeof initRefineMidiMonitor>;
const audition = { setLiveNotes: vi.fn(), setLiveGain: vi.fn(), resume: vi.fn(async () => {}) };
const onNotes = vi.fn();
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id)! as T;
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const send = (message_type: string, data1: number, data2 = 0, channel = 0) => window.dispatchEvent(new CustomEvent("auralprimer:midi-input", {
  detail: { message_type, data1, data2, channel, timestamp_us: ++timestamp, status: 0, bytes: [] },
}));
async function enable() {
  el<HTMLInputElement>("refineMidiSound").checked = true;
  el("refineMidiSound").dispatchEvent(new Event("change"));
  await flush();
}
beforeEach(async () => {
  vi.clearAllMocks();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  connected = true; timestamp = 0;
  invoke.mockImplementation(async (command: string) => {
    if (command === "midi_clock_input_get_saved_settings") return saved;
    if (command === "midi_clock_input_is_connected") return connected;
    if (command === "list_midi_input_ports") return [{ id: 3, name: "Renamed Axiom", stable_id: "axiom" }, { id: 0, name: "Other keyboard" }];
    if (command === "midi_clock_input_start") { connected = true; return; }
    throw Error(command);
  });
  document.body.innerHTML = `<div class="route isActive"><div id="root">
    <input type="checkbox" id="refineMidiSound"><input id="refineMidiGain" type="range" value="70">
    <button id="refineMidiConnect">Reconnect</button><button id="refineMidiPanic">All notes off</button>
    <span id="refineMidiStatus"></span><span id="refineMidiNotes"></span>
  </div></div>`;
  monitor = initRefineMidiMonitor({ root: el("root"), audition: audition as unknown as RefineAuditionHandle, onNotes });
  await flush();
});
afterEach(() => { monitor.dispose(); vi.restoreAllMocks(); });

describe("Cleanup live MIDI", () => {
  it("reuses the active shared input and shows keys without requiring sound", async () => {
    expect(el("refineMidiStatus").textContent).toBe("MIDI · USB Axiom 61");
    send("note_on", 60, 82);
    expect(onNotes).toHaveBeenLastCalledWith([expect.objectContaining({ pitch: 60, velocity: 82 })]);
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
    await enable();
    expect(audition.resume).toHaveBeenCalledOnce();
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([expect.objectContaining({ pitch: 60 })]);
    expect(invoke.mock.calls.some(([cmd]) => cmd === "midi_clock_input_start")).toBe(false);
    expect(audition.setLiveGain).toHaveBeenCalledWith(0.7);
  });

  it("keeps sustain consistent between the two views and sound and supports all notes off", async () => {
    await enable();
    send("note_on", 60, 100);
    send("control_change", 64, 127);
    send("note_off", 60);
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([expect.objectContaining({ pitch: 60, heldBySustain: true })]);
    expect(el("refineMidiNotes").textContent).toContain("C4 ♫");
    send("control_change", 64, 0);
    expect(onNotes).toHaveBeenLastCalledWith([]);
    send("note_on", 64, 90);
    send("note_on", 67, 90);
    send("note_on", 36, 100, 9);
    expect(onNotes.mock.lastCall![0]).toHaveLength(2);
    el("refineMidiPanic").click();
    expect(onNotes).toHaveBeenLastCalledWith([]);
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
  });

  it("releases sound on blur, route exit and disconnect", async () => {
    await enable();
    send("note_on", 60, 100);
    vi.mocked(document.hasFocus).mockReturnValue(false);
    window.dispatchEvent(new Event("blur"));
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
    send("note_on", 64, 100);
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
    document.querySelector(".route")!.classList.remove("isActive");
    await flush();
    expect(onNotes).toHaveBeenLastCalledWith([]);
    send("note_on", 67, 100);
    expect(onNotes).toHaveBeenLastCalledWith([]);
    document.querySelector(".route")!.classList.add("isActive");
    vi.mocked(document.hasFocus).mockReturnValue(true);
    send("note_on", 69, 100);
    window.dispatchEvent(new CustomEvent("auralprimer:midi-connection", { detail: { connected: false } }));
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
    expect(el("refineMidiStatus").textContent).toBe("MIDI disconnected");
  });

  it("reconnects only the saved stable device and leaves the shared selection unchanged", async () => {
    connected = false;
    el("refineMidiConnect").click();
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_clock_input_start", { portId: 3, tempoScale: 1, allowSysex: true });
    expect(el("refineMidiStatus").textContent).toBe("MIDI · Renamed Axiom");
    expect(invoke.mock.calls.some(([cmd]) => cmd === "midi_clock_input_start_and_persist")).toBe(false);
  });

  it("does not connect an arbitrary keyboard when the saved device is missing", async () => {
    connected = false;
    invoke.mockImplementation(async (command: string) => {
      if (command === "midi_clock_input_get_saved_settings") return saved;
      if (command === "midi_clock_input_is_connected") return false;
      if (command === "list_midi_input_ports") return [{ id: 0, name: "Other keyboard" }];
      throw Error(command);
    });
    await enable();
    expect(el<HTMLInputElement>("refineMidiSound").checked).toBe(false);
    expect(el("refineMidiStatus").textContent).toContain("Choose your keyboard in Configure");
    expect(audition.setLiveNotes).toHaveBeenLastCalledWith([]);
  });
});
