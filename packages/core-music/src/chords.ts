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
const TEMPLATES: { intervals: number[]; suffix: string }[] = [
  // Sixths and sevenths with extensions
  { intervals: [0, 4, 7, 11, 14], suffix: "maj9" },
  { intervals: [0, 3, 7, 10, 14], suffix: "m9" },
  { intervals: [0, 4, 7, 10, 14], suffix: "9" },
  { intervals: [0, 4, 7, 9], suffix: "6" },
  { intervals: [0, 3, 7, 9], suffix: "m6" },
  { intervals: [0, 4, 7, 11], suffix: "maj7" },
  { intervals: [0, 3, 7, 10], suffix: "m7" },
  { intervals: [0, 4, 7, 10], suffix: "7" },
  { intervals: [0, 3, 6, 10], suffix: "m7b5" },
  { intervals: [0, 3, 6, 9], suffix: "dim7" },
  { intervals: [0, 3, 7, 11], suffix: "mMaj7" },
  { intervals: [0, 5, 7, 10], suffix: "7sus4" },
  // Triads
  { intervals: [0, 4, 7], suffix: "" },
  { intervals: [0, 3, 7], suffix: "m" },
  { intervals: [0, 3, 6], suffix: "dim" },
  { intervals: [0, 4, 8], suffix: "aug" },
  { intervals: [0, 5, 7], suffix: "sus4" },
  { intervals: [0, 2, 7], suffix: "sus2" },
  // Two-note shapes: worth naming, since a player holding a fifth or a third
  // wants to see it rather than a blank.
  { intervals: [0, 7], suffix: "5" },
  // A bare fourth. Without this the shape gets re-rooted a fifth away and comes
  // back as an inversion ("G#5/D#") — technically defensible, but baffling next
  // to a readout that says D#4 G#4 D#5. Calling it sus4 reads the way a player
  // hears it: a root with a fourth and no third.
  { intervals: [0, 5], suffix: "sus4" },
  { intervals: [0, 4], suffix: "(3)" },
  { intervals: [0, 3], suffix: "m(3)" },
];

function mod12(n: number): number {
  return ((n % 12) + 12) % 12;
}

export function pitchClassName(pitchClass: number, spelling: ChordSpelling = "sharp"): string {
  const names = spelling === "flat" ? FLAT_NAMES : SHARP_NAMES;
  return names[mod12(pitchClass)];
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

  const sorted = [...pitches].sort((a, b) => a - b);
  const bass = mod12(sorted[0]);
  const classes = Array.from(new Set(sorted.map(mod12))).sort((a, b) => a - b);

  if (classes.length === 1) {
    return pitchClassName(classes[0], spelling);
  }

  let best: { root: number; suffix: string; score: number } | null = null;

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
        best = { root, suffix: template.suffix, score };
      }
    }
  }

  if (best === null) return null;

  const name = `${pitchClassName(best.root, spelling)}${best.suffix}`;
  return best.root === bass ? name : `${name}/${pitchClassName(bass, spelling)}`;
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
