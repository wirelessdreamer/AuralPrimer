import type { SongPackManifest } from "./manifest";
import { isSupportedSongPackSchemaVersion, isValidSongPackSchemaVersion } from "./version";

export interface SongPackSemanticIssue {
  code: "missing_required_file" | "schema_invalid" | "unsupported";
  path: string;
  message: string;
}

const TIME_FIELD_NAMES = new Set(["t", "t0", "t1", "t_on", "t_off", "start", "end"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function escapeJsonPointerSegment(segment: string): string {
  return segment.replaceAll("~", "~0").replaceAll("/", "~1");
}

function toJsonPointer(pathSegments: Array<string | number>): string {
  if (pathSegments.length === 0) return "";
  return `#/${pathSegments.map((segment) => escapeJsonPointerSegment(String(segment))).join("/")}`;
}

export async function validateManifestSemantics(
  manifest: SongPackManifest,
  fileExists: (relPath: string) => boolean | Promise<boolean>
): Promise<SongPackSemanticIssue[]> {
  const issues: SongPackSemanticIssue[] = [];

  if (!isValidSongPackSchemaVersion(manifest.schema_version)) {
    issues.push({
      code: "schema_invalid",
      path: "manifest.json#/schema_version",
      message: "schema_version must be a SemVer string"
    });
    return issues;
  }

  if (!isSupportedSongPackSchemaVersion(manifest.schema_version)) {
    issues.push({
      code: "unsupported",
      path: "manifest.json#/schema_version",
      message: `unsupported schema_version ${manifest.schema_version}`
    });
  }

  const audioAssets = isRecord(manifest.assets) && isRecord(manifest.assets.audio) ? manifest.assets.audio : null;
  if (!audioAssets) {
    return issues;
  }

  for (const [key, value] of Object.entries(audioAssets)) {
    if (!key.endsWith("_path") || typeof value !== "string" || value.length === 0) continue;
    if (await fileExists(value)) continue;
    issues.push({
      code: "missing_required_file",
      path: value,
      message: `manifest references missing asset file via assets.audio.${key}`
    });
  }

  return issues;
}

export function validateTimedJsonBounds(
  relPath: string,
  json: unknown,
  durationSec: number
): SongPackSemanticIssue[] {
  const issues: SongPackSemanticIssue[] = [];

  function visit(value: unknown, pathSegments: Array<string | number>) {
    if (Array.isArray(value)) {
      value.forEach((item, index) => visit(item, [...pathSegments, index]));
      return;
    }

    if (!isRecord(value)) {
      return;
    }

    for (const [key, nested] of Object.entries(value)) {
      const nextPath = [...pathSegments, key];
      if (typeof nested === "number" && Number.isFinite(nested) && TIME_FIELD_NAMES.has(key)) {
        if (nested < 0 || nested > durationSec) {
          issues.push({
            code: "schema_invalid",
            path: `${relPath}${toJsonPointer(nextPath)}`,
            message: `time value ${nested} is outside manifest duration 0..${durationSec}`
          });
        }
      }

      visit(nested, nextPath);
    }

    const t0 = value.t0;
    const t1 = value.t1;
    if (typeof t0 === "number" && typeof t1 === "number" && t1 < t0) {
      issues.push({
        code: "schema_invalid",
        path: `${relPath}${toJsonPointer(pathSegments)}`,
        message: "t1 must be greater than or equal to t0"
      });
    }

    const tOn = value.t_on;
    const tOff = value.t_off;
    if (typeof tOn === "number" && typeof tOff === "number" && tOff < tOn) {
      issues.push({
        code: "schema_invalid",
        path: `${relPath}${toJsonPointer(pathSegments)}`,
        message: "t_off must be greater than or equal to t_on"
      });
    }

    const start = value.start;
    const end = value.end;
    if (typeof start === "number" && typeof end === "number" && end < start) {
      issues.push({
        code: "schema_invalid",
        path: `${relPath}${toJsonPointer(pathSegments)}`,
        message: "end must be greater than or equal to start"
      });
    }
  }

  visit(json, []);

  return issues;
}
