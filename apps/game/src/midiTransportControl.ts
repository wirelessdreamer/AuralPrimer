/**
 * MIDI transport control.
 *
 * Drives playback from a control surface's transport buttons, so the song can
 * be run from the same hardware it is played on. Which message maps to which
 * action is learnable — see midiTransportBindings.ts — because controllers
 * disagree about whether transport buttons send CC or notes, and about the
 * numbers they use.
 *
 * Play toggles: pressing it while playing pauses. Stop and start-over both
 * land at zero and differ in whether playback continues, which is what makes
 * them worth separate buttons.
 *
 * Rewind / fast forward adapt to what the controller can express. A device
 * that sends a release (value 0, or note-off) gets true momentary hold: jog
 * while down, stop on release. A device that sends a single message per press
 * and nothing on release physically cannot signal "still held", so for those a
 * second press of the same button stops the jog. Without that, the jog started
 * and never stopped -- a runaway fast-forward with no way out.
 *
 * Pressing the opposite jog button switches direction rather than stopping, and
 * play / stop / start-over all cancel a jog in flight.
 *
 * The remaining buttons fire on press only, so a controller that does send a 0
 * on release cannot double-trigger.
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
  /**
   * The transport in use right now, resolved per call.
   *
   * Not the controller itself: when native audio stalls the host disposes the
   * controller and builds a new one around the HTML timebase, and anything
   * holding the old reference then drives a dead object in silence. That is
   * what took the transport keys and the MIDI transport buttons out together
   * -- they kept working exactly until the first fallback.
   */
  getTransportController: () => TransportController;
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
  /** Flip wait mode (advance-on-note-play) on or off. */
  toggleWaitMode?: () => void;
};

export type MidiTransportControl = {
  /** Remove the listener and cancel any in-flight jog. */
  dispose: () => void;
};

export function initMidiTransportControl(deps: MidiTransportDeps): MidiTransportControl {
  const transportController = (): TransportController => deps.getTransportController();

  let jogTimer: number | null = null;
  let jogTick = 0;
  let jogDir = 0;
  // Where the jog is steering to. Deliberately NOT re-read from the transport
  // each tick: during playback `state.t` comes from the audio clock, which both
  // lags an in-flight seek and keeps advancing on its own. Re-reading it made
  // the jog fight playback -- a rewind at the slow end of the ramp (0.25 s per
  // 100 ms tick) barely out-ran the 0.1 s of playback happening in the same
  // window, so it crawled, and above 1x playback rate it moved the wrong way.
  let jogTargetSec = 0;

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
    deps.onSeeked?.(transportController().getState().t);
  }

  function jogOnce(): void {
    jogTargetSec = Math.max(0, jogTargetSec + jogStepSecForTick(jogTick) * jogDir);
    transportController().seek(jogTargetSec);
    jogTick += 1;
    afterTransportChange();
  }

  /**
   * Handle a press of a jog button.
   *
   * Same button while already jogging = stop, which is the only stop signal a
   * controller that sends no release can give. Opposite button = switch
   * direction, so a two-button transport strip still behaves like one.
   */
  function onJogPress(direction: 1 | -1): void {
    if (jogTimer !== null && jogDir === direction) {
      cancelJog();
      return;
    }
    beginJog(direction);
  }

  function beginJog(direction: 1 | -1): void {
    if (!deps.isSessionRunning()) return;
    cancelJog();
    jogDir = direction;
    // Anchor to wherever playback actually is, once, at the moment of press.
    jogTargetSec = Math.max(0, transportController().getState().t);
    jogOnce(); // respond on the press itself, not a tick later
    jogTimer = window.setInterval(jogOnce, JOG_TICK_MS);
  }

  function playPause(): void {
    cancelJog();
    if (!deps.isSessionRunning()) {
      if (deps.canStartSession()) deps.startSession();
      return;
    }
    // Toggle, matching Space: a transport button you have to pair with Stop to
    // re-use is worse than one that just does the obvious thing.
    if (transportController().getState().isPlaying) transportController().pause();
    else void transportController().play();
    deps.onTransportChanged?.();
  }

  function restart(): void {
    cancelJog();
    if (!deps.isSessionRunning()) {
      if (deps.canStartSession()) deps.startSession();
      return;
    }
    transportController().seek(0);
    void transportController().play();
    afterTransportChange();
  }

  function stop(): void {
    cancelJog();
    transportController().stop();
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
        onJogPress(-1);
        break;
      case "fastForward":
        onJogPress(1);
        break;
      case "stop":
        stop();
        break;
      case "play":
        playPause();
        break;
      case "waitMode":
        deps.toggleWaitMode?.();
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
