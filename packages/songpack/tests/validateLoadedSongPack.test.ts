import { validateLoadedSongPack } from "../src/validateLoadedSongPack";
import type { LoadedSongPack } from "../src/loadSongPack";

function makeLoadedPack(overrides: Partial<LoadedSongPack> = {}): LoadedSongPack {
  const files: Record<string, string> = {
    "manifest.json": JSON.stringify({
      schema_version: "1.0.0",
      song_id: "x",
      title: "t",
      artist: "a",
      duration_sec: 10,
    }),
    "meta/custom.json": JSON.stringify({ ok: true }),
  };

  return {
    songPackPath: "X.songpack",
    containerKind: "directory",
    manifest: JSON.parse(files["manifest.json"]),
    features: {},
    charts: {},
    listFiles: () => Object.keys(files),
    readText: async (rel) => files[rel] ?? null,
    readBytes: async () => null,
    readJson: async (rel) => {
      const t = files[rel];
      return t ? JSON.parse(t) : null;
    },
    ...overrides,
  };
}

describe("validateLoadedSongPack", () => {
  it("returns ok for valid minimal loaded pack", async () => {
    const res = await validateLoadedSongPack(makeLoadedPack());
    expect(res.ok).toBe(true);
    expect(res.issues).toHaveLength(0);
  });

  it("reports missing required manifest", async () => {
    const res = await validateLoadedSongPack(makeLoadedPack({ manifest: null as any }));
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.code === "missing_required_file" && i.path === "manifest.json")).toBe(true);
  });

  it("reports schema invalid for malformed feature/chart and invalid json for extras", async () => {
    const files: Record<string, string> = {
      "manifest.json": JSON.stringify({
        schema_version: "1.0.0",
        song_id: "x",
        title: "t",
        artist: "a",
        duration_sec: 10,
      }),
      "meta/bad.json": "{nope",
    };

    const pack = makeLoadedPack({
      features: {
        beats: { beats: [{ t: -1 }] }, // invalid
      },
      charts: {
        "charts/easy.json": { chart_version: "bad" }, // invalid schema
      },
      listFiles: () => Object.keys(files),
      readText: async (rel) => files[rel] ?? null,
    });

    const res = await validateLoadedSongPack(pack);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.path === "features/beats.json" && i.code === "schema_invalid")).toBe(true);
    expect(res.issues.some((i) => i.path === "charts/easy.json" && i.code === "schema_invalid")).toBe(true);
    expect(res.issues.some((i) => i.path === "meta/bad.json" && i.code === "invalid_json")).toBe(true);
  });
});
