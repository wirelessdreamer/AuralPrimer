import { describe, expect, it } from "vitest";
import {
  inferKeySignature,
  midiToNoteName,
  noteToFretPosition,
  TUNING_GUITAR_STANDARD,
} from "../src/tabRenderer";

describe("tabRenderer theory helpers", () => {
  it("infers a flat key signature from Bb-major note content", () => {
    const notes = [
      { t_on: 0, t_off: 0.6, pitch: 58, velocity: 0.7 }, // Bb
      { t_on: 0, t_off: 0.6, pitch: 62, velocity: 0.6 }, // D
      { t_on: 0.6, t_off: 1.2, pitch: 65, velocity: 0.7 }, // F
      { t_on: 1.2, t_off: 1.8, pitch: 63, velocity: 0.65 }, // Eb
      { t_on: 1.8, t_off: 2.4, pitch: 70, velocity: 0.7 }, // Bb
      { t_on: 2.4, t_off: 3.0, pitch: 74, velocity: 0.6 }, // D
      { t_on: 3.0, t_off: 3.6, pitch: 77, velocity: 0.7 }, // F
    ];

    const key = inferKeySignature(notes);
    expect(key).not.toBeNull();
    expect(key?.label).toBe("Bb major");
    expect(key?.accidentalKind).toBe("flat");
    expect(key?.accidentalCount).toBe(2);
    expect(key?.accidentals).toEqual(["Bb", "Eb"]);
    expect(key?.noteLabelStyle).toBe("flat");
  });

  it("formats black-key note names using the requested enharmonic style", () => {
    expect(midiToNoteName(70, "flat")).toBe("Bb4");
    expect(midiToNoteName(70, "sharp")).toBe("A#4");
    expect(midiToNoteName(61, "dual")).toBe("C#/Db4");
  });

  it("prefers explicit string/fret metadata over pitch-derived fingering", () => {
    const note = { t_on: 0, t_off: 0.5, pitch: 64, velocity: 0.7, string: 0, fret: 24 };

    expect(noteToFretPosition(note, TUNING_GUITAR_STANDARD)).toEqual({ string: 0, fret: 24 });
  });

  it("accepts compact s/f fingering metadata", () => {
    const note = { t_on: 0, t_off: 0.5, pitch: 64, velocity: 0.7, s: 2, f: 14 };

    expect(noteToFretPosition(note, TUNING_GUITAR_STANDARD)).toEqual({ string: 2, fret: 14 });
  });

  it("falls back to pitch-derived fingering when explicit metadata is invalid", () => {
    const note = { t_on: 0, t_off: 0.5, pitch: 64, velocity: 0.7, string: 99, fret: 24 };

    expect(noteToFretPosition(note, TUNING_GUITAR_STANDARD)).toEqual({ string: 5, fret: 0 });
  });
});
