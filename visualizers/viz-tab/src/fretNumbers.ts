/**
 * fretNumbers.ts - Nashville numbers on a fretboard, by pentatonic position.
 *
 * The model from docs/guitar-prototype/nashville-fretboard.html and the plan
 * beside it, pure so it can be tested:
 *
 *  - Positions are geometry, numbers come from the key. The five minor-
 *    pentatonic boxes are places on the neck (blues/rock numbering: position 1
 *    is the minor box with its root on the lowest string); every dot gets its
 *    number from the key.
 *  - Three tiers: the box's five notes, the two that complete the scale, and
 *    notes outside the key (drawn only when the chart plays them).
 *  - A position is a focus, not a fence: numbers fade with distance from the
 *    focused box to a floor that never reaches zero.
 *  - Colour is the octave, starting fresh at each 1 counted up from the lowest
 *    string, and any octave of a number counts as that number.
 *
 * String index 0 is the lowest string, and `open` is the open strings as MIDI,
 * lowest first, so bass and other tunings work the same way as standard guitar.
 */

/**
 * Number for every semitone above the 1, counting from a major 1. Notes
 * outside a major key are spelled the way players say blue notes (♭3, ♭7),
 * with ♯4 for the tritone. These differ from viz-tab's NASHVILLE_SHARP table
 * (#2 #5 #6), which the keys views keep.
 */
export const LABELS_MAJOR = ["1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7"];
/** Counting from a minor 1: 1 2 ♭3 4 5 ♭6 ♭7, with the raised notes reading 3, 6 and 7. */
export const LABELS_MINOR = ["1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6", "♭7", "7"];

/** Minor pentatonic from its root: 1 ♭3 4 5 ♭7. */
export const PENTATONIC_OFFSETS = [0, 3, 5, 7, 10];

/** Octave colours, low to high; a new one starts at each 1. */
export const OCTAVE_COLORS = ["#fb7185", "#fbbf24", "#34d399", "#38bdf8", "#c084fc"];

/** Chord-tone ring colours: cyan root, rose third, violet fifth. */
export const TONE_COLORS = { root: "#22d3ee", third: "#ff7aa2", fifth: "#a78bfa" } as const;

/** Notes outside the key. */
export const CHROMATIC_COLOR = "#ffb547";

const mod = (a: number, n: number): number => ((a % n) + n) % n;

/** The song's key: a tonic pitch class and a mode, and how to say it. */
export type SongKey = { pitchClass: number; mode: "major" | "minor"; label?: string };

/** Count from the song's own 1, or from its relative (major <-> minor on the same notes). */
export type CountFrom = "song" | "relative";

/**
 * A key, as counted. `tonic` is the 1 and `minor` picks the spelling table;
 * `majorPc` and `boxRoot` fix the geometry, which counting never moves.
 */
export type NumberFrame = { tonic: number; minor: boolean; majorPc: number; boxRoot: number };

export function numberFrame(key: SongKey, count: CountFrom = "song"): NumberFrame {
  const majorPc = key.mode === "major" ? mod(key.pitchClass, 12) : mod(key.pitchClass + 3, 12);
  const boxRoot = mod(majorPc + 9, 12);
  const minor = (key.mode === "minor") !== (count === "relative");
  return { tonic: minor ? boxRoot : majorPc, minor, majorPc, boxRoot };
}

/** The number for a pitch (class) in the frame. */
export function degreeLabel(frame: NumberFrame, pitch: number): string {
  return (frame.minor ? LABELS_MINOR : LABELS_MAJOR)[mod(pitch - frame.tonic, 12)];
}

/** root: the 1 (ringed). box: pentatonic. mode: completes the scale. chrom: outside the key. */
export type Tier = "root" | "box" | "mode" | "chrom";

export function tierOf(frame: NumberFrame, pitch: number): Tier {
  const pc = mod(pitch, 12);
  if (pc === frame.tonic) return "root";
  if (PENTATONIC_OFFSETS.some((o) => mod(frame.boxRoot + o, 12) === pc)) return "box";
  // The 4 and 7 of the major key: ♭6 and 2 of the relative minor.
  if (pc === mod(frame.majorPc + 5, 12) || pc === mod(frame.majorPc + 11, 12)) return "mode";
  return "chrom";
}

/** Which octave colour a pitch takes: bands start at each 1, counted up from the lowest string. */
export function octaveBand(frame: NumberFrame, lowestOpen: number, pitch: number): number {
  const band = Math.floor((pitch - frame.tonic) / 12) - Math.floor((lowestOpen - frame.tonic) / 12);
  return Math.max(0, Math.min(OCTAVE_COLORS.length - 1, band));
}

export type NeckNote = { s: number; f: number; pc: number };

/** One copy of a position: two box notes per string, plus the scale-completers inside its frets. */
export type PositionInstance = {
  k: number;
  oct: number;
  notes: NeckNote[];
  /** The two box frets on each string, lowest string first. */
  perString: [number, number][];
  lo: number;
  hi: number;
  mode: NeckNote[];
};

/** One copy of position k (1..5), `oct` octaves up from where it starts on the lowest string. */
export function positionInstance(frame: NumberFrame, open: number[], k: number, oct: number): PositionInstance {
  const n = open.length;
  const pent = new Set(PENTATONIC_OFFSETS.map((o) => mod(frame.boxRoot + o, 12)));
  const rootFret = mod(frame.boxRoot - open[0], 12);
  let p = open[0] + rootFret + PENTATONIC_OFFSETS[k - 1] + 12 * oct;
  const pitches: number[] = [];
  while (pitches.length < 2 * n) {
    if (pent.has(mod(p, 12))) pitches.push(p);
    p += 1;
  }
  const notes: NeckNote[] = [];
  const perString: [number, number][] = [];
  for (let s = 0; s < n; s += 1) {
    const pair: [number, number] = [pitches[2 * s] - open[s], pitches[2 * s + 1] - open[s]];
    perString.push(pair);
    for (const f of pair) notes.push({ s, f, pc: mod(open[s] + f, 12) });
  }
  const frets = notes.map((x) => x.f);
  const lo = Math.min(...frets);
  const hi = Math.max(...frets);
  const mode: NeckNote[] = [];
  for (let s = 0; s < n; s += 1) {
    for (let f = lo; f <= hi; f += 1) {
      const pc = mod(open[s] + f, 12);
      if (tierOf(frame, pc) === "mode" && !perString[s].includes(f)) mode.push({ s, f, pc });
    }
  }
  return { k, oct, notes, perString, lo, hi, mode };
}

/** Every copy of position k that fits on a neck of `maxFret` frets. */
export function positionInstances(frame: NumberFrame, open: number[], k: number, maxFret: number): PositionInstance[] {
  const out: PositionInstance[] = [];
  for (let oct = -1; oct <= 3; oct += 1) {
    const inst = positionInstance(frame, open, k, oct);
    if (inst.lo >= 0 && inst.hi <= maxFret) out.push(inst);
  }
  return out;
}

const centre = (inst: { lo: number; hi: number }): number => (inst.lo + inst.hi) / 2;

/** The copy of position k nearest a fret. */
export function instanceNear(frame: NumberFrame, open: number[], k: number, fret: number, maxFret: number): PositionInstance {
  const all = positionInstances(frame, open, k, maxFret);
  if (all.length === 0) return positionInstance(frame, open, k, 0);
  return all.reduce((best, i) => (Math.abs(centre(i) - fret) < Math.abs(centre(best) - fret) ? i : best));
}

/** The positions either side of k on the neck: 1 sits between 5 below and 2 above. */
export function neighbourPositions(k: number): [number, number] {
  return [k === 1 ? 5 : k - 1, k === 5 ? 1 : k + 1];
}

export function neighbours(frame: NumberFrame, open: number[], inst: PositionInstance, maxFret: number): PositionInstance[] {
  const [below, above] = neighbourPositions(inst.k);
  return [instanceNear(frame, open, below, inst.lo - 2, maxFret), instanceNear(frame, open, above, inst.hi + 2, maxFret)];
}

/** Every in-key note between two frets: the box notes and the two that complete the scale. */
export function keyNotes(frame: NumberFrame, open: number[], f0: number, f1: number): NeckNote[] {
  const out: NeckNote[] = [];
  for (let s = 0; s < open.length; s += 1) {
    for (let f = Math.max(0, Math.floor(f0)); f <= f1; f += 1) {
      const pc = mod(open[s] + f, 12);
      if (tierOf(frame, pc) !== "chrom") out.push({ s, f, pc });
    }
  }
  return out;
}

/**
 * How strongly a fret reads: 1 inside a focused box, easing off with distance
 * to a floor of 0.2 that never reaches zero.
 */
export function focusWeight(fret: number, windows: ReadonlyArray<{ lo: number; hi: number }>): number {
  let best = 0;
  for (const { lo, hi } of windows) {
    const d = fret < lo ? lo - fret : fret > hi ? fret - hi : 0;
    best = Math.max(best, 1 - d * 0.12);
  }
  return Math.max(0.2, best);
}

/**
 * The position a hand is in: the copy of a box that overlaps the hand's window
 * (`fret` .. `fret + width - 1`) by the most frets, nearest first on a tie.
 * Position 1 when nothing on the neck fits at all.
 */
export function positionForHand(
  frame: NumberFrame,
  open: number[],
  hand: { fret: number; width: number },
  maxFret: number,
): PositionInstance {
  const handLo = hand.fret;
  const handHi = hand.fret + Math.max(1, hand.width) - 1;
  const handCentre = (handLo + handHi) / 2;
  let best: { inst: PositionInstance; overlap: number; d: number } | null = null;
  for (let k = 1; k <= 5; k += 1) {
    for (const inst of positionInstances(frame, open, k, maxFret)) {
      const overlap = Math.max(0, Math.min(handHi, inst.hi) - Math.max(handLo, inst.lo) + 1);
      const d = Math.abs(centre(inst) - handCentre);
      if (!best || overlap > best.overlap || (overlap === best.overlap && d < best.d)) best = { inst, overlap, d };
    }
  }
  return best?.inst ?? instanceNear(frame, open, 1, handCentre, maxFret);
}

/** A chord read from its notes: root, third (3 or 4 semitones, or none) and its suffix. */
export type Triad = { root: number; third: 3 | 4 | null; fifth: 6 | 7 | 8 | null; suffix: string };

// Most specific first; extra notes (sevenths, added tones) are allowed but cost.
const TRIADS: { intervals: number[]; third: 3 | 4 | null; fifth: 6 | 7 | 8 | null; suffix: string }[] = [
  { intervals: [0, 4, 7], third: 4, fifth: 7, suffix: "" },
  { intervals: [0, 3, 7], third: 3, fifth: 7, suffix: "m" },
  { intervals: [0, 3, 6], third: 3, fifth: 6, suffix: "°" },
  { intervals: [0, 4, 8], third: 4, fifth: 8, suffix: "+" },
  { intervals: [0, 5, 7], third: null, fifth: 7, suffix: "sus4" },
  { intervals: [0, 2, 7], third: null, fifth: 7, suffix: "sus2" },
  // A power chord has no third; Nashville writes it as the bare number.
  { intervals: [0, 7], third: null, fifth: 7, suffix: "" },
];

/** Name a chord's root and quality from its pitches; null for a single pitch class or no match. */
export function triadOf(pitches: ReadonlyArray<number>): Triad | null {
  if (pitches.length === 0) return null;
  const bass = mod(Math.min(...pitches), 12);
  const pcs = [...new Set(pitches.map((p) => mod(p, 12)))];
  if (pcs.length < 2) return null;
  let best: Triad | null = null;
  let bestScore = -Infinity;
  for (const root of pcs) {
    const intervals = new Set(pcs.map((pc) => mod(pc - root, 12)));
    for (let i = 0; i < TRIADS.length; i += 1) {
      const t = TRIADS[i];
      if (!t.intervals.every((iv) => intervals.has(iv))) continue;
      // Every note explained first, then the more specific shape, then the bass as root.
      const score = (intervals.size - t.intervals.length) * -100 + (TRIADS.length - i) + (root === bass ? 50 : 0);
      if (score > bestScore) {
        bestScore = score;
        best = { root, third: t.third, fifth: t.fifth, suffix: t.suffix };
      }
    }
  }
  return best;
}

/** The chord's Nashville number in the frame: 4, 6m, and in minor ♭3, ♭7. */
export function chordNumber(frame: NumberFrame, triad: Triad): string {
  return degreeLabel(frame, triad.root) + triad.suffix;
}

export type ChordTone = "R" | "3" | "♭3" | "5" | "♭5" | "♯5";

/** A note's job in the chord, or null when it is not the root, third or fifth. */
export function chordTone(triad: Triad, pitch: number): ChordTone | null {
  const d = mod(pitch - triad.root, 12);
  if (d === 0) return "R";
  if (triad.third !== null && d === triad.third) return triad.third === 3 ? "♭3" : "3";
  if (triad.fifth !== null && d === triad.fifth) return triad.fifth === 7 ? "5" : triad.fifth === 6 ? "♭5" : "♯5";
  return null;
}

export function toneColor(tone: ChordTone): string {
  return tone === "R" ? TONE_COLORS.root : tone === "3" || tone === "♭3" ? TONE_COLORS.third : TONE_COLORS.fifth;
}
