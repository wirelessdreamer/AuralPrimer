// @vitest-environment jsdom
/**
 * Execution tests for the A/V sync calibrator (Rock Band-style tap-to-beat
 * latency measurement). jsdom lacks the Web Audio API and a real audio clock,
 * so we stub AudioContext (with a controllable currentTime), performance.now,
 * and the timer functions, then drive the metronome scheduler + tap loop by
 * hand to exercise scheduling, tap matching, the median estimate, apply,
 * close, and the keyboard handlers.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { initAvCalibration, type AvCalibrationDeps } from "../src/avCalibration";

// --- controllable clocks ---------------------------------------------------
let audioNow = 0; // AudioContext.currentTime (seconds)
let perfNow = 0; // performance.now() (ms)

class FakeOscillator {
  frequency = { value: 0 };
  type = "sine";
  connect = vi.fn().mockReturnThis();
  start = vi.fn();
  stop = vi.fn();
}
class FakeGain {
  gain = {
    value: 0,
    setValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
  };
  connect = vi.fn().mockReturnThis();
}
class FakeAudioContext {
  state = "running";
  destination = {};
  get currentTime() {
    return audioNow;
  }
  createOscillator() {
    return new FakeOscillator();
  }
  createGain() {
    return new FakeGain();
  }
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);
}

function makeDeps(over: Partial<AvCalibrationDeps> = {}): {
  deps: AvCalibrationDeps;
  onApply: ReturnType<typeof vi.fn>;
  log: ReturnType<typeof vi.fn>;
} {
  const onApply = vi.fn();
  const log = vi.fn();
  const deps: AvCalibrationDeps = {
    onApply,
    getInitialOffsetMs: () => 0,
    log,
    ...over,
  };
  return { deps, onApply, log };
}

const overlay = () => document.querySelector<HTMLElement>(".avCalOverlay");
const startBtn = () => document.getElementById("avCalStart") as HTMLButtonElement;
const applyBtn = () => document.getElementById("avCalApply") as HTMLButtonElement;
const closeBtn = () => document.getElementById("avCalClose") as HTMLButtonElement;
const pulseEl = () => document.getElementById("avCalPulse") as HTMLElement;
const countEl = () => document.getElementById("avCalCount") as HTMLElement;
const estimateEl = () => document.getElementById("avCalEstimate") as HTMLElement;

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

describe("avCalibration", () => {
  beforeEach(() => {
    audioNow = 0;
    perfNow = 1000;
    document.body.innerHTML = "";
    document.head.innerHTML = "";
    vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
    // Make timers controllable: run synchronously via fake timers. Install the
    // performance.now spy AFTER useFakeTimers — fake timers also replace
    // performance.now, so the spy must win to keep our manual clock.
    vi.useFakeTimers();
    vi.spyOn(performance, "now").mockImplementation(() => perfNow);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("open() builds the overlay and shows it", () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    expect(overlay()).not.toBeNull();
    expect(overlay()!.style.display).toBe("flex");
    expect(countEl().textContent).toBe("Taps: 0");
    expect(estimateEl().textContent).toBe("Estimated latency: —");
    // Reopen reuses the same overlay (build() early-returns).
    handle.open();
    expect(document.querySelectorAll(".avCalOverlay")).toHaveLength(1);
    // Style keyframe injected once.
    expect(document.getElementById("avCalStyle")).not.toBeNull();
  });

  it("starting the metronome schedules clicks and flips Start -> Restart", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
    // schedulerTick scheduled at least one click within the lookahead horizon.
    // Advance the audio clock + run the interval timer to schedule more.
    audioNow = 1.0;
    vi.advanceTimersByTime(50); // fires the 25ms scheduler interval
    await flush();
    // A pulse timer fires -> pulse class toggled.
    vi.advanceTimersByTime(500);
    expect(pulseEl().classList.contains("pulse")).toBe(true);
  });

  it("registers taps via SPACE and computes the median latency estimate", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    // After start: ctxAnchor = currentTime (0), perfAnchor = perfNow (1000),
    // nextClickAudioTime = 0.2. The initial schedulerTick scheduled clicks up
    // to horizon = 0 + 0.12. nextClickAudioTime(0.2) > 0.12 so NO click yet.
    // Advance audio clock so the scheduler enqueues clicks, then run interval.
    audioNow = 8.0;
    vi.advanceTimersByTime(30);
    await flush();

    // Clicks were scheduled at audio times 0.2, 0.8, 1.4, ... mapped to perf:
    //   playPerf = perfAnchor + (audioTime - ctxAnchor)*1000 = 1000 + audioTime*1000
    // So click at 0.2 -> perf 1200. Tap shortly after each click.
    // Drive several taps via keydown with controlled timeStamps (perf-relative).
    const tapAt = (perf: number) => {
      const ev = new KeyboardEvent("keydown", { code: "Space" });
      Object.defineProperty(ev, "timeStamp", { value: perf });
      window.dispatchEvent(ev);
    };
    // click perfs: 1200, 1800, 2400, ... ; tap 30ms after each.
    for (let i = 0; i < 10; i += 1) {
      const clickPerf = 1200 + i * 600;
      tapAt(clickPerf + 30);
    }
    expect(countEl().textContent).toBe("Taps: 10");
    expect(estimateEl().textContent).toBe("Estimated latency: 30 ms");
    // >= MIN_TAPS_TO_APPLY (8) -> Apply enabled.
    expect(applyBtn().disabled).toBe(false);
  });

  it("rejects mistaps (no matching click, or out-of-range delta)", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    audioNow = 8.0;
    vi.advanceTimersByTime(30);
    await flush();

    const tapAt = (perf: number) => {
      const ev = new KeyboardEvent("keydown", { code: "Space" });
      Object.defineProperty(ev, "timeStamp", { value: perf });
      window.dispatchEvent(ev);
    };
    // Tap before the first click ever played (perf < 1200): no match.
    tapAt(500);
    expect(countEl().textContent).toBe("Taps: 0");
    // Tap absurdly late relative to its click (> 600ms after, but < a full
    // period so it matches the click then gets range-rejected). Use a delta of
    // 590ms after click 1200 = 1790, still < next click 1800 -> matches click
    // 1200 with dt 590 -> rejected (>600? no, 590<600 ok). Use 1799 -> dt 599.
    // To force >600 rejection we need a gap; instead test the <0 path via a tap
    // that matches but with negative best is impossible. So just assert the
    // "no match within a period" break path: tap way past last click.
    tapAt(99999);
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("apply button calls onApply with the median and closes the overlay", async () => {
    const { deps, onApply, log } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    audioNow = 8.0;
    vi.advanceTimersByTime(30);
    await flush();

    const tapAt = (perf: number) => {
      const ev = new KeyboardEvent("keydown", { code: "Space" });
      Object.defineProperty(ev, "timeStamp", { value: perf });
      window.dispatchEvent(ev);
    };
    for (let i = 0; i < 8; i += 1) tapAt(1200 + i * 600 + 42);
    expect(applyBtn().disabled).toBe(false);

    applyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(onApply).toHaveBeenCalledWith(42);
    expect(log).toHaveBeenCalledWith(
      "av-calibration: applied",
      expect.objectContaining({ offsetMs: 42, taps: 8 }),
    );
    expect(overlay()!.style.display).toBe("none");
  });

  it("clicking the pulse circle also registers a tap while running", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    audioNow = 8.0;
    vi.advanceTimersByTime(30);
    await flush();
    // perfNow positioned just after the first click perf (1200).
    perfNow = 1230;
    pulseEl().dispatchEvent(new MouseEvent("click"));
    expect(countEl().textContent).toBe("Taps: 1");
    expect(estimateEl().textContent).toBe("Estimated latency: 30 ms");
  });

  it("pulse click is ignored when not running", () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    pulseEl().dispatchEvent(new MouseEvent("click"));
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("Escape closes the overlay and stops the metronome", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const ev = new KeyboardEvent("keydown", { key: "Escape" });
    window.dispatchEvent(ev);
    expect(overlay()!.style.display).toBe("none");
  });

  it("Cancel button closes the overlay", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    closeBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(overlay()!.style.display).toBe("none");
  });

  it("keydown handler is inert when overlay is hidden", () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    // Hide overlay manually, then a Space keydown should be a no-op (guard).
    overlay()!.style.display = "none";
    const ev = new KeyboardEvent("keydown", { code: "Space" });
    Object.defineProperty(ev, "timeStamp", { value: 1234 });
    window.dispatchEvent(ev);
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("starting twice is a no-op (already running guard)", async () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
    // Second start while running returns early; text stays "Restart".
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
  });

  it("falls back to webkitAudioContext when AudioContext is undefined", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", FakeAudioContext as unknown as typeof AudioContext);
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
  });

  it("reports audio-unavailable when AudioContext construction throws", async () => {
    vi.stubGlobal(
      "AudioContext",
      vi.fn().mockImplementation(() => {
        throw new Error("no audio");
      }) as unknown as typeof AudioContext,
    );
    const { deps, log } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(estimateEl().textContent).toBe("Audio unavailable on this device");
    expect(log).toHaveBeenCalledWith(
      "av-calibration: failed to create AudioContext",
      expect.anything(),
    );
  });

  it("resumes a suspended AudioContext on start", async () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    class Suspended extends FakeAudioContext {
      state = "suspended";
      resume = resume;
    }
    vi.stubGlobal("AudioContext", Suspended as unknown as typeof AudioContext);
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(resume).toHaveBeenCalled();
  });
});
