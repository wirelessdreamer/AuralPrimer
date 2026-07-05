/**
 * Beat-grid math for the Cleanup/Edit editor's quantized placement.
 *
 * The pack's `song_timeline.json` gives beat times (and their measure, so we can
 * mark downbeats). A quant level subdivides each beat interval; placing/moving a
 * note snaps its onset to the nearest subdivision. Tempo changes are handled for
 * free because we subdivide each real beat interval rather than assume a fixed
 * BPM. Pure + unit-tested; the editor just calls these.
 */

export interface QuantLevel {
  /** Stable id used as the <select> option value. */
  value: string;
  label: string;
  /** Subdivisions per beat; `null` = quantization off. */
  perBeat: number | null;
}

/** Dropdown options, coarse → fine, plus triplets. Default is 1/16. */
export const QUANT_LEVELS: QuantLevel[] = [
  { value: "off", label: "Off", perBeat: null },
  { value: "1/4", label: "1/4", perBeat: 1 },
  { value: "1/8", label: "1/8", perBeat: 2 },
  { value: "1/8t", label: "1/8T", perBeat: 3 },
  { value: "1/16", label: "1/16", perBeat: 4 },
  { value: "1/16t", label: "1/16T", perBeat: 6 },
  { value: "1/32", label: "1/32", perBeat: 8 },
];

export const DEFAULT_QUANT_VALUE = "1/16";

export function quantLevelByValue(value: string): QuantLevel | undefined {
  return QUANT_LEVELS.find((q) => q.value === value);
}

/**
 * Grid (snap-target) times: each beat interval split into `perBeat` steps, with
 * the final beat's interval extrapolated to `durationSec` so the tail of the
 * song is still on-grid. Returns a sorted, de-duplicated array. `perBeat <= 0`
 * or fewer than 2 beats yields the beats themselves (no subdivision).
 */
export function buildGridTimes(beatTimes: number[], perBeat: number, durationSec: number): number[] {
  const beats = [...beatTimes].filter((t) => Number.isFinite(t)).sort((a, b) => a - b);
  if (beats.length === 0) return [];
  if (beats.length < 2 || perBeat <= 1) {
    return beats.slice();
  }
  const out: number[] = [];
  const end = durationSec > 0 ? durationSec : beats[beats.length - 1]!;
  for (let i = 0; i < beats.length; i += 1) {
    const b = beats[i]!;
    const interval = i < beats.length - 1 ? beats[i + 1]! - b : b - beats[i - 1]!;
    if (!(interval > 0)) {
      out.push(b);
      continue;
    }
    const step = interval / perBeat;
    const count = i < beats.length - 1 ? perBeat : Math.max(1, Math.ceil((end - b) / step) + 1);
    for (let k = 0; k < count; k += 1) {
      const t = b + k * step;
      if (t > end + 1e-6) break;
      out.push(t);
    }
  }
  out.sort((a, b) => a - b);
  // De-dup adjacent (a beat's k=0 equals the previous interval's last point).
  const dedup: number[] = [];
  for (const t of out) {
    if (dedup.length === 0 || t - dedup[dedup.length - 1]! > 1e-6) dedup.push(t);
  }
  return dedup;
}

/** Snap `t` to the nearest grid time (binary search). Returns `t` if the grid is empty. */
export function snapTimeToGrid(t: number, gridTimes: number[]): number {
  if (gridTimes.length === 0) return t;
  let lo = 0;
  let hi = gridTimes.length - 1;
  if (t <= gridTimes[0]!) return gridTimes[0]!;
  if (t >= gridTimes[hi]!) return gridTimes[hi]!;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (gridTimes[mid]! < t) lo = mid + 1;
    else hi = mid - 1;
  }
  // lo is the first index with gridTimes[lo] >= t; nearest is lo or lo-1.
  const hiT = gridTimes[lo]!;
  const loT = gridTimes[lo - 1]!;
  return t - loT <= hiT - t ? loT : hiT;
}

/** Downbeat times from a `song_timeline.json` beats array (first beat of each measure). */
export function downbeatTimes(beats: Array<{ time?: number; measure?: number }>): number[] {
  const out: number[] = [];
  let prevMeasure: number | undefined;
  for (const b of beats) {
    if (typeof b?.time !== "number") continue;
    if (prevMeasure === undefined || b.measure !== prevMeasure) out.push(b.time);
    prevMeasure = b.measure;
  }
  return out;
}
