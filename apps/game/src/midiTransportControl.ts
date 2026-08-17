/**
 * MIDI transport control.
 *
 * Drives playback from a control surface's transport buttons, so the song can
 * be run from the same hardware it is played on. Which message maps to which
 * action is learnable — see midiTransportBindings.ts — because controllers
 * disagree about whether transport buttons send CC or notes, and about the
 * numbers they use.
 *
 * Stop and start-over both land at zero; they differ in whether playback
 * continues, which is what makes them worth separate buttons.
 *
 * Rewind / fast forward act on both edges so they can be held. The rest are
 * momentary and fire on press only, so a controller that sends a 0 on release
 * cannot double-trigger.
 *
 * Jogging accelerates the longer a button is held, like a DAW shuttle: short
 * taps nudge, long holds cross the song.
 *
 * Input arrives as the `auralprimer:midi-input` DOM event that midiPanel
 * re-dispatches from the Rust `midi_input_message` stream.
 */

import type { MidiInputMessageEvent } from "./midiInput";
import type { TransportController } from "./transportController";
import {
  TRANSPORT_ACTIONS,
  matchBinding,
  type TransportAction,
  type TransportBindings,
} from "./midiTransportBindings";

/** How often a held jog button repeats. */
export const JOG_TICK_MS = 100;
/** Seconds moved by the first tick of a hold. */
export const JOG_STEP_START_SEC = 0.25;
/** Ceiling on the per-tick step, so a long hold stays controllable. */
export const JOG_STEP_MAX_SEC = 4;
/** Growth per tick — reaches the ceiling after roughly 2.5 s of holding. */
export const JOG_ACCEL_PER_TICK = 1.12;

/** Seconds to move on the Nth tick of a held jog (0-based). */
export function jogStepSecForTick(tickIndex: number): number {
  const step = JOG_STEP_START_SEC * JOG_ACCEL_PER_TICK ** Math.max(0, tickIndex);
  return Math.min(JOG_STEP_MAX_SEC, step);
}

export type MidiTransportDeps = {
  transportController: TransportController;
  /** Current bindings; read per message so Learn takes effect immediately. */
  getBindings: () => TransportBindings;
  /** Current app route; transport buttons only act on "play". */
  getCurrentRoute: () => string;
  /** Pause menu open? If so we stay out of its way, as the hotkeys do. */
  isPauseMenuVisible: () => boolean;
  /** True once a session is running (i.e. the visualizer exists). */
  isSessionRunning: () => boolean;
  /** True when Start is pressable — Play doubles as Start before a session. */
  canStartSession: () => boolean;
  startSession: () => void;
  /**
   * Suppresses all transport action — used while the Configure panel is
   * capturing a button, so learning a binding doesn't also fire it.
   */
  isSuppressed?: () => boolean;
  /** Refresh the host's cached transport state after a change. */
  onTransportChanged?: () => void;
  /** Told about seeks so the MIDI clock output can follow. */
  onSeeked?: (tSec: number) => void;
};

export type MidiTransportControl = {
  /** Remove the listener and cancel any in-flight jog. */
  dispose: () => void;
};

export function initMidiTransportControl(deps: MidiTransportDeps): MidiTransportControl {
  const { transportController } = deps;

  let jogTimer: number | null = null;
  let jogTick = 0;
  let jogDir = 0;

  function cancelJog(): void {
    if (jogTimer !== null) {
      window.clearInterval(jogTimer);
      jogTimer = null;
    }
    jogTick = 0;
    jogDir = 0;
  }

  function afterTransportChange(): void {
    deps.onTransportChanged?.();
    deps.onSeeked?.(transportController.getState().t);
  }

  function jogOnce(): void {
    const st = transportController.getState();
    const next = Math.max(0, st.t + jogStepSecForTick(jogTick) * jogDir);
    transportController.seek(next);
    jogTick += 1;
    afterTransportChange();
  }

  function beginJog(direction: 1 | -1): void {
    if (!deps.isSessionRunning()) return;
    cancelJog();
    jogDir = direction;
    jogOnce(); // respond on the press itself, not a tick later
    jogTimer = window.setInterval(jogOnce, JOG_TICK_MS);
  }

  function play(): void {
    if (!deps.isSessionRunning()) {
      if (deps.canStartSession()) deps.startSession();
      return;
    }
    void transportController.play();
    deps.onTransportChanged?.();
  }

  function restart(): void {
    if (!deps.isSessionRunning()) {
      if (deps.canStartSession()) deps.startSession();
      return;
    }
    transportController.seek(0);
    void transportController.play();
    afterTransportChange();
  }

  function stop(): void {
    cancelJog();
    transportController.stop();
    afterTransportChange();
  }

  function isActive(): boolean {
    if (deps.isSuppressed?.()) return false;
    return deps.getCurrentRoute() === "play" && !deps.isPauseMenuVisible();
  }

  function onMidiInput(evt: Event): void {
    const msg = (evt as CustomEvent<MidiInputMessageEvent>).detail;
    if (!msg) return;

    const bindings = deps.getBindings();
    let hit: { action: TransportAction; edge: "press" | "release" } | null = null;
    for (const { id } of TRANSPORT_ACTIONS) {
      const edge = matchBinding(bindings[id], msg);
      if (edge) {
        hit = { action: id, edge };
        break;
      }
    }
    if (!hit) return;

    const isJog = hit.action === "rewind" || hit.action === "fastForward";

    // Honour a release unconditionally. If the gate closed while a jog button
    // was held, the release still has to land or the jog would run forever.
    if (isJog && hit.edge === "release") {
      cancelJog();
      return;
    }
    if (!isActive()) return;
    if (hit.edge !== "press") return;

    switch (hit.action) {
      case "restart":
        restart();
        break;
      case "rewind":
        beginJog(-1);
        break;
      case "fastForward":
        beginJog(1);
        break;
      case "stop":
        stop();
        break;
      case "play":
        play();
        break;
    }
  }

  window.addEventListener("auralprimer:midi-input", onMidiInput);
  return {
    dispose: () => {
      cancelJog();
      window.removeEventListener("auralprimer:midi-input", onMidiInput);
    },
  };
}
