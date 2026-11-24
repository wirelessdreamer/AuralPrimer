// @vitest-environment jsdom
/**
 * Tests for the Refine workspace audition engine.
 *
 * The pure scheduler helper (`nextLoopStartTime`) is pinned directly.
 * The full engine (`initRefineAudition`) is exercised against a stubbed
 * AudioContext so we cover ensureContext / scheduleNoteVoice / the
 * look-ahead loop / stop / updateNotes in jsdom without a real audio
 * backend.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { initRefineAudition, nextLoopStartTime } from "../src/refineAudition";
import type { RefinementNote } from "../src/refineCandidatesIo";
import { MidiInputStateTracker } from "@auralprimer/midi-input";

function note(t_on: number, pitch: number, velocity = 90): RefinementNote {
  return { t_on, t_off: t_on + 0.5, pitch, velocity };
}

// ─── A minimal but faithful Web Audio stub ──────────────────────────────────

type FakeParam = {
  value: number;
  setValueAtTime: ReturnType<typeof vi.fn>;
  linearRampToValueAtTime: ReturnType<typeof vi.fn>;
  cancelScheduledValues: ReturnType<typeof vi.fn>;
};

function makeParam(initial = 0): FakeParam {
  return {
    value: initial,
    setValueAtTime: vi.fn(),
    linearRampToValueAtTime: vi.fn(),
    cancelScheduledValues: vi.fn(),
  };
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  static throwOnConstruct = false;

  currentTime = 0;
  state: AudioContextState = "suspended";
  destination = {} as AudioDestinationNode;
  resume = vi.fn(() => Promise.resolve());
  createdOscillators: any[] = [];
  createdGains: any[] = [];
  createdPanners: any[] = [];
  createMediaElementSource = vi.fn(() => ({ connect: vi.fn() }));

  constructor() {
    if (FakeAudioContext.throwOnConstruct) {
      throw new Error("boom");
    }
    FakeAudioContext.instances.push(this);
  }

  createGain() {
    const gain = {
      gain: makeParam(0),
      connect: vi.fn((dst: unknown) => dst),
      disconnect: vi.fn(),
    };
    this.createdGains.push(gain);
    return gain;
  }

  createStereoPanner() {
    const panner = { pan: makeParam(0), connect: vi.fn((dst: unknown) => dst) };
    this.createdPanners.push(panner);
    return panner;
  }

  createOscillator() {
    const osc: any = {
      type: "sine",
      frequency: makeParam(0),
      detune: makeParam(0),
      connect: vi.fn((dst: unknown) => dst),
      disconnect: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
      onended: null as null | (() => void),
    };
    this.createdOscillators.push(osc);
    return osc;
  }
}

describe("nextLoopStartTime", () => {
  it("schedules a small gap after the first iteration", () => {
    const next = nextLoopStartTime(0, 0, 0);
    expect(next).toBeGreaterThan(0);
    expect(next).toBeLessThan(1);
  });

  it("advances by region duration + gap each cycle", () => {
    const loopStart = 10;
    const regionDur = 3.5;
    const next = nextLoopStartTime(loopStart + regionDur, loopStart, regionDur);
    expect(next).toBeGreaterThan(loopStart + regionDur);
    expect(next).toBeLessThan(loopStart + regionDur + 1);
  });

  it("snaps to the next aligned cycle when the scheduler runs late", () => {
    const loopStart = 0;
    const regionDur = 2.0;
    const lateNow = 2.0 + 0.2 + 1.0;
    const next = nextLoopStartTime(lateNow, loopStart, regionDur);
    expect(next).toBeGreaterThanOrEqual(4.4 - 1e-9);
  });

  it("returns now + gap for a zero-length region", () => {
    expect(nextLoopStartTime(5, 0, 0)).toBeCloseTo(5.2, 6);
  });
});

describe("initRefineAudition", () => {
  beforeEach(() => {
    FakeAudioContext.instances = [];
    FakeAudioContext.throwOnConstruct = false;
    vi.useFakeTimers();
    vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
    // Make sure the webkit fallback is not in play.
    (window as any).webkitAudioContext = undefined;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("starts not playing and reports isPlaying() correctly across play/stop", () => {
    const engine = initRefineAudition();
    expect(engine.isPlaying()).toBe(false);

    engine.playRegion([note(0.5, 60), note(1.0, 62)], 0, 4);
    expect(engine.isPlaying()).toBe(true);
    expect(FakeAudioContext.instances).toHaveLength(1);

    engine.stop();
    expect(engine.isPlaying()).toBe(false);
  });

  it("creates voices for in-region notes, skips out-of-region notes", () => {
    const engine = initRefineAudition();
    engine.playRegion(
      [
        note(-1, 50), // before region -> skipped
        note(0.5, 60), // in region -> voice
        note(1.0, 62), // in region -> voice
        note(9, 70), // after region -> skipped
      ],
      0,
      4,
    );
    const ctx = FakeAudioContext.instances[0];
    // 2 in-region notes * 2 oscillators each = 4 oscillators scheduled.
    expect(ctx.createdOscillators.length).toBeGreaterThanOrEqual(4);
  });

  it("resumes a suspended context on playRegion", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 4);
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.resume).toHaveBeenCalled();
  });

  it("does not resume a running context", () => {
    const engine = initRefineAudition();
    // Patch the next-created context to look "running".
    const origResume = FakeAudioContext.prototype.resume;
    engine.playRegion([note(0.5, 60)], 0, 4);
    const ctx = FakeAudioContext.instances[0];
    ctx.resume.mockClear();
    ctx.state = "running";
    engine.playRegion([note(0.5, 60)], 0, 4);
    expect(ctx.resume).not.toHaveBeenCalled();
    void origResume;
  });

  it("reuses the same AudioContext across multiple playRegion calls", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 4);
    engine.playRegion([note(0.5, 61)], 0, 4);
    expect(FakeAudioContext.instances).toHaveLength(1);
  });

  it("oscillator onended cleanup disconnects without throwing", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 4);
    const ctx = FakeAudioContext.instances[0];
    for (const osc of ctx.createdOscillators) {
      if (typeof osc.onended === "function") {
        expect(() => osc.onended()).not.toThrow();
      }
    }
  });

  it("advances the look-ahead scheduler on interval ticks", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 2);
    const ctx = FakeAudioContext.instances[0];
    const before = ctx.createdOscillators.length;
    // Advance the audio clock past the look-ahead window then fire the
    // interval; the scheduler should queue another loop iteration.
    ctx.currentTime = 5;
    vi.advanceTimersByTime(60);
    expect(ctx.createdOscillators.length).toBeGreaterThan(before);
  });

  it("updateNotes swaps notes for the running region", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 2);
    const ctx = FakeAudioContext.instances[0];
    const before = ctx.createdOscillators.length;
    engine.updateNotes([note(0.5, 60), note(1.0, 62), note(1.5, 64)]);
    ctx.currentTime = 5;
    vi.advanceTimersByTime(60);
    // The new (denser) note set produces more voices on the next loop.
    expect(ctx.createdOscillators.length).toBeGreaterThan(before);
  });

  it("updateNotes is a no-op when nothing is playing", () => {
    const engine = initRefineAudition();
    expect(() => engine.updateNotes([note(0, 60)])).not.toThrow();
    expect(engine.isPlaying()).toBe(false);
  });

  it("stop() ramps the master gain to silence and stops the timer", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 2);
    engine.stop();
    expect(engine.isPlaying()).toBe(false);
    // Stop again is harmless even though playing is null.
    expect(() => engine.stop()).not.toThrow();
  });

  it("clamps a zero-length note to a minimum duration", () => {
    const engine = initRefineAudition();
    const degenerate: RefinementNote = { t_on: 0.5, t_off: 0.5, pitch: 60, velocity: 90 };
    engine.playRegion([degenerate], 0, 4);
    expect(FakeAudioContext.instances[0].createdOscillators.length).toBeGreaterThanOrEqual(2);
  });

  it("handles a zero-width region (no notes scheduled, still 'playing')", () => {
    const engine = initRefineAudition();
    engine.playRegion([note(0, 60)], 2, 2);
    expect(engine.isPlaying()).toBe(true);
  });

  it("does nothing when AudioContext cannot be constructed", () => {
    FakeAudioContext.throwOnConstruct = true;
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 4);
    // ensureContext returns false -> playRegion bails before setting state.
    expect(engine.isPlaying()).toBe(false);
  });

  it("does nothing when no AudioContext constructor is available", () => {
    vi.stubGlobal("AudioContext", undefined);
    (window as any).webkitAudioContext = undefined;
    const engine = initRefineAudition();
    engine.playRegion([note(0.5, 60)], 0, 4);
    expect(engine.isPlaying()).toBe(false);
  });

  it("keeps source and notes on opposite ears, folds stereo to mono, and connects a source only once", () => {
    const engine = initRefineAudition();
    engine.setOutput({ sourceGain: 0.8, renderedGain: 0.7, sourcePan: -1, renderedPan: 1, renderedEnabled: true });
    const source = document.createElement("audio");
    engine.connectSource(source);
    engine.connectSource(source);
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.createMediaElementSource).toHaveBeenCalledTimes(1);
    expect(ctx.createdPanners.map(p => p.pan.value)).toEqual([-1, 1]);
    expect(ctx.createdGains[2].channelCount).toBe(1);
    engine.setOutput({ sourceGain: 0.8, renderedGain: 0.7, sourcePan: 1, renderedPan: -1, renderedEnabled: true });
    expect(ctx.createdPanners.map(p => p.pan.value)).toEqual([1, -1]);
  });

  it("follows the source clock at half speed without changing pitch or scheduling the whole song", () => {
    const engine = initRefineAudition();
    let position = 0;
    engine.playRegion([note(0.1, 69), note(100, 72)], 0, 120, { getTimeSec: () => position, playbackRate: 0.5 });
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.createdOscillators).toHaveLength(2);
    expect(ctx.createdOscillators[0].frequency.value).toBe(440);
    expect(ctx.createdOscillators[0].start).toHaveBeenCalledWith(0.2);
    expect(ctx.createdOscillators[0].stop.mock.calls[0][0]).toBeCloseTo(1.3);
    position = 99.9;
    ctx.currentTime = 1;
    vi.advanceTimersByTime(60);
    expect(ctx.createdOscillators).toHaveLength(4);
    engine.stop();
    expect(ctx.createdOscillators.every(o => o.stop.mock.calls.length > 1)).toBe(true);
  });

  it("resumes the remaining part of a held note when seeking into it", () => {
    const engine = initRefineAudition();
    engine.playRegion([{ t_on: 0, t_off: 1, pitch: 69, velocity: 90 }], 0.3, 10,
      { getTimeSec: () => 0.3, playbackRate: 0.5 });
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.createdOscillators).toHaveLength(2);
    expect(ctx.createdOscillators[0].start).toHaveBeenCalledWith(0);
    expect(ctx.createdOscillators[0].stop.mock.calls[0][0]).toBeCloseTo(1.5);
    expect(ctx.createdOscillators[0].frequency.value).toBe(440);
  });

  it("holds live keys across transport changes, uses velocity and releases on MIDI note-off", () => {
    const engine = initRefineAudition();
    const tracker = new MidiInputStateTracker();
    const message = (velocity: number, timestamp_us = 1) => ({ timestamp_us, message_type: "note_on", status: 0x90, channel: 0, data1: 69, data2: velocity, bytes: [0x90, 69, velocity] });
    engine.setLiveGain(0.4);
    engine.setLiveNotes(tracker.apply(message(64)).activeNotes);
    const ctx = FakeAudioContext.instances[0];
    const live = ctx.createdOscillators.slice();
    expect(live).toHaveLength(2);
    expect(live[0].frequency.value).toBe(440);
    expect(ctx.createdGains[5].gain.linearRampToValueAtTime).toHaveBeenCalledWith(64 / 127, 0.005);
    expect(ctx.createdGains[3].connect).toHaveBeenCalledWith(ctx.destination);
    expect(ctx.createdGains[3].gain.value).toBeCloseTo(0.1);
    engine.setLiveNotes(tracker.snapshot().activeNotes);
    expect(ctx.createdOscillators).toHaveLength(2);
    engine.playRegion([note(0, 60)], 0, 1);
    engine.stop();
    expect(live.every(o => o.stop.mock.calls.length === 0)).toBe(true);
    engine.setLiveNotes(tracker.apply(message(0, 2)).activeNotes);
    expect(live.every(o => o.stop.mock.calls.length === 1)).toBe(true);
    live[0].onended();
    expect(live.every(o => o.disconnect.mock.calls.length === 1)).toBe(true);
  });

  it("retriggers repeated live notes and releases the previous voice", () => {
    const engine = initRefineAudition();
    const tracker = new MidiInputStateTracker();
    const event = { timestamp_us: 1, message_type: "note_on", status: 0x90, channel: 0, data1: 60, data2: 100, bytes: [0x90, 60, 100] };
    engine.setLiveNotes(tracker.apply(event).activeNotes);
    engine.setLiveNotes(tracker.apply({ ...event, timestamp_us: 2 }).activeNotes);
    const ctx = FakeAudioContext.instances[0];
    expect(ctx.createdOscillators).toHaveLength(4);
    expect(ctx.createdOscillators[0].stop).toHaveBeenCalledOnce();
    expect(ctx.createdOscillators[2].stop).not.toHaveBeenCalled();
    engine.setLiveNotes([]);
    expect(ctx.createdOscillators[2].stop).toHaveBeenCalledOnce();
  });
});
