/**
 * Naming a chord from the notes being held.
 *
 * Pure and dependency-free so both clients can share it — the 2D piano roll and
 * the MR headset must never disagree about what a chord is called.
 *
 * The approach is deliberately simple: try every held pitch class as the root,
 * match the resulting interval set against a table, and score the candidates.
 * Full harmonic analysis needs the surrounding key and voice leading, which is
 * far more than a live readout warrants — what a player wants while their hands
 * are on the keys is "that's a D#m", fast and stable.
 */

const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

export type ChordSpelling = "sharp" | "flat";

/**
 * Chord templates as semitone offsets from the root.
 *
 * Ordered most-specific first. A dominant 7th contains a major triad, so a
 * plain triad match would win on every seventh chord if the longer shapes were
 * not tried first.
 */
const TEMPLATES: { intervals: number[]; suffix: string; name: string; degrees: string }[] = [
  // Sixths and sevenths with extensions
  { intervals: [0, 4, 7, 11, 14], suffix: "maj9", name: "major 9th", degrees: "1 3 5 7 9" },
  { intervals: [0, 3, 7, 10, 14], suffix: "m9", name: "minor 9th", degrees: "1 ♭3 5 ♭7 9" },
  { intervals: [0, 4, 7, 10, 14], suffix: "9", name: "dominant 9th", degrees: "1 3 5 ♭7 9" },
  { intervals: [0, 4, 7, 9], suffix: "6", name: "major 6th", degrees: "1 3 5 6" },
  { intervals: [0, 3, 7, 9], suffix: "m6", name: "minor 6th", degrees: "1 ♭3 5 6" },
  { intervals: [0, 4, 7, 11], suffix: "maj7", name: "major 7th", degrees: "1 3 5 7" },
  { intervals: [0, 3, 7, 10], suffix: "m7", name: "minor 7th", degrees: "1 ♭3 5 ♭7" },
  { intervals: [0, 4, 7, 10], suffix: "7", name: "dominant 7th", degrees: "1 3 5 ♭7" },
  { intervals: [0, 3, 6, 10], suffix: "m7b5", name: "half-diminished 7th", degrees: "1 ♭3 ♭5 ♭7" },
  { intervals: [0, 3, 6, 9], suffix: "dim7", name: "diminished 7th", degrees: "1 ♭3 ♭5 𝄫7" },
  { intervals: [0, 3, 7, 11], suffix: "mMaj7", name: "minor-major 7th", degrees: "1 ♭3 5 7" },
  { intervals: [0, 5, 7, 10], suffix: "7sus4", name: "7th sus 4", degrees: "1 4 5 ♭7" },
  // A triad with a ninth and no seventh. Without these the ninth is an
  // unexplained extra and the shape falls back to a plain triad or a sus2.
  { intervals: [0, 4, 7, 14], suffix: "add9", name: "add 9", degrees: "1 3 5 9" },
  { intervals: [0, 3, 7, 14], suffix: "madd9", name: "minor add 9", degrees: "1 ♭3 5 9" },
  // Triads
  { intervals: [0, 4, 7], suffix: "", name: "major", degrees: "1 3 5" },
  { intervals: [0, 3, 7], suffix: "m", name: "minor", degrees: "1 ♭3 5" },
  { intervals: [0, 3, 6], suffix: "dim", name: "diminished", degrees: "1 ♭3 ♭5" },
  { intervals: [0, 4, 8], suffix: "aug", name: "augmented", degrees: "1 3 ♯5" },
  { intervals: [0, 5, 7], suffix: "sus4", name: "sus 4", degrees: "1 4 5" },
  { intervals: [0, 2, 7], suffix: "sus2", name: "sus 2", degrees: "1 2 5" },
  // Two-note shapes: worth naming, since a player holding a fifth or a third
  // wants to see it rather than a blank.
  { intervals: [0, 7], suffix: "5", name: "power chord", degrees: "1 5" },
  // A bare fourth. Without this the shape gets re-rooted a fifth away and comes
  // back as an inversion ("G#5/D#") — technically defensible, but baffling next
  // to a readout that says D#4 G#4 D#5. Calling it sus4 reads the way a player
  // hears it: a root with a fourth and no third.
  { intervals: [0, 5], suffix: "sus4", name: "sus 4", degrees: "1 4" },
  { intervals: [0, 4], suffix: "(3)", name: "major third", degrees: "1 3" },
  { intervals: [0, 3], suffix: "m(3)", name: "minor third", degrees: "1 ♭3" },
];

function mod12(n: number): number {
  return ((n % 12) + 12) % 12;
}

export function pitchClassName(pitchClass: number, spelling: ChordSpelling = "sharp"): string {
  const names = spelling === "flat" ? FLAT_NAMES : SHARP_NAMES;
  return names[mod12(pitchClass)];
}

/** A chord read from its notes: its root, its bass, and its form. */
export type ChordAnalysis = {
  /** Pitch class of the root, 0 = C. */
  root: number;
  /** Pitch class of the lowest note; differs from the root in an inversion. */
  bass: number;
  /** The form's suffix: "" major, "m", "dim", "aug", "7", "add9", "5"... */
  suffix: string;
  /** The form's semitones above the root, as the table spells them. */
  intervals: number[];
};

/**
 * Read the chord `pitches` (MIDI numbers) make, or null for a single pitch
 * class or a set no form explains.
 *
 * Every held pitch class is tried as the root and the intervals above it are
 * matched against the forms; the lowest sounding pitch is the bass.
 */
export function analyzeChord(pitches: number[]): ChordAnalysis | null {
  if (!pitches || pitches.length === 0) return null;

  const sorted = [...pitches].sort((a, b) => a - b);
  const bass = mod12(sorted[0]);
  const classes = Array.from(new Set(sorted.map(mod12))).sort((a, b) => a - b);
  if (classes.length < 2) return null;

  let best: { root: number; t: number; score: number } | null = null;

  for (const root of classes) {
    // Intervals above the root, as a set. Compared against templates reduced
    // the same way, so a ninth voiced an octave up still matches.
    const intervals = new Set(classes.map((c) => mod12(c - root)));

    for (let t = 0; t < TEMPLATES.length; t++) {
      const template = TEMPLATES[t];
      const wanted = template.intervals.map(mod12);

      let covered = 0;
      for (const interval of wanted) {
        if (intervals.has(interval)) covered++;
      }
      if (covered !== new Set(wanted).size) continue; // template not fully present

      const extras = intervals.size - new Set(wanted).size;
      // Prefer: every held note explained (no extras), then more specific
      // templates (earlier in the table), then the bass as root — an inversion
      // is a weaker reading than a root-position chord of equal quality.
      const score =
        extras * -100 +
        (TEMPLATES.length - t) +
        (root === bass ? 50 : 0);

      if (best === null || score > best.score) {
        best = { root, t, score };
      }
    }
  }

  if (best === null) return null;
  const template = TEMPLATES[best.t];
  return { root: best.root, bass, suffix: template.suffix, intervals: [...template.intervals] };
}

/**
 * Name the chord formed by `pitches` (MIDI numbers), or null if there is
 * nothing sensible to say.
 *
 * The lowest sounding pitch is treated as the bass. When the best-matching root
 * is not the bass, the chord is written as a slash chord — an inversion the
 * player can actually see in their hands, rather than silently renaming it.
 */
export function nameChord(pitches: number[], spelling: ChordSpelling = "sharp"): string | null {
  if (!pitches || pitches.length === 0) return null;

  const classes = new Set(pitches.map(mod12));
  if (classes.size === 1) {
    return pitchClassName(pitches[0], spelling);
  }

  const chord = analyzeChord(pitches);
  if (chord === null) return null;

  const name = `${pitchClassName(chord.root, spelling)}${chord.suffix}`;
  return chord.root === chord.bass ? name : `${name}/${pitchClassName(chord.bass, spelling)}`;
}

/** What a form is called: "" -> "major", "m7b5" -> "half-diminished 7th". */
export function chordFormName(suffix: string): string {
  return TEMPLATES.find((x) => x.suffix === suffix)?.name ?? suffix;
}

/** A form's degrees above the root: "m" -> "1 ♭3 5". */
export function chordFormDegrees(suffix: string): string {
  return TEMPLATES.find((x) => x.suffix === suffix)?.degrees ?? "";
}

/**
 * Group notes into chords by onset, the way a listener hears them.
 *
 * Notes struck within `toleranceSec` are one chord; a performance is never
 * perfectly simultaneous, so an exact match would split every hand-played chord
 * into separate events.
 */
export function groupNotesIntoChords<T extends { t_on: number; pitch: number }>(
  notes: T[],
  toleranceSec = 0.05,
): { tSec: number; pitches: number[] }[] {
  if (!notes || notes.length === 0) return [];

  const sorted = [...notes].sort((a, b) => a.t_on - b.t_on);
  const groups: { tSec: number; pitches: number[] }[] = [];

  for (const note of sorted) {
    const current = groups[groups.length - 1];
    if (current && note.t_on - current.tSec <= toleranceSec) {
      if (!current.pitches.includes(note.pitch)) current.pitches.push(note.pitch);
    } else {
      groups.push({ tSec: note.t_on, pitches: [note.pitch] });
    }
  }

  return groups;
}

/**
 * Chord labels for a chart: one per onset group, with single notes and
 * unrecognised shapes dropped.
 *
 * Labelling every single note would turn the roll into a wall of text and bury
 * the chords worth seeing.
 */
export function chordLabels<T extends { t_on: number; pitch: number }>(
  notes: T[],
  spelling: ChordSpelling = "sharp",
  toleranceSec = 0.05,
): { tSec: number; label: string }[] {
  const out: { tSec: number; label: string }[] = [];
  let lastLabel: string | null = null;

  for (const group of groupNotesIntoChords(notes, toleranceSec)) {
    if (group.pitches.length < 2) {
      continue;
    }
    const label = nameChord(group.pitches, spelling);
    if (!label) continue;
    // Repeating the same chord on every restrike is noise; only changes matter.
    if (label === lastLabel) continue;
    lastLabel = label;
    out.push({ tSec: group.tSec, label });
  }

  return out;
}
