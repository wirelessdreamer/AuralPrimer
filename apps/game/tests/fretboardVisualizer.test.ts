import { describe, expect, it } from "vitest";

import { parseFrettedNotes, pickFretboardNotes } from "@auralprimer/viz-fretboard";

describe("viz-fretboard note helpers", () => {
  it("parses explicit string/fret and compact s/f metadata", () => {
    const notes = parseFrettedNotes([
      { t_on: 0.5, t_off: 1.0, pitch: 64, velocity: 90, string: 0, fret: 24, trackName: "Lead" },
      { t_on: 0.2, pitch: 55, velocity: 80, s: 2, f: 5 },
      { t_on: 0.1, pitch: 36, channel: 9, string: 0, fret: 0 },
      { t_on: 0.3, pitch: 60 },
      { t_on: 0.4, pitch: 62, string: 99, fret: 3 },
    ]);

    expect(notes).toEqual([
      { t_on: 0.2, t_off: 0.32, pitch: 55, velocity: 80, stringIdx: 2, fret: 5, trackName: undefined },
      { t_on: 0.5, t_off: 1.0, pitch: 64, velocity: 90, stringIdx: 0, fret: 24, trackName: "Lead" },
    ]);
  });

  it("prioritizes active notes before upcoming notes", () => {
    const notes = parseFrettedNotes([
      { t_on: 1.2, t_off: 1.4, pitch: 67, string: 1, fret: 3 },
      { t_on: 0.9, t_off: 1.3, pitch: 64, string: 0, fret: 2 },
      { t_on: 2.8, t_off: 3.0, pitch: 70, string: 2, fret: 5 },
    ]);

    const picked = pickFretboardNotes(notes, 1.0);

    expect(picked.map((n) => n.fret)).toEqual([2, 3]);
  });
});
