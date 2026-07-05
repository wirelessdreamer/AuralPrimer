/**
 * A metronome that clicks on an EXPLICIT list of beat times (the Cleanup/Edit
 * editor's detected beat grid), accenting the downbeats ("the one").
 *
 * Unlike the uniform-BPM `Metronome`, this follows the real, non-uniform detected
 * beats — so it doubles as an audible alignment test: with the grid correct the
 * clicks land with the music, and any drift is instantly hearable. It is driven
 * per frame by the editor's AUDIBLE song time (the same latency/calibration-
 * compensated clock the playhead uses), and it compensates its own WebAudio
 * output latency, so the click reaches the speakers exactly when the stem hits
 * the beat rather than a few ms late.
 */

export interface GridBeat {
  t: number;
  accent: boolean;
}

/** First index i with grid[i].t >= t (grid sorted ascending). Binary search. */
export function firstBeatAtOrAfter(grid: GridBeat[], t: number): number {
  let lo = 0;
  let hi = grid.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (grid[m]!.t < t - 1e-6) lo = m + 1;
    else hi = m;
  }
  return lo;
}

/**
 * Beats due in the look-ahead window [songT, songT + aheadSong], scanning forward
 * from `fromIdx`. Beats already passed (before songT) are skipped but still
 * advance the index so they never fire late. Returns the due beats and the next
 * index to resume from. Pure — the scheduler layers WebAudio timing on top.
 */
export function selectDueBeats(
  grid: GridBeat[],
  fromIdx: number,
  songT: number,
  aheadSong: number,
): { due: GridBeat[]; nextIdx: number } {
  const due: GridBeat[] = [];
  let i = Math.max(0, fromIdx);
  const end = songT + aheadSong;
  while (i < grid.length && grid[i]!.t <= end + 1e-6) {
    if (grid[i]!.t >= songT - 1e-6) due.push(grid[i]!);
    i += 1;
  }
  return { due, nextIdx: i };
}

/** Merge beat + downbeat time lists into a sorted GridBeat[] (downbeats accented). */
export function buildGridBeats(beatTimes: number[], downbeatTimes: number[]): GridBeat[] {
  const downs = new Set(downbeatTimes.filter((t) => Number.isFinite(t)));
  return beatTimes
    .filter((t) => Number.isFinite(t))
    .sort((a, b) => a - b)
    .map((t) => ({ t, accent: downs.has(t) }));
}

function clamp01(v: number): number {
  return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
}

export interface GridMetronomeOptions {
  /** Normal (off-beat) click volume 0..1. */
  volume?: number;
  /** Downbeat ("one") click volume 0..1. */
  accentVolume?: number;
  /** Seconds of audio-context time to schedule ahead. */
  scheduleAheadSec?: number;
  /** Provide an AudioContext (tests / integration). */
  audioContext?: AudioContext;
}

export class GridMetronome {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private readonly providedCtx?: AudioContext;

  private enabled = false;
  private volume = 0.3;
  private accentVolume = 0.5;
  private scheduleAheadSec = 0.2;

  private grid: GridBeat[] = [];
  private nextIdx = 0;
  private lastSongT: number | null = null;
  private scheduled: OscillatorNode[] = [];

  constructor(opts: GridMetronomeOptions = {}) {
    this.providedCtx = opts.audioContext;
    if (typeof opts.volume === "number") this.volume = clamp01(opts.volume);
    if (typeof opts.accentVolume === "number") this.accentVolume = clamp01(opts.accentVolume);
    if (typeof opts.scheduleAheadSec === "number") this.scheduleAheadSec = opts.scheduleAheadSec;
  }

  private ensureContext(): void {
    if (this.ctx && this.gain) return;
    this.ctx = this.providedCtx ?? new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 1;
    this.gain.connect(this.ctx.destination);
  }

  /** Replace the beat grid the metronome clicks on (from the editor's setBeatGrid). */
  setBeats(beatTimes: number[], downbeatTimes: number[]): void {
    this.grid = buildGridBeats(beatTimes, downbeatTimes);
    this.nextIdx = 0;
    this.lastSongT = null;
  }

  setEnabled(v: boolean): void {
    this.enabled = v;
    if (!v) {
      this.reset();
      // Keep the desktop app "no WebAudio unless asked": tear the context down.
      if (this.ctx && !this.providedCtx) {
        void this.ctx.close();
        this.ctx = null;
        this.gain = null;
      }
      return;
    }
    this.ensureContext();
  }

  getEnabled(): boolean {
    return this.enabled;
  }

  /**
   * Per-frame update. `songT` is the audible song position (sec), `isPlaying`
   * the transport state, `playbackRate` the speed multiplier. Schedules any
   * clicks due within the look-ahead window.
   */
  update(songT: number, isPlaying: boolean, playbackRate: number): void {
    if (!this.enabled) return;
    if (!isPlaying) {
      if (this.lastSongT != null) this.reset();
      this.lastSongT = null;
      return;
    }
    this.ensureContext();
    const ctx = this.ctx;
    if (!ctx) return;
    if (ctx.state === "suspended") void ctx.resume();

    const rate = playbackRate > 0 ? playbackRate : 1;

    // Seek / stop->play / loop wrap: re-find our place and drop pending clicks.
    if (this.lastSongT == null || songT + 1e-4 < this.lastSongT || songT - this.lastSongT > 1.0) {
      this.reset();
      this.nextIdx = firstBeatAtOrAfter(this.grid, songT);
    }
    this.lastSongT = songT;

    const { due, nextIdx } = selectDueBeats(this.grid, this.nextIdx, songT, this.scheduleAheadSec * rate);
    this.nextIdx = nextIdx;

    // baseLatency is the WebAudio buffer delay before a scheduled sound is
    // audible; subtract it so the click hits the speakers on the beat, not late.
    const outLat = (ctx.outputLatency || ctx.baseLatency || 0) as number;
    for (const b of due) {
      const when = ctx.currentTime + (b.t - songT) / rate - outLat;
      this.scheduleClick(when, b.accent);
    }
  }

  /** Cancel pending clicks (call on pause/stop). */
  stop(): void {
    this.reset();
    this.lastSongT = null;
  }

  dispose(): void {
    this.reset();
    if (this.ctx && !this.providedCtx) void this.ctx.close();
    this.ctx = null;
    this.gain = null;
  }

  private scheduleClick(ctxTime: number, accent: boolean): void {
    const ctx = this.ctx;
    const gain = this.gain;
    if (!ctx || !gain) return;
    if (ctxTime < ctx.currentTime + 0.0005) return; // too imminent / in the past

    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = accent ? 1600 : 800; // the "one" is an octave up
    const v = accent ? this.accentVolume : this.volume;
    g.gain.setValueAtTime(0, ctxTime);
    g.gain.linearRampToValueAtTime(v, ctxTime + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, ctxTime + 0.05);
    osc.connect(g);
    g.connect(gain);
    osc.start(ctxTime);
    osc.stop(ctxTime + 0.06);

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

  private reset(): void {
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
