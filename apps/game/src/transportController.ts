import type { TransportState } from "@auralprimer/viz-sdk";
import type { TransportTimebase } from "./audioBackend";
import { clampLoop, clampToLoop } from "./audioBackend";

export type TransportControllerOptions = {
  bpm?: number;
  timeSignature?: [number, number];
};

/**
 * Transport controller responsible for producing a stable `TransportState` each frame.
 *
 * Key behavior:
 * - When the underlying timebase has a real audio clock, `t` is derived from it.
 * - When no audio is loaded, we fall back to a simulated monotonic clock.
 * - Loop is enforced at the controller layer (time clamping + wrap).
 */
export class TransportController {
  private state: TransportState;

  // Simulated clock fields (used when duration is unknown / nothing loaded)
  private simT = 0;

  // External clock following (MIDI clock input)
  private followExternalClock = false;
  private externalBpm: number | null = null;
  private externalRunning = false;
  private lastExternalTickT: number | null = null;
  private pendingExternalDtSec: number = 0;

  constructor(
    private readonly timebase: TransportTimebase,
    opts: TransportControllerOptions = {}
  ) {
    this.state = {
      t: 0,
      isPlaying: false,
      playbackRate: 1,
      bpm: opts.bpm ?? 120,
      timeSignature: opts.timeSignature ?? [4, 4]
    };
  }

  dispose(): void {
    this.timebase.dispose();
  }

  getState(): TransportState {
    return this.state;
  }

  /**
   * Load audio into the timebase and reset transport to t=0.
   */
  async loadAudio(source: { blob: Blob; mime: string }): Promise<void> {
    await this.timebase.load(source);
    // Some backends reset playbackRate on load; re-apply.
    this.timebase.setPlaybackRate(this.state.playbackRate || 1);

    this.simT = 0;
    this.state = { ...this.state, t: 0, isPlaying: false };
  }

  /**
   * Desktop-friendly alternative: load audio directly from a SongPack.
   *
   * This avoids moving large audio blobs over the JS<->Rust IPC boundary.
   */
  async loadAudioFromSongPack(containerPath: string): Promise<void> {
    if (this.timebase.loadFromSongPack) {
      await this.timebase.loadFromSongPack(containerPath);
      // Some backends reset playbackRate on load; re-apply.
      this.timebase.setPlaybackRate(this.state.playbackRate || 1);

      this.simT = 0;
      this.state = { ...this.state, t: 0, isPlaying: false };
      return;
    }

    throw new Error("timebase does not support loadFromSongPack()");
  }

  setPlaybackRate(rate: number): void {
    const r = Number.isFinite(rate) && rate > 0 ? rate : 1;
    this.timebase.setPlaybackRate(r);
    this.state = { ...this.state, playbackRate: r };
  }

  /** Enable/disable following external clock (e.g., MIDI clock). */
  setFollowExternalClock(enabled: boolean): void {
    this.followExternalClock = enabled;
    if (!enabled) {
      // Reset external clock state so we don't jump when re-enabled.
      this.externalRunning = false;
      this.externalBpm = null;
      this.lastExternalTickT = null;
    }
  }

  /** Best-effort: start/stop info from external clock transport messages. */
  setExternalClockRunning(isRunning: boolean): void {
    this.externalRunning = isRunning;
    // On start, anchor the external clock at current t.
    if (isRunning) {
      this.lastExternalTickT = this.state.t;
      this.pendingExternalDtSec = 0;
    }
  }

  /** Advance the external-clock-driven song time by dt seconds (accumulated until tick()). */
  pushExternalClockDelta(dtSec: number): void {
    if (!Number.isFinite(dtSec) || dtSec <= 0) return;
    // Avoid runaway accumulation.
    this.pendingExternalDtSec = Math.min(1.0, this.pendingExternalDtSec + dtSec);
  }

  /** Update external clock tempo (already includes any user tempo_scale). */
  setExternalClockBpm(bpm: number): void {
    if (!Number.isFinite(bpm) || bpm <= 0) return;
    this.externalBpm = bpm;
  }

  /** Best-effort seek from external SPP or other transport message. */
  seekFromExternalClock(t: number): void {
    this.seek(t);
  }

  getPlaybackRate(): number {
    return this.state.playbackRate;
  }

  setLoop(loop?: { t0: number; t1: number }): void {
    if (!loop) {
      this.state = { ...this.state, loop: undefined };
      this.timebase.setLoop(undefined);
      return;
    }

    const clamped = clampLoop(loop);
    this.state = { ...this.state, loop: clamped };
    this.timebase.setLoop(clamped);

    // Clamp current time into loop.
    if (this.state.t < clamped.t0 || this.state.t > clamped.t1) {
      this.seek(clamped.t0);
    }
  }

  async play(): Promise<void> {
    await this.timebase.play();
    this.state = { ...this.state, isPlaying: true };
  }

  pause(): void {
    this.timebase.pause();
    this.state = { ...this.state, isPlaying: false };
  }

  stop(): void {
    this.timebase.stop();
    this.simT = 0;
    this.state = { ...this.state, t: 0, isPlaying: false };
  }

  seek(t: number): void {
    const tClamped = clampToLoop(Math.max(0, t), this.state.loop);
    this.timebase.seek(tClamped);
    this.simT = tClamped;
    this.state = { ...this.state, t: tClamped };
  }

  /**
   * Advance transport state.
   *
   * Call this once per frame. Returns the current TransportState.
   */
  tick(dt: number): TransportState {
    // If configured, follow external clock for time progression.
    // This path overrides the simulated clock and also *pauses* audio timebase from being authoritative.
    // (We still allow audio to be loaded for sound; time is driven by external clock.)
    if (this.followExternalClock && this.externalRunning && this.externalBpm != null) {
      const rate = this.state.playbackRate || 1;
      // External clock BPM already represents the *effective* tempo; playbackRate is applied to audio only.
      // For time progression, use dt directly.

      // Advance time. Prefer externally-provided delta (MIDI clock tick timing),
      // falling back to frame dt if no tick events are wired.
      const adv = this.pendingExternalDtSec > 0 ? this.pendingExternalDtSec : dt;
      this.pendingExternalDtSec = 0;

      this.simT = this.state.t;
      this.simT += adv;

      // Enforce loop.
      const loop = this.state.loop;
      if (loop) {
        if (this.simT >= loop.t1) this.simT = loop.t0;
        this.simT = clampToLoop(this.simT, loop);
      }

      // Keep state bpm in sync so metronome/visualizers align.
      this.state = {
        ...this.state,
        t: this.simT,
        isPlaying: true,
        bpm: this.externalBpm,
        playbackRate: rate
      };

      return this.state;
    }

    const duration = this.timebase.getDurationSec();
    const hasAudio = duration != null && duration > 0;

    if (hasAudio) {
      const audioT = this.timebase.getCurrentTimeSec();
      const isPlaying = this.timebase.getIsPlaying();
      const playbackRate = this.timebase.getPlaybackRate();

      let t = clampToLoop(audioT, this.state.loop);

      // Enforce loop by seeking back to loop start.
      const loop = this.state.loop;
      if (loop && audioT >= loop.t1 && isPlaying) {
        this.timebase.seek(loop.t0);
        t = loop.t0;
      }

      this.state = { ...this.state, t, isPlaying, playbackRate };
      return this.state;
    }

    // Simulated clock.
    if (this.state.isPlaying) {
      this.simT += dt * (this.state.playbackRate || 1);
    }

    const loop = this.state.loop;
    if (loop) {
      if (this.simT >= loop.t1) this.simT = loop.t0;
      this.simT = clampToLoop(this.simT, loop);
    }

    this.state = { ...this.state, t: this.simT, playbackRate: this.timebase.getPlaybackRate() };
    return this.state;
  }
}
