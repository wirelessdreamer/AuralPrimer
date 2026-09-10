/**
 * The hand split has to agree with the ingest pipeline's, or a note the
 * importer considered left-handed shows up in the right hand on screen.
 *
 * These cases are ported one for one from
 * `python/ingest/tests/test_piano_playability.py` and
 * `python/ingest/tests/test_piano_reduction.py`, so a change to either side
 * that breaks the agreement fails here.
 */
import { describe, it, expect } from "vitest";
import { handSplit, leftHandCeiling, assignHands, isSelectedHand } from "../src/pianoHands";
import type { MelodicNote } from "../src/chartLoader";

const note = (t_on: number, pitch: number, t_off = t_on + 0.4): MelodicNote => ({
  t_on,
  t_off,
  pitch,
  velocity: 0.8,
});

describe("handSplit", () => {
  it("gives each hand its own triad", () => {
    expect(handSplit([48, 52, 55, 72, 76, 79])).toEqual([
      [48, 52, 55],
      [72, 76, 79],
    ]);
  });

  it("breaks at the widest gap, not at a fixed pivot", () => {
    // The break falls between 40 and 79 -- a fixed middle-C pivot would agree
    // here by luck, so the case that matters is that the gap chose it.
    expect(handSplit([36, 40, 79, 84])).toEqual([
      [36, 40],
      [79, 84],
    ]);
  });

  it("refuses a spread no two hands can cover", () => {
    expect(handSplit([48, 60, 61, 62, 63, 84])).toBeNull();
    expect(handSplit([40, 56, 72, 88, 100, 104, 106, 107])).toBeNull();
  });

  it("refuses more fingers than two hands have", () => {
    expect(handSplit([60, 61, 62, 63, 64, 65, 66, 67, 68])).toBeNull();
  });

  it("has no hands to divide when there are no pitches", () => {
    expect(handSplit([])).toEqual([[], []]);
  });
});

describe("leftHandCeiling", () => {
  // The asymmetry that a naive port gets wrong: handSplit parks an
  // unsplittable run in the RIGHT hand, so register alone has to rescue a
  // lone low note. Without this, every monophonic melody is right-handed.
  it("sends a lone low note to the left hand", () => {
    expect(leftHandCeiling([40])).toBe(40);
  });

  it("sends a lone high note to the right hand", () => {
    expect(leftHandCeiling([76])).toBeNull();
  });

  it("treats middle C itself as right-handed", () => {
    expect(leftHandCeiling([60])).toBeNull();
  });
});

describe("assignHands", () => {
  it("splits a two-handed chord", () => {
    const tagged = assignHands([note(0, 48), note(0, 55), note(0, 72), note(0, 79)]);
    expect(tagged.map((n) => `${n.pitch}${n.hand}`)).toEqual(["48L", "55L", "72R", "79R"]);
  });

  it("keeps a bass line in the left hand and a melody in the right", () => {
    const tagged = assignHands([note(0, 40), note(0.5, 43), note(1.0, 76), note(1.5, 79)]);
    expect(tagged.map((n) => n.hand)).toEqual(["L", "L", "R", "R"]);
  });

  it("groups notes that strike together and separates ones that do not", () => {
    // 30 ms apart is one attack; 300 ms apart is two.
    const together = assignHands([note(0, 48), note(0.03, 79)]);
    expect(together.map((n) => n.hand)).toEqual(["L", "R"]);

    const apart = assignHands([note(0, 48), note(0.3, 79)]);
    expect(apart.map((n) => n.hand)).toEqual(["L", "R"]);
  });

  it("does not mutate its input", () => {
    const input = [note(0, 48), note(0, 72)];
    assignHands(input);
    expect(input.every((n) => !("hand" in n) || n.hand === undefined)).toBe(true);
  });

  it("keeps a note's hand for its whole sustain", () => {
    // A long left-hand pedal under a moving right hand must not flip when the
    // right hand's chord shape changes underneath it.
    const tagged = assignHands([note(0, 36, 4.0), note(1, 72), note(2, 76), note(3, 79)]);
    expect(tagged.find((n) => n.pitch === 36)?.hand).toBe("L");
  });
});

describe("isSelectedHand", () => {
  it("passes everything in both-hands mode", () => {
    expect(isSelectedHand({ hand: "L" }, "both")).toBe(true);
    expect(isSelectedHand({ hand: "R" }, "both")).toBe(true);
  });

  it("selects only the chosen hand", () => {
    expect(isSelectedHand({ hand: "L" }, "left")).toBe(true);
    expect(isSelectedHand({ hand: "R" }, "left")).toBe(false);
    expect(isSelectedHand({ hand: "R" }, "right")).toBe(true);
    expect(isSelectedHand({ hand: "L" }, "right")).toBe(false);
  });

  it("passes untagged notes, so non-piano parts need no special case", () => {
    expect(isSelectedHand({}, "left")).toBe(true);
    expect(isSelectedHand({}, "right")).toBe(true);
  });
});
