import { promises as fs } from "node:fs";
import path from "node:path";
import { unzipSync, strFromU8 } from "fflate";

import { validateManifest } from "./validateManifest";
import { validateBeats, validateEvents, validateLyrics, validateSections, validateTempoMap } from "./validateFeatures";
import { validateChart } from "./validateCharts";

export type SongPackContainerKind = "directory" | "zip";

export interface SongPackValidationIssue {
  code:
    | "missing_required_file"
    | "invalid_json"
    | "schema_invalid"
    | "unreadable"
    | "unsupported";
  path: string;
  message: string;
}

export interface SongPackValidationResult {
  ok: boolean;
  containerKind: SongPackContainerKind;
  issues: SongPackValidationIssue[];
}

const REQUIRED_TOP_LEVEL = ["manifest.json"] as const;

const OPTIONAL_JSON_FILES = [
  "features/beats.json",
  "features/tempo_map.json",
  "features/sections.json",
  "features/events.json",
  "features/lyrics.json"
] as const;

const CHARTS_DIR = "charts" as const;

async function fileExists(p: string): Promise<boolean> {
  try {
    const st = await fs.stat(p);
    return st.isFile();
  } catch {
    return false;
  }
}

async function readJsonFile(p: string): Promise<unknown> {
  const raw = await fs.readFile(p, "utf-8");
  return JSON.parse(raw);
}

function readZipJson(files: Record<string, Uint8Array>, p: string): unknown {
  const bytes = files[p];
  if (!bytes) {
    throw new Error("missing");
  }
  const text = strFromU8(bytes);
  return JSON.parse(text);
}

function validateKnownJson(relPath: string, json: unknown): SongPackValidationIssue[] {
  const issues: SongPackValidationIssue[] = [];

  if (relPath === "manifest.json") {
    const v = validateManifest(json);
    if (!v.ok) {
      issues.push({
        code: "schema_invalid",
        path: relPath,
        message: `manifest.json failed schema validation (${v.errors?.length ?? 0} errors)`
      });
    }
    return issues;
  }

  if (relPath === "features/beats.json") {
    const v = validateBeats(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "beats.json failed schema validation" });
    }
    return issues;
  }

  if (relPath === "features/tempo_map.json") {
    const v = validateTempoMap(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "tempo_map.json failed schema validation" });
    }
    return issues;
  }

  if (relPath === "features/sections.json") {
    const v = validateSections(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "sections.json failed schema validation" });
    }
    return issues;
  }

  if (relPath === "features/events.json") {
    const v = validateEvents(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "events.json failed schema validation" });
    }
    return issues;
  }

  if (relPath === "features/lyrics.json") {
    const v = validateLyrics(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "lyrics.json failed schema validation" });
    }
    return issues;
  }

  if (relPath.startsWith("charts/") && relPath.endsWith(".json")) {
    const v = validateChart(json);
    if (!v.ok) {
      issues.push({ code: "schema_invalid", path: relPath, message: "chart json failed schema validation" });
    }
    return issues;
  }

  return issues;
}

export async function validateSongPack(songPackPath: string): Promise<SongPackValidationResult> {
  const issues: SongPackValidationIssue[] = [];

  // decide container kind
  let containerKind: SongPackContainerKind;
  try {
    const st = await fs.stat(songPackPath);
    containerKind = st.isDirectory() ? "directory" : "zip";
  } catch {
    return { ok: false, containerKind: "directory", issues: [{ code: "unreadable", path: songPackPath, message: "cannot stat path" }] };
  }

  if (containerKind === "directory") {
    // required files
    for (const rel of REQUIRED_TOP_LEVEL) {
      const abs = path.join(songPackPath, rel);
      if (!(await fileExists(abs))) {
        issues.push({ code: "missing_required_file", path: rel, message: "required file missing" });
      }
    }

    // validate manifest (if present)
    const manifestAbs = path.join(songPackPath, "manifest.json");
    if (await fileExists(manifestAbs)) {
      try {
        const json = await readJsonFile(manifestAbs);
        issues.push(...validateKnownJson("manifest.json", json));
      } catch {
        issues.push({ code: "invalid_json", path: "manifest.json", message: "invalid JSON" });
      }
    }

    // validate optional feature JSON if present
    for (const rel of OPTIONAL_JSON_FILES) {
      const abs = path.join(songPackPath, rel);
      if (!(await fileExists(abs))) continue;
      try {
        const json = await readJsonFile(abs);
        issues.push(...validateKnownJson(rel, json));
      } catch {
        issues.push({ code: "invalid_json", path: rel, message: "invalid JSON" });
      }
    }

    // validate charts/*.json if present
    try {
      const chartsAbs = path.join(songPackPath, CHARTS_DIR);
      const st = await fs.stat(chartsAbs);
      if (st.isDirectory()) {
        const entries = await fs.readdir(chartsAbs);
        for (const ent of entries) {
          if (!ent.endsWith(".json")) continue;
          const rel = `${CHARTS_DIR}/${ent}`;
          const abs = path.join(songPackPath, rel);
          try {
            const json = await readJsonFile(abs);
            issues.push(...validateKnownJson(rel, json));
          } catch {
            issues.push({ code: "invalid_json", path: rel, message: "invalid JSON" });
          }
        }
      }
    } catch {
      // charts dir missing is fine
    }

    return { ok: issues.length === 0, containerKind, issues };
  }

  // zip
  try {
    const bytes = await fs.readFile(songPackPath);
    const files = unzipSync(new Uint8Array(bytes)) as Record<string, Uint8Array>;

    // required
    for (const rel of REQUIRED_TOP_LEVEL) {
      if (!files[rel]) {
        issues.push({ code: "missing_required_file", path: rel, message: "required file missing" });
      }
    }

    // validate manifest if present
    if (files["manifest.json"]) {
      try {
        const json = readZipJson(files, "manifest.json");
        issues.push(...validateKnownJson("manifest.json", json));
      } catch {
        issues.push({ code: "invalid_json", path: "manifest.json", message: "invalid JSON" });
      }
    }

    // optional features
    for (const rel of OPTIONAL_JSON_FILES) {
      if (!files[rel]) continue;
      try {
        const json = readZipJson(files, rel);
        issues.push(...validateKnownJson(rel, json));
      } catch {
        issues.push({ code: "invalid_json", path: rel, message: "invalid JSON" });
      }
    }

    // charts/*.json
    for (const rel of Object.keys(files)) {
      if (!rel.startsWith("charts/") || !rel.endsWith(".json")) continue;
      try {
        const json = readZipJson(files, rel);
        issues.push(...validateKnownJson(rel, json));
      } catch {
        issues.push({ code: "invalid_json", path: rel, message: "invalid JSON" });
      }
    }

    return { ok: issues.length === 0, containerKind, issues };
  } catch {
    return {
      ok: false,
      containerKind,
      issues: [{ code: "unreadable", path: songPackPath, message: "unable to read/decompress zip" }]
    };
  }
}
