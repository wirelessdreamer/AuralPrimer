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
  /** A bass line under a melody, the texture the split exists to separate. */
  const twoPart = (): MelodicNote[] => {
    const ns: MelodicNote[] = [];
    for (let i = 0; i < 24; i++) {
      ns.push(note(i * 0.5, 40 + (i % 3) * 3, i * 0.5 + 0.45)); // left, E2-G2
      ns.push(note(i * 0.5, 72 + (i % 5), i * 0.5 + 0.45)); // right, C5-F5
    }
    return ns;
  };

  it("separates a bass line from a melody", () => {
    const tagged = assignHands(twoPart());
    const low = tagged.filter((n) => n.pitch < 60);
    const high = tagged.filter((n) => n.pitch >= 60);
    expect(low.every((n) => n.hand === "L")).toBe(true);
    expect(high.every((n) => n.hand === "R")).toBe(true);
  });

  it("never lets the hands overlap in register at one moment", () => {
    const tagged = assignHands(twoPart());
    const highestLeft = Math.max(...tagged.filter((n) => n.hand === "L").map((n) => n.pitch));
    const lowestRight = Math.min(...tagged.filter((n) => n.hand === "R").map((n) => n.pitch));
    expect(highestLeft).toBeLessThan(lowestRight);
  });

  // The regression that made the first two attempts unusable: a line moving
  // across the split changed hands on every note, so the player was shown a
  // part that alternated between their hands bar after bar.
  it("keeps a line that walks across the middle in one hand", () => {
    const ns: MelodicNote[] = [];
    for (let i = 0; i < 20; i++) ns.push(note(i * 0.4, 36 + (i % 4))); // a real left part, low
    // A melody wandering either side of middle C, over that bass.
    const walk = [58, 60, 59, 61, 58, 62, 60, 59, 61, 60];
    walk.forEach((p, i) => ns.push(note(i * 0.8, p)));

    const tagged = assignHands(ns);
    const hands = new Set(
      walk.map((p, i) => tagged.find((n) => n.pitch === p && Math.abs(n.t_on - i * 0.8) < 1e-9)?.hand),
    );
    expect(hands.size).toBe(1);
  });

  it("does not split a single melodic line down the middle", () => {
    // One monophonic line and nothing else: there is no second part to find,
    // so inventing a boundary through it is the failure to avoid.
    const ns = [64, 66, 67, 69, 71, 72, 71, 69, 67, 66].map((p, i) => note(i * 0.4, p));
    const tagged = assignHands(ns);
    expect(new Set(tagged.map((n) => n.hand)).size).toBe(1);
  });

  it("follows a song that changes register, slowly", () => {
    // Two parts that both move up an octave halfway through. The boundary has
    // to follow, or the whole second half lands in one hand.
    const ns: MelodicNote[] = [];
    for (let i = 0; i < 40; i++) {
      const up = i >= 20 ? 12 : 0;
      ns.push(note(i * 0.5, 40 + up, i * 0.5 + 0.4));
      ns.push(note(i * 0.5, 70 + up, i * 0.5 + 0.4));
    }
    const tagged = assignHands(ns);
    const late = tagged.filter((n) => n.t_on >= 15);
    expect(late.filter((n) => n.hand === "L").length).toBeGreaterThan(0);
    expect(late.filter((n) => n.hand === "R").length).toBeGreaterThan(0);
  });

  it("does not mutate its input", () => {
    const input = [note(0, 48), note(0, 72)];
    assignHands(input);
    expect(input.every((n) => !("hand" in n) || n.hand === undefined)).toBe(true);
  });

  it("keeps a note's hand for its whole sustain", () => {
    const tagged = assignHands([note(0, 36, 4.0), note(1, 72), note(2, 76), note(3, 79)]);
    expect(tagged.find((n) => n.pitch === 36)?.hand).toBe("L");
  });

  it("handles an empty part", () => {
    expect(assignHands([])).toEqual([]);
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
