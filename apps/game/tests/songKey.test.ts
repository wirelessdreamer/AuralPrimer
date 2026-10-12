import { describe, expect, it } from "vitest";
import { songKeyFor } from "../src/songKey";

const note = (pitch: number, t: number) => ({ t_on: t, t_off: t + 0.5, pitch, velocity: 0.8 });

describe("songKeyFor", () => {
  it("takes the pack's harmony key first", () => {
    const key = songKeyFor({ harmony: { version: 1, key: "A", mode: "minor", events: [] } }, [note(60, 0)]);
    expect(key).toEqual({ pitchClass: 9, mode: "minor", label: "A minor" });
  });

  it("then keys.json", () => {
    const key = songKeyFor({ keys: { events: [{ key: "Eb", mode: "major" }] } }, []);
    expect(key).toMatchObject({ pitchClass: 3, mode: "major" });
  });

  it("guesses from the notes only without either, and says so", () => {
    // G major: G B D, C E, D F# A.
    const notes = [67, 71, 74, 72, 76, 74, 78, 69, 67, 71, 74, 67].map((p, i) => note(p, i * 0.5));
    const key = songKeyFor({ harmony: null, keys: null }, notes);
    expect(key?.pitchClass).toBe(7);
    expect(key?.label).toMatch(/from the notes/);
    // Flagged, so the headset can say it is a guess too.
    expect(key?.inferred).toBe(true);
  });

  it("takes the root from a bass that leans on it, for a modal song", () => {
    // E dorian funk: the notes are the D major scale's, so they alone say D
    // major -- but the bass sits on E, and G (not G#) makes it minor.
    const scale = [64, 66, 67, 69, 71, 73, 74, 62, 64, 67, 71, 74].map((p, i) => note(p, i * 0.5));
    const bass = [40, 40, 40, 52, 40, 43, 40, 45, 40, 40].map((p, i) => note(p, i * 0.5));
    const key = songKeyFor({}, [...scale, ...bass], bass);
    expect(key).toMatchObject({ pitchClass: 4, mode: "minor", inferred: true });
    expect(key?.label).toBe("E minor (from the notes)");
    // A bass with no clear home leaves the notes' own guess alone.
    const wandering = [40, 42, 43, 45, 47, 49, 50].map((p, i) => note(p, i * 0.5));
    expect(songKeyFor({}, scale, wandering)?.pitchClass).toBe(songKeyFor({}, scale)?.pitchClass);
  });

  it("is null when nothing says, so no numbers are invented", () => {
    expect(songKeyFor({}, [])).toBeNull();
  });
});
