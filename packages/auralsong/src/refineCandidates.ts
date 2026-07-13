/**
 * AuralSong Refine Candidates — editor-time pre-computed per-region candidate
 * transcriptions produced by the ingest sidecar.
 *
 * This file is consumed ONLY by the Studio Refine workspace UI. The runtime
 * game never reads it; the game's `chartLoader` only knows about
 * `refinement.json` (the user's accepted choices, materialized).
 *
 * Stored at `features/refine_candidates.<instrument>.json` (one file per
 * instrument).
 */

import type { RefinementInstrument, RefinementNote } from "./refinement";

export type RefineCandidateHotSpotType =
  | "octave_ghost"
  | "off_chord"
  | "density_outlier"
  | "bass_gap"
  | "low_confidence"
  | "clean"
  | "manual";

export type RefineCandidateDefinition = {
  /** Short human label shown in the UI palette ("Stem-only", "Consensus 100/2"). */
  label: string;
  /** Hex tint (#RRGGBB) used for the diff overlay outline + swatch chip. */
  color: string;
  /** Free-form parameters that produced this candidate; provenance only. */
  params?: Record<string, unknown>;
};

export type RefineCandidatesRegion = {
  id: string;
  t_start: number;
  t_end: number;
  section_label?: string;
  hot_spot_type: RefineCandidateHotSpotType;
  confidence: number;
  chord?: string;
  /** candidate_id with the highest score; the UI pre-selects this. */
  auto_picked: string;
  /** candidate_id -> score in 0..1. */
  candidate_scores: Record<string, number>;
  /** candidate_id -> the notes that candidate produces for this region. */
  candidate_notes: Record<string, RefinementNote[]>;
};

export type RefineCandidatesFile = {
  version: string;
  instrument: RefinementInstrument;
  computed_at?: string;
  /** Hash/signature of pipeline versions + params; the Studio uses it to detect re-precompute drift. */
  pipeline_signature?: string;
  song_duration_sec: number;
  candidates: Record<string, RefineCandidateDefinition>;
  regions: RefineCandidatesRegion[];
};

import type { ValidationError, ValidationResult } from "./refinement";

const SEMVER_RE = /^\d+\.\d+\.\d+$/;
const HEX_RE = /^#[0-9A-Fa-f]{6}$/;
const VALID_HOTSPOTS: ReadonlySet<RefineCandidateHotSpotType> = new Set([
  "octave_ghost",
  "off_chord",
  "density_outlier",
  "bass_gap",
  "low_confidence",
  "clean",
  "manual",
]);
const VALID_INSTRUMENTS: ReadonlySet<RefinementInstrument> = new Set([
  "keys",
  "bass",
  "lead_guitar",
  "rhythm_guitar",
  "drums",
  "vocals",
  "melodic",
]);

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function validateNote(x: unknown, path: string, errors: ValidationError[]): void {
  if (!isObject(x)) {
    errors.push({ path, message: "expected object" });
    return;
  }
  for (const k of ["t_on", "t_off", "pitch", "velocity"] as const) {
    if (typeof x[k] !== "number") {
      errors.push({ path: `${path}.${k}`, message: "expected number" });
    }
  }
  for (const [k, min, max] of [
    ["string", 0, 8],
    ["fret", 0, 36],
    ["s", 0, 8],
    ["f", 0, 36],
  ] as const) {
    const value = x[k];
    if (value === undefined) continue;
    if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
      errors.push({ path: `${path}.${k}`, message: `expected integer ${min}..${max}` });
    }
  }
}

function validateCandidateDef(x: unknown, path: string, errors: ValidationError[]): void {
  if (!isObject(x)) {
    errors.push({ path, message: "expected object" });
    return;
  }
  if (typeof x.label !== "string" || (x.label as string).length === 0)
    errors.push({ path: `${path}.label`, message: "expected non-empty string" });
  if (typeof x.color !== "string" || !HEX_RE.test(x.color))
    errors.push({ path: `${path}.color`, message: "expected hex color #RRGGBB" });
}

function validateRegion(
  x: unknown,
  path: string,
  knownCandidateIds: Set<string>,
  errors: ValidationError[],
): void {
  if (!isObject(x)) {
    errors.push({ path, message: "expected object" });
    return;
  }
  if (typeof x.id !== "string" || (x.id as string).length === 0)
    errors.push({ path: `${path}.id`, message: "expected non-empty string" });
  for (const k of ["t_start", "t_end", "confidence"] as const) {
    if (typeof x[k] !== "number")
      errors.push({ path: `${path}.${k}`, message: "expected number" });
  }
  if (
    typeof x.hot_spot_type !== "string" ||
    !VALID_HOTSPOTS.has(x.hot_spot_type as RefineCandidateHotSpotType)
  )
    errors.push({ path: `${path}.hot_spot_type`, message: "unknown hot_spot_type" });
  if (typeof x.auto_picked !== "string" || (x.auto_picked as string).length === 0)
    errors.push({ path: `${path}.auto_picked`, message: "expected non-empty string" });
  else if (!knownCandidateIds.has(x.auto_picked as string))
    errors.push({
      path: `${path}.auto_picked`,
      message: `auto_picked '${x.auto_picked}' not declared in $.candidates`,
    });

  if (!isObject(x.candidate_scores)) {
    errors.push({ path: `${path}.candidate_scores`, message: "expected object" });
  } else {
    for (const [k, v] of Object.entries(x.candidate_scores)) {
      if (!knownCandidateIds.has(k))
        errors.push({
          path: `${path}.candidate_scores.${k}`,
          message: `candidate '${k}' not declared in $.candidates`,
        });
      if (typeof v !== "number" || v < 0 || v > 1)
        errors.push({
          path: `${path}.candidate_scores.${k}`,
          message: "expected number in 0..1",
        });
    }
  }
  if (!isObject(x.candidate_notes)) {
    errors.push({ path: `${path}.candidate_notes`, message: "expected object" });
  } else {
    for (const [k, notes] of Object.entries(x.candidate_notes)) {
      if (!knownCandidateIds.has(k))
        errors.push({
          path: `${path}.candidate_notes.${k}`,
          message: `candidate '${k}' not declared in $.candidates`,
        });
      if (!Array.isArray(notes)) {
        errors.push({ path: `${path}.candidate_notes.${k}`, message: "expected array" });
      } else {
        for (let i = 0; i < notes.length; i++) {
          validateNote(notes[i], `${path}.candidate_notes.${k}[${i}]`, errors);
        }
      }
    }
  }
}

export function validateRefineCandidates(
  raw: unknown,
): ValidationResult<RefineCandidatesFile> {
  const errors: ValidationError[] = [];
  if (!isObject(raw)) {
    return { ok: false, errors: [{ path: "$", message: "expected object" }] };
  }
  if (typeof raw.version !== "string" || !SEMVER_RE.test(raw.version))
    errors.push({ path: "$.version", message: "expected semver string" });
  if (
    typeof raw.instrument !== "string" ||
    !VALID_INSTRUMENTS.has(raw.instrument as RefinementInstrument)
  )
    errors.push({ path: "$.instrument", message: "unknown instrument" });
  if (typeof raw.song_duration_sec !== "number" || raw.song_duration_sec <= 0)
    errors.push({
      path: "$.song_duration_sec",
      message: "expected positive number",
    });

  const knownCandidateIds = new Set<string>();
  if (!isObject(raw.candidates)) {
    errors.push({ path: "$.candidates", message: "expected object" });
  } else {
    if (Object.keys(raw.candidates).length === 0)
      errors.push({ path: "$.candidates", message: "must declare at least one candidate" });
    for (const [k, v] of Object.entries(raw.candidates)) {
      knownCandidateIds.add(k);
      validateCandidateDef(v, `$.candidates.${k}`, errors);
    }
  }
  if (!Array.isArray(raw.regions)) {
    errors.push({ path: "$.regions", message: "expected array" });
  } else {
    for (let i = 0; i < raw.regions.length; i++) {
      validateRegion(raw.regions[i], `$.regions[${i}]`, knownCandidateIds, errors);
    }
  }
  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, value: raw as unknown as RefineCandidatesFile };
}
