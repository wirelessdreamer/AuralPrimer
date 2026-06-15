export const AURALSONG_CURRENT_SCHEMA_VERSION = "1.0.0";

export interface ParsedAuralSongSchemaVersion {
  major: number;
  minor: number;
  patch: number;
}

const SEMVER_RE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

export function parseAuralSongSchemaVersion(version: string): ParsedAuralSongSchemaVersion | null {
  const match = SEMVER_RE.exec(version);
  if (!match) return null;

  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3])
  };
}

export function isValidAuralSongSchemaVersion(version: string): boolean {
  return parseAuralSongSchemaVersion(version) != null;
}

export function isSupportedAuralSongSchemaVersion(version: string): boolean {
  return version === AURALSONG_CURRENT_SCHEMA_VERSION;
}
