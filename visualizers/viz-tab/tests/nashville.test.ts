import { describe, it, expect } from "vitest";
import { pitchToNashville, diatonicDegree, type KeySignatureAnalysis } from "../src/index";

function key(
  tonic: string,
  pitchClass: number,
  mode: "major" | "minor",
  noteLabelStyle: "sharp" | "flat" = "sharp",
): KeySignatureAnalysis {
  return {
    tonic,
    mode,
    pitchClass,
    label: `${tonic} ${mode}`,
    accidentalKind: noteLabelStyle === "flat" ? "flat" : "natural",
    accidentalCount: 0,
    accidentals: [],
    noteLabelStyle,
    score: 1,
    confidence: 1,
  } as KeySignatureAnalysis;
}

const A_MINOR = key("A", 9, "minor");
const C_MAJOR = key("C", 0, "major");
const F_MAJOR = key("F", 5, "major", "flat");

/** MIDI number for a pitch class in a fixed octave — degree is octave-independent. */
const p = (pitchClass: number) => 60 + pitchClass;

describe("pitchToNashville", () => {
  it("numbers a minor key against its own tonic, with a flat third", () => {
    // A minor: A B C D E F G. The natural-minor degrees are 1 2 b3 4 5 b6 b7.
    expect(pitchToNashville(p(9), A_MINOR)).toBe("1"); // A
    expect(pitchToNashville(p(11), A_MINOR)).toBe("2"); // B
    expect(pitchToNashville(p(0), A_MINOR)).toBe("b3"); // C
    expect(pitchToNashville(p(2), A_MINOR)).toBe("4"); // D
    expect(pitchToNashville(p(4), A_MINOR)).toBe("5"); // E
    expect(pitchToNashville(p(5), A_MINOR)).toBe("b6"); // F
    expect(pitchToNashville(p(7), A_MINOR)).toBe("b7"); // G
  });

  it("spells Bb in A minor as b2, not #1", () => {
    // Regression: the old minor table returned "#1" here. A raised tonic is
    // vanishingly rare; b2 (the Neapolitan) is ordinary — and in Fur Elise's
    // F major section this pitch is unambiguously Bb.
    expect(pitchToNashville(p(10), A_MINOR)).toBe("b2");
  });

  it("never emits b8, which is not a Nashville degree", () => {
    // Regression: the old minor flat table had "b8" at the leading tone.
    for (let pc = 0; pc < 12; pc += 1) {
      expect(pitchToNashville(p(pc), A_MINOR)).not.toBe("b8");
      expect(pitchToNashville(p(pc), C_MAJOR)).not.toBe("b8");
    }
  });

  it("calls the harmonic-minor leading tone 7", () => {
    expect(pitchToNashville(p(8), A_MINOR)).toBe("7"); // G# in A minor
  });

  it("calls the raised third 3, not b4", () => {
    // Regression: the old minor flat table had "b4" at the natural third.
    expect(pitchToNashville(p(1), A_MINOR)).toBe("3"); // C# — Picardy third
  });

  it("leaves major keys on plain diatonic numbers", () => {
    const expected = ["1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7"];
    for (let pc = 0; pc < 12; pc += 1) {
      expect(pitchToNashville(p(pc), C_MAJOR)).toBe(expected[pc]);
    }
  });

  it("uses flats for a flat-signature major key", () => {
    expect(pitchToNashville(p(5), F_MAJOR)).toBe("1"); // F
    expect(pitchToNashville(p(10), F_MAJOR)).toBe("4"); // Bb is diatonic in F
    expect(pitchToNashville(p(6), F_MAJOR)).toBe("b2"); // Gb, not #1
  });

  it("returns null without a key analysis so callers can fall back to names", () => {
    expect(pitchToNashville(60, null)).toBeNull();
  });

  it("is octave-independent", () => {
    for (const octave of [24, 36, 48, 72, 96]) {
      expect(pitchToNashville(octave + 10, A_MINOR)).toBe("b2");
    }
  });
});

describe("diatonicDegree", () => {
  // What the key bed numbers. Seven of the twelve get a digit; the other five
  // get nothing, because an accidental on every remaining key buries the
  // pattern the numbering exists to show.
  it("numbers exactly the seven notes of a major key", () => {
    const degrees = Array.from({ length: 12 }, (_, pc) => diatonicDegree(p(pc), C_MAJOR));
    expect(degrees).toEqual(["1", null, "2", null, "3", "4", null, "5", null, "6", null, "7"]);
  });

  it("numbers the seven of a minor key, flat spellings included", () => {
    // A minor: the b3, b6 and b7 ARE the key, so they must be numbered 3, 6, 7
    // rather than skipped for carrying a flat in their Nashville spelling.
    const degrees = Array.from({ length: 12 }, (_, pc) => diatonicDegree(p(pc), A_MINOR));
    expect(degrees.filter(Boolean)).toHaveLength(7);
    expect(diatonicDegree(p(9), A_MINOR)).toBe("1");   // A
    expect(diatonicDegree(p(0), A_MINOR)).toBe("3");   // C — a b3 that is diatonic
    expect(diatonicDegree(p(10), A_MINOR)).toBeNull(); // Bb — genuinely outside
  });

  it("numbers black keys when the key puts them in the scale", () => {
    // F major: Bb is the 4, and Bb is a black key. The pattern has to follow
    // the key, not the colour of the keys.
    expect(diatonicDegree(p(10), F_MAJOR)).toBe("4");
    expect(diatonicDegree(p(11), F_MAJOR)).toBeNull();
  });

  it("gives nothing without a key analysis", () => {
    expect(diatonicDegree(p(0), null)).toBeNull();
  });
});

