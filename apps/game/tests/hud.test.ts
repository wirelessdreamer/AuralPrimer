// @vitest-environment jsdom
import { extractKeyModeFromManifest, formatKeyMode } from "../src/hud";

describe("HUD key/mode", () => {
  it("falls back to placeholder when manifest has no key/mode", () => {
    expect(extractKeyModeFromManifest({})).toEqual({ key: "C", mode: "major" });
  });

  it("extracts key/mode when present at top-level", () => {
    expect(extractKeyModeFromManifest({ key: "E♭", mode: "minor" })).toEqual({ key: "E♭", mode: "minor" });
  });

  it("extracts key/mode when nested under harmony", () => {
    expect(extractKeyModeFromManifest({ harmony: { tonic: "F#", mode: "maj" } })).toEqual({ key: "F#", mode: "major" });
  });

  it("normalizes common shorthand and formats key/mode text", () => {
    expect(extractKeyModeFromManifest({ key: "A", mode: "min" })).toEqual({ key: "A", mode: "minor" });
    expect(formatKeyMode({ key: "D", mode: "dorian" })).toBe("D dorian");
  });
});
