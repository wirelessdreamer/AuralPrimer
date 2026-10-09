import { describe, expect, it } from "vitest";
import { readSongTimeline, tempoAt } from "../src/songTimeline";

describe("readSongTimeline", () => {
  it("marks a downbeat wherever the bar number changes", () => {
    const { beats } = readSongTimeline({
      beats: [
        { time: 0.5, measure: 1 },
        { time: 0, measure: 0 },
        { time: 1, measure: 1 },
        { time: 1.5, measure: 2 },
        { time: "bad", measure: 3 },
      ],
    });
    expect(beats).toEqual([
      { t: 0, down: true, m: 0 },
      { t: 0.5, down: true, m: 1 },
      { t: 1, down: false, m: 1 },
      { t: 1.5, down: true, m: 2 },
    ]);
  });

  it("carries the bar through beats marked -1", () => {
    const { beats } = readSongTimeline({
      beats: [
        { time: 0, measure: 4 },
        { time: 0.5, measure: -1 },
        { time: 1, measure: 5 },
      ],
    });
    expect(beats.map((b) => [b.down, b.m])).toEqual([
      [true, 4],
      [false, 4],
      [true, 5],
    ]);
  });

  it("reads section names the way a player says them", () => {
    const { sections } = readSongTimeline({
      sections: [
        { name: "pre_chorus", time: 30 },
        { name: "verse", time: 10 },
        { time: 50 },
      ],
    });
    expect(sections).toEqual([
      { t: 10, name: "Verse" },
      { t: 30, name: "Pre chorus" },
      { t: 50, name: "Section" },
    ]);
  });

  it("is empty for a song with no timeline", () => {
    expect(readSongTimeline(null)).toEqual({ beats: [], sections: [], tempos: [] });
    expect(readSongTimeline({ beats: "nope" })).toEqual({ beats: [], sections: [], tempos: [] });
  });
});

describe("tempoAt", () => {
  const { tempos } = readSongTimeline({
    tempos: [
      { time: 30, bpm: 90 },
      { time: 0, bpm: 120 },
      { time: 60, bpm: -5 },
    ],
  });

  it("follows the tempo map through its changes", () => {
    expect(tempos).toEqual([
      { t: 0, bpm: 120 },
      { t: 30, bpm: 90 },
    ]);
    expect(tempoAt(tempos, 0)).toBe(120);
    expect(tempoAt(tempos, 29.9)).toBe(120);
    expect(tempoAt(tempos, 30)).toBe(90);
    expect(tempoAt(tempos, 500)).toBe(90);
  });

  it("uses the first tempo before the song reaches it, and null without a map", () => {
    expect(tempoAt([{ t: 2, bpm: 100 }], 0)).toBe(100);
    expect(tempoAt([], 10)).toBeNull();
  });
});
