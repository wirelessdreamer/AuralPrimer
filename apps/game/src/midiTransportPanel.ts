/**
 * Configure -> MIDI -> Transport control panel.
 *
 * One row per transport action with a Learn button: click Learn, press the
 * button on the controller, and whatever it sends becomes the binding. This
 * exists because guessing is unreliable — controllers send CC or notes, on
 * numbers that vary by vendor — and because a wrong guess is indistinguishable
 * from a dead connection without a monitor.
 *
 * The always-on "last message" line is the other half of that: it shows the
 * raw traffic whether or not anything is bound, so a silent device can be told
 * apart from a mis-bound one.
 *
 * Follows the `init(deps)` pattern established by scrollSpeedController.
 */

import type { MidiInputMessageEvent } from "./midiInput";
import {
  TRANSPORT_ACTIONS,
  bindingFromMessage,
  bindingsConflict,
  matchBinding,
  describeBinding,
  describeMessage,
  type TransportAction,
  type TransportBindings,
} from "./midiTransportBindings";

export type MidiTransportPanelDeps = {
  getBindings: () => TransportBindings;
  /** Persist + publish the new binding set. */
  setBindings: (bindings: TransportBindings) => void;
};

export type MidiTransportPanelHandle = {
  /** True while waiting to capture a button — transport action is suppressed. */
  isLearning: () => boolean;
  /** Re-read the bindings and redraw; used after the async durable load. */
  refresh: () => void;
  dispose: () => void;
};

const ROWS_ID = "midiTransportRows";
const STATUS_ID = "midiTransportStatus";

export function initMidiTransportPanel(
  deps: MidiTransportPanelDeps,
): MidiTransportPanelHandle {
  const rowsEl = document.getElementById(ROWS_ID);
  const statusEl = document.getElementById(STATUS_ID);
  if (!rowsEl || !statusEl) {
    throw new Error("midiTransportPanel: required DOM (#midiTransportRows/#midiTransportStatus) missing");
  }

  let learning: TransportAction | null = null;
  /**
   * Rolling log of recent input, each line annotated with which action it
   * matched and whether it read as a press or a release.
   *
   * One "last message" line was not enough to debug a button: whether a
   * controller sends a distinct release (value 0) or repeats its press value on
   * both edges decides whether momentary hold-to-jog can work at all, and that
   * is only visible as a sequence.
   */
  let recentLines: string[] = [];
  const RECENT_LIMIT = 10;

  const valueEls = new Map<TransportAction, HTMLElement>();
  const learnBtns = new Map<TransportAction, HTMLButtonElement>();

  function renderStatus(): void {
    const parts: string[] = [];
    if (learning) {
      const info = TRANSPORT_ACTIONS.find((a) => a.id === learning);
      parts.push(`Listening — press the ${info?.label ?? learning} button on your controller...`);
    }
    if (!recentLines.length) parts.push("(no MIDI received yet - connect an input port above)");
    else parts.push(...recentLines);
    statusEl!.textContent = parts.join("\n");
  }

  /** Describe a message and how the current bindings interpret it. */
  function annotate(msg: MidiInputMessageEvent): string {
    const bindings = deps.getBindings();
    for (const { id, label } of TRANSPORT_ACTIONS) {
      const edge = matchBinding(bindings[id], msg);
      if (edge) return `${describeMessage(msg)}  ->  ${label}: ${edge.toUpperCase()}`;
    }
    return `${describeMessage(msg)}  ->  (unbound)`;
  }

  function renderBindings(): void {
    const bindings = deps.getBindings();
    for (const { id } of TRANSPORT_ACTIONS) {
      const el = valueEls.get(id);
      if (el) {
        el.textContent = describeBinding(bindings[id]);
        el.dataset.unassigned = bindings[id] ? "false" : "true";
      }
      const btn = learnBtns.get(id);
      if (btn) btn.textContent = learning === id ? "Cancel" : "Learn";
    }
    renderStatus();
  }

  function setLearning(action: TransportAction | null): void {
    learning = action;
    renderBindings();
  }

  function assign(action: TransportAction, binding: TransportBindings[TransportAction]): void {
    // Clear the same binding off any other action, so one button can't drive
    // two transport commands at once.
    const next: TransportBindings = { ...deps.getBindings() };
    if (binding) {
      for (const { id } of TRANSPORT_ACTIONS) {
        if (id !== action && bindingsConflict(next[id], binding)) next[id] = null;
      }
    }
    next[action] = binding;
    deps.setBindings(next);
    renderBindings();
  }

  for (const info of TRANSPORT_ACTIONS) {
    const row = document.createElement("div");
    row.className = "row midiTransportRow";

    const label = document.createElement("label");
    label.className = "meta midiTransportLabel";
    label.textContent = info.label;

    const value = document.createElement("span");
    value.className = "midiTransportValue";
    valueEls.set(info.id, value);

    const learnBtn = document.createElement("button");
    learnBtn.type = "button";
    learnBtn.textContent = "Learn";
    learnBtn.addEventListener("click", () => {
      setLearning(learning === info.id ? null : info.id);
    });
    learnBtns.set(info.id, learnBtn);

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.textContent = "Clear";
    clearBtn.addEventListener("click", () => {
      if (learning === info.id) learning = null;
      assign(info.id, null);
    });

    const hint = document.createElement("span");
    hint.className = "meta midiTransportHint";
    hint.textContent = info.hint;

    row.append(label, value, learnBtn, clearBtn, hint);
    rowsEl.appendChild(row);
  }

  function onMidiInput(evt: Event): void {
    const msg = (evt as CustomEvent<MidiInputMessageEvent>).detail;
    if (!msg || msg.message_type === "clock") return;

    recentLines.push(annotate(msg));
    if (recentLines.length > RECENT_LIMIT) recentLines = recentLines.slice(-RECENT_LIMIT);

    if (learning) {
      const binding = bindingFromMessage(msg);
      // Only a press captures; a release would overwrite what the press set.
      if (binding) {
        const action = learning;
        learning = null;
        assign(action, binding);
        return;
      }
    }
    renderStatus();
  }

  function onKeyDown(ev: KeyboardEvent): void {
    if (ev.key === "Escape" && learning) setLearning(null);
  }

  window.addEventListener("auralprimer:midi-input", onMidiInput);
  window.addEventListener("keydown", onKeyDown);
  renderBindings();

  return {
    isLearning: () => learning !== null,
    refresh: renderBindings,
    dispose: () => {
      window.removeEventListener("auralprimer:midi-input", onMidiInput);
      window.removeEventListener("keydown", onKeyDown);
    },
  };
}
