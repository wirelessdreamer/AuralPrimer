import type { TransportState } from "@auralprimer/viz-sdk";

export type MetronomeOptions = {
  /** Whether metronome is enabled. */
  enabled?: boolean;
  /** Volume 0..1 */
  volume?: number;
  /** Seconds of audio-context time to schedule ahead. */
  scheduleAheadSec?: number;
  /** Provide an AudioContext (useful for tests / integration). */
  audioContext?: AudioContext;
};

export function beatDurationSec(bpm: number): number {
  if (!Number.isFinite(bpm) || bpm <= 0) return 0.5;
  return 60 / bpm;
}

function quartersPerBar(timeSignature: [number, number]): number {
  const [n, d] = timeSignature;
  if (!Number.isFinite(n) || !Number.isFinite(d) || n <= 0 || d <= 0) return 4;
  return n * (4 / d);
}

function almostEqual(a: number, b: number, eps: number): boolean {
  return Math.abs(a - b) <= eps;
}

function isDownbeat(songT: number, bpm: number, timeSignature: [number, number]): boolean {
  const qpb = quartersPerBar(timeSignature);
  const beatDur = beatDurationSec(bpm);
  const barDur = beatDur * qpb;
  if (!(barDur > 0)) return false;
  const barIdx = Math.round(songT / barDur);
  return almostEqual(songT, barIdx * barDur, 1e-3);
}

/**
 * WebAudio metronome scheduler driven by the host TransportState.
 *
 * The scheduler runs on each animation frame and schedules short "click" beeps
 * slightly ahead of time using an AudioContext.
 */
export class Metronome {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;

  private enabled = false;
  private volume = 0.25;
  private scheduleAheadSec = 0.2;

  // Keep user-provided context for tests/integration. If not provided, we lazily create
  // an AudioContext only when the metronome is enabled.
  private readonly providedCtx: AudioContext | undefined;

  private nextClickSongT: number | null = null;
  private lastTransportT: number | null = null;
  private lastPlaying = false;

  /** Keep references so we can stop on reset (best-effort). */
  private scheduled: OscillatorNode[] = [];

  constructor(opts: MetronomeOptions = {}) {
    this.providedCtx = opts.audioContext;

    this.enabled = !!opts.enabled;
    if (typeof opts.volume === "number") this.setVolume(opts.volume);
    if (typeof opts.scheduleAheadSec === "number") this.scheduleAheadSec = opts.scheduleAheadSec;

    // IMPORTANT: we intentionally do not create a WebAudio context unless enabled.
    // (Desktop app prefers Rust native audio; WebAudio is only used for optional metronome beeps.)
    if (this.enabled) this.ensureContext();
  }

  private ensureContext(): void {
    if (this.ctx && this.gain) return;
    this.ctx = this.providedCtx ?? new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 1;
    this.gain.connect(this.ctx.destination);
  }

  setEnabled(v: boolean): void {
    this.enabled = v;
    if (!v) {
      this.reset();
      // If we created our own context, shut it down so the desktop app can remain
      // "no web audio" unless metronome is explicitly enabled.
      if (this.ctx && !this.providedCtx) {
        void this.ctx.close();
      }
      if (!this.providedCtx) {
        this.ctx = null;
        this.gain = null;
      }
      return;
    }

    // Enabled: ensure WebAudio is available.
    this.ensureContext();
  }

  getEnabled(): boolean {
    return this.enabled;
  }

  setVolume(v: number): void {
    const vv = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.25;
    this.volume = vv;
  }

  getVolume(): number {
    return this.volume;
  }

  /**
   * Called once per frame with the latest transport state.
   */
  update(state: TransportState): void {
    if (!this.enabled) return;

    this.ensureContext();
    const ctx = this.ctx;
    if (!ctx) return;

    const isPlaying = state.isPlaying;
    const t = state.t;
    const bpm = state.bpm;
    const rate = state.playbackRate || 1;

    // When transport stops/pauses, clear pending scheduling.
    if (!isPlaying) {
      if (this.lastPlaying) this.reset();
      this.lastPlaying = false;
      this.lastTransportT = t;
      return;
    }

    // Ensure audio context is running.
    if (ctx.state === "suspended") {
      void ctx.resume();
    }

    // Detect discontinuities (seek, stop->play, loop wrap).
    const lastT = this.lastTransportT;
    const discontinuity =
      lastT == null ||
      t + 1e-4 < lastT ||
      // big jump forward (seek)
      t - lastT > 1.0;

    if (discontinuity || !this.lastPlaying || this.nextClickSongT == null) {
      this.nextClickSongT = computeNextClickSongT(t, bpm);
    }

    this.lastPlaying = true;
    this.lastTransportT = t;

    // Schedule clicks in a window ahead of current time.
    const windowSongSec = this.scheduleAheadSec * rate;
    const endSongT = advanceSongTimeWithLoop(t, windowSongSec, state.loop);

    // If loop exists and window crosses loop boundary, schedule in two segments.
    if (state.loop && t + windowSongSec >= state.loop.t1) {
      this.scheduleSegment(state, t, state.loop.t1, rate);
      const remaining = t + windowSongSec - state.loop.t1;
      this.scheduleSegment(state, state.loop.t0, state.loop.t0 + remaining, rate);
    } else {
      this.scheduleSegment(state, t, endSongT, rate);
    }
  }

  dispose(): void {
    this.reset();
    if (this.ctx && !this.providedCtx) {
      void this.ctx.close();
    }
    this.ctx = null;
    this.gain = null;
  }

  private scheduleSegment(state: TransportState, segStartSongT: number, segEndSongT: number, rate: number) {
    const ctx = this.ctx;
    if (!ctx) return;
    if (this.nextClickSongT == null) return;

    const beatDur = beatDurationSec(state.bpm);
    if (!(beatDur > 0)) return;

    // Ensure we don't schedule behind the segment.
    if (this.nextClickSongT + 1e-6 < segStartSongT) {
      this.nextClickSongT = computeNextClickSongT(segStartSongT, state.bpm);
    }

    while (this.nextClickSongT <= segEndSongT + 1e-6) {
      const clickSongT = this.nextClickSongT;
      const ctxWhen = ctx.currentTime + (clickSongT - state.t) / rate;

      this.scheduleClick(ctxWhen, isDownbeat(clickSongT, state.bpm, state.timeSignature));

      this.nextClickSongT += beatDur;
    }

    // Prevent drift due to floating point by snapping to beat grid occasionally.
    const beatIndex = Math.round(this.nextClickSongT / beatDur);
    this.nextClickSongT = beatIndex * beatDur;
  }

  private scheduleClick(ctxTime: number, downbeat: boolean) {
    const ctx = this.ctx;
    const gain = this.gain;
    if (!ctx || !gain) return;

    // Don't schedule in the past.
    if (ctxTime < ctx.currentTime + 0.001) return;

    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = downbeat ? 880 : 440;

    const v = this.volume;
    g.gain.setValueAtTime(0, ctxTime);
    g.gain.linearRampToValueAtTime(v, ctxTime + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, ctxTime + 0.06);

    osc.connect(g);
    g.connect(gain);

    osc.start(ctxTime);
    osc.stop(ctxTime + 0.07);

    this.scheduled.push(osc);
    osc.onended = () => {
      this.scheduled = this.scheduled.filter((x) => x !== osc);
      try {
        osc.disconnect();
        g.disconnect();
      } catch {
        // ignore
      }
    };
  }

  private reset() {
    this.nextClickSongT = null;
    this.lastTransportT = null;

    // Best-effort cancel any already scheduled clicks.
    for (const osc of this.scheduled) {
      try {
        osc.onended = null;
        osc.stop();
      } catch {
        // ignore
      }
    }
    this.scheduled = [];
  }
}

export function computeNextClickSongT(currentSongT: number, bpm: number): number {
  const beatDur = beatDurationSec(bpm);
  if (!(beatDur > 0)) return currentSongT;
  const k = Math.ceil((currentSongT - 1e-6) / beatDur);
  return k * beatDur;
}

function advanceSongTimeWithLoop(t: number, dt: number, loop?: { t0: number; t1: number }): number {
  if (!loop) return t + dt;
  const len = loop.t1 - loop.t0;
  if (!(len > 0)) return loop.t0;

  let x = t + dt;
  if (x < loop.t0) x = loop.t0;
  if (x >= loop.t1) {
    x = loop.t0 + ((x - loop.t0) % len);
  }
  return x;
}
