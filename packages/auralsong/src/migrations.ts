import type { AuralSongManifest } from "./manifest";
import type { LoadedAuralSong } from "./loadAuralSong";
import { AURALSONG_CURRENT_SCHEMA_VERSION, isSupportedAuralSongSchemaVersion } from "./version";

export interface AuralSongMigrationResult<T> {
  fromVersion: string;
  toVersion: string;
  migrated: boolean;
  value: T;
}

export function migrateManifestToCurrent(manifest: AuralSongManifest): AuralSongMigrationResult<AuralSongManifest> {
  if (!isSupportedAuralSongSchemaVersion(manifest.schema_version)) {
    throw new Error(`unsupported AuralSong schema_version: ${manifest.schema_version}`);
  }

  return {
    fromVersion: manifest.schema_version,
    toVersion: AURALSONG_CURRENT_SCHEMA_VERSION,
    migrated: false,
    value: manifest
  };
}

export function migrateLoadedAuralSongToCurrent(pack: LoadedAuralSong): AuralSongMigrationResult<LoadedAuralSong> {
  const manifest = pack.manifest as AuralSongManifest;
  const migratedManifest = migrateManifestToCurrent(manifest);

  return {
    fromVersion: migratedManifest.fromVersion,
    toVersion: migratedManifest.toVersion,
    migrated: false,
    value: {
      ...pack,
      manifest: migratedManifest.value
    }
  };
}
