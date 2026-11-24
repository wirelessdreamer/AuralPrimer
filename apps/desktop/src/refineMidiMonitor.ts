import { invoke } from "@tauri-apps/api/core";
import { MidiInputStateTracker, type MidiActiveNote, type MidiInputMessageEvent } from "@auralprimer/midi-input";
import type { RefineAuditionHandle } from "./refineAudition";

type Port = { id: number; name: string; stable_id?: string };
type SavedInput = { port: Port | null; tempo_scale: number; allow_sysex: boolean };

/** One subscription to Studio's existing native MIDI bus, shared by analysis,
 * the game preview and the live synth. Playing never mutates the song chart. */
export function initRefineMidiMonitor(options: {
  root: HTMLElement;
  audition: RefineAuditionHandle;
  onNotes: (notes: MidiActiveNote[]) => void;
}): { reset: () => void; dispose: () => void } {
  const { root, audition, onNotes } = options;
  const sound = root.querySelector<HTMLInputElement>("#refineMidiSound")!;
  const volume = root.querySelector<HTMLInputElement>("#refineMidiGain")!;
  const reconnect = root.querySelector<HTMLButtonElement>("#refineMidiConnect")!;
  const panic = root.querySelector<HTMLButtonElement>("#refineMidiPanic")!;
  const status = root.querySelector<HTMLElement>("#refineMidiStatus")!;
  const readout = root.querySelector<HTMLElement>("#refineMidiNotes")!;
  const tracker = new MidiInputStateTracker();
  const active = (): boolean => root.isConnected && !!root.closest(".isActive");
  let enabled = false;
  let actionGeneration = 0;
  let inputName = "keyboard";

  function publish(): void {
    const notes = tracker.snapshot().activeNotes.filter((note) => note.channel !== 9);
    onNotes(notes);
    audition.setLiveNotes(enabled && active() && document.hasFocus() ? notes : []);
    readout.textContent = notes.length ? notes.map((note) => `${note.noteName}${note.heldBySustain ? " ♫" : ""}`).join(" · ") : "Play your keyboard";
  }

  function reset(): void { tracker.clear(); publish(); }
  function showConnected(connected: boolean): void {
    status.textContent = connected ? `MIDI · ${inputName}` : "MIDI disconnected";
    status.dataset.connected = String(connected);
  }

  async function refresh(ensureConnected: boolean): Promise<void> {
    const [saved, connected] = await Promise.all([
      invoke<SavedInput>("midi_clock_input_get_saved_settings"),
      invoke<boolean>("midi_clock_input_is_connected"),
    ]);
    inputName = saved.port?.name || "keyboard";
    if (connected || !ensureConnected) { showConnected(connected); return; }
    const ports = await invoke<Port[]>("list_midi_input_ports");
    const port = ports.find((p) => saved.port?.stable_id && p.stable_id === saved.port.stable_id)
      ?? ports.find((p) => p.name === saved.port?.name);
    if (!port) throw new Error("Choose your keyboard in Configure → MIDI, then reconnect here.");
    // Reuse the saved shared port and options without replacing the selection
    // or opening a second connection inside Studio.
    await invoke("midi_clock_input_start", { portId: port.id, tempoScale: saved.tempo_scale || 1, allowSysex: saved.allow_sysex });
    inputName = port.name;
    showConnected(true);
    window.dispatchEvent(new CustomEvent("auralprimer:midi-connection", { detail: { connected: true, name: inputName } }));
  }

  sound.addEventListener("change", () => {
    const generation = ++actionGeneration;
    enabled = false;
    publish();
    if (!sound.checked) return;
    // Resume during the pointer/key gesture, before awaiting native device I/O.
    void Promise.all([audition.resume(), refresh(true)]).then(() => {
      if (generation !== actionGeneration || !sound.checked) return;
      enabled = true;
      publish();
    }).catch((error) => {
      if (generation !== actionGeneration) return;
      sound.checked = false;
      status.textContent = String(error);
    });
  });
  volume.addEventListener("input", () => audition.setLiveGain(Number(volume.value) / 100));
  audition.setLiveGain(Number(volume.value) / 100);
  reconnect.addEventListener("click", () => {
    reset();
    void refresh(true).catch((error) => { status.textContent = String(error); });
  });
  panic.addEventListener("click", reset);
  const onInput = (event: Event): void => {
    if (!active()) return;
    const message = (event as CustomEvent<MidiInputMessageEvent>).detail;
    if (!message || !["note_on", "note_off", "control_change"].includes(message.message_type)) return;
    tracker.apply(message);
    publish();
  };
  const onConnection = (event: Event): void => {
    const detail = (event as CustomEvent<{ connected: boolean; name?: string }>).detail;
    if (!detail) return;
    if (detail.name) inputName = detail.name;
    showConnected(detail.connected);
    if (!detail.connected) reset();
  };
  window.addEventListener("auralprimer:midi-input", onInput);
  window.addEventListener("auralprimer:midi-reset", reset);
  window.addEventListener("auralprimer:midi-connection", onConnection);
  window.addEventListener("blur", reset);
  // Switching Studio routes may not blur the webview. Clear held voices as
  // soon as Cleanup is hidden, including while transport is already paused.
  const route = root.closest(".route") ?? root.parentElement!;
  const observer = new MutationObserver(() => { if (!active()) reset(); });
  observer.observe(route, { attributes: true, attributeFilter: ["class"] });
  void refresh(false).catch(() => { status.textContent = "MIDI · Configure → MIDI"; });
  return { reset, dispose: () => {
    actionGeneration++;
    enabled = false;
    reset();
    observer.disconnect();
    window.removeEventListener("auralprimer:midi-input", onInput);
    window.removeEventListener("auralprimer:midi-reset", reset);
    window.removeEventListener("auralprimer:midi-connection", onConnection);
    window.removeEventListener("blur", reset);
  } };
}
