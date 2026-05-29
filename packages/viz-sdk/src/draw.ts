/**
 * Shared, dependency-free drawing/scroll helpers for time-scrolling
 * visualizers (viz-drum-highway, viz-beats, viz-nashville).
 *
 * These centralize the scroll-window and beat-grid math each highway
 * previously duplicated. They are pure where possible: `clamp`,
 * `scrollWindow`, and `beatGridLines` return data with no side effects;
 * `roundRectPath` only builds a path on the supplied 2D context.
 *
 * The per-visualizer base pixels-per-second constant is NOT encoded here --
 * each viz passes its own `basePxPerSec` so their distinct densities (drum
 * highway's height-derived 190..320, beats' 120, nashville's 140) are
 * preserved.
 */

/** Clamp `n` to the inclusive range [lo, hi]. */
export function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/**
 * Trace a rounded-rectangle path on `ctx`. Does not fill or stroke -- the
 * caller decides. The corner radius is clamped so it never exceeds half the
 * smaller side (matching the highways' prior local helpers).
 */
export function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
): void {
  const rr = clamp(r, 0, Math.min(w, h) * 0.5);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}

export type ScrollWindowParams = {
  /**
   * Reference span in pixels along the scroll axis -- the distance one full
   * visible window occupies. Horizontal highways pass `width - originX`;
   * the vertical drum highway passes its travel-distance in pixels. Named
   * `heightPx` for the SDK contract; it is simply "the reference dimension".
   */
  heightPx: number;
  /** Per-visualizer base pixels-per-second at scrollMul = 1.0. */
  basePxPerSec: number;
  /** Clamped host scroll-speed multiplier (see clampScrollSpeedMultiplier). */
  scrollMul: number;
};

export type ScrollWindow = {
  /** Effective pixels-per-second after applying the multiplier. */
  pxPerSec: number;
  /** Seconds of song time visible across `heightPx`. */
  windowSec: number;
};

/**
 * Derive the effective scroll density and visible window from a base
 * px/sec, the host multiplier, and a reference pixel span.
 *
 * A higher `scrollMul` raises `pxPerSec` and (because the same pixel span
 * now holds fewer seconds) shrinks `windowSec` -- spreading notes further
 * apart on screen. Tempo-lock is preserved: only pixel density changes, not
 * note timing.
 */
export function scrollWindow({ heightPx, basePxPerSec, scrollMul }: ScrollWindowParams): ScrollWindow {
  const pxPerSec = basePxPerSec * scrollMul;
  const windowSec = heightPx / pxPerSec;
  return { pxPerSec, windowSec };
}

export type BeatGridParams = {
  /** Current song time in seconds (start of the visible window). */
  t: number;
  /** Seconds of song time visible across the window. */
  windowSec: number;
  /** Quarter-notes per minute. */
  bpm: number;
  /** Beats per bar (numerator of the time signature). */
  beatsPerBar: number;
};

export type BeatGridLine = {
  /** Absolute song time of this beat line, in seconds. */
  tSec: number;
  /** Fraction 0..1 of this line across the visible window. */
  x01: number;
  /** True when this beat is the downbeat (start) of a bar. */
  isDownbeat: boolean;
  /** Index of the bar this beat belongs to. */
  barIndex: number;
};

/**
 * Compute the beat gridlines visible in `[t, t + windowSec]`.
 *
 * One entry per beat, ordered ascending. `x01` is the line's fractional
 * position across the window (0 at `t`, 1 at `t + windowSec`). `isDownbeat`
 * marks bar starts; `barIndex` is the containing bar. This is the single
 * source the time-scrolling highways consume for their gridline math.
 */
export function beatGridLines({ t, windowSec, bpm, beatsPerBar }: BeatGridParams): BeatGridLine[] {
  const secPerBeat = 60 / Math.max(1e-6, bpm);
  const bars = Math.max(1, beatsPerBar);
  const t1 = t + windowSec;

  const firstBeat = Math.floor(t / secPerBeat);
  const lastBeat = Math.ceil(t1 / secPerBeat);

  const lines: BeatGridLine[] = [];
  for (let b = firstBeat; b <= lastBeat; b += 1) {
    const tSec = b * secPerBeat;
    const x01 = (tSec - t) / windowSec;
    const barIndex = Math.floor(b / bars);
    const isDownbeat = ((b % bars) + bars) % bars === 0;
    lines.push({ tSec, x01, isDownbeat, barIndex });
  }
  return lines;
}
