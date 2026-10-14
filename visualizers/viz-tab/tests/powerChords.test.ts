import { describe, expect, it } from "vitest";
import {
  ShapeKey,
  buildFretChart,
  powerChordOf,
  powerChordsIn,
  repeatsPrevious,
  shapeDetail,
  shapeName,
  type MelodicNote,
  type MelodicTrackSelection,
} from "../src/index";

// E standard, lowest string first: E2 A2 D3 G3 B3 E4.
const OPEN = [40, 45, 50, 55, 59, 64];
const at = (s: number, f: number) => ({ s, f, midi: OPEN[s] + f });

function note(t: number, s: number, f: number): MelodicNote {
  return { t_on: t, t_off: t + 0.25, pitch: OPEN[s] + f, velocity: 0.8, string: s, fret: f };
}

function rhythm(notes: MelodicNote[]): MelodicTrackSelection {
  return { role: "rhythm_guitar", trackName: "Rhythm Guitar", channel: 1, notes };
}

describe("powerChordOf", () => {
  it("reads a root, its fifth and its octave on adjacent strings, named by the root's string", () => {
    // F#5 with the root on the 6th string: 2-4-4.
    expect(powerChordOf([at(0, 2), at(1, 4), at(2, 4)], 6)).toEqual({
      code: "P6",
      rootString: 0,
      rootFret: 2,
      rootMidi: 42,
      octave: true,
    });
    expect(powerChordOf([at(1, 2), at(2, 4), at(3, 4)], 6)?.code).toBe("P5");
    expect(powerChordOf([at(2, 2), at(3, 4), at(4, 5)], 6)?.code).toBe("P4");
  });

  it("does not need the octave, and takes the notes in any order", () => {
    expect(powerChordOf([at(1, 4), at(0, 2)], 6)).toMatchObject({ code: "P6", rootFret: 2, octave: false });
  });

  it("rejects anything else", () => {
    expect(powerChordOf([at(0, 2)], 6)).toBeNull(); // one note
    expect(powerChordOf([at(0, 2), at(1, 3)], 6)).toBeNull(); // a fourth, not a fifth
    expect(powerChordOf([at(0, 2), at(1, 4), at(2, 3)], 6)).toBeNull(); // third note not the octave
    expect(powerChordOf([at(0, 5), at(2, 2)], 6)).toBeNull(); // root and fifth, but a string skipped
    expect(powerChordOf([at(0, 2), at(1, 4), at(2, 4), at(3, 3)], 6)).toBeNull(); // a full chord
  });

  it("numbers strings from the top on a bass too", () => {
    const bassOpen = [28, 33, 38, 43];
    expect(powerChordOf([{ s: 0, f: 3, midi: 31 }, { s: 1, f: 5, midi: 38 }].map((x) => ({ ...x, midi: bassOpen[x.s] + x.f })), 4)?.code).toBe("P4");
  });
});

describe("powerChordsIn", () => {
  it("finds the power chords among the chart's struck chords, within the onset tolerance", () => {
    const chart = buildFretChart(
      rhythm([
        // F#5, its notes 20 ms apart: still one chord.
        note(1, 0, 2), note(1.02, 1, 4), note(1.02, 2, 4),
        // The same shape spread 60 ms apart: three separate notes.
        note(3, 0, 2), note(3.06, 1, 4), note(3.12, 2, 4),
        // An E minor chord is a chord but not a power chord.
        note(5, 0, 0), note(5, 1, 2), note(5, 2, 2), note(5, 3, 0),
      ]),
    );
    const found = powerChordsIn(chart);
    expect(found.size).toBe(1);
    const [[chord, shape]] = [...found];
    expect(chord.t).toBeCloseTo(1, 6);
    expect(shape.code).toBe("P6");
  });
});

describe("repeatsPrevious", () => {
  const p6 = (t: number, fret = 2) => ({ t, code: "P6", shape: { code: "P6", rootString: 0, rootFret: fret, rootMidi: 40 + fret, octave: true } });
  it("marks a chug: the same shape on the same fret, close behind", () => {
    const events = [p6(1), p6(1.2), p6(1.4), p6(2.2), p6(2.4, 4), { ...p6(2.6, 4), code: "P5" }];
    expect(repeatsPrevious(events)).toEqual([false, true, true, false, false, false]);
  });
});

describe("shape names", () => {
  it("says what each shape is", () => {
    expect(shapeName("P6")).toBe("P6 power chord");
    expect(shapeDetail({ code: "P6", rootString: 0, rootFret: 2, rootMidi: 42, octave: true }, 6)).toBe("root on 6th · 1 5 8");
    expect(shapeDetail({ code: "P5", rootString: 1, rootFret: 2, rootMidi: 47, octave: false }, 6)).toBe("root on 5th · 1 5");
  });
});

describe("ShapeKey", () => {
  const events = [
    { t: 1, code: "P6" },
    { t: 2, code: "P6" },
    { t: 3, code: "P5" },
    { t: 4, code: "P6" },
    { t: 5, code: "P5" },
    { t: 6, code: "P5" },
  ];
  /** Play from `from` to `to` frame by frame, as the renderer calls it. */
  const play = (key: ShapeKey, from: number, to: number) => {
    for (let t = from; t <= to + 1e-9; t += 0.02) key.observe(events, t);
  };

  it("shows only when the section has shapes", () => {
    const key = new ShapeKey();
    expect(key.shown([])).toBe(false);
    expect(key.shown(["P6"])).toBe(true);
  });

  it("fades once every shape in the section has been seen three times", () => {
    const key = new ShapeKey();
    play(key, 0, 4.5); // P6 x3, P5 x1
    expect(key.shown(["P6"])).toBe(false);
    expect(key.shown(["P6", "P5"])).toBe(true);
    play(key, 4.5, 6.5); // P5 x3
    expect(key.shown(["P6", "P5"])).toBe(false);
  });

  it("counts each onset once, and does not count while jumping back", () => {
    const key = new ShapeKey();
    play(key, 0, 2.5); // P6 at 1 and 2
    key.observe(events, 2.5);
    key.observe(events, 0.5); // a seek back
    play(key, 0.5, 1.5); // P6 at 1 crossed again: seen again
    expect(key.seen("P6")).toBe(3);
  });

  it("counts only what goes by during play, not what a jump skips over", () => {
    const key = new ShapeKey();
    // Opening the song at 4.5 s, or seeking there: nothing was seen.
    key.observe(events, 4.5);
    expect(key.seen("P6")).toBe(0);
    expect(key.shown(["P6"])).toBe(true);
    // Then playing on, frame by frame.
    for (let t = 4.5; t <= 6.5; t += 0.02) key.observe(events, t);
    expect(key.seen("P5")).toBe(2);
    expect(key.seen("P6")).toBe(0);
  });

  it("stays up while pinned, and K toggles it", () => {
    const key = new ShapeKey();
    play(key, 0, 6.5);
    expect(key.shown(["P6"])).toBe(false);
    expect(key.togglePin()).toBe(true);
    expect(key.shown(["P6"])).toBe(true);
    expect(key.shown([])).toBe(false); // nothing to explain, even pinned
    expect(key.togglePin()).toBe(false);
    expect(key.shown(["P6"])).toBe(false);
  });
});
