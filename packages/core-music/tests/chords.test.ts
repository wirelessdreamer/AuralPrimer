/**
 * Chord naming. Both clients show these live while the player's hands are on
 * the keys, so a wrong name is worse than no name — it teaches the wrong thing.
 */
import { describe, it, expect } from "vitest";

import { nameChord, chordLabels, groupNotesIntoChords, pitchClassName } from "../src/chords";

// Middle C is 60.
const C4 = 60, Cs4 = 61, D4 = 62, Ds4 = 63, E4 = 64, F4 = 65;
const Fs4 = 66, G4 = 67, Gs4 = 68, A4 = 69, As4 = 70, B4 = 71;
const C5 = 72, D5 = 74, E5 = 76, G5 = 79;

describe("nameChord — triads", () => {
  it("names major, minor, diminished and augmented", () => {
    expect(nameChord([C4, E4, G4])).toBe("C");
    expect(nameChord([C4, Ds4, G4])).toBe("Cm");
    expect(nameChord([C4, Ds4, Fs4])).toBe("Cdim");
    expect(nameChord([C4, E4, Gs4])).toBe("Caug");
  });

  it("names suspensions", () => {
    expect(nameChord([C4, F4, G4])).toBe("Csus4");
    expect(nameChord([C4, D4, G4])).toBe("Csus2");
  });

  it("names the chord from the screenshot", () => {
    // D#4 G#4 D#5 — a fourth plus its octave. Root D#, so D#sus4 is the honest
    // reading rather than inventing a third that is not being played.
    expect(nameChord([Ds4, Gs4, Ds4 + 12])).toBe("D#sus4");
  });
});

describe("nameChord — sevenths and extensions", () => {
  it("prefers the seventh over the triad it contains", () => {
    // A dominant 7th contains a major triad; if templates were tried in the
    // wrong order every seventh would come back as a plain triad.
    expect(nameChord([C4, E4, G4, As4])).toBe("C7");
    expect(nameChord([C4, E4, G4, B4])).toBe("Cmaj7");
    expect(nameChord([C4, Ds4, G4, As4])).toBe("Cm7");
    expect(nameChord([C4, Ds4, Fs4, As4])).toBe("Cm7b5");
    expect(nameChord([C4, Ds4, Fs4, A4])).toBe("Cdim7");
  });

  it("names sixths and ninths", () => {
    expect(nameChord([C4, E4, G4, A4])).toBe("C6");
    expect(nameChord([C4, E4, G4, As4, D5])).toBe("C9");
    expect(nameChord([C4, E4, G4, B4, D5])).toBe("Cmaj9");
  });
});

describe("nameChord — inversions", () => {
  it("writes an inversion as a slash chord", () => {
    // E in the bass with C and G above is still a C major triad, and saying so
    // while naming the actual bass is more useful than silently re-rooting it.
    expect(nameChord([E4, G4, C5])).toBe("C/E");
    expect(nameChord([G4, C5, E5])).toBe("C/G");
  });

  it("prefers root position when the bass supports it", () => {
    expect(nameChord([C4, E4, G4, C5])).toBe("C");
  });
});

describe("nameChord — small and degenerate inputs", () => {
  it("names a single pitch class", () => {
    expect(nameChord([C4])).toBe("C");
    expect(nameChord([C4, C5])).toBe("C");
  });

  it("names bare intervals rather than going blank", () => {
    // A player holding a fifth wants to see it.
    expect(nameChord([C4, G4])).toBe("C5");
  });

  it("returns null for nothing held", () => {
    expect(nameChord([])).toBeNull();
    expect(nameChord(null as unknown as number[])).toBeNull();
  });

  it("respects flat spelling", () => {
    expect(nameChord([Ds4, G4, As4], "flat")).toBe("Eb");
    expect(pitchClassName(1, "flat")).toBe("Db");
    expect(pitchClassName(1, "sharp")).toBe("C#");
  });
});

describe("groupNotesIntoChords", () => {
  it("groups notes struck together despite human timing", () => {
    // A hand-played chord is never perfectly simultaneous; an exact match would
    // split every one of them.
    const groups = groupNotesIntoChords([
      { t_on: 1.000, pitch: C4 },
      { t_on: 1.012, pitch: E4 },
      { t_on: 1.030, pitch: G4 },
      { t_on: 2.000, pitch: F4 },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].pitches.sort((a, b) => a - b)).toEqual([C4, E4, G4]);
    expect(groups[1].pitches).toEqual([F4]);
  });

  it("handles unsorted input", () => {
    const groups = groupNotesIntoChords([
      { t_on: 2.0, pitch: F4 },
      { t_on: 1.0, pitch: C4 },
    ]);
    expect(groups.map((g) => g.tSec)).toEqual([1.0, 2.0]);
  });

  it("returns nothing for no notes", () => {
    expect(groupNotesIntoChords([])).toEqual([]);
  });
});

describe("chordLabels", () => {
  it("labels chords and skips single notes", () => {
    // Labelling every passing note would bury the chords worth seeing.
    const labels = chordLabels([
      { t_on: 0.0, pitch: C4 },
      { t_on: 0.01, pitch: E4 },
      { t_on: 0.02, pitch: G4 },
      { t_on: 1.0, pitch: D4 },
      { t_on: 2.0, pitch: F4 },
      { t_on: 2.01, pitch: A4 },
      { t_on: 2.02, pitch: C5 },
    ]);
    expect(labels.map((l) => l.label)).toEqual(["C", "F"]);
    expect(labels[1].tSec).toBeCloseTo(2.0, 6);
  });

  it("only reports changes, not every restrike", () => {
    const labels = chordLabels([
      { t_on: 0.0, pitch: C4 }, { t_on: 0.01, pitch: E4 }, { t_on: 0.02, pitch: G4 },
      { t_on: 1.0, pitch: C4 }, { t_on: 1.01, pitch: E4 }, { t_on: 1.02, pitch: G4 },
      { t_on: 2.0, pitch: G4 }, { t_on: 2.01, pitch: B4 }, { t_on: 2.02, pitch: D5 },
    ]);
    expect(labels.map((l) => l.label)).toEqual(["C", "G"]);
  });
});
