/**
 * AuralSong Refinement — user-authored per-region overlay for instrument note tracks.
 *
 * Loaded from `features/refinement.<instrument>.json` (one file per instrument).
 * The game's chartLoader applies each region as a destructive overlay: any base
 * notes whose `t_on` falls within `[t_start, t_end)` are removed and replaced by
 * the region's `notes` array. Outside regions, the base track from `notes.mid`
 * is rendered unchanged.
 *
 * Materialization principle: the region's `notes` is the final note list to
 * play. `accepted_candidate` is provenance only — the runtime loader never
 * needs `refine_candidates.json`. This keeps the runtime stateless and
 * preserves the user's choice across re-precomputes.
 */

export type RefinementInstrument =
  | "keys"
  | "bass"
  | "lead_guitar"
  | "rhythm_guitar"
  | "drums"
  | "vocals"
  | "melodic";

export type RefinementHotSpotType =
  | "octave_ghost"
  | "off_chord"
  | "density_outlier"
  | "bass_gap"
  | "low_confidence"
  | "manual";

export type RefinementNote = {
  t_on: number;
  t_off: number;
  pitch: number;
  velocity: number;
  /** Zero-based string index, lowest/thickest string first. */
  string?: number;
  /** Fret number on `string`. */
  fret?: number;
  /** Compact alias for `string`, matching arrangement wire JSON. */
  s?: number;
  /** Compact alias for `fret`, matching arrangement wire JSON. */
  f?: number;
};

export type RefinementRegion = {
  id: string;
  t_start: number;
  t_end: number;
  section_label?: string;
  hot_spot_type?: RefinementHotSpotType;
  confidence?: number;
  accepted_candidate: string;
  accepted_at: string;
  auto_accepted?: boolean;
  notes: RefinementNote[];
};

export type RefinementSummary = {
  total_regions?: number;
  reviewed?: number;
  auto_accepted?: number;
  explicitly_accepted?: number;
  skipped?: number;
};

export type RefinementFile = {
  version: string;
  instrument: RefinementInstrument;
  session_id?: string;
  started_at?: string;
  updated_at?: string;
  regions: RefinementRegion[];
  summary?: RefinementSummary;
};

/**
 * Validate a parsed refinement JSON. Returns the typed object on success
 * or a list of errors (path + message) on failure. Mirrors the JSON Schema
 * in `schemas/refinement.schema.json` for the fields the runtime actually
 * relies on; the schema file is the canonical source.
 */
export type ValidationError = { path: string; message: string };
export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: ValidationError[] };

const SEMVER_RE = /^\d+\.\d+\.\d+$/;
const VALID_INSTRUMENTS: ReadonlySet<RefinementInstrument> = new Set([
  "keys",
  "bass",
  "lead_guitar",
  "rhythm_guitar",
  "drums",
  "vocals",
  "melodic",
]);
const VALID_HOTSPOTS: ReadonlySet<RefinementHotSpotType> = new Set([
  "octave_ghost",
  "off_chord",
  "density_outlier",
  "bass_gap",
  "low_confidence",
  "manual",
]);

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function validateNote(x: unknown, path: string, errors: ValidationError[]): boolean {
  if (!isObject(x)) {
    errors.push({ path, message: "expected object" });
    return false;
  }
  const startErrors = errors.length;
  let ok = true;
  for (const k of ["t_on", "t_off", "pitch", "velocity"] as const) {
    if (typeof x[k] !== "number") {
      errors.push({ path: `${path}.${k}`, message: "expected number" });
      ok = false;
    }
  }
  if (ok) {
    const n = x as Record<string, number>;
    if (n.t_on < 0) errors.push({ path: `${path}.t_on`, message: "must be >= 0" });
    if (n.t_off <= n.t_on) errors.push({ path: `${path}.t_off`, message: "must be > t_on" });
    if (!Number.isInteger(n.pitch) || n.pitch < 0 || n.pitch > 127)
      errors.push({ path: `${path}.pitch`, message: "must be integer in 0..127" });
    if (!Number.isInteger(n.velocity) || n.velocity < 1 || n.velocity > 127)
      errors.push({ path: `${path}.velocity`, message: "must be integer in 1..127" });
  }
  for (const k of ["string", "s"] as const) {
    if (
      x[k] !== undefined &&
      (!Number.isInteger(x[k]) || (x[k] as number) < 0 || (x[k] as number) > 8)
    ) {
      errors.push({ path: `${path}.${k}`, message: "must be integer in 0..8" });
    }
  }
  for (const k of ["fret", "f"] as const) {
    if (
      x[k] !== undefined &&
      (!Number.isInteger(x[k]) || (x[k] as number) < 0 || (x[k] as number) > 36)
    ) {
      errors.push({ path: `${path}.${k}`, message: "must be integer in 0..36" });
    }
  }
  return ok && errors.length === startErrors;
}

function validateRegion(x: unknown, path: string, errors: ValidationError[]): boolean {
  if (!isObject(x)) {
    errors.push({ path, message: "expected object" });
    return false;
  }
  const startErrors = errors.length;
  for (const k of ["id", "accepted_candidate", "accepted_at"] as const) {
    if (typeof x[k] !== "string" || (x[k] as string).length === 0) {
      errors.push({ path: `${path}.${k}`, message: "expected non-empty string" });
    }
  }
  for (const k of ["t_start", "t_end"] as const) {
    if (typeof x[k] !== "number") {
      errors.push({ path: `${path}.${k}`, message: "expected number" });
    }
  }
  if (typeof x.t_start === "number" && x.t_start < 0)
    errors.push({ path: `${path}.t_start`, message: "must be >= 0" });
  if (
    typeof x.t_start === "number" &&
    typeof x.t_end === "number" &&
    x.t_end <= x.t_start
  )
    errors.push({ path: `${path}.t_end`, message: "must be > t_start" });
  if (
    x.hot_spot_type !== undefined &&
    !VALID_HOTSPOTS.has(x.hot_spot_type as RefinementHotSpotType)
  )
    errors.push({ path: `${path}.hot_spot_type`, message: "unknown hot_spot_type" });
  if (
    x.confidence !== undefined &&
    (typeof x.confidence !== "number" || x.confidence < 0 || x.confidence > 1)
  )
    errors.push({ path: `${path}.confidence`, message: "must be number in 0..1" });
  if (!Array.isArray(x.notes)) {
    errors.push({ path: `${path}.notes`, message: "expected array" });
  } else {
    for (let i = 0; i < x.notes.length; i++) {
      validateNote(x.notes[i], `${path}.notes[${i}]`, errors);
    }
  }
  return errors.length === startErrors;
}

export function validateRefinement(raw: unknown): ValidationResult<RefinementFile> {
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
  if (!Array.isArray(raw.regions)) {
    errors.push({ path: "$.regions", message: "expected array" });
  } else {
    for (let i = 0; i < raw.regions.length; i++) {
      validateRegion(raw.regions[i], `$.regions[${i}]`, errors);
    }
  }
  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, value: raw as unknown as RefinementFile };
}

/**
 * Apply a refinement file as a per-region overlay onto a base note list.
 *
 * Semantics:
 *   - For each region in the refinement (sorted by t_start), drop any base
 *     note whose `t_on` falls within `[region.t_start, region.t_end)`.
 *   - Append the region's `notes` array.
 *   - Return the result sorted by `t_on`.
 *
 * The base list is not mutated. Regions with overlapping intervals are
 * applied in `t_start` order (later regions only see notes the earlier
 * regions' filters leave behind); use `findOverlappingRegions()` if a UI
 * wants to surface that as a warning.
 *
 * Returns the base notes unchanged when `refinement` is `null` / has no
 * regions — this is the no-op path used for AuralSongs without a refinement
 * file yet.
 */
export function applyRefinementOverlay<N extends RefinementNote>(
  baseNotes: ReadonlyArray<N>,
  refinement: RefinementFile | null | undefined,
): N[] {
  if (
    refinement == null ||
    !Array.isArray(refinement.regions) ||
    refinement.regions.length === 0
  ) {
    return [...baseNotes];
  }
  const regions = [...refinement.regions].sort((a, b) => a.t_start - b.t_start);

  const inAnyRegion = (t: number): boolean => {
    // Linear scan is fine for the expected region counts (tens, not thousands).
    for (const r of regions) {
      if (t >= r.t_start && t < r.t_end) return true;
    }
    return false;
  };

  const kept: N[] = [];
  for (const n of baseNotes) {
    if (!inAnyRegion(n.t_on)) kept.push(n);
  }
  for (const r of regions) {
    for (const n of r.notes) {
      // Cast: region notes are RefinementNote (the same shape N extends).
      kept.push(n as unknown as N);
    }
  }
  kept.sort((a, b) => a.t_on - b.t_on);
  return kept;
}

/**
 * Return any pairs of refinement regions whose time intervals overlap.
 * The overlay loader still works on overlapping regions (later region's
 * notes are kept too), but a UI should flag this as a probable authoring
 * mistake. Pure function, useful for both Studio + validation tooling.
 */
export function findOverlappingRegions(
  refinement: RefinementFile,
): Array<{ a: RefinementRegion; b: RefinementRegion }> {
  const out: Array<{ a: RefinementRegion; b: RefinementRegion }> = [];
  const sorted = [...refinement.regions].sort((a, b) => a.t_start - b.t_start);
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length; j++) {
      const a = sorted[i];
      const b = sorted[j];
      if (b.t_start >= a.t_end) break; // sorted by t_start, no further overlap
      out.push({ a, b });
    }
  }
  return out;
}
