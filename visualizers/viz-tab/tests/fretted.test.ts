import { describe, expect, it } from "vitest";
import {
  anchorAt,
  bendLabel,
  buildFretChart,
  constantBeats,
  deriveAnchors,
  fretLabel,
  fretWireFraction,
  isFrettedRole,
  isSounding,
  lookAheadSec,
  neckFretCount,
  normalizeTech,
  stringLabel,
  tabStaffLayout,
  tuningName,
  visibleNotes,
  type MelodicNote,
  type MelodicTrackSelection,
} from "../src/index";

function note(t: number, pitch: number, extra: Partial<MelodicNote> = {}): MelodicNote {
  return { t_on: t, t_off: t + 0.5, pitch, velocity: 0.8, ...extra };
}

function guitar(notes: MelodicNote[], extra: Partial<MelodicTrackSelection> = {}): MelodicTrackSelection {
  return { role: "lead_guitar", trackName: "Lead Guitar", channel: 2, notes, ...extra };
}

describe("fretted roles", () => {
  it("are bass and the two guitars", () => {
    expect(["bass", "rhythm_guitar", "lead_guitar"].every(isFrettedRole)).toBe(true);
    expect(isFrettedRole("keys")).toBe(false);
    expect(isFrettedRole(null)).toBe(false);
  });
});

describe("buildFretChart", () => {
  it("keeps the tab's string and fret, and falls back to the lowest fret without them", () => {
    const chart = buildFretChart(guitar([note(0, 64, { string: 4, fret: 5 }), note(1, 64)]));
    expect(chart.notes.map((x) => [x.s, x.f])).toEqual([
      [4, 5],
      [5, 0],
    ]);
  });

  it("uses the sidecar's tuning over the role's standard one", () => {
    const dropD = [38, 45, 50, 55, 59, 64];
    const chart = buildFretChart(guitar([note(0, 38)], { tuning: dropD }));
    expect(chart.open).toEqual(dropD);
    expect(chart.tuningName).toBe("Drop D");
    // Low D is the open low string in drop D, not unplayable as in standard.
    expect(chart.notes[0]).toMatchObject({ s: 0, f: 0 });
  });

  it("gives bass four strings an octave down", () => {
    const chart = buildFretChart({ role: "bass", trackName: "Bass", channel: 0, notes: [note(0, 28)] });
    expect(chart.n).toBe(4);
    expect(chart.open).toEqual([28, 33, 38, 43]);
    expect(chart.tuningName).toBe("E standard");
    expect(chart.isBass).toBe(true);
  });

  it("groups notes struck together into a chord, named from its pitches", () => {
    // E5 power chord: E2 on the low E string, B2 on the A string.
    const chart = buildFretChart(guitar([note(1, 40, { string: 0, fret: 0 }), note(1.01, 47, { string: 1, fret: 2 }), note(2, 52)]));
    const [a, b, single] = chart.notes;
    expect(a.chord).not.toBeNull();
    expect(a.chord).toBe(b.chord);
    expect(a.chord!.name).toBe("E5");
    expect(a.chord!.size).toBe(2);
    expect(single.chord).toBeNull();
    expect(chart.changes).toEqual([{ t: 1, end: 1, name: "E5", frets: [0, 2, -1, -1, -1, -1], fingers: [-1, -1, -1, -1, -1, -1] }]);
  });

  it("prefers the tab's chord name and fingers", () => {
    const chart = buildFretChart(
      guitar([
        note(1, 45, { string: 1, fret: 0, chord: "Am" }),
        note(1, 52, { string: 2, fret: 2, chord: "Am", finger: 2 }),
        note(1, 57, { string: 3, fret: 2, chord: "Am", finger: 3 }),
      ]),
    );
    expect(chart.notes[0].chord!.name).toBe("Am");
    expect(chart.notes[0].chord!.fingers).toEqual([-1, -1, 2, 3, -1, -1]);
    expect(chart.notes[2].finger).toBe(3);
  });

  it("does not call two notes on one string a chord", () => {
    const chart = buildFretChart(guitar([note(1, 64, { string: 5, fret: 0 }), note(1.01, 65, { string: 5, fret: 1 })]));
    expect(chart.notes.every((x) => x.chord === null)).toBe(true);
  });

  it("merges a held shape with the chords struck inside it into one card", () => {
    const chart = buildFretChart(
      guitar(
        [note(2, 40, { string: 0, fret: 0 }), note(2, 47, { string: 1, fret: 2 })],
        { handshapes: [{ t: 1, t_end: 4, name: "E5", frets: [0, 2, -1, -1, -1, -1], fingers: [-1, 1, -1, -1, -1, -1] }] },
      ),
    );
    expect(chart.changes).toHaveLength(1);
    expect(chart.changes[0]).toMatchObject({ t: 1, end: 4, name: "E5" });
  });

  it("names an unnamed held shape from its frets", () => {
    const chart = buildFretChart(
      guitar([note(0, 64)], { handshapes: [{ t: 1, t_end: 2, name: "", frets: [-1, 0, 2, 2, 1, 0], fingers: [] }] }),
    );
    expect(chart.changes[0].name).toBe("Am");
  });

  it("uses authored hand positions, and derives them when the tab has none", () => {
    const authored = buildFretChart(guitar([note(0, 64)], { anchors: [{ t: 0, fret: 0, width: 1 }, { t: 3, fret: 7, width: 5 }] }));
    // Clamped to a real window: no fret 0, at least two frets wide.
    expect(authored.anchors).toEqual([
      { t: 0, fret: 1, width: 2 },
      { t: 3, fret: 7, width: 5 },
    ]);
    const derived = buildFretChart(guitar([note(0, 69, { string: 5, fret: 5 })]));
    expect(derived.anchors).toEqual([{ t: -0.25, fret: 2, width: 4 }]);
  });

  it("keeps only the techniques it draws, and only when set", () => {
    expect(normalizeTech({ pm: true, sl: 7, bn: 0, vb: 1, hm: false, xyz: 1, slu: -1 })).toEqual({ pm: true, sl: 7, vb: 1 });
    const chart = buildFretChart(guitar([note(0, 64, { string: 5, fret: 0, tech: { mt: true } })]));
    expect(fretLabel(chart.notes[0])).toBe("×");
  });
});

describe("deriveAnchors", () => {
  it("moves the hand only as far as the next note needs", () => {
    const anchors = deriveAnchors([
      { t: 1, f: 3 },
      { t: 2, f: 4 },
      { t: 3, f: 0 },
      { t: 4, f: 9 },
      { t: 5, f: 7 },
      { t: 6, f: 2 },
    ]);
    expect(anchors.map((a) => [a.t, a.fret])).toEqual([
      [0.75, 1],
      [3.75, 6],
      [5.75, 2],
    ]);
  });

  it("puts an all-open part at the first fret", () => {
    expect(deriveAnchors([{ t: 0, f: 0 }])).toEqual([{ t: 0, fret: 1, width: 4 }]);
  });
});

describe("tuning names", () => {
  it("names the common tunings and spells the rest", () => {
    expect(tuningName([40, 45, 50, 55, 59, 64], false)).toBe("E standard");
    expect(tuningName([39, 44, 49, 54, 58, 63], false)).toBe("E♭ standard");
    expect(tuningName([38, 43, 48, 53, 57, 62], false)).toBe("D standard");
    expect(tuningName([26, 33, 38, 43], true)).toBe("Drop D");
    expect(tuningName([38, 45, 50, 55, 59, 62], false)).toBe("D A D G B D");
  });

  it("calls the top string of a guitar e", () => {
    const open = [40, 45, 50, 55, 59, 64];
    expect(open.map((_, s) => stringLabel(open, s))).toEqual(["E", "A", "D", "G", "B", "e"]);
    expect(stringLabel([28, 33, 38, 43], 3)).toBe("G");
  });
});

describe("layout maths", () => {
  it("divides every view's look-ahead by the note spacing", () => {
    expect(lookAheadSec(2.4, 1)).toBe(2.4);
    expect(lookAheadSec(2.4, 2)).toBe(1.2);
    expect(lookAheadSec(2.4, undefined)).toBe(2.4);
    // Clamped like every other view: a broken multiplier cannot zero it.
    expect(lookAheadSec(2.4, 0)).toBe(2.4);
  });

  it("spaces frets as a real neck does", () => {
    expect(fretWireFraction(0, 12)).toBe(0);
    expect(fretWireFraction(12, 12)).toBeCloseTo(1);
    // The first fret is wider than the twelfth.
    const first = fretWireFraction(1, 12);
    const twelfth = fretWireFraction(12, 12) - fretWireFraction(11, 12);
    expect(first / twelfth).toBeCloseTo(2 ** (11 / 12));
  });

  it("shows at least twelve frets and at most 24", () => {
    expect(neckFretCount(3)).toBe(12);
    expect(neckFretCount(17)).toBe(18);
    expect(neckFretCount(30)).toBe(24);
  });

  it("fits a six-string staff with its marks into the old 160 px tab strip", () => {
    const { gap, yTop, yBot } = tabStaffLayout(160, 6);
    expect(gap).toBeGreaterThanOrEqual(10);
    expect(yTop).toBeGreaterThanOrEqual(50);
    expect(yBot + 16).toBeLessThanOrEqual(160);
    // A bass staff in the same strip spreads its four strings wider.
    expect(tabStaffLayout(160, 4).gap).toBeGreaterThan(gap);
  });

  it("writes bends the way tab does", () => {
    expect(bendLabel(1)).toBe("½");
    expect(bendLabel(2)).toBe("full");
    expect(bendLabel(4)).toBe("2");
  });

  it("finds the hand, sounding notes and what is on screen", () => {
    const chart = buildFretChart(
      guitar([note(1, 64, { string: 5, fret: 0 }), note(2, 69, { string: 5, fret: 5 })], {
        anchors: [{ t: 1.5, fret: 5, width: 4 }],
      }),
    );
    expect(anchorAt(chart, 0).fret).toBe(5);
    expect(anchorAt(chart, 3).fret).toBe(5);
    expect(isSounding(chart.notes[0], 1.2)).toBe(true);
    expect(isSounding(chart.notes[0], 1.6)).toBe(false);
    expect(visibleNotes(chart.notes, 1.4, 2.5).map((x) => x.t)).toEqual([1, 2]);
    expect(visibleNotes(chart.notes, 1.6, 2.5).map((x) => x.t)).toEqual([2]);
  });

  it("draws bar lines at one tempo when the song has no timeline", () => {
    const beats = constantBeats(120, 4, 0, 2.1);
    expect(beats.map((b) => b.t)).toEqual([0, 0.5, 1, 1.5, 2]);
    expect(beats.map((b) => b.down)).toEqual([true, false, false, false, true]);
    expect(beats[4].m).toBe(2);
  });
});
