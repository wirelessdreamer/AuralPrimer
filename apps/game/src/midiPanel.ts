/**
 * MIDI panel — owns the entire MIDI control surface (input port + clock follow,
 * output port + clock generation, message sending, raw hex send) plus the
 * MIDI clock + input event listeners and the input-state tracker.
 *
 * Extracted from main.ts as Phase 2.F (MIDI half). Single large module because
 * the surface is genuinely cohesive and tangled around the same Tauri command
 * + DOM identifiers; splitting input/output further would just produce
 * cross-references for no gain. ~30 DOM grabs, ~20 functions, 5 listen()
 * subscriptions, and ~20 event listeners all move here together.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import {
  MidiInputStateTracker,
  formatMidiActiveNotes,
  formatMidiInputMessage,
  type MidiInputMessageEvent,
} from "./midiInput";
import type { TransportController } from "./transportController";

type MidiPortInfo = {
  id: number;
  name: string;
  stable_id?: string;
  backend?: string;
};

type MidiOutputSelection = { id: number; name: string; stable_id?: string | null };
type MidiInputSelection = { id: number; name: string; stable_id?: string | null };

type MidiInputSavedSettings = {
  port: MidiInputSelection | null;
  tempo_scale: number;
  allow_sysex: boolean;
};

export type MidiPanelDeps = {
  transportController: TransportController;
  haveTauri: () => boolean;
  escapeHtml: (s: string) => string;
  /** Notify the host when the external clock's BPM changes (so main.ts can keep
   * its cached transport state in sync). Called from the midi_clock_bpm event. */
  onExternalBpmChange: (bpm: number) => void;
};

export type MidiPanelHandle = {
  /** Refresh both input and output port lists from the Rust backend. */
  refreshAll: () => Promise<void>;
  /** Disable every control on the panel (used when !haveTauri()). */
  disableAll: () => void;
  /** Throttle-aware BPM publish; the host calls this from the visualizer tick. */
  outSetBpmIfNeeded: (bpm: number) => Promise<void>;
  /** Begin or continue the output clock (called from session start). */
  outStartOrContinue: () => Promise<void>;
  /** Stop the output clock (called from transport stop). */
  outStop: () => Promise<void>;
  /** Best-effort shutdown of the Rust output worker on beforeunload. */
  outShutdown: () => Promise<void>;
  /** Snapshot of currently-pressed MIDI input notes (for tab renderer overlay). */
  inputActiveNotes: () => ReturnType<MidiInputStateTracker["snapshot"]>;
};

export function initMidiPanel(deps: MidiPanelDeps): MidiPanelHandle {
  // --- DOM grabs ---
  const followEnabledInput = document.getElementById("midiFollowEnabled") as HTMLInputElement;
  const inPortSelect = document.getElementById("midiInPort") as HTMLSelectElement;
  const inRefreshBtn = document.getElementById("midiInRefresh") as HTMLButtonElement;
  const inConnectBtn = document.getElementById("midiInConnect") as HTMLButtonElement;
  const inDisconnectBtn = document.getElementById("midiInDisconnect") as HTMLButtonElement;
  const tempoScaleInput = document.getElementById("midiTempoScale") as HTMLInputElement;
  const inSysexEnabledInput = document.getElementById("midiInSysexEnabled") as HTMLInputElement;
  const inPanicBtn = document.getElementById("midiInPanic") as HTMLButtonElement;
  const statusEl = document.getElementById("midiStatus") as HTMLPreElement;
  const inActiveNotesEl = document.getElementById("midiInActiveNotes") as HTMLPreElement;
  const inEventsEl = document.getElementById("midiInEvents") as HTMLPreElement;
  const outStatusEl = document.getElementById("midiOutStatus") as HTMLPreElement;
  const outEnabledInput = document.getElementById("midiOutEnabled") as HTMLInputElement;
  const outPortSelect = document.getElementById("midiOutPort") as HTMLSelectElement;
  const outRefreshBtn = document.getElementById("midiOutRefresh") as HTMLButtonElement;
  const outSelectBtn = document.getElementById("midiOutSelect") as HTMLButtonElement;
  const outStartBtn = document.getElementById("midiOutStart") as HTMLButtonElement;
  const outContinueBtn = document.getElementById("midiOutContinue") as HTMLButtonElement;
  const outStopBtn = document.getElementById("midiOutStop") as HTMLButtonElement;
  const outSysexEnabledInput = document.getElementById("midiOutSysexEnabled") as HTMLInputElement;
  const msgChannelInput = document.getElementById("midiMsgChannel") as HTMLInputElement;
  const msgNoteInput = document.getElementById("midiMsgNote") as HTMLInputElement;
  const msgVelocityInput = document.getElementById("midiMsgVelocity") as HTMLInputElement;
  const msgNoteOnBtn = document.getElementById("midiMsgNoteOn") as HTMLButtonElement;
  const msgNoteOffBtn = document.getElementById("midiMsgNoteOff") as HTMLButtonElement;
  const msgAllNotesOffBtn = document.getElementById("midiMsgAllNotesOff") as HTMLButtonElement;
  const msgCcInput = document.getElementById("midiMsgCc") as HTMLInputElement;
  const msgCcValueInput = document.getElementById("midiMsgCcValue") as HTMLInputElement;
  const msgCcSendBtn = document.getElementById("midiMsgCcSend") as HTMLButtonElement;
  const outRawHexInput = document.getElementById("midiOutRawHex") as HTMLInputElement;
  const outRawSendBtn = document.getElementById("midiOutRawSend") as HTMLButtonElement;

  // --- State ---
  let midiConnected = false;
  let midiOutSysexEnabled = false;
  let midiOutEnabled = false;
  let midiOutRunning = false;
  let midiOutEverStarted = false;
  let lastMidiOutBpmSent = 0;
  let lastMidiOutBpmSentAtMs = 0;
  let inputEventLines: string[] = [];
  const tracker = new MidiInputStateTracker();

  // --- Status setters ---
  function setMidiStatus(msg: string): void { statusEl.textContent = msg; }
  function setMidiOutStatus(msg: string): void { outStatusEl.textContent = msg; }
  function setInputActiveNotesStatus(msg: string): void { inActiveNotesEl.textContent = msg; }
  function setInputEventsStatus(msg: string): void { inEventsEl.textContent = msg; }
  function appendInputEventLine(line: string): void {
    const s = line.trim();
    if (!s) return;
    inputEventLines.push(s);
    if (inputEventLines.length > 14) inputEventLines = inputEventLines.slice(-14);
    setInputEventsStatus(inputEventLines.join("\n"));
  }

  // --- Pure helpers ---
  function uiChannelToZeroBased(channelFromUi: number): number {
    const ch = Math.floor(channelFromUi);
    if (!Number.isFinite(ch) || ch < 1 || ch > 16) throw new Error("MIDI channel must be 1-16");
    return ch - 1;
  }
  function requireDataByte(name: string, value: number): number {
    const v = Math.floor(value);
    if (!Number.isFinite(v) || v < 0 || v > 127) throw new Error(`${name} must be 0-127`);
    return v;
  }
  function parseRawHexBytes(raw: string): number[] {
    const tokens = raw.trim().split(/[\s,]+/).filter((t) => t.length > 0);
    if (!tokens.length) throw new Error("Enter one or more hex bytes (example: 90 3C 64)");
    return tokens.map((tok) => {
      const clean = tok.startsWith("0x") || tok.startsWith("0X") ? tok.slice(2) : tok;
      if (!/^[0-9a-fA-F]{1,2}$/.test(clean)) throw new Error(`Invalid hex byte: ${tok}`);
      const v = Number.parseInt(clean, 16);
      if (!Number.isFinite(v) || v < 0 || v > 255) throw new Error(`Invalid hex byte: ${tok}`);
      return v;
    });
  }
  function portBackendLabel(ports: MidiPortInfo[]): string {
    return ports.find((p) => p.backend?.trim())?.backend?.trim() || "native";
  }
  function renderPortOptions(ports: MidiPortInfo[], emptyLabel: string): string {
    if (!ports.length) return `<option value="" selected>${deps.escapeHtml(emptyLabel)}</option>`;
    return ports.map((p) => {
      const titleParts = [
        p.backend ? `backend=${p.backend}` : "",
        p.stable_id ? `id=${p.stable_id}` : "",
      ].filter(Boolean);
      const title = titleParts.length ? ` title="${deps.escapeHtml(titleParts.join(" "))}"` : "";
      return `<option value="${p.id}"${title}>${deps.escapeHtml(p.name)}</option>`;
    }).join("\n");
  }
  function findSavedPortMatch(
    ports: MidiPortInfo[],
    saved: MidiInputSelection | MidiOutputSelection | null,
  ): MidiPortInfo | undefined {
    if (!saved) return undefined;
    if (saved.stable_id?.trim()) {
      const m = ports.find((p) => p.stable_id === saved.stable_id);
      if (m) return m;
    }
    return ports.find((p) => p.id === saved.id && p.name === saved.name)
      ?? ports.find((p) => p.name === saved.name);
  }
  function portNamesPreview(ports: MidiPortInfo[], maxPorts = 4): string {
    const names = ports.slice(0, maxPorts).map((p) => p.name.trim()).filter(Boolean);
    const suffix = ports.length > names.length ? `, +${ports.length - names.length} more` : "";
    return names.length ? `${names.join(", ")}${suffix}` : "(unnamed ports)";
  }

  // --- Refresh ---
  async function refreshInputPorts(): Promise<void> {
    const prevDisabled = inRefreshBtn.disabled;
    try {
      inRefreshBtn.disabled = true;
      setMidiStatus("midi input: refreshing ports...");
      const ports = await invoke<MidiPortInfo[]>("list_midi_input_ports");
      inPortSelect.innerHTML = renderPortOptions(ports, "No MIDI inputs found");

      let savedWarning = "";
      try {
        const saved = await invoke<MidiInputSavedSettings>("midi_clock_input_get_saved_settings");
        tempoScaleInput.value = String(saved.tempo_scale ?? 1);
        inSysexEnabledInput.checked = Boolean(saved.allow_sysex);
        const match = findSavedPortMatch(ports, saved.port);
        if (match) inPortSelect.value = String(match.id);
      } catch (settingsError) {
        savedWarning = `; saved settings ignored: ${String(settingsError)}`;
      }

      if (!ports.length) {
        inPortSelect.value = "";
        setMidiStatus("midi input: 0 ports found via native MIDI backend. Windows uses WinRT; macOS uses CoreMIDI; Linux uses ALSA. If another app sees the keyboard, close apps that may hold the port, replug the keyboard, then refresh.");
        return;
      }

      const backend = portBackendLabel(ports);
      const selectedName = inPortSelect.selectedOptions[0]?.textContent?.trim();
      setMidiStatus(`midi input: ${ports.length} port(s) found via ${backend}: ${portNamesPreview(ports)}${selectedName ? `; selected ${selectedName}` : ""}${savedWarning}`);
    } catch (e) {
      inPortSelect.innerHTML = renderPortOptions([], "MIDI input refresh failed");
      inPortSelect.value = "";
      setMidiStatus(`midi input ports error: ${String(e)}`);
    } finally {
      inRefreshBtn.disabled = prevDisabled;
    }
  }

  async function refreshOutputPorts(): Promise<void> {
    try {
      const ports = await invoke<MidiPortInfo[]>("list_midi_output_ports");
      outPortSelect.innerHTML = renderPortOptions(ports, "No MIDI outputs found");
      const [saved, savedSysex] = await Promise.all([
        invoke<MidiOutputSelection | null>("midi_clock_output_get_saved_port"),
        invoke<boolean>("midi_output_get_saved_allow_sysex"),
      ]);
      midiOutSysexEnabled = Boolean(savedSysex);
      outSysexEnabledInput.checked = midiOutSysexEnabled;
      const match = findSavedPortMatch(ports, saved);
      if (match) outPortSelect.value = String(match.id);
    } catch (e) {
      outPortSelect.innerHTML = renderPortOptions([], "MIDI output refresh failed");
      outPortSelect.value = "";
      setMidiOutStatus(`midi output ports error: ${String(e)}`);
    }
  }

  async function selectOutputPortAndPersist(): Promise<void> {
    const portId = Number(outPortSelect.value);
    if (!Number.isFinite(portId)) {
      setMidiOutStatus("midi output: no port selected");
      return;
    }
    await invoke("midi_clock_output_select_port_and_persist", { portId });
    await invoke("midi_output_set_allow_sysex_and_persist", { enabled: midiOutSysexEnabled });
    setMidiOutStatus(`midi output: selected port=${portId} sysex=${midiOutSysexEnabled ? "on" : "off"}`);
  }

  // --- Output clock control ---
  async function outSetBpmIfNeeded(bpm: number): Promise<void> {
    if (!midiOutEnabled) return;
    if (!Number.isFinite(bpm) || bpm <= 0) return;
    const now = performance.now();
    if (now - lastMidiOutBpmSentAtMs < 200 && Math.abs(bpm - lastMidiOutBpmSent) < 0.05) return;
    await invoke("midi_clock_output_set_bpm", { bpm });
    lastMidiOutBpmSent = bpm;
    lastMidiOutBpmSentAtMs = now;
  }

  async function outSeek(tSec: number): Promise<void> {
    if (!midiOutEnabled) return;
    if (!Number.isFinite(tSec) || tSec < 0) return;
    await invoke("midi_clock_output_seek", { tSec });
  }

  async function outStartOrContinue(): Promise<void> {
    if (!midiOutEnabled) return;
    await selectOutputPortAndPersist();
    const st = deps.transportController.getState();
    await outSetBpmIfNeeded(st.bpm);
    await outSeek(st.t);
    if (midiOutRunning) return;
    if (!midiOutEverStarted || st.t <= 0.0001) {
      await invoke("midi_clock_output_start");
      midiOutEverStarted = true;
      midiOutRunning = true;
      setMidiOutStatus("midi clock out: START");
    } else {
      await invoke("midi_clock_output_continue");
      midiOutRunning = true;
      setMidiOutStatus("midi clock out: CONTINUE");
    }
  }

  async function outStop(): Promise<void> {
    if (!midiOutEnabled) return;
    await invoke("midi_clock_output_stop");
    midiOutRunning = false;
    midiOutEverStarted = true;
    setMidiOutStatus("midi clock out: STOP");
  }

  async function setOutSysex(enabled: boolean, persist: boolean): Promise<void> {
    midiOutSysexEnabled = Boolean(enabled);
    outSysexEnabledInput.checked = midiOutSysexEnabled;
    if (persist) {
      await invoke("midi_output_set_allow_sysex_and_persist", { enabled: midiOutSysexEnabled });
    } else {
      await invoke("midi_output_set_allow_sysex", { enabled: midiOutSysexEnabled });
    }
  }

  // --- Message senders ---
  async function sendNoteOnFromUi(): Promise<void> {
    const channel = uiChannelToZeroBased(Number(msgChannelInput.value));
    const note = requireDataByte("note", Number(msgNoteInput.value));
    const velocity = requireDataByte("velocity", Number(msgVelocityInput.value));
    await invoke("midi_output_send_note_on", { channel, note, velocity });
    setMidiOutStatus(`midi out note on: ch${channel + 1} note=${note} vel=${velocity}`);
  }
  async function sendNoteOffFromUi(): Promise<void> {
    const channel = uiChannelToZeroBased(Number(msgChannelInput.value));
    const note = requireDataByte("note", Number(msgNoteInput.value));
    const velocity = requireDataByte("velocity", Number(msgVelocityInput.value));
    await invoke("midi_output_send_note_off", { channel, note, velocity });
    setMidiOutStatus(`midi out note off: ch${channel + 1} note=${note} vel=${velocity}`);
  }
  async function sendCcFromUi(): Promise<void> {
    const channel = uiChannelToZeroBased(Number(msgChannelInput.value));
    const controller = requireDataByte("cc", Number(msgCcInput.value));
    const value = requireDataByte("cc value", Number(msgCcValueInput.value));
    await invoke("midi_output_send_control_change", { channel, controller, value });
    setMidiOutStatus(`midi out cc: ch${channel + 1} cc=${controller} value=${value}`);
  }
  async function sendAllNotesOffFromUi(): Promise<void> {
    const channel = uiChannelToZeroBased(Number(msgChannelInput.value));
    await invoke("midi_output_all_notes_off", { channel });
    setMidiOutStatus(`midi out: all notes off ch${channel + 1}`);
  }
  async function sendRawFromUi(): Promise<void> {
    const bytes = parseRawHexBytes(outRawHexInput.value);
    await invoke("midi_output_send_raw", { bytes });
    setMidiOutStatus(`midi out raw: ${bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ")}`);
  }

  // --- Input clock connect ---
  async function connectClockInput(): Promise<void> {
    const portId = Number(inPortSelect.value);
    const tempoScale = Number(tempoScaleInput.value);
    const allowSysex = inSysexEnabledInput.checked;
    if (!Number.isFinite(portId)) {
      setMidiStatus("midi input: no port selected; refresh after connecting the keyboard");
      return;
    }
    await invoke("midi_clock_input_start_and_persist", { portId, tempoScale, allowSysex });
    midiConnected = true;
    const portName = inPortSelect.selectedOptions[0]?.textContent?.trim() || `port ${portId}`;
    setInputActiveNotesStatus(formatMidiActiveNotes(tracker.clear()));
    setMidiStatus(`midi input connected: ${portName} scale=${tempoScale} sysex=${allowSysex ? "on" : "off"}`);
  }
  async function disconnectClockInput(): Promise<void> {
    await invoke("midi_clock_input_stop");
    midiConnected = false;
    deps.transportController.setExternalClockRunning(false);
    setInputActiveNotesStatus(formatMidiActiveNotes(tracker.clear()));
    setMidiStatus("midi input disconnected");
  }
  async function outShutdown(): Promise<void> {
    try {
      await invoke("midi_clock_output_shutdown");
    } catch {
      // ignore
    }
  }

  // --- Wire event listeners ---
  followEnabledInput.addEventListener("change", () => {
    deps.transportController.setFollowExternalClock(followEnabledInput.checked);
    setMidiStatus(`follow external clock: ${followEnabledInput.checked ? "on" : "off"}`);
  });
  inRefreshBtn.addEventListener("click", () => { void refreshInputPorts(); });
  inConnectBtn.addEventListener("click", () => {
    void connectClockInput().catch((e) => setMidiStatus(String(e)));
  });
  inDisconnectBtn.addEventListener("click", () => {
    void disconnectClockInput().catch((e) => setMidiStatus(String(e)));
  });
  inSysexEnabledInput.addEventListener("change", () => {
    if (midiConnected) {
      void connectClockInput().catch((e) => setMidiStatus(String(e)));
    } else {
      setMidiStatus(`midi input SysEx: ${inSysexEnabledInput.checked ? "enabled (on next connect)" : "disabled"}`);
    }
  });
  inPanicBtn.addEventListener("click", () => {
    setInputActiveNotesStatus(formatMidiActiveNotes(tracker.clear()));
    appendInputEventLine("input monitor cleared");
  });

  outEnabledInput.addEventListener("change", () => {
    midiOutEnabled = outEnabledInput.checked;
    if (midiOutEnabled) {
      setMidiOutStatus("midi clock out: enabled");
      void refreshOutputPorts();
    } else {
      void outStop();
      setMidiOutStatus("midi clock out: disabled");
    }
  });
  outRefreshBtn.addEventListener("click", () => { void refreshOutputPorts(); });
  outSelectBtn.addEventListener("click", () => {
    void selectOutputPortAndPersist().catch((e) => setMidiOutStatus(String(e)));
  });
  outSysexEnabledInput.addEventListener("change", () => {
    void setOutSysex(outSysexEnabledInput.checked, true).catch((e) => setMidiOutStatus(String(e)));
  });
  outStartBtn.addEventListener("click", () => {
    outEnabledInput.checked = true;
    midiOutEnabled = true;
    void outStartOrContinue().catch((e) => setMidiOutStatus(String(e)));
  });
  outContinueBtn.addEventListener("click", () => {
    outEnabledInput.checked = true;
    midiOutEnabled = true;
    midiOutEverStarted = true;
    void selectOutputPortAndPersist()
      .then(() => invoke("midi_clock_output_continue"))
      .then(() => {
        midiOutRunning = true;
        setMidiOutStatus("midi clock out: CONTINUE");
      })
      .catch((e) => setMidiOutStatus(String(e)));
  });
  outStopBtn.addEventListener("click", () => {
    void outStop().catch((e) => setMidiOutStatus(String(e)));
  });
  msgNoteOnBtn.addEventListener("click", () => {
    void sendNoteOnFromUi().catch((e) => setMidiOutStatus(String(e)));
  });
  msgNoteOffBtn.addEventListener("click", () => {
    void sendNoteOffFromUi().catch((e) => setMidiOutStatus(String(e)));
  });
  msgCcSendBtn.addEventListener("click", () => {
    void sendCcFromUi().catch((e) => setMidiOutStatus(String(e)));
  });
  msgAllNotesOffBtn.addEventListener("click", () => {
    void sendAllNotesOffFromUi().catch((e) => setMidiOutStatus(String(e)));
  });
  outRawSendBtn.addEventListener("click", () => {
    void sendRawFromUi().catch((e) => setMidiOutStatus(String(e)));
  });

  // --- MIDI clock event listeners (from Rust) ---
  if (deps.haveTauri()) {
    void listen("midi_clock_start", () => {
      deps.transportController.setExternalClockRunning(true);
      setMidiStatus("midi clock: START");
    });
    void listen("midi_clock_stop", () => {
      deps.transportController.setExternalClockRunning(false);
      setMidiStatus("midi clock: STOP");
    });
    void listen<{ bpm: number; raw_bpm: number; tempo_scale: number }>("midi_clock_bpm", (ev) => {
      deps.transportController.setExternalClockBpm(ev.payload.bpm);
      deps.onExternalBpmChange(ev.payload.bpm);
    });
    void listen<{ dt_sec: number }>("midi_clock_tick", (ev) => {
      deps.transportController.pushExternalClockDelta(ev.payload.dt_sec);
    });
    void listen<{ t_sec: number }>("midi_clock_seek", (ev) => {
      deps.transportController.seekFromExternalClock(ev.payload.t_sec);
      setMidiStatus(`midi clock: SEEK ${ev.payload.t_sec.toFixed(2)}s`);
    });
    void listen<MidiInputMessageEvent>("midi_input_message", (ev) => {
      if (ev.payload.message_type !== "clock") {
        const snapshot = tracker.apply(ev.payload);
        setInputActiveNotesStatus(formatMidiActiveNotes(snapshot));
        appendInputEventLine(formatMidiInputMessage(ev.payload));
      }
      window.dispatchEvent(new CustomEvent<MidiInputMessageEvent>("auralprimer:midi-input", {
        detail: ev.payload,
      }));
    });
  }

  // Initial active-notes status text.
  setInputActiveNotesStatus(formatMidiActiveNotes(tracker.snapshot()));

  function disableAll(): void {
    inPortSelect.disabled = true;
    inRefreshBtn.disabled = true;
    inConnectBtn.disabled = true;
    inDisconnectBtn.disabled = true;
    tempoScaleInput.disabled = true;
    inSysexEnabledInput.disabled = true;
    inPanicBtn.disabled = true;
    outEnabledInput.disabled = true;
    outPortSelect.disabled = true;
    outRefreshBtn.disabled = true;
    outSelectBtn.disabled = true;
    outStartBtn.disabled = true;
    outContinueBtn.disabled = true;
    outStopBtn.disabled = true;
    outSysexEnabledInput.disabled = true;
    msgChannelInput.disabled = true;
    msgNoteInput.disabled = true;
    msgVelocityInput.disabled = true;
    msgNoteOnBtn.disabled = true;
    msgNoteOffBtn.disabled = true;
    msgAllNotesOffBtn.disabled = true;
    msgCcInput.disabled = true;
    msgCcValueInput.disabled = true;
    msgCcSendBtn.disabled = true;
    outRawHexInput.disabled = true;
    outRawSendBtn.disabled = true;
  }

  return {
    refreshAll: async () => {
      await refreshInputPorts();
      await refreshOutputPorts();
    },
    disableAll,
    outSetBpmIfNeeded,
    outStartOrContinue,
    outStop,
    outShutdown,
    inputActiveNotes: () => tracker.snapshot(),
  };
}
