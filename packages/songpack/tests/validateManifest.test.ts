import { validateManifest } from "../src/validateManifest";

describe("validateManifest", () => {
  it("accepts a minimal valid manifest", () => {
    const res = validateManifest({
      schema_version: "1.0.0",
      song_id: "abc",
      title: "Song",
      artist: "Artist",
      duration_sec: 1.23
    });

    expect(res.ok).toBe(true);
    expect(res.value?.title).toBe("Song");
  });

  it("rejects missing required fields", () => {
    const res = validateManifest({
      schema_version: "1.0.0",
      title: "Song"
    });

    expect(res.ok).toBe(false);
    expect(res.errors?.length).toBeGreaterThan(0);
  });

  it("rejects negative duration", () => {
    const res = validateManifest({
      schema_version: "1.0.0",
      song_id: "abc",
      title: "Song",
      artist: "Artist",
      duration_sec: -1
    });

    expect(res.ok).toBe(false);
  });
});
