// @vitest-environment jsdom
import { extractKeyModeFromManifest, formatKeyMode, hasExplicitKeyModeInManifest } from "../src/hud";

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

  it("detects whether key/mode was explicitly provided", () => {
    expect(hasExplicitKeyModeInManifest({})).toBe(false);
    expect(hasExplicitKeyModeInManifest({ harmony: { key: "G", mode: "major" } })).toBe(true);
    expect(hasExplicitKeyModeInManifest({ mode: "minor" })).toBe(true);
  });

  it("extracts key/mode from loaded harmony and keys artifacts", () => {
    expect(
      extractKeyModeFromManifest(
        { harmony: "harmony.json" },
        { harmony: { key: "Bb", mode: "min", confidence: 0.8 } },
      ),
    ).toEqual({ key: "Bb", mode: "minor" });

    expect(
      extractKeyModeFromManifest(
        {},
        { keys: { version: 1, events: [{ t: 0, key: "D", scale: "major" }] } },
      ),
    ).toEqual({ key: "D", mode: "major" });

    expect(
      hasExplicitKeyModeInManifest(
        {},
        { keys: { version: 1, events: [{ t: 0, key: "A", scale: "minor" }] } },
      ),
    ).toBe(true);
  });
});
