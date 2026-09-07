/**
 * Play-mode transport hotkeys.
 *
 * Space starts / pauses / resumes the song; Left / Right jog the playhead.
 * Both are play-route only and stay out of the way of typing.
 *
 * Two collisions this has to handle, both from native browser behaviour:
 *   - Space activates whichever button still has focus (Start, or the
 *     transport buttons in the rail), so handled presses must preventDefault
 *     or the song toggles twice.
 *   - While the pause menu is open its Resume button holds focus and already
 *     does the right thing with Space, so we stay out of its way entirely.
 *
 * Follows the `init(deps)` pattern established by scrollSpeedController.
 */

import type { TransportController } from "./transportController";

/** Plain arrow press. */
export const JOG_COARSE_SEC = 5;
/** Shift+arrow, matching the Shift = finer idiom the `[` / `]` keys use. */
export const JOG_FINE_SEC = 1;

export type TransportHotkeysDeps = {
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
  /** Current app route; hotkeys only act on "play". */
  getCurrentRoute: () => string;
  /** Pause menu open? If so we defer to it completely. */
  isPauseMenuVisible: () => boolean;
  /** True once a session is running (i.e. the visualizer exists). */
  isSessionRunning: () => boolean;
  /** True when Start is pressable — Space doubles as Start before a session. */
  canStartSession: () => boolean;
  startSession: () => void;
  /** Refresh the host's cached transport state after a change. */
  onTransportChanged?: () => void;
  /** Told about seeks so the MIDI clock output can follow. */
  onSeeked?: (tSec: number) => void;
};

export type TransportHotkeys = {
  /** Remove the window listener (tests + teardown). */
  dispose: () => void;
};

function targetIsTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

export function initTransportHotkeys(deps: TransportHotkeysDeps): TransportHotkeys {
  const transportController = (): TransportController => deps.getTransportController();

  function jogBy(deltaSec: number): void {
    const st = transportController().getState();
    const next = Math.max(0, st.t + deltaSec);
    transportController().seek(next);
    deps.onTransportChanged?.();
    deps.onSeeked?.(transportController().getState().t);
  }

  function togglePlayback(): void {
    // Nothing running yet: Space is the same as pressing Start.
    if (!deps.isSessionRunning()) {
      if (deps.canStartSession()) deps.startSession();
      return;
    }
    if (transportController().getState().isPlaying) transportController().pause();
    else void transportController().play();
    deps.onTransportChanged?.();
  }

  function onKeyDown(ev: KeyboardEvent): void {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (targetIsTyping(ev.target)) return;
    if (deps.getCurrentRoute() !== "play") return;
    if (deps.isPauseMenuVisible()) return;

    if (ev.key === " " || ev.code === "Space") {
      if (ev.repeat) return;
      ev.preventDefault();
      togglePlayback();
      return;
    }

    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      // Auto-repeat is wanted here — holding an arrow scrubs.
      ev.preventDefault();
      const step = ev.shiftKey ? JOG_FINE_SEC : JOG_COARSE_SEC;
      jogBy(ev.key === "ArrowLeft" ? -step : step);
    }
  }

  window.addEventListener("keydown", onKeyDown);
  return {
    dispose: () => window.removeEventListener("keydown", onKeyDown),
  };
}
