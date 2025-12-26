// @vitest-environment jsdom
import { TransportController } from "../src/transportController";
import type { TransportTimebase } from "../src/audioBackend";

class FakeTimebase implements TransportTimebase {
  private t = 0;
  private duration: number | null = null;
  private playing = false;
  private loop: { t0: number; t1: number } | undefined;
  private rate = 1;

  async load(_source: { blob: Blob; mime: string }): Promise<void> {
    this.t = 0;
    this.duration = 10;
    this.playing = false;
  }

  async play(): Promise<void> {
    this.playing = true;
  }

  pause(): void {
    this.playing = false;
  }

  stop(): void {
    this.playing = false;
    this.t = 0;
  }

  seek(tSec: number): void {
    this.t = Math.max(0, tSec);
  }

  setLoop(loop?: { t0: number; t1: number }): void {
    this.loop = loop;
  }

  setPlaybackRate(rate: number): void {
    this.rate = rate;
  }

  getPlaybackRate(): number {
    return this.rate;
  }

  getDurationSec(): number | null {
    return this.duration;
  }

  getCurrentTimeSec(): number {
    return this.t;
  }

  getIsPlaying(): boolean {
    return this.playing;
  }

  dispose(): void {
    // nothing
  }

  // Test helper to simulate underlying clock progression.
  advanceUnderlyingClock(dt: number) {
    if (!this.playing) return;
    this.t += dt;
    if (this.loop && this.t >= this.loop.t1) {
      this.t = this.loop.t0;
    }
  }
}

describe("TransportController", () => {
  it("ticks a simulated clock when no audio is loaded", async () => {
    const tb = new FakeTimebase();
    // duration null => no audio
    (tb as any).duration = null;

    const tc = new TransportController(tb);

    // must be playing for simulated clock to advance
    await tc.play();
    tc.tick(0.5);
    tc.tick(0.5);

    expect(tc.getState().t).toBeCloseTo(1.0, 6);

    tc.dispose();
  });

  it("applies playbackRate to simulated clock", async () => {
    const tb = new FakeTimebase();
    (tb as any).duration = null;

    const tc = new TransportController(tb);
    tc.setPlaybackRate(0.5);
    await tc.play();

    tc.tick(2.0);
    expect(tc.getState().t).toBeCloseTo(1.0, 6);
    expect(tc.getState().playbackRate).toBeCloseTo(0.5, 6);

    tc.dispose();
  });

  it("clamps seek into loop and wraps simulated time at loop end", async () => {
    const tb = new FakeTimebase();
    (tb as any).duration = null;
    const tc = new TransportController(tb);

    tc.setLoop({ t0: 2, t1: 3 });
    tc.seek(0);
    expect(tc.getState().t).toBe(2);

    await tc.play();
    tc.tick(2); // would go to 4, should wrap to 2
    expect(tc.getState().t).toBe(2);

    tc.dispose();
  });

  it("uses timebase currentTime when duration is known and enforces loop by seeking", async () => {
    const tb = new FakeTimebase();
    await tb.load({ blob: new Blob([]), mime: "audio/ogg" });

    const tc = new TransportController(tb);

    tc.setLoop({ t0: 1, t1: 2 });
    await tc.play();

    tb.seek(1.5);
    tc.tick(0.016);
    expect(tc.getState().t).toBeCloseTo(1.5, 6);

    // Cross loop end at the timebase level.
    tb.seek(2.01);
    tc.tick(0.016);
    expect(tb.getCurrentTimeSec()).toBe(1);
    expect(tc.getState().t).toBe(1);

    tc.dispose();
  });

  it("can follow external clock (when enabled) and ignores timebase as authority", async () => {
    const tb = new FakeTimebase();
    await tb.load({ blob: new Blob([]), mime: "audio/ogg" });
    const tc = new TransportController(tb);

    // External clock drives transport.
    tc.setFollowExternalClock(true);
    tc.setExternalClockBpm(120);
    tc.setExternalClockRunning(true);

    // Even if timebase changes, transport follows external tick deltas.
    tb.seek(5);
    tc.pushExternalClockDelta(0.5);
    tc.tick(0.016);
    expect(tc.getState().t).toBeCloseTo(0.5, 6);
    expect(tc.getState().bpm).toBeCloseTo(120, 6);

    tc.pushExternalClockDelta(0.5);
    tc.tick(0.016);
    expect(tc.getState().t).toBeCloseTo(1.0, 6);

    tc.dispose();
  });
});
