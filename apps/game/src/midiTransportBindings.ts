/**
 * MIDI transport bindings — what message drives which transport action.
 *
 * Controllers disagree wildly about how transport buttons speak: some send
 * Control Change, some send notes, and the numbers vary by vendor. Rather than
 * hard-coding a guess, the bindings are learnable — Configure -> MIDI has a
 * Learn button per action that captures whatever the device actually sends.
 *
 * The CC 31-35 defaults below are only a starting point; anything learned
 * replaces them and persists.
 */

import type { MidiInputMessageEvent } from "./midiInput";

export type TransportAction =
  | "restart"
  | "rewind"
  | "fastForward"
  | "stop"
  | "play"
  | "waitMode";

export type TransportActionInfo = {
  id: TransportAction;
  label: string;
  hint: string;
};

/** Display order in the Configure panel. */
export const TRANSPORT_ACTIONS: TransportActionInfo[] = [
  { id: "restart", label: "Start song over", hint: "Back to the beginning, playing" },
  { id: "rewind", label: "Rewind", hint: "Hold to jog backward" },
  { id: "fastForward", label: "Fast forward", hint: "Hold to jog forward" },
  { id: "stop", label: "Stop", hint: "Halt and return to the beginning" },
  { id: "play", label: "Play / pause", hint: "Toggles — pauses if already playing" },
  { id: "waitMode", label: "Wait mode on/off", hint: "Toggles advance-on-note-play" },
];

/**
 * A learned button. `channel` null means "any channel" — used for the
 * defaults, since we cannot know which channel a device will use.
 */
export type MidiBinding = {
  kind: "cc" | "note";
  number: number;
  channel: number | null;
};

export type TransportBindings = Record<TransportAction, MidiBinding | null>;

/** Value at or above this is a button press, below it a release. */
export const CC_PRESS_THRESHOLD = 64;

export const STORAGE_KEY = "auralprimer.midiTransportBindings";

export function defaultBindings(): TransportBindings {
  return {
    restart: { kind: "cc", number: 31, channel: null },
    rewind: { kind: "cc", number: 32, channel: null },
    fastForward: { kind: "cc", number: 33, channel: null },
    stop: { kind: "cc", number: 34, channel: null },
    play: { kind: "cc", number: 35, channel: null },
    waitMode: null,
  };
}

export type BindingEdge = "press" | "release";

function isDataByte(n: unknown): n is number {
  return typeof n === "number" && Number.isInteger(n) && n >= 0 && n <= 127;
}

/**
 * Does this message drive this binding, and is it a press or a release?
 *
 * Returns null when the message is unrelated. Note-off (and the note-on with
 * velocity 0 that many keyboards send instead) both read as a release, so
 * hold-to-jog works on note bindings as well as CC ones.
 */
export function matchBinding(
  binding: MidiBinding | null,
  msg: MidiInputMessageEvent,
): BindingEdge | null {
  if (!binding) return null;
  if (!isDataByte(msg.data1) || msg.data1 !== binding.number) return null;
  if (binding.channel !== null && msg.channel !== binding.channel) return null;

  if (binding.kind === "cc") {
    if (msg.message_type !== "control_change") return null;
    if (!isDataByte(msg.data2)) return null;
    return msg.data2 >= CC_PRESS_THRESHOLD ? "press" : "release";
  }

  if (msg.message_type === "note_on") {
    if (!isDataByte(msg.data2)) return null;
    return msg.data2 > 0 ? "press" : "release";
  }
  if (msg.message_type === "note_off") return "release";
  return null;
}

/**
 * Turn an incoming message into a binding, for Learn.
 *
 * Only presses are captured — a release would otherwise immediately overwrite
 * the binding the press just set. The channel is pinned to whatever the device
 * used, which keeps a note binding from being triggered by the same pitch
 * played on the keyboard's own channel.
 */
export function bindingFromMessage(msg: MidiInputMessageEvent): MidiBinding | null {
  if (!isDataByte(msg.data1)) return null;
  const channel = typeof msg.channel === "number" ? msg.channel : null;

  if (msg.message_type === "control_change") {
    if (!isDataByte(msg.data2) || msg.data2 < CC_PRESS_THRESHOLD) return null;
    return { kind: "cc", number: msg.data1, channel };
  }
  if (msg.message_type === "note_on") {
    if (!isDataByte(msg.data2) || msg.data2 === 0) return null;
    return { kind: "note", number: msg.data1, channel };
  }
  return null;
}

/**
 * Would these two bindings both fire on the same physical button?
 *
 * Channels overlap when either side is "any" (null), so a freshly learned
 * binding pinned to channel 3 still displaces an any-channel default on the
 * same number — otherwise one button would drive two transport actions.
 */
export function bindingsConflict(a: MidiBinding | null, b: MidiBinding | null): boolean {
  if (!a || !b) return false;
  if (a.kind !== b.kind || a.number !== b.number) return false;
  return a.channel === null || b.channel === null || a.channel === b.channel;
}

export function describeBinding(binding: MidiBinding | null): string {
  if (!binding) return "unassigned";
  const label = binding.kind === "cc" ? `CC ${binding.number}` : `Note ${binding.number}`;
  return binding.channel === null ? `${label} (any ch)` : `${label} (ch ${binding.channel + 1})`;
}

/** Human-readable summary of an arbitrary message, for the learn monitor. */
export function describeMessage(msg: MidiInputMessageEvent): string {
  const ch = typeof msg.channel === "number" ? ` ch${msg.channel + 1}` : "";
  const d1 = isDataByte(msg.data1) ? msg.data1 : "-";
  const d2 = isDataByte(msg.data2) ? msg.data2 : "-";
  switch (msg.message_type) {
    case "control_change":
      return `CC ${d1} = ${d2}${ch}`;
    case "note_on":
      return `Note ${d1} on, vel ${d2}${ch}`;
    case "note_off":
      return `Note ${d1} off${ch}`;
    default:
      return `${msg.message_type}${ch}`;
  }
}

function isValidBinding(v: unknown): v is MidiBinding {
  if (!v || typeof v !== "object") return false;
  const b = v as Record<string, unknown>;
  if (b.kind !== "cc" && b.kind !== "note") return false;
  if (!isDataByte(b.number)) return false;
  if (b.channel !== null && !(typeof b.channel === "number" && b.channel >= 0 && b.channel <= 15)) {
    return false;
  }
  return true;
}

/** Parse persisted JSON, falling back per-action so one bad entry isn't fatal. */
export function parseBindings(raw: string | null): TransportBindings {
  const out = defaultBindings();
  if (!raw) return out;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return out;
  }
  if (!parsed || typeof parsed !== "object") return out;
  const rec = parsed as Record<string, unknown>;
  for (const { id } of TRANSPORT_ACTIONS) {
    if (!(id in rec)) continue;
    const v = rec[id];
    // An explicit null is a deliberate "unassigned", not a parse failure.
    if (v === null) out[id] = null;
    else if (isValidBinding(v)) out[id] = v;
  }
  return out;
}

/** localStorage read — the fallback when the native settings file is absent. */
export function loadBindingsLocal(): TransportBindings {
  try {
    return parseBindings(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return defaultBindings();
  }
}

/** localStorage write — mirrors the durable copy, and is all browser dev gets. */
export function saveBindingsLocal(bindings: TransportBindings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(bindings));
  } catch {
    // Best-effort — session-only persistence is acceptable.
  }
}

// Loaded lazily so this module stays importable (and testable) outside Tauri.
async function invokeTauri<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

/**
 * Read the durable bindings.
 *
 * Bindings live in the app's settings.json, NOT webview localStorage: the
 * portable packer deletes the webview data directory on every repack, so
 * anything stored there is silently lost between builds — which is exactly how
 * learned bindings kept disappearing. localStorage remains the fallback for
 * browser dev, and is migrated forward the first time a native read comes back
 * empty.
 */
export async function loadBindings(): Promise<TransportBindings> {
  try {
    const raw = await invokeTauri<string | null>("midi_transport_bindings_get");
    if (raw) return parseBindings(raw);
    // Nothing durable yet — adopt whatever the old localStorage copy holds and
    // write it through, so a user who already learned bindings keeps them.
    const local = loadBindingsLocal();
    await saveBindings(local);
    return local;
  } catch {
    return loadBindingsLocal();
  }
}

/** Persist bindings durably, mirroring to localStorage for browser dev. */
export async function saveBindings(bindings: TransportBindings): Promise<void> {
  saveBindingsLocal(bindings);
  try {
    await invokeTauri("midi_transport_bindings_set", {
      value: JSON.stringify(bindings),
    });
  } catch {
    // Not running under Tauri — the localStorage mirror above is all we get.
  }
}
