// @vitest-environment jsdom
/**
 * Execution tests for MIDI transport control (CC 31-35).
 *
 * The interesting behaviour is the hold-to-jog edge handling: presses and
 * releases arrive as separate CC messages, acceleration is time-based, and a
 * release has to land even if the route gate closed mid-hold — otherwise a
 * jog would run forever.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  initMidiTransportControl,
  jogStepSecForTick,
  CC_PRESS_THRESHOLD,
  JOG_TICK_MS,
  JOG_STEP_START_SEC,
  JOG_STEP_MAX_SEC,
  MIDI_TRANSPORT_CC,
  type MidiTransportControl,
  type MidiTransportDeps,
} from "../src/midiTransportControl";

let handle: MidiTransportControl | null = null;

function setup(overrides: Partial<MidiTransportDeps> = {}, state = { t: 30, isPlaying: false }) {
  const tc = {
    getState: vi.fn(() => state),
    seek: vi.fn((t: number) => {
      state.t = t;
    }),
    play: vi.fn(async () => {}),
    pause: vi.fn(),
    stop: vi.fn(() => {
      state.t = 0;
      state.isPlaying = false;
    }),
  };
  const startSession = vi.fn();
  const onSeeked = vi.fn();
  const deps = {
    transportController: tc as unknown as MidiTransportDeps["transportController"],
    getCurrentRoute: () => "play",
    isPauseMenuVisible: () => false,
    isSessionRunning: () => true,
    canStartSession: () => true,
    startSession,
    onSeeked,
    ...overrides,
  } as MidiTransportDeps;
  handle = initMidiTransportControl(deps);
  return { tc, startSession, onSeeked, state };
}

/** Dispatch a control-change exactly as midiPanel re-broadcasts it. */
function cc(number: number, value: number): void {
  window.dispatchEvent(
    new CustomEvent("auralprimer:midi-input", {
      detail: {
        timestamp_us: 0,
        message_type: "control_change",
        status: 0xb0,
        channel: 0,
        data1: number,
        data2: value,
        bytes: [0xb0, number, value],
      },
    }),
  );
}

const press = (n: number) => cc(n, 127);
const release = (n: number) => cc(n, 0);

describe("midiTransportControl", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    handle?.dispose();
    handle = null;
    vi.useRealTimers();
  });

  describe("jog step curve", () => {
    it("starts at the configured step and accelerates", () => {
      expect(jogStepSecForTick(0)).toBeCloseTo(JOG_STEP_START_SEC, 6);
      expect(jogStepSecForTick(5)).toBeGreaterThan(jogStepSecForTick(0));
      expect(jogStepSecForTick(20)).toBeGreaterThan(jogStepSecForTick(5));
    });

    it("caps so a long hold stays controllable", () => {
      expect(jogStepSecForTick(1000)).toBe(JOG_STEP_MAX_SEC);
    });

    it("treats negative ticks as the first tick", () => {
      expect(jogStepSecForTick(-5)).toBeCloseTo(JOG_STEP_START_SEC, 6);
    });
  });

  describe("momentary buttons", () => {
    it("play starts playback", () => {
      const h = setup();
      press(MIDI_TRANSPORT_CC.play);
      expect(h.tc.play).toHaveBeenCalledTimes(1);
    });

    it("stop halts and returns to zero", () => {
      const h = setup({}, { t: 42, isPlaying: true });
      press(MIDI_TRANSPORT_CC.stop);
      expect(h.tc.stop).toHaveBeenCalledTimes(1);
    });

    it("start-over returns to zero and plays", () => {
      const h = setup({}, { t: 42, isPlaying: true });
      press(MIDI_TRANSPORT_CC.restart);
      expect(h.tc.seek).toHaveBeenCalledWith(0);
      expect(h.tc.play).toHaveBeenCalledTimes(1);
    });

    it("fires on press only, so a release can't double-trigger", () => {
      const h = setup();
      press(MIDI_TRANSPORT_CC.play);
      release(MIDI_TRANSPORT_CC.play);
      expect(h.tc.play).toHaveBeenCalledTimes(1);
    });

    it("treats the threshold value as a press", () => {
      const h = setup();
      cc(MIDI_TRANSPORT_CC.play, CC_PRESS_THRESHOLD);
      expect(h.tc.play).toHaveBeenCalledTimes(1);
    });

    it("treats just below the threshold as a release", () => {
      const h = setup();
      cc(MIDI_TRANSPORT_CC.play, CC_PRESS_THRESHOLD - 1);
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("play starts the session when none is running", () => {
      const h = setup({ isSessionRunning: () => false });
      press(MIDI_TRANSPORT_CC.play);
      expect(h.startSession).toHaveBeenCalledTimes(1);
      expect(h.tc.play).not.toHaveBeenCalled();
    });
  });

  describe("hold to jog", () => {
    it("moves immediately on press rather than waiting for a tick", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      expect(h.tc.seek).toHaveBeenCalledTimes(1);
      expect(h.tc.seek).toHaveBeenCalledWith(30 + JOG_STEP_START_SEC);
    });

    it("keeps moving while held and stops on release", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      vi.advanceTimersByTime(JOG_TICK_MS * 4);
      const whileHeld = h.tc.seek.mock.calls.length;
      expect(whileHeld).toBe(5); // press + 4 ticks

      release(MIDI_TRANSPORT_CC.fastForward);
      vi.advanceTimersByTime(JOG_TICK_MS * 10);
      expect(h.tc.seek.mock.calls.length).toBe(whileHeld);
    });

    it("accelerates — later ticks move further than the first", () => {
      const h = setup({}, { t: 0, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      vi.advanceTimersByTime(JOG_TICK_MS * 30);
      const positions = h.tc.seek.mock.calls.map((c) => c[0] as number);
      const firstStep = positions[0] - 0;
      const lastStep = positions[positions.length - 1] - positions[positions.length - 2];
      expect(lastStep).toBeGreaterThan(firstStep);
    });

    it("rewind moves backwards and clamps at zero", () => {
      const h = setup({}, { t: 0.1, isPlaying: true });
      press(MIDI_TRANSPORT_CC.rewind);
      expect(h.tc.seek).toHaveBeenCalledWith(0);
      vi.advanceTimersByTime(JOG_TICK_MS * 5);
      for (const call of h.tc.seek.mock.calls) {
        expect(call[0] as number).toBeGreaterThanOrEqual(0);
      }
    });

    it("switching direction mid-hold restarts the ramp", () => {
      const h = setup({}, { t: 100, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      vi.advanceTimersByTime(JOG_TICK_MS * 20);

      // Position reached at full forward speed, captured before the switch.
      const tBeforeSwitch = h.state.t;
      h.tc.seek.mockClear();

      press(MIDI_TRANSPORT_CC.rewind);
      const firstRewind = h.tc.seek.mock.calls[0][0] as number;
      // Back at the slow end of the curve, not still travelling at full speed.
      expect(tBeforeSwitch - firstRewind).toBeCloseTo(JOG_STEP_START_SEC, 6);
    });

    it("does not jog when no session is running", () => {
      const h = setup({ isSessionRunning: () => false }, { t: 30, isPlaying: false });
      press(MIDI_TRANSPORT_CC.fastForward);
      vi.advanceTimersByTime(JOG_TICK_MS * 5);
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    it("reports seeks so the MIDI clock output can follow", () => {
      const h = setup({}, { t: 10, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      expect(h.onSeeked).toHaveBeenCalledWith(10 + JOG_STEP_START_SEC);
    });

    it("stop cancels an in-flight jog", () => {
      const h = setup({}, { t: 50, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      press(MIDI_TRANSPORT_CC.stop);
      h.tc.seek.mockClear();
      vi.advanceTimersByTime(JOG_TICK_MS * 10);
      expect(h.tc.seek).not.toHaveBeenCalled();
    });
  });

  describe("guards", () => {
    it("ignores transport CCs outside the play route", () => {
      const h = setup({ getCurrentRoute: () => "config" });
      press(MIDI_TRANSPORT_CC.play);
      press(MIDI_TRANSPORT_CC.stop);
      expect(h.tc.play).not.toHaveBeenCalled();
      expect(h.tc.stop).not.toHaveBeenCalled();
    });

    it("defers to the pause menu while it is open", () => {
      const h = setup({ isPauseMenuVisible: () => true });
      press(MIDI_TRANSPORT_CC.play);
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("still honours a jog release after the gate closes mid-hold", () => {
      let route = "play";
      const h = setup({ getCurrentRoute: () => route }, { t: 30, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      route = "config"; // navigated away while the button is down
      release(MIDI_TRANSPORT_CC.fastForward);
      h.tc.seek.mockClear();
      vi.advanceTimersByTime(JOG_TICK_MS * 10);
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    it.each([30, 36, 1, 64])("ignores CC %s outside the transport block", (number) => {
      const h = setup();
      press(number);
      expect(h.tc.play).not.toHaveBeenCalled();
      expect(h.tc.stop).not.toHaveBeenCalled();
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    it("ignores non control-change messages on the same numbers", () => {
      const h = setup();
      window.dispatchEvent(
        new CustomEvent("auralprimer:midi-input", {
          detail: {
            timestamp_us: 0,
            message_type: "note_on",
            status: 0x90,
            channel: 0,
            data1: MIDI_TRANSPORT_CC.play,
            data2: 127,
            bytes: [0x90, MIDI_TRANSPORT_CC.play, 127],
          },
        }),
      );
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("ignores malformed messages with missing data bytes", () => {
      const h = setup();
      window.dispatchEvent(
        new CustomEvent("auralprimer:midi-input", {
          detail: {
            timestamp_us: 0,
            message_type: "control_change",
            status: 0xb0,
            channel: 0,
            data1: null,
            data2: null,
            bytes: [0xb0],
          },
        }),
      );
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("stops responding after dispose, including an active jog", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      press(MIDI_TRANSPORT_CC.fastForward);
      handle!.dispose();
      handle = null;
      h.tc.seek.mockClear();
      vi.advanceTimersByTime(JOG_TICK_MS * 10);
      press(MIDI_TRANSPORT_CC.play);
      expect(h.tc.seek).not.toHaveBeenCalled();
      expect(h.tc.play).not.toHaveBeenCalled();
    });
  });
});
