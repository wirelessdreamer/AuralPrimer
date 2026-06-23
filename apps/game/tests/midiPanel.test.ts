// @vitest-environment jsdom
/**
 * Execution tests for the MIDI panel. Stages the full MIDI control surface in
 * jsdom, mocks the Tauri `invoke` + `listen` and the TransportController, and
 * exercises: init wiring, port refresh (input + output, saved-settings happy
 * path + error path + empty path), output clock control (start/continue/stop/
 * seek/bpm throttle), message senders (note on/off, cc, all-notes-off, raw hex)
 * including their validation errors, input connect/disconnect, the Rust
 * event-listener callbacks, and disableAll.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

// Capture every listen() registration so tests can fire the Rust events.
const listeners = new Map<string, (ev: { payload: unknown }) => void>();
const listen = vi.fn(
  async (name: string, cb: (ev: { payload: unknown }) => void) => {
    listeners.set(name, cb);
    return () => listeners.delete(name);
  },
);
vi.mock("@tauri-apps/api/event", () => ({ listen: (...a: unknown[]) => (listen as any)(...a) }));

import { initMidiPanel, type MidiPanelDeps } from "../src/midiPanel";

const MIDI_IDS = [
  ["midiFollowEnabled", "input", "checkbox"],
  ["midiInPort", "select", ""],
  ["midiInRefresh", "button", ""],
  ["midiInConnect", "button", ""],
  ["midiInDisconnect", "button", ""],
  ["midiTempoScale", "input", "number"],
  ["midiInSysexEnabled", "input", "checkbox"],
  ["midiInPanic", "button", ""],
  ["midiStatus", "pre", ""],
  ["midiInActiveNotes", "pre", ""],
  ["midiInEvents", "pre", ""],
  ["midiOutStatus", "pre", ""],
  ["midiOutEnabled", "input", "checkbox"],
  ["midiOutPort", "select", ""],
  ["midiOutRefresh", "button", ""],
  ["midiOutSelect", "button", ""],
  ["midiOutStart", "button", ""],
  ["midiOutContinue", "button", ""],
  ["midiOutStop", "button", ""],
  ["midiOutSysexEnabled", "input", "checkbox"],
  ["midiMsgChannel", "input", "number"],
  ["midiMsgNote", "input", "number"],
  ["midiMsgVelocity", "input", "number"],
  ["midiMsgNoteOn", "button", ""],
  ["midiMsgNoteOff", "button", ""],
  ["midiMsgAllNotesOff", "button", ""],
  ["midiMsgCc", "input", "number"],
  ["midiMsgCcValue", "input", "number"],
  ["midiMsgCcSend", "button", ""],
  ["midiOutRawHex", "input", "text"],
  ["midiOutRawSend", "button", ""],
] as const;

function stageDom(): void {
  const html = MIDI_IDS.map(([id, tag, type]) => {
    if (tag === "input") return `<input id="${id}" type="${type}" />`;
    if (tag === "select") return `<select id="${id}"></select>`;
    if (tag === "button") return `<button id="${id}"></button>`;
    return `<pre id="${id}"></pre>`;
  }).join("");
  document.body.innerHTML = html;
}

const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const click = (id: string) => $(id).dispatchEvent(new MouseEvent("click"));
const change = (id: string) => $(id).dispatchEvent(new Event("change"));
const txt = (id: string) => $(id).textContent ?? "";
const flush = async () => {
  // Drain a deep chain of awaited invoke() microtasks (some handlers await
  // 4-5 sequential invokes before reaching their terminal state).
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

function makeTransport() {
  return {
    getState: vi.fn(() => ({ bpm: 120, t: 0 })),
    setFollowExternalClock: vi.fn(),
    setExternalClockRunning: vi.fn(),
    setExternalClockBpm: vi.fn(),
    pushExternalClockDelta: vi.fn(),
    seekFromExternalClock: vi.fn(),
  };
}

function makeDeps(overrides: Partial<MidiPanelDeps> = {}): MidiPanelDeps {
  const transport = makeTransport();
  return {
    transportController: transport as unknown as MidiPanelDeps["transportController"],
    haveTauri: () => true,
    escapeHtml: (s: string) => s,
    onExternalBpmChange: vi.fn(),
    ...overrides,
  };
}

describe("midiPanel", () => {
  beforeEach(() => {
    stageDom();
    invoke.mockReset();
    invoke.mockResolvedValue(undefined);
    listeners.clear();
    listen.mockClear();
  });

  it("initializes, registers all Rust event listeners, and seeds active-notes text", () => {
    const handle = initMidiPanel(makeDeps());
    expect(handle).toBeTruthy();
    // 6 listen() subscriptions from the haveTauri() block
    expect(listen).toHaveBeenCalledTimes(6);
    expect(txt("midiInActiveNotes")).toContain("no active notes");
  });

  it("does NOT register Rust listeners when haveTauri() is false", () => {
    initMidiPanel(makeDeps({ haveTauri: () => false }));
    expect(listen).not.toHaveBeenCalled();
  });

  it("follow-clock checkbox toggles transport follow", () => {
    const deps = makeDeps();
    initMidiPanel(deps);
    const tc = deps.transportController as any;
    ($("midiFollowEnabled") as HTMLInputElement).checked = true;
    change("midiFollowEnabled");
    expect(tc.setFollowExternalClock).toHaveBeenCalledWith(true);
    expect(txt("midiStatus")).toContain("on");
  });

  it("refreshInputPorts: renders ports, applies saved settings, sets status", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports")
        return [
          { id: 1, name: "Keystation", stable_id: "ks1", backend: "winrt" },
          { id: 2, name: "Other" },
        ];
      if (cmd === "midi_clock_input_get_saved_settings")
        return { port: { id: 1, name: "Keystation", stable_id: "ks1" }, tempo_scale: 2, allow_sysex: true };
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll();
    const sel = $("midiInPort") as HTMLSelectElement;
    expect(sel.options.length).toBe(2);
    expect(sel.value).toBe("1");
    expect(($("midiTempoScale") as HTMLInputElement).value).toBe("2");
    expect(($("midiInSysexEnabled") as HTMLInputElement).checked).toBe(true);
    expect(txt("midiStatus")).toContain("port(s) found via winrt");
  });

  it("refreshInputPorts: empty port list shows the no-ports guidance", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports") return [];
      if (cmd === "midi_clock_input_get_saved_settings")
        return { port: null, tempo_scale: 1, allow_sysex: false };
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll();
    expect(txt("midiStatus")).toContain("0 ports found");
    expect(($("midiInPort") as HTMLSelectElement).value).toBe("");
  });

  it("refreshInputPorts: saved-settings error is captured as a warning suffix", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports") return [{ id: 1, name: "Port" }];
      if (cmd === "midi_clock_input_get_saved_settings") throw new Error("no-prefs");
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll();
    expect(txt("midiStatus")).toContain("saved settings ignored");
  });

  it("refreshInputPorts: list error path renders failure option + error status", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports") throw new Error("backend-dead");
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll().catch(() => {});
    expect(txt("midiStatus")).toContain("midi input ports error");
  });

  it("refreshOutputPorts: renders ports + applies saved port/sysex", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_output_ports")
        return [{ id: 5, name: "OutA", stable_id: "o5" }];
      if (cmd === "midi_clock_output_get_saved_port")
        return { id: 5, name: "OutA", stable_id: "o5" };
      if (cmd === "midi_output_get_saved_allow_sysex") return true;
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll();
    expect(($("midiOutPort") as HTMLSelectElement).value).toBe("5");
    expect(($("midiOutSysexEnabled") as HTMLInputElement).checked).toBe(true);
  });

  it("refreshOutputPorts: error path sets output error status", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports") return [];
      if (cmd === "midi_clock_input_get_saved_settings")
        return { port: null, tempo_scale: 1, allow_sysex: false };
      if (cmd === "list_midi_output_ports") throw new Error("out-dead");
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await handle.refreshAll();
    expect(txt("midiOutStatus")).toContain("midi output ports error");
  });

  it("output enable checkbox refreshes ports; disable stops the clock", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_output_ports") return [];
      if (cmd === "midi_clock_output_get_saved_port") return null;
      if (cmd === "midi_output_get_saved_allow_sysex") return false;
      return undefined;
    });
    initMidiPanel(makeDeps());
    ($("midiOutEnabled") as HTMLInputElement).checked = true;
    change("midiOutEnabled");
    await flush();
    expect(invoke).toHaveBeenCalledWith("list_midi_output_ports");
    expect(txt("midiOutStatus")).toContain("enabled");

    ($("midiOutEnabled") as HTMLInputElement).checked = false;
    change("midiOutEnabled");
    await flush();
    expect(txt("midiOutStatus")).toContain("disabled");
  });

  it("outStartOrContinue does nothing while output is disabled", async () => {
    const handle = initMidiPanel(makeDeps());
    await handle.outStartOrContinue();
    expect(invoke).not.toHaveBeenCalledWith("midi_clock_output_start");
  });

  it("Start button selects port, sends START, then Stop sends STOP", async () => {
    const handle = initMidiPanel(makeDeps());
    ($("midiOutPort") as HTMLSelectElement).innerHTML = '<option value="3">P</option>';
    ($("midiOutPort") as HTMLSelectElement).value = "3";
    click("midiOutStart");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_select_port_and_persist", { portId: 3 });
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_start");
    expect(txt("midiOutStatus")).toContain("START");

    click("midiOutStop");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_stop");
    expect(txt("midiOutStatus")).toContain("STOP");
    void handle;
  });

  it("Continue button issues a CONTINUE on the output clock", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutPort") as HTMLSelectElement).innerHTML = '<option value="2">P</option>';
    ($("midiOutPort") as HTMLSelectElement).value = "2";
    click("midiOutContinue");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_continue");
    expect(txt("midiOutStatus")).toContain("CONTINUE");
  });

  it("outStartOrContinue issues CONTINUE when already started and seeked past 0", async () => {
    const deps = makeDeps();
    (deps.transportController as any).getState = vi.fn(() => ({ bpm: 120, t: 10 }));
    const handle = initMidiPanel(deps);
    ($("midiOutEnabled") as HTMLInputElement).checked = true;
    ($("midiOutPort") as HTMLSelectElement).innerHTML = '<option value="1">P</option>';
    ($("midiOutPort") as HTMLSelectElement).value = "1";
    // first start sets everStarted; running flips false after stop
    click("midiOutStart");
    await flush();
    await handle.outStop();
    invoke.mockClear();
    await handle.outStartOrContinue();
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_continue");
  });

  it("outSetBpmIfNeeded: gated by enable + throttle + validity", async () => {
    const handle = initMidiPanel(makeDeps());
    // disabled -> no-op
    await handle.outSetBpmIfNeeded(120);
    expect(invoke).not.toHaveBeenCalledWith("midi_clock_output_set_bpm", expect.anything());
    // enable
    ($("midiOutEnabled") as HTMLInputElement).checked = true;
    change("midiOutEnabled");
    await flush();
    invoke.mockClear();
    await handle.outSetBpmIfNeeded(0); // invalid -> no-op
    expect(invoke).not.toHaveBeenCalledWith("midi_clock_output_set_bpm", expect.anything());
    await handle.outSetBpmIfNeeded(130);
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_set_bpm", { bpm: 130 });
  });

  it("outSeek: gated by enable + validity then invokes seek", async () => {
    const handle = initMidiPanel(makeDeps());
    await handle.outSeek(5); // disabled
    expect(invoke).not.toHaveBeenCalledWith("midi_clock_output_seek", expect.anything());
    ($("midiOutEnabled") as HTMLInputElement).checked = true;
    change("midiOutEnabled");
    await flush();
    await handle.outSeek(-1); // invalid
    expect(invoke).not.toHaveBeenCalledWith("midi_clock_output_seek", expect.anything());
    await handle.outSeek(7.5);
    expect(invoke).toHaveBeenCalledWith("midi_clock_output_seek", { tSec: 7.5 });
  });

  it("output sysex checkbox persists the new value", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutSysexEnabled") as HTMLInputElement).checked = true;
    change("midiOutSysexEnabled");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_set_allow_sysex_and_persist", { enabled: true });
  });

  it("note-on / note-off / cc / all-notes-off senders invoke the right commands", async () => {
    initMidiPanel(makeDeps());
    ($("midiMsgChannel") as HTMLInputElement).value = "1";
    ($("midiMsgNote") as HTMLInputElement).value = "60";
    ($("midiMsgVelocity") as HTMLInputElement).value = "100";
    ($("midiMsgCc") as HTMLInputElement).value = "7";
    ($("midiMsgCcValue") as HTMLInputElement).value = "64";

    click("midiMsgNoteOn");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_send_note_on", { channel: 0, note: 60, velocity: 100 });

    click("midiMsgNoteOff");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_send_note_off", { channel: 0, note: 60, velocity: 100 });

    click("midiMsgCcSend");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_send_control_change", { channel: 0, controller: 7, value: 64 });

    click("midiMsgAllNotesOff");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_all_notes_off", { channel: 0 });
  });

  it("invalid channel surfaces a validation error in the output status", async () => {
    initMidiPanel(makeDeps());
    ($("midiMsgChannel") as HTMLInputElement).value = "0"; // < 1
    ($("midiMsgNote") as HTMLInputElement).value = "60";
    ($("midiMsgVelocity") as HTMLInputElement).value = "100";
    click("midiMsgNoteOn");
    await flush();
    expect(txt("midiOutStatus")).toContain("MIDI channel must be 1-16");
  });

  it("raw hex send: parses bytes and invokes send_raw", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutRawHex") as HTMLInputElement).value = "90 3C 64";
    click("midiOutRawSend");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_output_send_raw", { bytes: [0x90, 0x3c, 0x64] });
    expect(txt("midiOutStatus")).toContain("90 3C 64");
  });

  it("raw hex send: invalid token surfaces an error", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutRawHex") as HTMLInputElement).value = "zz";
    click("midiOutRawSend");
    await flush();
    expect(txt("midiOutStatus")).toContain("Invalid hex byte");
  });

  it("raw hex send: empty input surfaces a prompt", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutRawHex") as HTMLInputElement).value = "   ";
    click("midiOutRawSend");
    await flush();
    expect(txt("midiOutStatus")).toContain("one or more hex bytes");
  });

  it("output select button: no port selected reports gracefully", async () => {
    initMidiPanel(makeDeps());
    ($("midiOutPort") as HTMLSelectElement).innerHTML = "";
    // value "" -> Number("") === 0 which IS finite, so to exercise the
    // not-finite branch we set a non-numeric value.
    const sel = $("midiOutPort") as HTMLSelectElement;
    sel.innerHTML = '<option value="abc">x</option>';
    sel.value = "abc";
    click("midiOutSelect");
    await flush();
    expect(txt("midiOutStatus")).toContain("no port selected");
  });

  it("connect input: no port selected reports guidance", async () => {
    initMidiPanel(makeDeps());
    const sel = $("midiInPort") as HTMLSelectElement;
    sel.innerHTML = '<option value="x">x</option>';
    sel.value = "x";
    click("midiInConnect");
    await flush();
    expect(txt("midiStatus")).toContain("no port selected");
  });

  it("connect + disconnect input clock", async () => {
    const deps = makeDeps();
    initMidiPanel(deps);
    const sel = $("midiInPort") as HTMLSelectElement;
    sel.innerHTML = '<option value="1">Keystation</option>';
    sel.value = "1";
    ($("midiTempoScale") as HTMLInputElement).value = "1";
    click("midiInConnect");
    await flush();
    expect(invoke).toHaveBeenCalledWith(
      "midi_clock_input_start_and_persist",
      expect.objectContaining({ portId: 1 }),
    );
    expect(txt("midiStatus")).toContain("connected");

    click("midiInDisconnect");
    await flush();
    expect(invoke).toHaveBeenCalledWith("midi_clock_input_stop");
    expect((deps.transportController as any).setExternalClockRunning).toHaveBeenCalledWith(false);
    expect(txt("midiStatus")).toContain("disconnected");
  });

  it("input sysex checkbox: not connected just updates status", async () => {
    initMidiPanel(makeDeps());
    ($("midiInSysexEnabled") as HTMLInputElement).checked = true;
    change("midiInSysexEnabled");
    await flush();
    expect(txt("midiStatus")).toContain("on next connect");
  });

  it("input sysex checkbox: reconnects when already connected", async () => {
    initMidiPanel(makeDeps());
    const sel = $("midiInPort") as HTMLSelectElement;
    sel.innerHTML = '<option value="1">K</option>';
    sel.value = "1";
    click("midiInConnect");
    await flush();
    invoke.mockClear();
    change("midiInSysexEnabled");
    await flush();
    expect(invoke).toHaveBeenCalledWith(
      "midi_clock_input_start_and_persist",
      expect.objectContaining({ portId: 1 }),
    );
  });

  it("input refresh button + panic button work", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_input_ports") return [];
      if (cmd === "midi_clock_input_get_saved_settings")
        return { port: null, tempo_scale: 1, allow_sysex: false };
      return undefined;
    });
    initMidiPanel(makeDeps());
    click("midiInRefresh");
    await flush();
    expect(invoke).toHaveBeenCalledWith("list_midi_input_ports");

    click("midiInPanic");
    expect(txt("midiInEvents")).toContain("input monitor cleared");
  });

  it("output refresh button triggers a list", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "list_midi_output_ports") return [];
      if (cmd === "midi_clock_output_get_saved_port") return null;
      if (cmd === "midi_output_get_saved_allow_sysex") return false;
      return undefined;
    });
    initMidiPanel(makeDeps());
    click("midiOutRefresh");
    await flush();
    expect(invoke).toHaveBeenCalledWith("list_midi_output_ports");
  });

  it("Rust midi_clock_* events drive transport + status", () => {
    const deps = makeDeps();
    initMidiPanel(deps);
    const tc = deps.transportController as any;

    listeners.get("midi_clock_start")!({ payload: {} });
    expect(tc.setExternalClockRunning).toHaveBeenCalledWith(true);
    expect(txt("midiStatus")).toContain("START");

    listeners.get("midi_clock_stop")!({ payload: {} });
    expect(tc.setExternalClockRunning).toHaveBeenCalledWith(false);

    listeners.get("midi_clock_bpm")!({ payload: { bpm: 128, raw_bpm: 128, tempo_scale: 1 } });
    expect(tc.setExternalClockBpm).toHaveBeenCalledWith(128);
    expect(deps.onExternalBpmChange).toHaveBeenCalledWith(128);

    listeners.get("midi_clock_tick")!({ payload: { dt_sec: 0.01 } });
    expect(tc.pushExternalClockDelta).toHaveBeenCalledWith(0.01);

    listeners.get("midi_clock_seek")!({ payload: { t_sec: 3.25 } });
    expect(tc.seekFromExternalClock).toHaveBeenCalledWith(3.25);
    expect(txt("midiStatus")).toContain("SEEK");
  });

  it("midi_input_message event updates active notes + dispatches a window event", () => {
    initMidiPanel(makeDeps());
    const seen = vi.fn();
    window.addEventListener("auralprimer:midi-input", seen as EventListener);

    listeners.get("midi_input_message")!({
      payload: {
        timestamp_us: 1,
        message_type: "note_on",
        status: 144,
        channel: 0,
        data1: 60,
        data2: 100,
        bytes: [144, 60, 100],
      },
    });
    expect(txt("midiInActiveNotes")).toContain("active notes");
    expect(txt("midiInEvents")).toContain("note_on");
    expect(seen).toHaveBeenCalled();
  });

  it("midi_input_message of type clock skips the note tracker but still dispatches", () => {
    initMidiPanel(makeDeps());
    const seen = vi.fn();
    window.addEventListener("auralprimer:midi-input", seen as EventListener);
    listeners.get("midi_input_message")!({
      payload: { timestamp_us: 1, message_type: "clock", status: 248, bytes: [248] },
    });
    expect(txt("midiInActiveNotes")).toContain("no active notes");
    expect(seen).toHaveBeenCalled();
  });

  it("disableAll disables every control", () => {
    const handle = initMidiPanel(makeDeps());
    handle.disableAll();
    expect(($("midiInPort") as HTMLSelectElement).disabled).toBe(true);
    expect(($("midiOutRawSend") as HTMLButtonElement).disabled).toBe(true);
    expect(($("midiMsgChannel") as HTMLInputElement).disabled).toBe(true);
  });

  it("outShutdown swallows invoke errors", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "midi_clock_output_shutdown") throw new Error("nope");
      return undefined;
    });
    const handle = initMidiPanel(makeDeps());
    await expect(handle.outShutdown()).resolves.toBeUndefined();
  });

  it("inputActiveNotes returns the tracker snapshot", () => {
    const handle = initMidiPanel(makeDeps());
    const snap = handle.inputActiveNotes();
    expect(snap.activeNotes).toEqual([]);
    expect(snap.pressedCount).toBe(0);
  });
});
