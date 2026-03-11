import type { TransportTimebase } from "./audioBackend";
import { clampLoop } from "./audioBackend";

/**
 * WebAudio-based TransportTimebase.
 *
 * Notes:
 * - We decode the provided Blob into an AudioBuffer.
 * - Playback is via AudioBufferSourceNode, which is one-shot, so we recreate the
 *   source node on each play/seek.
 * - We compute current time as (context.currentTime - startedAt) + startOffset.
 * - Loop region is enforced using AudioBufferSourceNode.loop* for sample-accurate loops.
 */
export class WebAudioTimebase implements TransportTimebase {
  private ctx: AudioContext;
  private buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  private gain: GainNode;

  private isPlaying = false;

  /** Position when not playing (seconds). */
  private offsetSec = 0;
  /** context.currentTime when last started (seconds). */
  private startedAtCtxSec = 0;

  private loop: { t0: number; t1: number } | undefined;
  private playbackRate = 1;

  constructor(opts: { audioContext?: AudioContext } = {}) {
    this.ctx = opts.audioContext ?? new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 1;
    this.gain.connect(this.ctx.destination);
  }

  async load(source: { blob: Blob; mime: string }): Promise<void> {
    // Decode audio into a buffer.
    const ab = await source.blob.arrayBuffer();

    // Some browsers require resume before decode; do best-effort.
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }

    this.buffer = await this.ctx.decodeAudioData(ab.slice(0));

    // Reset transport.
    this.stop();
  }

  async play(): Promise<void> {
    if (!this.buffer) throw new Error("no audio loaded");

    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }

    if (this.isPlaying) return;

    this.startedAtCtxSec = this.ctx.currentTime;
    this.source = this.ctx.createBufferSource();
    this.source.buffer = this.buffer;
    this.source.playbackRate.value = this.playbackRate;

    // Configure looping.
    const loop = this.loop;
    if (loop) {
      this.source.loop = true;
      this.source.loopStart = loop.t0;
      this.source.loopEnd = loop.t1;

      // Clamp offset into loop so first start is valid.
      if (this.offsetSec < loop.t0 || this.offsetSec > loop.t1) {
        this.offsetSec = loop.t0;
      }
    } else {
      this.source.loop = false;
    }

    this.source.connect(this.gain);

    // When ended (no loop), transition to paused state.
    this.source.onended = () => {
      // When looping, onended is not generally called, but keep it safe.
      if (!this.isPlaying) return;
      this.isPlaying = false;
      this.offsetSec = this.getCurrentTimeSec();
      this.cleanupSource();
    };

    this.source.start(0, this.offsetSec);
    this.isPlaying = true;
  }

  pause(): void {
    if (!this.isPlaying) return;
    this.offsetSec = this.getCurrentTimeSec();
    this.isPlaying = false;
    this.stopSourceNode();
  }

  stop(): void {
    this.offsetSec = 0;
    this.isPlaying = false;
    this.stopSourceNode();
  }

  seek(tSec: number): void {
    this.offsetSec = Math.max(0, tSec);

    // If playing, restart at new offset.
    if (this.isPlaying) {
      this.stopSourceNode();
      // Mark playing false, then re-play (which will rebuild node).
      this.isPlaying = false;
      // Fire and forget; caller expects sync-ish.
      void this.play();
    }
  }

  setLoop(loop?: { t0: number; t1: number }): void {
    this.loop = loop ? clampLoop(loop) : undefined;

    // If playing, restart so new loop params apply.
    if (this.isPlaying) {
      this.seek(this.getCurrentTimeSec());
    }
  }

  setPlaybackRate(rate: number): void {
    const r = Number.isFinite(rate) && rate > 0 ? rate : 1;

    // If we're currently playing, adjust offset so song time stays continuous
    // across the rate change.
    if (this.isPlaying) {
      const songNow = this.getCurrentTimeSec();
      this.offsetSec = songNow;
      this.startedAtCtxSec = this.ctx.currentTime;
    }

    this.playbackRate = r;

    if (this.source) {
      this.source.playbackRate.value = r;
    }
  }

  getPlaybackRate(): number {
    return this.playbackRate;
  }

  getDurationSec(): number | null {
    return this.buffer ? this.buffer.duration : null;
  }

  getCurrentTimeSec(): number {
    if (!this.buffer) return 0;

    if (!this.isPlaying) {
      return this.offsetSec;
    }

    const playedCtxSec = this.ctx.currentTime - this.startedAtCtxSec;
    let t = this.offsetSec + playedCtxSec * this.playbackRate;

    // If we have a loop region, wrap into it for a stable time.
    const loop = this.loop;
    if (loop) {
      const len = loop.t1 - loop.t0;
      if (len > 0 && t >= loop.t1) {
        t = loop.t0 + ((t - loop.t0) % len);
      }
      // Clamp just in case of numeric drift.
      if (t < loop.t0) t = loop.t0;
      if (t > loop.t1) t = loop.t1;
    }

    // Clamp to buffer duration.
    if (t < 0) t = 0;
    if (t > this.buffer.duration) t = this.buffer.duration;

    return t;
  }

  getIsPlaying(): boolean {
    return this.isPlaying;
  }

  getOutputLatencySec(): number {
    // outputLatency isn’t supported everywhere; fall back to 0.
    const anyCtx = this.ctx as any;
    const ol = typeof anyCtx.outputLatency === "number" ? (anyCtx.outputLatency as number) : 0;
    return Number.isFinite(ol) && ol >= 0 ? ol : 0;
  }

  dispose(): void {
    this.stop();
    // Close only if we created the context.
    void this.ctx.close();
  }

  private stopSourceNode() {
    if (!this.source) return;
    try {
      this.source.onended = null;
      this.source.stop();
    } catch {
      // ignore
    }
    this.cleanupSource();
  }

  private cleanupSource() {
    if (!this.source) return;
    try {
      this.source.disconnect();
    } catch {
      // ignore
    }
    this.source = null;
  }
}
