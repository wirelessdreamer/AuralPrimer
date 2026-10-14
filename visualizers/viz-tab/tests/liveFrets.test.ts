import { describe, expect, it } from "vitest";
import { buildFretChart, judgeLive, liveFrets, sharesChannel, type MelodicNote } from "../src/index";

// E standard, lowest string first: E2 A2 D3 G3 B3 E4.
const OPEN = [40, 45, 50, 55, 59, 64];
/** A note as the Jamstik sends it in Multi-Channel mode: string 1 (high E) on MIDI channel 1 (0 on the wire). */
const jam = (jamString: number, fret: number, velocity = 100) => {
  const s = 6 - jamString;
  return { pitch: OPEN[s] + fret, velocity, channel: jamString - 1, isPressed: true };
};

describe("liveFrets", () => {
  it("turns each string's channel into our string, low E = 0, and the pitch into a fret", () => {
    // Every open string plucked: Jamstik's string 6 (low E) is our string 0.
    const open = [6, 5, 4, 3, 2, 1].map((j) => jam(j, 0));
    expect(liveFrets(open).map((x) => [x.s, x.f, x.midi])).toEqual([
      [0, 0, 40], [1, 0, 45], [2, 0, 50], [3, 0, 55], [4, 0, 59], [5, 0, 64],
    ]);
    expect(liveFrets([jam(6, 3), jam(1, 12)]).map((x) => [x.s, x.f])).toEqual([[0, 3], [5, 12]]);
  });

  it("reads MPE (strings on channels 2-7) by shifting the first channel", () => {
    const mpe = { ...jam(6, 5), channel: 6 }; // string 6 on channel 7
    expect(liveFrets([mpe], 1).map((x) => [x.s, x.f])).toEqual([[0, 5]]);
  });

  it("drops what cannot be on that string: below the open note, past the 24th fret, an unknown channel", () => {
    expect(liveFrets([{ ...jam(1, 0), pitch: 60 }])).toEqual([]); // below high E's open note
    expect(liveFrets([jam(1, 25)])).toEqual([]);
    expect(liveFrets([{ ...jam(1, 0), channel: 9 }])).toEqual([]);
    expect(liveFrets([{ pitch: 64, velocity: 100 }])).toEqual([]); // no channel at all
  });

  it("keeps a note held by the pedal off the neck: only fretted strings count", () => {
    expect(liveFrets([{ ...jam(2, 1), isPressed: false }])).toEqual([]);
  });
});

describe("sharesChannel", () => {
  it("spots Single Channel mode: two held notes on one channel", () => {
    expect(sharesChannel([jam(6, 3), jam(5, 2)])).toBe(false);
    expect(sharesChannel([{ ...jam(6, 3), channel: 0 }, { ...jam(5, 2), channel: 0 }])).toBe(true);
  });
});

describe("judgeLive", () => {
  function note(t: number, s: number, f: number): MelodicNote {
    return { t_on: t, t_off: t + 0.5, pitch: OPEN[s] + f, velocity: 0.8, string: s, fret: f };
  }
  const chart = buildFretChart({ role: "lead_guitar", trackName: "Lead", channel: 1, notes: [note(1, 2, 7), note(3, 0, 5)] });

  it("calls the chart's note on its string and fret a hit", () => {
    expect(judgeLive(chart, liveFrets([jam(4, 7)]), 1.02)).toEqual([{ s: 2, f: 7, midi: 57, velocity: 100, kind: "hit", target: null }]);
  });

  it("calls the right pitch on another string 'other place', pointing at where the chart has it", () => {
    // A3 on the A string's 12th fret, where the chart wants the D string's 7th.
    const [x] = judgeLive(chart, liveFrets([jam(5, 12)]), 1.0);
    expect(x.kind).toBe("other");
    expect(x.target).toEqual({ s: 2, f: 7 });
  });

  it("calls anything else off, and nothing is due between notes", () => {
    expect(judgeLive(chart, liveFrets([jam(4, 8)]), 1.0)[0].kind).toBe("off");
    expect(judgeLive(chart, liveFrets([jam(4, 7)]), 2.0)[0].kind).toBe("off");
  });

  it("allows a little early, and a held note while it rings", () => {
    expect(judgeLive(chart, liveFrets([jam(6, 5)]), 2.93)[0].kind).toBe("hit");
    expect(judgeLive(chart, liveFrets([jam(6, 5)]), 3.4)[0].kind).toBe("hit");
  });
});
