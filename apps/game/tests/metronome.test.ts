// @vitest-environment jsdom

import { Metronome, computeNextClickSongT, beatDurationSec } from "../src/metronome";
import type { TransportState } from "@auralprimer/viz-sdk";

class FakeGainNode {
  gain = {
    value: 1,
    setValueAtTime: vi.fn(),
    linearRampToValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
  };
  connect = vi.fn();
  disconnect = vi.fn();
}

class FakeOscillatorNode {
  type = "square";
  frequency = { value: 0 };
  onended: (() => void) | null = null;
  connect = vi.fn();
  disconnect = vi.fn();
  start = vi.fn();
  stop = vi.fn(() => {
    this.onended?.();
  });
}

class FakeAudioContext {
  currentTime = 0;
  state: "running" | "suspended" = "running";
  destination = {};
  createdOsc: FakeOscillatorNode[] = [];
  createGain() {
    return new FakeGainNode() as any;
  }
  createOscillator() {
    const osc = new FakeOscillatorNode();
    this.createdOsc.push(osc);
    return osc as any;
  }
  resume = vi.fn(async () => {
    this.state = "running";
  });
  close = vi.fn(async () => {});
}

function playingState(overrides: Partial<TransportState> = {}): TransportState {
  return {
    t: 0,
    isPlaying: true,
    playbackRate: 1,
    bpm: 120,
    timeSignature: [4, 4],
    ...overrides,
  };
}

describe("metronome", () => {
  it("computes beat duration from bpm", () => {
    expect(beatDurationSec(120)).toBeCloseTo(0.5, 6);
    expect(beatDurationSec(60)).toBeCloseTo(1.0, 6);
    expect(beatDurationSec(0)).toBeCloseTo(0.5, 6);
    expect(beatDurationSec(Number.NaN)).toBeCloseTo(0.5, 6);
  });

  it("computes next click time on beat grid", () => {
    // 120 bpm => 0.5s per beat
    expect(computeNextClickSongT(0.0, 120)).toBeCloseTo(0.0, 6);
    expect(computeNextClickSongT(0.01, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.49, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.5, 120)).toBeCloseTo(0.5, 6);
    expect(computeNextClickSongT(0.51, 120)).toBeCloseTo(1.0, 6);
  });

  it("schedules clicks when enabled and playing", () => {
    const ctx = new FakeAudioContext();
    const m = new Metronome({ enabled: true, audioContext: ctx as any, scheduleAheadSec: 0.2 });

    m.update(playingState({ t: 0.49 }));

    expect(ctx.createdOsc.length).toBeGreaterThan(0);
    expect((m as any).scheduled.length).toBeGreaterThan(0);
  });

  it("resumes suspended context on update", () => {
    const ctx = new FakeAudioContext();
    ctx.state = "suspended";
    const m = new Metronome({ enabled: true, audioContext: ctx as any });

    m.update(playingState({ t: 0.0 }));

    expect(ctx.resume).toHaveBeenCalled();
  });

  it("handles loop-window scheduling across loop boundary", () => {
    const ctx = new FakeAudioContext();
    const m = new Metronome({ enabled: true, audioContext: ctx as any, scheduleAheadSec: 0.2 });

    m.update(playingState({ t: 0.45, loop: { t0: 0.0, t1: 0.5 } }));

    // At 120bpm (0.5 beat), crossing a 0.5s loop boundary should still schedule.
    expect(ctx.createdOsc.length).toBeGreaterThan(0);
  });

  it("reset/disable clears scheduled oscillators", () => {
    const ctx = new FakeAudioContext();
    const m = new Metronome({ enabled: true, audioContext: ctx as any, scheduleAheadSec: 0.2 });

    m.update(playingState({ t: 0.49 }));
    expect((m as any).scheduled.length).toBeGreaterThan(0);

    m.setEnabled(false);
    expect((m as any).scheduled.length).toBe(0);
    // Provided contexts are not closed by design.
    expect(ctx.close).not.toHaveBeenCalled();
  });

  it("closes internally created context when disabling", () => {
    const oldCtor = (globalThis as any).AudioContext;
    try {
      (globalThis as any).AudioContext = FakeAudioContext as any;
      const m = new Metronome({ enabled: true });
      const ctx = (m as any).ctx as FakeAudioContext;

      m.setEnabled(false);
      expect(ctx.close).toHaveBeenCalled();
    } finally {
      (globalThis as any).AudioContext = oldCtor;
    }
  });

  it("volume/getEnabled controls clamp and report values", () => {
    const ctx = new FakeAudioContext();
    const m = new Metronome({ enabled: false, audioContext: ctx as any });
    expect(m.getEnabled()).toBe(false);

    m.setEnabled(true);
    expect(m.getEnabled()).toBe(true);

    m.setVolume(2);
    expect(m.getVolume()).toBe(1);
    m.setVolume(-1);
    expect(m.getVolume()).toBe(0);
  });

  it("resets scheduling when transport stops", () => {
    const ctx = new FakeAudioContext();
    const m = new Metronome({ enabled: true, audioContext: ctx as any, scheduleAheadSec: 0.2 });

    m.update(playingState({ t: 0.49 }));
    expect((m as any).scheduled.length).toBeGreaterThan(0);

    m.update(playingState({ isPlaying: false, t: 0.49 }));
    expect((m as any).scheduled.length).toBe(0);
  });
});
