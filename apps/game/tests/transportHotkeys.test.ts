// @vitest-environment jsdom
/**
 * Execution tests for the play-mode transport hotkeys (Space start/pause/
 * resume, Left/Right jog). Covers the guard matrix — route, typing focus,
 * pause menu, modifier keys, auto-repeat — since those are what make a global
 * key binding safe to add to an app this size.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  initTransportHotkeys,
  JOG_COARSE_SEC,
  JOG_FINE_SEC,
  type TransportHotkeys,
  type TransportHotkeysDeps,
} from "../src/transportHotkeys";

type Harness = {
  deps: TransportHotkeysDeps;
  tc: {
    getState: ReturnType<typeof vi.fn>;
    seek: ReturnType<typeof vi.fn>;
    play: ReturnType<typeof vi.fn>;
    pause: ReturnType<typeof vi.fn>;
  };
  startSession: ReturnType<typeof vi.fn>;
  onSeeked: ReturnType<typeof vi.fn>;
  onTransportChanged: ReturnType<typeof vi.fn>;
};

let handle: TransportHotkeys | null = null;

function setup(overrides: Partial<TransportHotkeysDeps> = {}, state = { t: 30, isPlaying: false }): Harness {
  const tc = {
    getState: vi.fn(() => state),
    seek: vi.fn((t: number) => {
      state.t = t;
    }),
    play: vi.fn(async () => {}),
    pause: vi.fn(),
  };
  const startSession = vi.fn();
  const onSeeked = vi.fn();
  const onTransportChanged = vi.fn();
  const deps = {
    transportController: tc as unknown as TransportHotkeysDeps["transportController"],
    getCurrentRoute: () => "play",
    isPauseMenuVisible: () => false,
    isSessionRunning: () => true,
    canStartSession: () => true,
    startSession,
    onSeeked,
    onTransportChanged,
    ...overrides,
  } as TransportHotkeysDeps;
  handle = initTransportHotkeys(deps);
  return { deps, tc, startSession, onSeeked, onTransportChanged };
}

function press(key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  window.dispatchEvent(ev);
  return ev;
}

describe("transportHotkeys", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });
  afterEach(() => {
    handle?.dispose();
    handle = null;
  });

  describe("space", () => {
    it("plays when paused, and swallows the key so a focused button can't re-fire it", () => {
      const h = setup({}, { t: 0, isPlaying: false });
      const ev = press(" ");
      expect(h.tc.play).toHaveBeenCalledTimes(1);
      expect(h.tc.pause).not.toHaveBeenCalled();
      expect(ev.defaultPrevented).toBe(true);
      expect(h.onTransportChanged).toHaveBeenCalled();
    });

    it("pauses when playing", () => {
      const h = setup({}, { t: 12, isPlaying: true });
      press(" ");
      expect(h.tc.pause).toHaveBeenCalledTimes(1);
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("recognises the key by code as well (layouts that report Space)", () => {
      const h = setup({}, { t: 0, isPlaying: false });
      press("Unidentified", { code: "Space" });
      expect(h.tc.play).toHaveBeenCalledTimes(1);
    });

    it("starts the session when none is running", () => {
      const h = setup({ isSessionRunning: () => false }, { t: 0, isPlaying: false });
      press(" ");
      expect(h.startSession).toHaveBeenCalledTimes(1);
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("does nothing when no session is running and Start is disabled", () => {
      const h = setup(
        { isSessionRunning: () => false, canStartSession: () => false },
        { t: 0, isPlaying: false },
      );
      press(" ");
      expect(h.startSession).not.toHaveBeenCalled();
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it("ignores auto-repeat so a held key doesn't stutter the transport", () => {
      const h = setup({}, { t: 5, isPlaying: false });
      press(" ", { repeat: true });
      expect(h.tc.play).not.toHaveBeenCalled();
    });
  });

  describe("jog", () => {
    it("seeks forward and back by the coarse step", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      press("ArrowRight");
      expect(h.tc.seek).toHaveBeenCalledWith(30 + JOG_COARSE_SEC);
      press("ArrowLeft");
      expect(h.tc.seek).toHaveBeenLastCalledWith(30 + JOG_COARSE_SEC - JOG_COARSE_SEC);
    });

    it("uses the fine step with Shift held", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      press("ArrowRight", { shiftKey: true });
      expect(h.tc.seek).toHaveBeenCalledWith(30 + JOG_FINE_SEC);
    });

    it("clamps at zero instead of seeking negative", () => {
      const h = setup({}, { t: 2, isPlaying: true });
      press("ArrowLeft");
      expect(h.tc.seek).toHaveBeenCalledWith(0);
    });

    it("reports the seek so the MIDI clock output can follow", () => {
      const h = setup({}, { t: 10, isPlaying: true });
      press("ArrowRight");
      expect(h.onSeeked).toHaveBeenCalledWith(10 + JOG_COARSE_SEC);
    });

    it("allows auto-repeat so holding an arrow scrubs", () => {
      const h = setup({}, { t: 10, isPlaying: true });
      press("ArrowRight", { repeat: true });
      expect(h.tc.seek).toHaveBeenCalledTimes(1);
    });
  });

  describe("guards", () => {
    it("ignores keys outside the play route", () => {
      const h = setup({ getCurrentRoute: () => "config" }, { t: 0, isPlaying: false });
      press(" ");
      press("ArrowRight");
      expect(h.tc.play).not.toHaveBeenCalled();
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    it("defers to the pause menu while it is open", () => {
      const h = setup({ isPauseMenuVisible: () => true }, { t: 0, isPlaying: false });
      const ev = press(" ");
      expect(h.tc.play).not.toHaveBeenCalled();
      // Not swallowed — the menu's focused Resume button still gets the key.
      expect(ev.defaultPrevented).toBe(false);
    });

    it.each(["INPUT", "TEXTAREA", "SELECT"])("ignores keys typed into %s", (tag) => {
      const h = setup({}, { t: 0, isPlaying: false });
      const el = document.createElement(tag);
      document.body.appendChild(el);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
      );
      expect(h.tc.play).not.toHaveBeenCalled();
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    // A slider counts as "typing" here, which is how the seek bar disabled the
    // very hotkeys it sits next to: clicking it left focus on a range input,
    // and from then on Space and the arrows were swallowed for the rest of the
    // session. The guard is not wrong -- a focused slider should own its own
    // arrow keys -- so the fix belongs at the other end: whoever adds a slider
    // to the play surface has to hand focus back after a pointer interaction.
    // This pins the trap so the next person meets it here rather than in the
    // app.
    it("stands down for a focused range slider, which is why sliders must release focus", () => {
      const h = setup({}, { t: 30, isPlaying: true });
      const slider = document.createElement("input");
      slider.type = "range";
      document.body.appendChild(slider);
      slider.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
      );
      slider.dispatchEvent(
        new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }),
      );
      expect(h.tc.seek).not.toHaveBeenCalled();
      expect(h.tc.pause).not.toHaveBeenCalled();

      // …and once focus is elsewhere the hotkeys are live again.
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
      );
      expect(h.tc.seek).toHaveBeenCalledTimes(1);
    });

    it("ignores keys in a contenteditable element", () => {
      const h = setup({}, { t: 0, isPlaying: false });
      const el = document.createElement("div");
      el.setAttribute("contenteditable", "true");
      // jsdom does not implement isContentEditable from the attribute alone.
      Object.defineProperty(el, "isContentEditable", { value: true });
      document.body.appendChild(el);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
      expect(h.tc.play).not.toHaveBeenCalled();
    });

    it.each([
      ["ctrlKey", { ctrlKey: true }],
      ["metaKey", { metaKey: true }],
      ["altKey", { altKey: true }],
    ])("leaves %s combos to their own handlers", (_label, mods) => {
      const h = setup({}, { t: 10, isPlaying: false });
      press(" ", mods);
      press("ArrowRight", mods);
      expect(h.tc.play).not.toHaveBeenCalled();
      expect(h.tc.seek).not.toHaveBeenCalled();
    });

    it("stops handling keys after dispose", () => {
      const h = setup({}, { t: 0, isPlaying: false });
      handle!.dispose();
      handle = null;
      press(" ");
      expect(h.tc.play).not.toHaveBeenCalled();
    });
  });
});
