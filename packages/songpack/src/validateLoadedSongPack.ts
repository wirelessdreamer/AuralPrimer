import { validateManifest } from "./validateManifest";
import { validateBeats, validateEvents, validateLyrics, validateSections, validateTempoMap } from "./validateFeatures";
import { validateChart } from "./validateCharts";
import type { LoadedSongPack } from "./loadSongPack";

export interface LoadedSongPackValidationIssue {
  code: "missing_required_file" | "invalid_json" | "schema_invalid";
  path: string;
  message: string;
}

export interface LoadedSongPackValidationResult {
  ok: boolean;
  issues: LoadedSongPackValidationIssue[];
}

function safeJsonParse(raw: string): unknown {
  return JSON.parse(raw);
}

export async function validateLoadedSongPack(pack: LoadedSongPack): Promise<LoadedSongPackValidationResult> {
  const issues: LoadedSongPackValidationIssue[] = [];

  // Required: manifest.json
  if (!pack.manifest) {
    issues.push({ code: "missing_required_file", path: "manifest.json", message: "required file missing" });
  } else {
    const v = validateManifest(pack.manifest);
    if (!v.ok) {
      issues.push({
        code: "schema_invalid",
        path: "manifest.json",
        message: `manifest.json failed schema validation (${v.errors?.length ?? 0} errors)`
      });
    }
  }

  // Optional features
  if (pack.features.beats !== undefined) {
    const v = validateBeats(pack.features.beats);
    if (!v.ok) issues.push({ code: "schema_invalid", path: "features/beats.json", message: "beats.json failed schema validation" });
  }

  if (pack.features.tempo_map !== undefined) {
    const v = validateTempoMap(pack.features.tempo_map);
    if (!v.ok) issues.push({ code: "schema_invalid", path: "features/tempo_map.json", message: "tempo_map.json failed schema validation" });
  }

  if (pack.features.sections !== undefined) {
    const v = validateSections(pack.features.sections);
    if (!v.ok) issues.push({ code: "schema_invalid", path: "features/sections.json", message: "sections.json failed schema validation" });
  }

  if (pack.features.events !== undefined) {
    const v = validateEvents(pack.features.events);
    if (!v.ok) issues.push({ code: "schema_invalid", path: "features/events.json", message: "events.json failed schema validation" });
  }

  if (pack.features.lyrics !== undefined) {
    const v = validateLyrics(pack.features.lyrics);
    if (!v.ok) issues.push({ code: "schema_invalid", path: "features/lyrics.json", message: "lyrics.json failed schema validation" });
  }

  // Charts
  for (const [rel, json] of Object.entries(pack.charts)) {
    const v = validateChart(json);
    if (!v.ok) issues.push({ code: "schema_invalid", path: rel, message: "chart json failed schema validation" });
  }

  // Also validate any other JSON files in the pack best-effort (catch invalid_json)
  // (This is intentionally shallow; schema checks only cover known files.)
  for (const rel of pack.listFiles()) {
    if (!rel.toLowerCase().endsWith(".json")) continue;
    if (rel === "manifest.json") continue;
    if (rel.startsWith("features/") || rel.startsWith("charts/")) continue;

    const txt = await pack.readText(rel);
    if (txt == null) continue;
    try {
      safeJsonParse(txt);
    } catch {
      issues.push({ code: "invalid_json", path: rel, message: "invalid JSON" });
    }
  }

  return { ok: issues.length === 0, issues };
}
