export const SONGPACK_CURRENT_SCHEMA_VERSION = "1.0.0";

export interface ParsedSongPackSchemaVersion {
  major: number;
  minor: number;
  patch: number;
}

const SEMVER_RE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

export function parseSongPackSchemaVersion(version: string): ParsedSongPackSchemaVersion | null {
  const match = SEMVER_RE.exec(version);
  if (!match) return null;

  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3])
  };
}

export function isValidSongPackSchemaVersion(version: string): boolean {
  return parseSongPackSchemaVersion(version) != null;
}

export function isSupportedSongPackSchemaVersion(version: string): boolean {
  return version === SONGPACK_CURRENT_SCHEMA_VERSION;
}
