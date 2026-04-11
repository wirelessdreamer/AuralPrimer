import type { SongPackManifest } from "./manifest";
import type { LoadedSongPack } from "./loadSongPack";
import { SONGPACK_CURRENT_SCHEMA_VERSION, isSupportedSongPackSchemaVersion } from "./version";

export interface SongPackMigrationResult<T> {
  fromVersion: string;
  toVersion: string;
  migrated: boolean;
  value: T;
}

export function migrateManifestToCurrent(manifest: SongPackManifest): SongPackMigrationResult<SongPackManifest> {
  if (!isSupportedSongPackSchemaVersion(manifest.schema_version)) {
    throw new Error(`unsupported SongPack schema_version: ${manifest.schema_version}`);
  }

  return {
    fromVersion: manifest.schema_version,
    toVersion: SONGPACK_CURRENT_SCHEMA_VERSION,
    migrated: false,
    value: manifest
  };
}

export function migrateLoadedSongPackToCurrent(pack: LoadedSongPack): SongPackMigrationResult<LoadedSongPack> {
  const manifest = pack.manifest as SongPackManifest;
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
