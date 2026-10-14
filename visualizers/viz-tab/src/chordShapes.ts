/**
 * Chords, read as shapes.
 *
 * Drawn note by note, a chord is a pile of numbers falling together; what a
 * player needs is its root and its shape. The chord's form comes from its
 * intervals (major, minor, diminished, augmented, sus, sixths, sevenths,
 * add 9...), its shape from where the notes sit on the neck, and the views
 * stamp one tile per chord at the root.
 *
 * A power chord (a root and its perfect fifth, sometimes the octave, on
 * neighbouring strings) is named by its root's string: P6 with the root on
 * the 6th (lowest) string, P5 on the 5th, P4 on the 4th. Other forms carry
 * the root's string the same way: R6, R5, R4.
 *
 * The key that explains the shapes fades once each shape in the section has
 * gone by a few times, and can be pinned up.
 */

import { analyzeChord, chordFormDegrees, chordFormName } from "@auralprimer/core-music";
import type { FretChart, FretChord } from "./fretted";

/** A struck note, as much of it as a shape needs. */
type ShapeNote = { s: number; f: number; midi: number };

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

/** A chord's shape: its form, its root, and its notes on the neck. */
export type ChordShape = {
  /** Which shape this is, for the key and for spotting repeats: the form and where the notes sit relative to the root. */
  key: string;
  /** "P6" for a power chord, else "R5" and so on: the root's string, counted from the highest. */
  code: string;
  /** The form's suffix: "" major, "m", "dim", "aug", "7", "add9", "5"... */
  form: string;
  power: boolean;
  /** The root's string, 0 = lowest: the lowest string playing the root. */
  rootString: number;
  rootFret: number;
  rootMidi: number;
  /** The lowest note; not the root in an inversion. */
  bassMidi: number;
  /** The form's degrees, as played: "1 3 5", "1 5 8". */
  degrees: string;
  /** In an inversion, the bass's degree ("3"); else null. */
  bassDegree: string | null;
  /** The notes, lowest string first; the root's pitch class marked. */
  notes: { s: number; f: number; root: boolean }[];
};

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

const mod12 = (n: number): number => ((n % 12) + 12) % 12;

/** Forms too thin to be a shape worth stamping: a bare third or fourth. */
const DYAD_FORMS = new Set(["(3)", "m(3)", "sus4"]);

/**
 * The shape a group of notes struck together makes, or null when no
 * standard form fits. A power chord must be the tight power-chord shape;
 * a bare third or fourth is not stamped.
 */
export function chordShapeOf(notes: ReadonlyArray<ShapeNote>, stringCount: number): ChordShape | null {
  if (notes.length < 2) return null;
  const byString = [...notes].sort((a, b) => a.s - b.s);
  const chord = analyzeChord(byString.map((x) => x.midi));
  if (!chord) return null;
  const classes = new Set(byString.map((x) => mod12(x.midi)));
  if (classes.size === 2 && DYAD_FORMS.has(chord.suffix)) return null;
  // A form with notes it does not explain is not that form.
  if (classes.size !== new Set(chord.intervals.map(mod12)).size) return null;

  const power = chord.suffix === "5";
  let degrees = chordFormDegrees(chord.suffix);
  if (power) {
    const p = powerChordOf(byString, stringCount);
    if (!p) return null;
    if (p.octave) degrees = "1 5 8";
  }

  const rootNote = byString.reduce((lo, x) => (mod12(x.midi) === chord.root && (lo === null || x.midi < lo.midi) ? x : lo), null as ShapeNote | null)!;
  const bassNote = byString.reduce((lo, x) => (x.midi < lo.midi ? x : lo));
  let bassDegree: string | null = null;
  if (chord.bass !== chord.root) {
    const i = chord.intervals.map(mod12).indexOf(mod12(chord.bass - chord.root));
    bassDegree = chordFormDegrees(chord.suffix).split(" ")[i] ?? null;
  }
  const code = `${power ? "P" : "R"}${stringCount - rootNote.s}`;
  // Where each string's note sits against the root's fret, lowest string
  // first: an open E and an F barre are the same shape a fret apart.
  const lo = byString[0].s;
  const hi = byString[byString.length - 1].s;
  const pattern: string[] = [];
  for (let s = lo; s <= hi; s += 1) {
    const x = byString.find((n) => n.s === s);
    pattern.push(x ? String(x.f - rootNote.f) : "x");
  }
  return {
    key: power ? code : `${chord.suffix}|${lo}|${pattern.join(",")}`,
    code,
    form: chord.suffix,
    power,
    rootString: rootNote.s,
    rootFret: rootNote.f,
    rootMidi: rootNote.midi,
    bassMidi: bassNote.midi,
    degrees,
    bassDegree,
    notes: byString.map((x) => ({ s: x.s, f: x.f, root: mod12(x.midi) === chord.root })),
  };
}

/** Every struck chord in the chart that makes a standard form, with its shape. Muted strikes are left out. */
export function chordShapesIn(chart: FretChart): Map<FretChord, ChordShape> {
  const members = new Map<FretChord, ShapeNote[]>();
  for (const x of chart.notes) {
    if (!x.chord || x.tech.mt) continue;
    const list = members.get(x.chord);
    if (list) list.push(x);
    else members.set(x.chord, [x]);
  }
  const out = new Map<FretChord, ChordShape>();
  for (const [chord, notes] of members) {
    const shape = chordShapeOf(notes, chart.n);
    if (shape) out.set(chord, shape);
  }
  return out;
}

/** "6th", "5th", "4th", "3rd", "2nd", "1st". */
function ordinal(n: number): string {
  return `${n}${n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th"}`;
}

/** "P6 power chord", "Minor 7th chord". */
export function shapeName(shape: ChordShape): string {
  if (shape.power) return `${shape.code} power chord`;
  const name = chordFormName(shape.form);
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} chord`;
}

/** "root on 6th · 1 5 8": where the root is, any other bass, and the chord's degrees. */
export function shapeDetail(shape: ChordShape, stringCount: number): string {
  const bass = shape.bassDegree ? `, ${shape.bassDegree} in bass` : "";
  return `root on ${ordinal(stringCount - shape.rootString)}${bass} · ${shape.degrees}`;
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
  seen(key: string): number {
    return this.counts.get(key) ?? 0;
  }

  /**
   * Count the shapes whose onsets the playhead crossed since the last call.
   * Only play counts: a jump (opening the song part way in, a seek, a loop)
   * counts nothing, so a shape is not "seen" because the playhead skipped
   * over it. Crossing it again later counts it again.
   */
  observe(events: ReadonlyArray<{ t: number; key: string }>, now: number): void {
    if (now > this.lastT && now - this.lastT <= SHAPE_KEY_MAX_STEP_SEC) {
      for (const e of events) {
        if (e.t > this.lastT && e.t <= now) this.counts.set(e.key, this.seen(e.key) + 1);
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
  shown(keys: ReadonlyArray<string>): boolean {
    if (keys.length === 0) return false;
    return this.pinned || keys.some((k) => this.seen(k) < SHAPE_KEY_FADE_AFTER);
  }
}

/** Closer than this behind the same shape on the same fret, a chord is a repeat (seconds). */
const SHAPE_REPEAT_GAP_SEC = 0.45;

/**
 * For each chord in time order, whether it repeats the one before: the same
 * shape on the same fret, close behind. A chugged riff or a strummed chord is
 * one shape struck again and again; the lane stamps the first of a run and
 * marks the rest, rather than piling tiles on each other.
 */
export function repeatsPrevious(events: ReadonlyArray<{ t: number; key: string; shape: { rootFret: number } }>): boolean[] {
  return events.map((e, i) => {
    const prev = events[i - 1];
    return prev !== undefined && prev.key === e.key && prev.shape.rootFret === e.shape.rootFret && e.t - prev.t <= SHAPE_REPEAT_GAP_SEC;
  });
}
