import { describe, expect, it } from "vitest";
import {
  ShapeKey,
  buildFretChart,
  chordShapeOf,
  chordShapesIn,
  powerChordOf,
  repeatsPrevious,
  shapeDetail,
  shapeName,
  type MelodicNote,
  type MelodicTrackSelection,
} from "../src/index";

// E standard, lowest string first: E2 A2 D3 G3 B3 E4.
const OPEN = [40, 45, 50, 55, 59, 64];
const at = (s: number, f: number) => ({ s, f, midi: OPEN[s] + f });
/** A chord diagram, lowest string first: "x32010" is an open C. */
const grip = (frets: string) =>
  [...frets].flatMap((c, s) => (c === "x" ? [] : [at(s, parseInt(c, 16))]));
const shape = (frets: string) => chordShapeOf(grip(frets), 6);

function note(t: number, s: number, f: number, muted = false): MelodicNote {
  const n: MelodicNote = { t_on: t, t_off: t + 0.25, pitch: OPEN[s] + f, velocity: 0.8, string: s, fret: f };
  return muted ? ({ ...n, tech: { mt: true } } as MelodicNote) : n;
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

describe("chordShapeOf", () => {
  it("reads the form from the intervals and the root's string from the neck", () => {
    expect(shape("022100")).toMatchObject({ form: "", code: "R6", rootString: 0, rootFret: 0, degrees: "1 3 5", bassDegree: null });
    expect(shape("x02210")).toMatchObject({ form: "m", code: "R5", rootString: 1, rootFret: 0 });
    expect(shape("x2343x")).toMatchObject({ form: "dim" }); // B dim
    expect(shape("x3211x")).toMatchObject({ form: "aug", degrees: "1 3 ♯5" }); // C aug
    expect(shape("x32000")).toMatchObject({ form: "maj7" });
    expect(shape("x02010")).toMatchObject({ form: "m7" });
    expect(shape("020100")).toMatchObject({ form: "7" });
    expect(shape("xx0230")).toMatchObject({ form: "sus2" });
    expect(shape("xx0233")).toMatchObject({ form: "sus4" });
    expect(shape("x32030")).toMatchObject({ form: "add9" });
    expect(shape("x3424x")).toMatchObject({ form: "dim7" }); // C dim7: C Gb A Eb
  });

  it("marks the root's notes, and the root is the lowest of them", () => {
    const c = shape("x32010")!;
    expect(c.rootString).toBe(1);
    expect(c.notes.map((x) => x.root)).toEqual([true, false, false, true, false]);
  });

  it("names an inversion's bass", () => {
    // C over E: the root on the 5th string, its 3 under it.
    const c = shape("032010")!;
    expect(c).toMatchObject({ form: "", rootString: 1, rootFret: 3, bassDegree: "3", bassMidi: 40 });
    expect(shapeDetail(c, 6)).toBe("root on 5th, 3 in bass · 1 3 5");
  });

  it("knows an open shape and its barre a fret up as one shape", () => {
    expect(shape("133211")!.key).toBe(shape("022100")!.key);
    expect(shape("x02210")!.key).not.toBe(shape("022100")!.key);
    expect(shape("x02210")!.key).not.toBe(shape("x02200")!.key); // A minor and A sus2 differ
  });

  it("keeps the power chord's shape rule and name", () => {
    expect(shape("244xxx")).toMatchObject({ form: "5", power: true, code: "P6", key: "P6", degrees: "1 5 8" });
    expect(shape("x24xxx")).toMatchObject({ code: "P5", degrees: "1 5" });
    expect(shape("5x2xxx")).toBeNull(); // root and fifth with a string skipped
  });

  it("leaves bare thirds and fourths, and notes no form explains, alone", () => {
    expect(shape("xx21xx")).toBeNull(); // E G#: a third
    expect(shape("xx22xx")).toBeNull(); // E A: a fourth
    expect(shape("x0123x")).toBeNull(); // A Eb A D: no form
    expect(shape("2xxxxx")).toBeNull(); // one note
  });
});

describe("chordShapesIn", () => {
  it("finds the shapes among the chart's struck chords, within the onset tolerance", () => {
    const chart = buildFretChart(
      rhythm([
        // F#5, its notes 20 ms apart: still one chord.
        note(1, 0, 2), note(1.02, 1, 4), note(1.02, 2, 4),
        // The same shape spread 60 ms apart: three separate notes.
        note(3, 0, 2), note(3.06, 1, 4), note(3.12, 2, 4),
        // An open E minor.
        note(5, 0, 0), note(5, 1, 2), note(5, 2, 2), note(5, 3, 0),
        // A muted scratch: no shape.
        note(7, 0, 2, true), note(7, 1, 4, true), note(7, 2, 4, true),
      ]),
    );
    const found = [...chordShapesIn(chart)].sort((a, b) => a[0].t - b[0].t);
    expect(found.map(([chord, s]) => [Math.round(chord.t), s.code, s.form])).toEqual([
      [1, "P6", "5"],
      [5, "R6", "m"],
    ]);
  });
});

describe("repeatsPrevious", () => {
  const p6 = (t: number, fret = 2) => ({ t, key: "P6", shape: { rootFret: fret } });
  it("marks a chug: the same shape on the same fret, close behind", () => {
    const events = [p6(1), p6(1.2), p6(1.4), p6(2.2), p6(2.4, 4), { ...p6(2.6, 4), key: "P5" }];
    expect(repeatsPrevious(events)).toEqual([false, true, true, false, false, false]);
  });
});

describe("shape names", () => {
  it("says what each shape is", () => {
    expect(shapeName(shape("244xxx")!)).toBe("P6 power chord");
    expect(shapeDetail(shape("244xxx")!, 6)).toBe("root on 6th · 1 5 8");
    expect(shapeDetail(shape("x24xxx")!, 6)).toBe("root on 5th · 1 5");
    expect(shapeName(shape("x02010")!)).toBe("Minor 7th chord");
    expect(shapeName(shape("x3211x")!)).toBe("Augmented chord");
    expect(shapeDetail(shape("x02210")!, 6)).toBe("root on 5th · 1 ♭3 5");
  });
});

describe("ShapeKey", () => {
  const events = [
    { t: 1, key: "P6" },
    { t: 2, key: "P6" },
    { t: 3, key: "P5" },
    { t: 4, key: "P6" },
    { t: 5, key: "P5" },
    { t: 6, key: "P5" },
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
