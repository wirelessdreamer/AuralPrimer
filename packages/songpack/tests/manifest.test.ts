import { isSongPackManifest } from "../src/manifest";

describe("manifest type guard", () => {
  it("accepts minimal valid manifest shape", () => {
    expect(
      isSongPackManifest({
        schema_version: "1.0.0",
        song_id: "x",
        title: "t",
        artist: "a",
        duration_sec: 12.3,
      })
    ).toBe(true);
  });

  it("rejects missing or invalid required fields", () => {
    expect(isSongPackManifest(null)).toBe(false);
    expect(
      isSongPackManifest({
        schema_version: "1.0.0",
        song_id: "x",
        title: "t",
        artist: "a",
        duration_sec: "12.3",
      })
    ).toBe(false);
  });
});
