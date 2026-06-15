import {
  AURALSONG_CURRENT_SCHEMA_VERSION,
  isSupportedAuralSongSchemaVersion,
  isValidAuralSongSchemaVersion,
  migrateLoadedAuralSongToCurrent,
  migrateManifestToCurrent
} from "../src/index";
import type { LoadedAuralSong } from "../src/loadAuralSong";

describe("auralsong version/migration entry points", () => {
  it("exposes version helpers from the package root", () => {
    expect(AURALSONG_CURRENT_SCHEMA_VERSION).toBe("1.0.0");
    expect(isValidAuralSongSchemaVersion("1.0.0")).toBe(true);
    expect(isValidAuralSongSchemaVersion("v1")).toBe(false);
    expect(isSupportedAuralSongSchemaVersion("1.0.0")).toBe(true);
    expect(isSupportedAuralSongSchemaVersion("2.0.0")).toBe(false);
  });

  it("provides identity migration for current v1 manifests and loaded AuralSongs", () => {
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

    const pack: LoadedAuralSong = {
      auralSongPath: "fixture.auralsong",
      containerKind: "directory",
      manifest,
      features: {},
      charts: {},
      listFiles: () => ["manifest.json"],
      readText: async () => null,
      readBytes: async () => null,
      readJson: async () => null
    };

    const migratedPack = migrateLoadedAuralSongToCurrent(pack);
    expect(migratedPack.migrated).toBe(false);
    expect(migratedPack.toVersion).toBe("1.0.0");
    expect(migratedPack.value.manifest).toBe(manifest);
  });
});
