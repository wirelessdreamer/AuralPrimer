import {
  SONGPACK_CURRENT_SCHEMA_VERSION,
  isSupportedSongPackSchemaVersion,
  isValidSongPackSchemaVersion,
  migrateLoadedSongPackToCurrent,
  migrateManifestToCurrent
} from "../src/index";
import type { LoadedSongPack } from "../src/loadSongPack";

describe("songpack version/migration entry points", () => {
  it("exposes version helpers from the package root", () => {
    expect(SONGPACK_CURRENT_SCHEMA_VERSION).toBe("1.0.0");
    expect(isValidSongPackSchemaVersion("1.0.0")).toBe(true);
    expect(isValidSongPackSchemaVersion("v1")).toBe(false);
    expect(isSupportedSongPackSchemaVersion("1.0.0")).toBe(true);
    expect(isSupportedSongPackSchemaVersion("2.0.0")).toBe(false);
  });

  it("provides identity migration for current v1 manifests and loaded SongPacks", () => {
    const manifest = {
      schema_version: "1.0.0",
      song_id: "fixture",
      title: "Fixture",
      artist: "Fixture Artist",
      duration_sec: 5
    };

    const migratedManifest = migrateManifestToCurrent(manifest);
    expect(migratedManifest.fromVersion).toBe("1.0.0");
    expect(migratedManifest.toVersion).toBe("1.0.0");
    expect(migratedManifest.migrated).toBe(false);
    expect(migratedManifest.value).toBe(manifest);

    const pack: LoadedSongPack = {
      songPackPath: "fixture.songpack",
      containerKind: "directory",
      manifest,
      features: {},
      charts: {},
      listFiles: () => ["manifest.json"],
      readText: async () => null,
      readBytes: async () => null,
      readJson: async () => null
    };

    const migratedPack = migrateLoadedSongPackToCurrent(pack);
    expect(migratedPack.migrated).toBe(false);
    expect(migratedPack.toVersion).toBe("1.0.0");
    expect(migratedPack.value.manifest).toBe(manifest);
  });
});
