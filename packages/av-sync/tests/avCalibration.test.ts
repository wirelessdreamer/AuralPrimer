// @vitest-environment jsdom
/**
 * Execution tests for the shared two-pass A/V calibrator. jsdom lacks the Web
 * Audio API and a real audio clock, so we stub AudioContext (controllable
 * currentTime), performance.now, and timers, then drive the metronome
 * scheduler + tap loop by hand to exercise scheduling, tap matching, the
 * median estimate, the audio->video phase advance, apply, skip, close, and the
 * keyboard handlers.
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
  createOscillatorCalls = 0;
  get currentTime() {
    return audioNow;
  }
  createOscillator() {
    this.createOscillatorCalls += 1;
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
    getInitial: () => ({ audioMs: 0, videoMs: 0 }),
    log,
    ...over,
  };
  return { deps, onApply, log };
}

const overlay = () => document.querySelector<HTMLElement>(".avCalOverlay");
const startBtn = () => document.getElementById("avCalStart") as HTMLButtonElement;
const nextBtn = () => document.getElementById("avCalNext") as HTMLButtonElement;
const skipBtn = () => document.getElementById("avCalSkip") as HTMLButtonElement;
const closeBtn = () => document.getElementById("avCalClose") as HTMLButtonElement;
const pulseEl = () => document.getElementById("avCalPulse") as HTMLElement;
const countEl = () => document.getElementById("avCalCount") as HTMLElement;
const estimateEl = () => document.getElementById("avCalEstimate") as HTMLElement;
const stepEl = () => document.getElementById("avCalStep") as HTMLElement;

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const tapAt = (perf: number) => {
  // The calibrator reads performance.now() at keydown (not ev.timeStamp), so
  // drive the mocked clock to the tap time before dispatching.
  perfNow = perf;
  const ev = new KeyboardEvent("keydown", { code: "Space" });
  Object.defineProperty(ev, "timeStamp", { value: perf });
  window.dispatchEvent(ev);
};

const PERIOD_MS = 600;
const AUDIO_LEAD_MS = 50;
const FIRST_BEAT_MS = 200;

// Drive the wall-clock metronome: advance the mocked performance clock + fake
// timers to the next beat and return its stimulus time to tap against. Beats
// fire +200ms after Start, then every 600ms; audio beeps lead the beat by 50ms
// (folded into the reference), video flashes land on the beat.
function beatDriver(startPerf: number) {
  let beatPerf = startPerf + FIRST_BEAT_MS;
  let fired = 0;
  return {
    async next(kind: "audio" | "video"): Promise<number> {
      perfNow = beatPerf;
      vi.advanceTimersByTime(fired === 0 ? FIRST_BEAT_MS : PERIOD_MS);
      await flush();
      fired += 1;
      const stim = kind === "audio" ? beatPerf + AUDIO_LEAD_MS : beatPerf;
      beatPerf += PERIOD_MS;
      return stim;
    },
  };
}

describe("avCalibration (two-pass)", () => {
  beforeEach(() => {
    audioNow = 0;
    perfNow = 1000;
    document.body.innerHTML = "";
    document.head.innerHTML = "";
    vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
    vi.useFakeTimers();
    vi.spyOn(performance, "now").mockImplementation(() => perfNow);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("open() builds the overlay starting on the audio phase", () => {
    const { deps } = makeDeps();
    const handle = initAvCalibration(deps);
    handle.open();
    expect(overlay()).not.toBeNull();
    expect(overlay()!.style.display).toBe("flex");
    expect(stepEl().textContent).toContain("Audio");
    expect(countEl().textContent).toBe("Taps: 0");
    expect(estimateEl().textContent).toBe("Estimated audio latency: —");
    // Reopen reuses the same overlay (build() early-returns).
    handle.open();
    expect(document.querySelectorAll(".avCalOverlay")).toHaveLength(1);
    expect(document.getElementById("avCalStyle")).not.toBeNull();
  });

  it("registers audio taps via SPACE and computes the median estimate", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    const beats = beatDriver(perfNow);
    for (let i = 0; i < 10; i += 1) tapAt((await beats.next("audio")) + 30);
    expect(countEl().textContent).toBe("Taps: 10");
    expect(estimateEl().textContent).toBe("Estimated audio latency: 30 ms");
    expect(nextBtn().disabled).toBe(false);
    expect(nextBtn().textContent).toContain("Next");
  });

  it("rejects mistaps (no matching click within a period)", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const beats = beatDriver(perfNow);
    await beats.next("audio"); // one stimulus at ~1250

    tapAt(500); // before the first stimulus ever played
    expect(countEl().textContent).toBe("Taps: 0");
    tapAt(99999); // far past the last stimulus
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("advances audio -> video, and the video pass is silent", async () => {
    const oscSpy = vi.spyOn(FakeAudioContext.prototype, "createOscillator");
    const { deps } = makeDeps();
    initAvCalibration(deps).open();

    // Audio pass.
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const audio = beatDriver(perfNow);
    for (let i = 0; i < 8; i += 1) tapAt((await audio.next("audio")) + 30);
    expect(oscSpy.mock.calls.length).toBeGreaterThan(0); // audio pass beeps
    nextBtn().dispatchEvent(new MouseEvent("click")); // advance to video
    expect(stepEl().textContent).toContain("Video");
    expect(nextBtn().textContent).toContain("Apply");
    expect(nextBtn().disabled).toBe(true);
    expect(countEl().textContent).toBe("Taps: 0");
    expect(estimateEl().textContent).toBe("Estimated video latency: —");

    // Video pass: wall-clock flashes, and NO oscillator is ever created.
    const oscBefore = oscSpy.mock.calls.length;
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const video = beatDriver(perfNow);
    for (let i = 0; i < 8; i += 1) tapAt((await video.next("video")) + 45);
    expect(estimateEl().textContent).toBe("Estimated video latency: 45 ms");
    expect(oscSpy.mock.calls.length).toBe(oscBefore); // silent
  });

  it("video estimate stays stable when the audio clock stalls/jumps (no climb)", async () => {
    // Regression: the old code referenced each flash to the AudioContext clock,
    // which stalls when silent (the video pass); the estimate then climbed as the
    // clock fell behind. The wall-clock design must ignore the audio clock here.
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    skipBtn().dispatchEvent(new MouseEvent("click")); // skip audio -> video
    expect(stepEl().textContent).toContain("Video");

    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const video = beatDriver(perfNow);
    for (let i = 0; i < 12; i += 1) {
      // Hostile audio clock: stalled/jumping. Fed to stimulusPerf by the old
      // code; must have zero effect now.
      audioNow = i % 2 === 0 ? 0 : 999;
      tapAt((await video.next("video")) + 40); // constant 40ms reaction each beat
    }
    // No accumulation — the estimate is the true, steady reaction, not a climb.
    expect(estimateEl().textContent).toBe("Estimated video latency: 40 ms");
  });

  it("apply (after both phases) reports both medians and closes", async () => {
    const { deps, onApply, log } = makeDeps();
    initAvCalibration(deps).open();

    // Audio pass -> median 42.
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const audio = beatDriver(perfNow);
    for (let i = 0; i < 8; i += 1) tapAt((await audio.next("audio")) + 42);
    nextBtn().dispatchEvent(new MouseEvent("click"));

    // Video pass -> median 16.
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const video = beatDriver(perfNow);
    for (let i = 0; i < 8; i += 1) tapAt((await video.next("video")) + 16);
    expect(nextBtn().disabled).toBe(false);
    nextBtn().dispatchEvent(new MouseEvent("click")); // Apply

    expect(onApply).toHaveBeenCalledWith({ audioMs: 42, videoMs: 16 });
    expect(log).toHaveBeenCalledWith("av-calibration: applied", expect.objectContaining({ audioMs: 42, videoMs: 16 }));
    expect(overlay()!.style.display).toBe("none");
  });

  it("skipping the audio pass keeps its initial value, then applies", async () => {
    const { deps, onApply } = makeDeps({ getInitial: () => ({ audioMs: 123, videoMs: 0 }) });
    initAvCalibration(deps).open();

    // Skip audio -> video phase, audio keeps 123.
    skipBtn().dispatchEvent(new MouseEvent("click"));
    expect(stepEl().textContent).toContain("Video");

    // Skip video too -> applies with the seeded values unchanged.
    skipBtn().dispatchEvent(new MouseEvent("click"));
    expect(onApply).toHaveBeenCalledWith({ audioMs: 123, videoMs: 0 });
    expect(overlay()!.style.display).toBe("none");
  });

  it("clicking the pulse circle registers a tap while running", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    const beats = beatDriver(perfNow);
    const stim = await beats.next("audio");
    perfNow = stim + 30;
    pulseEl().dispatchEvent(new MouseEvent("click"));
    expect(countEl().textContent).toBe("Taps: 1");
    expect(estimateEl().textContent).toBe("Estimated audio latency: 30 ms");
  });

  it("pulse click is ignored when not running", () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    pulseEl().dispatchEvent(new MouseEvent("click"));
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("Escape closes the overlay and stops the metronome", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(overlay()!.style.display).toBe("none");
  });

  it("Cancel button closes the overlay", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    closeBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(overlay()!.style.display).toBe("none");
  });

  it("keydown handler is inert when overlay is hidden", () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    overlay()!.style.display = "none";
    tapAt(1234);
    expect(countEl().textContent).toBe("Taps: 0");
  });

  it("Restart resets the tap count and lets you tap again", async () => {
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
    const beats = beatDriver(perfNow);
    for (let i = 0; i < 3; i += 1) tapAt((await beats.next("audio")) + 30);
    expect(countEl().textContent).toBe("Taps: 3");

    // Clicking "Restart" must clear the taps and re-anchor — not be a no-op
    // (the button bailed here before, so Restart appeared dead).
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(startBtn().textContent).toBe("Restart");
    expect(countEl().textContent).toBe("Taps: 0");
    expect(nextBtn().disabled).toBe(true);

    // …and tapping works again after the restart.
    const beats2 = beatDriver(perfNow);
    for (let i = 0; i < 5; i += 1) tapAt((await beats2.next("audio")) + 20);
    expect(countEl().textContent).toBe("Taps: 5");
  });

  it("falls back to webkitAudioContext when AudioContext is undefined", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", FakeAudioContext as unknown as typeof AudioContext);
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
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
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(estimateEl().textContent).toBe("Audio unavailable on this device");
    expect(log).toHaveBeenCalledWith("av-calibration: failed to create AudioContext", expect.anything());
  });

  it("resumes a suspended AudioContext on start", async () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    class Suspended extends FakeAudioContext {
      state = "suspended";
      resume = resume;
    }
    vi.stubGlobal("AudioContext", Suspended as unknown as typeof AudioContext);
    const { deps } = makeDeps();
    initAvCalibration(deps).open();
    startBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(resume).toHaveBeenCalled();
  });
});
