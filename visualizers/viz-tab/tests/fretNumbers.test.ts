import { describe, expect, it } from "vitest";
import {
  LABELS_MAJOR,
  LABELS_MINOR,
  OCTAVE_COLORS,
  chordNumber,
  chordTone,
  degreeLabel,
  focusWeight,
  instanceNear,
  keyNotes,
  neighbourPositions,
  neighbours,
  numberFrame,
  octaveBand,
  positionForHand,
  positionInstance,
  positionInstances,
  tierOf,
  toneColor,
  triadOf,
} from "../src/index";

const STD = [40, 45, 50, 55, 59, 64];
const BASS = [28, 33, 38, 43];
const C_MAJOR = numberFrame({ pitchClass: 0, mode: "major" });
const A_MINOR = numberFrame({ pitchClass: 9, mode: "minor" });
const pcs = (names: string) => names.split(" ").map((n) => ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"].indexOf(n));

describe("spelling", () => {
  it("is one table per counting, as the mock-up spells it", () => {
    expect(LABELS_MAJOR).toEqual(["1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7"]);
    expect(LABELS_MINOR).toEqual(["1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6", "♭7", "7"]);
  });

  it("numbers a major key, with blue notes as ♭3 and ♭7", () => {
    expect(pcs("C D E F G A B").map((pc) => degreeLabel(C_MAJOR, pc))).toEqual(["1", "2", "3", "4", "5", "6", "7"]);
    expect(pcs("Eb Bb F#").map((pc) => degreeLabel(C_MAJOR, pc))).toEqual(["♭3", "♭7", "♯4"]);
  });

  it("numbers a minor key from its own 1, raised notes reading 3, 6 and 7", () => {
    expect(pcs("A B C D E F G").map((pc) => degreeLabel(A_MINOR, pc))).toEqual(["1", "2", "♭3", "4", "5", "♭6", "♭7"]);
    expect(pcs("C# F# Ab").map((pc) => degreeLabel(A_MINOR, pc))).toEqual(["3", "6", "7"]);
  });

  it("counts from the relative on the same notes: the A-minor box root is 6 in C major, 1 counted relative", () => {
    expect(degreeLabel(C_MAJOR, 9)).toBe("6");
    const relative = numberFrame({ pitchClass: 0, mode: "major" }, "relative");
    expect(degreeLabel(relative, 9)).toBe("1");
    expect(relative.minor).toBe(true);
    // And back the other way for a minor song.
    expect(degreeLabel(numberFrame({ pitchClass: 9, mode: "minor" }, "relative"), 0)).toBe("1");
    // Geometry never moves with counting.
    expect(relative.boxRoot).toBe(C_MAJOR.boxRoot);
  });
});

describe("tiers", () => {
  it("rings the 1, fills the box, hollows the scale-completers and dashes the rest", () => {
    expect(tierOf(C_MAJOR, 60)).toBe("root");
    expect(pcs("D E G A").map((pc) => tierOf(C_MAJOR, pc))).toEqual(["box", "box", "box", "box"]);
    expect(pcs("F B").map((pc) => tierOf(C_MAJOR, pc))).toEqual(["mode", "mode"]);
    expect(pcs("Eb F# Bb").map((pc) => tierOf(C_MAJOR, pc))).toEqual(["chrom", "chrom", "chrom"]);
    // In A minor the 1 moves, the box does not: C is now a box note.
    expect(tierOf(A_MINOR, 9)).toBe("root");
    expect(tierOf(A_MINOR, 0)).toBe("box");
  });
});

describe("octave colour", () => {
  it("starts a new colour at each 1, counted up from the lowest string", () => {
    // C major on guitar: E2 and B2 are below the first C, C3 starts the next band.
    expect([40, 47, 48, 59, 60, 72].map((m) => octaveBand(C_MAJOR, 40, m))).toEqual([0, 0, 1, 1, 2, 3]);
    // A minor: the band turns over at each A.
    expect([40, 44, 45, 57].map((m) => octaveBand(A_MINOR, 40, m))).toEqual([0, 0, 1, 2]);
    // Clamped to the palette.
    expect(octaveBand(C_MAJOR, 40, 120)).toBe(OCTAVE_COLORS.length - 1);
  });
});

describe("positions", () => {
  it("puts position 1 as the minor box with its root on the low E string", () => {
    const p1 = positionInstance(C_MAJOR, STD, 1, 0);
    expect([p1.lo, p1.hi]).toEqual([5, 8]);
    expect(p1.perString).toEqual([[5, 8], [5, 7], [5, 7], [5, 7], [5, 8], [5, 8]]);
    // The scale-completers inside its frets: 2 on low E 7, ♭6 on A 8 and B 6, 2 on high e 7.
    expect(p1.mode.map((n) => [n.s, n.f])).toEqual([[0, 7], [1, 8], [4, 6], [5, 7]]);
  });

  it("places 1 between 5 below and 2 above", () => {
    expect(neighbourPositions(1)).toEqual([5, 2]);
    expect(neighbourPositions(5)).toEqual([4, 1]);
    const p1 = positionInstance(C_MAJOR, STD, 1, 0);
    const [below, above] = neighbours(C_MAJOR, STD, p1, 24);
    expect([below.k, below.lo, below.hi]).toEqual([5, 2, 5]);
    expect([above.k, above.lo, above.hi]).toEqual([2, 7, 10]);
  });

  it("finds every copy that fits the neck, and the one nearest a fret", () => {
    expect(positionInstances(C_MAJOR, STD, 1, 24).map((i) => i.lo)).toEqual([5, 17]);
    expect(instanceNear(C_MAJOR, STD, 1, 16, 24).lo).toBe(17);
  });

  it("works on a four-string bass", () => {
    const p1 = positionInstance(C_MAJOR, BASS, 1, 0);
    expect(p1.perString).toEqual([[5, 8], [5, 7], [5, 7], [5, 7]]);
  });

  it("maps a hand window to the box it overlaps most, else position 1", () => {
    expect(positionForHand(C_MAJOR, STD, { fret: 5, width: 4 }, 24).k).toBe(1);
    expect(positionForHand(C_MAJOR, STD, { fret: 2, width: 4 }, 24).k).toBe(5);
    expect(positionForHand(C_MAJOR, STD, { fret: 7, width: 4 }, 24).k).toBe(2);
    // In E minor (G major) position 1 sits at the open position.
    const eMinor = numberFrame({ pitchClass: 4, mode: "minor" });
    expect(positionForHand(eMinor, STD, { fret: 1, width: 4 }, 24)).toMatchObject({ k: 1, lo: 0 });
  });

  it("lists every in-key note between two frets", () => {
    const notes = keyNotes(C_MAJOR, STD, 5, 5);
    // Fret 5 on each string: A D G C E A -- all in C major.
    expect(notes.map((n) => n.s)).toEqual([0, 1, 2, 3, 4, 5]);
    // Fret 6 holds the in-key F on the B string and nothing else in key except... A# etc. are dropped.
    expect(keyNotes(C_MAJOR, STD, 6, 6).map((n) => [n.s, n.f])).toEqual([[4, 6]]);
  });
});

describe("the bleed", () => {
  it("is full inside a box and fades with distance to a floor", () => {
    const box = [{ lo: 5, hi: 8 }];
    expect(focusWeight(6, box)).toBe(1);
    expect(focusWeight(10, box)).toBeCloseTo(0.76);
    expect(focusWeight(20, box)).toBe(0.2);
    // The nearest of several windows wins.
    expect(focusWeight(10, [...box, { lo: 10, hi: 12 }])).toBe(1);
  });
});

describe("chords", () => {
  it("reads root and quality from the notes", () => {
    expect(triadOf([57, 60, 64])).toMatchObject({ root: 9, third: 3, suffix: "m" });
    expect(triadOf([52, 55, 60])).toMatchObject({ root: 0, third: 4, suffix: "" }); // C/E
    expect(triadOf([40, 47, 52])).toMatchObject({ root: 4, third: null, suffix: "" }); // E5
    expect(triadOf([55, 59, 62, 65])).toMatchObject({ root: 7, suffix: "" }); // G7 reads as its triad
    expect(triadOf([59, 62, 65])).toMatchObject({ root: 11, suffix: "°" });
    expect(triadOf([60, 72])).toBeNull();
  });

  it("numbers a chord in the key: 4 and 6m in major, ♭3 and ♭7 in minor", () => {
    expect(chordNumber(C_MAJOR, triadOf([53, 57, 60])!)).toBe("4");
    expect(chordNumber(C_MAJOR, triadOf([57, 60, 64])!)).toBe("6m");
    expect(chordNumber(A_MINOR, triadOf([48, 52, 55])!)).toBe("♭3");
    expect(chordNumber(A_MINOR, triadOf([55, 59, 62])!)).toBe("♭7");
    expect(chordNumber(A_MINOR, triadOf([57, 60, 64])!)).toBe("1m");
  });

  it("names each note's job in the chord, with its ring colour", () => {
    const am = triadOf([57, 60, 64])!;
    expect([57, 60, 64, 67].map((m) => chordTone(am, m))).toEqual(["R", "♭3", "5", null]);
    expect(chordTone(triadOf([48, 52, 55])!, 52)).toBe("3");
    expect(["R", "3", "♭3", "5"].map((t) => toneColor(t as "R"))).toEqual(["#22d3ee", "#ff7aa2", "#ff7aa2", "#a78bfa"]);
  });
});
