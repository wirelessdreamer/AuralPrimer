import { describe, expect, it } from "vitest";
import { inferKeySignature, midiToNoteName } from "../src/tabRenderer";

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
});
