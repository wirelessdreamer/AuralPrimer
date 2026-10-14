/**
 * Power chords, read as shapes.
 *
 * A power chord is a root and its perfect fifth, sometimes the octave too, on
 * neighbouring strings. Drawn note by note it is two or three numbers piled
 * on each other; what a player needs is the root and the shape, so the views
 * stamp one tile per chord and name the shape by the root's string: P6 with
 * the root on the 6th (lowest) string, P5 on the 5th, P4 on the 4th.
 *
 * The key that explains the shapes fades once each shape in the section has
 * gone by a few times, and can be pinned up.
 */

import type { FretChart, FretChord } from "./fretted";

/** A power chord's shape: where its root is, and whether it has the octave. */
export type PowerShape = {
  /** "P6", "P5", "P4"...: the root's string, counted from the highest. */
  code: string;
  /** The root's string, 0 = lowest. */
  rootString: number;
  rootFret: number;
  rootMidi: number;
  octave: boolean;
};

/** A struck note, as much of it as a shape needs. */
type ShapeNote = { s: number; f: number; midi: number };

/**
 * The power chord a group of notes struck together makes, or null: two or
 * three notes on adjacent strings, the lowest the root, then its perfect
 * fifth, then (optionally) its octave.
 */
export function powerChordOf(notes: ReadonlyArray<ShapeNote>, stringCount: number): PowerShape | null {
  if (notes.length < 2 || notes.length > 3) return null;
  const byString = [...notes].sort((a, b) => a.s - b.s);
  for (let i = 1; i < byString.length; i += 1) {
    if (byString[i].s !== byString[i - 1].s + 1) return null;
  }
  const root = byString[0];
  if (byString[1].midi - root.midi !== 7) return null;
  if (byString.length === 3 && byString[2].midi - root.midi !== 12) return null;
  return {
    code: `P${stringCount - root.s}`,
    rootString: root.s,
    rootFret: root.f,
    rootMidi: root.midi,
    octave: byString.length === 3,
  };
}

/** Every struck chord in the chart that is a power chord, with its shape. */
export function powerChordsIn(chart: FretChart): Map<FretChord, PowerShape> {
  const members = new Map<FretChord, ShapeNote[]>();
  for (const x of chart.notes) {
    if (!x.chord) continue;
    const list = members.get(x.chord);
    if (list) list.push(x);
    else members.set(x.chord, [x]);
  }
  const out = new Map<FretChord, PowerShape>();
  for (const [chord, notes] of members) {
    const shape = powerChordOf(notes, chart.n);
    if (shape) out.set(chord, shape);
  }
  return out;
}

/** "6th", "5th", "4th", "3rd", "2nd", "1st". */
function ordinal(n: number): string {
  return `${n}${n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th"}`;
}

/** "P6 power chord". */
export function shapeName(code: string): string {
  return `${code} power chord`;
}

/** "root on 6th · 1 5 8": where the root is, and the chord's degrees. */
export function shapeDetail(shape: PowerShape, stringCount: number): string {
  return `root on ${ordinal(stringCount - shape.rootString)} · ${shape.octave ? "1 5 8" : "1 5"}`;
}

/** How many times each shape goes by before the key that explains it fades. */
export const SHAPE_KEY_FADE_AFTER = 3;

/** A step of the playhead longer than this is a jump, not play (seconds). */
const SHAPE_KEY_MAX_STEP_SEC = 0.5;

/**
 * The shape key's state: how often each shape has gone past the playhead,
 * and whether the player pinned the key up.
 */
export class ShapeKey {
  private readonly counts = new Map<string, number>();
  private lastT = Number.NEGATIVE_INFINITY;
  private pinned = false;

  /** Times a shape has gone past the playhead. */
  seen(code: string): number {
    return this.counts.get(code) ?? 0;
  }

  /**
   * Count the shapes whose onsets the playhead crossed since the last call.
   * Only play counts: a jump (opening the song part way in, a seek, a loop)
   * counts nothing, so a shape is not "seen" because the playhead skipped
   * over it. Crossing it again later counts it again.
   */
  observe(events: ReadonlyArray<{ t: number; code: string }>, now: number): void {
    if (now > this.lastT && now - this.lastT <= SHAPE_KEY_MAX_STEP_SEC) {
      for (const e of events) {
        if (e.t > this.lastT && e.t <= now) this.counts.set(e.code, this.seen(e.code) + 1);
      }
    }
    this.lastT = now;
  }

  /** Pin or unpin the key (K); returns whether it is now pinned. */
  togglePin(): boolean {
    this.pinned = !this.pinned;
    return this.pinned;
  }

  isPinned(): boolean {
    return this.pinned;
  }

  /** Whether the key should be up for a section holding these shapes. */
  shown(codes: ReadonlyArray<string>): boolean {
    if (codes.length === 0) return false;
    return this.pinned || codes.some((c) => this.seen(c) < SHAPE_KEY_FADE_AFTER);
  }
}

/** Closer than this behind the same shape on the same fret, a power chord is a repeat (seconds). */
const SHAPE_REPEAT_GAP_SEC = 0.45;

/**
 * For each power chord in time order, whether it repeats the one before:
 * the same shape on the same fret, close behind. A chugged riff is one
 * shape struck again and again; the lane stamps the first of a run and
 * marks the rest, rather than piling tiles on each other.
 */
export function repeatsPrevious(events: ReadonlyArray<{ t: number; code: string; shape: PowerShape }>): boolean[] {
  return events.map((e, i) => {
    const prev = events[i - 1];
    return prev !== undefined && prev.code === e.code && prev.shape.rootFret === e.shape.rootFret && e.t - prev.t <= SHAPE_REPEAT_GAP_SEC;
  });
}
