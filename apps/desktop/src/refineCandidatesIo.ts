/**
 * Refine Candidates I/O — read precomputed candidates + read/write the
 * user's accepted picks back to the SongPack.
 *
 * The data contract is locked by the JSON Schemas:
 *   - features/refine_candidates.<instrument>.json  ← Python emits via
 *     `aural_ingest refine-candidates`, the workspace consumes (read-only)
 *   - features/refinement.<instrument>.json         ← workspace writes
 *     the user's accepted notes per region; the runtime game reads this
 *     at SongPack load and overlays it onto features/notes.mid
 *
 * Validators (`validateRefineCandidates`, `validateRefinement`) live in
 * `@auralprimer/songpack`. We re-use them here so a malformed file
 * surfaces a clear error instead of silently rendering garbage.
 *
 * Tauri commands used:
 *   - read_songpack_json(container_path, rel_path)         → existing
 *   - write_songpack_features_json(container_path, rel_path, value)
 *     → new (added in this commit)
 */

import { invoke } from "@tauri-apps/api/core";
import {
  validateRefineCandidates,
  type RefineCandidatesFile,
  type RefineCandidatesRegion,
  type RefineCandidateDefinition,
} from "@auralprimer/songpack/refineCandidates";
import {
  validateRefinement,
  type RefinementFile,
  type RefinementRegion,
  type RefinementNote,
  type RefinementInstrument,
} from "@auralprimer/songpack/refinement";

export {
  type RefineCandidatesFile,
  type RefineCandidatesRegion,
  type RefineCandidateDefinition,
  type RefinementFile,
  type RefinementRegion,
  type RefinementNote,
  type RefinementInstrument,
};

const REFINEMENT_SCHEMA_VERSION = "0.1.0";

/**
 * The decision a user has registered for one region: which candidate id
 * they picked, and the materialized notes that candidate produces. The
 * latter is what the runtime game overlays; the candidate id is kept
 * for the workspace UI (so we can show "you picked 'consensus_tight'")
 * and for re-precompute detection (if the candidate set changes
 * underneath, the saved id may no longer be available).
 */
export type RefineDecision = {
  region_id: string;
  candidate_id: string;
  notes: RefinementNote[];
  /** Free-form ms epoch; UI shows "last edited 2 min ago". */
  edited_at: string;
};

/**
 * Materialized in-memory state for one instrument: candidates + decisions
 * keyed by region id.
 */
export type RefineSession = {
  containerPath: string;
  instrument: RefinementInstrument;
  candidates: RefineCandidatesFile;
  /** region_id → decision. Absent entries mean "auto_picked still wins". */
  decisions: Map<string, RefineDecision>;
};

/** Tauri-shaped read helper. The Tauri stub injected by tests overrides this. */
async function readSongPackJson(
  containerPath: string,
  relPath: string,
): Promise<unknown> {
  return invoke<unknown>("read_songpack_json", {
    containerPath,
    relPath,
  });
}

/** Tauri-shaped write helper. */
async function writeSongPackFeaturesJson(
  containerPath: string,
  relPath: string,
  value: unknown,
): Promise<void> {
  await invoke<void>("write_songpack_features_json", {
    containerPath,
    relPath,
    value,
  });
}

/**
 * Load the precomputed candidates for one instrument. Returns null when
 * the file is missing -- v1 surfaces that via a "No candidates yet --
 * run `aural_ingest refine-candidates` first" empty state in the UI.
 *
 * Throws when the file exists but is malformed; better to surface that
 * than silently render an incomplete view.
 */
export async function loadCandidates(
  containerPath: string,
  instrument: RefinementInstrument,
): Promise<RefineCandidatesFile | null> {
  const rel = `features/refine_candidates.${instrument}.json`;
  let raw: unknown;
  try {
    raw = await readSongPackJson(containerPath, rel);
  } catch (e) {
    const msg = String(e);
    if (msg.includes("not found") || msg.includes("No such") || msg.includes("NotFound")) {
      return null;
    }
    throw new Error(`failed to read ${rel}: ${msg}`);
  }
  const result = validateRefineCandidates(raw);
  if (!result.ok) {
    const detail = result.errors
      .map((err) => `${err.path}: ${err.message}`)
      .join(", ");
    throw new Error(
      `${rel} is malformed: ${detail || "validation failed"}`,
    );
  }
  return result.value;
}

/**
 * Load any previously-saved decisions for this instrument. Returns an
 * empty Map if the refinement file doesn't exist yet (the common case
 * the first time a user opens this SongPack in the workspace).
 */
export async function loadDecisions(
  containerPath: string,
  instrument: RefinementInstrument,
): Promise<Map<string, RefineDecision>> {
  const rel = `features/refinement.${instrument}.json`;
  let raw: unknown;
  try {
    raw = await readSongPackJson(containerPath, rel);
  } catch (e) {
    const msg = String(e);
    if (msg.includes("not found") || msg.includes("No such") || msg.includes("NotFound")) {
      return new Map();
    }
    throw new Error(`failed to read ${rel}: ${msg}`);
  }
  const result = validateRefinement(raw);
  if (!result.ok) {
    const detail = result.errors
      .map((err) => `${err.path}: ${err.message}`)
      .join(", ");
    throw new Error(
      `${rel} is malformed: ${detail || "validation failed"}`,
    );
  }
  const decisions = new Map<string, RefineDecision>();
  for (const region of result.value.regions) {
    decisions.set(region.id, {
      region_id: region.id,
      // Existing refinement.json files predate the per-region
      // candidate_id metadata -- if absent, we'll mark it as "manual".
      candidate_id:
        (region as { source_candidate_id?: string }).source_candidate_id ?? "manual",
      notes: region.notes,
      edited_at:
        (region as { edited_at?: string }).edited_at
          ?? new Date().toISOString(),
    });
  }
  return decisions;
}

/**
 * Build a session by loading the candidates + the user's prior decisions
 * in one shot. The candidates file is required for a refine session;
 * decisions are best-effort.
 */
export async function loadSession(
  containerPath: string,
  instrument: RefinementInstrument,
): Promise<RefineSession | null> {
  const candidates = await loadCandidates(containerPath, instrument);
  if (!candidates) return null;
  const decisions = await loadDecisions(containerPath, instrument).catch(() => {
    // A corrupt refinement.json shouldn't block the session entirely --
    // log + start fresh. Caller can offer an "overwrite?" prompt.
    return new Map<string, RefineDecision>();
  });
  return {
    containerPath,
    instrument,
    candidates,
    decisions,
  };
}

/**
 * Persist the user's decisions back to `features/refinement.<inst>.json`
 * in the schema-compliant shape the runtime game expects. The candidates
 * file is left untouched -- it's editor-time only.
 *
 * Idempotent: writing twice with the same decisions produces the same
 * file. Sorting is stable so git diffs stay readable.
 */
export async function saveDecisions(session: RefineSession): Promise<void> {
  const regions: RefinementRegion[] = [];
  for (const region of session.candidates.regions) {
    const decision = session.decisions.get(region.id);
    if (!decision) continue;
    regions.push({
      id: region.id,
      t_start: region.t_start,
      t_end: region.t_end,
      notes: decision.notes,
      // Tag with the candidate id + edit time. The runtime overlay
      // ignores these but they help the workspace + future re-precompute
      // detect drift.
      ...({
        source_candidate_id: decision.candidate_id,
        edited_at: decision.edited_at,
      } as Record<string, string>),
    });
  }
  const file: RefinementFile = {
    version: REFINEMENT_SCHEMA_VERSION,
    instrument: session.instrument,
    regions: regions.sort((a, b) => a.t_start - b.t_start),
  };
  const rel = `features/refinement.${session.instrument}.json`;
  await writeSongPackFeaturesJson(session.containerPath, rel, file);
}

/**
 * Compute the materialized notes for a region given the current session.
 * Order of precedence:
 *   1. user decision     → decision.notes
 *   2. auto_picked       → candidates[auto_picked]
 *
 * Returns the candidate id used + the notes themselves, so the UI can
 * label "showing: consensus_default" vs "showing: your pick".
 */
export function activeNotesForRegion(
  session: RefineSession,
  regionId: string,
): { candidate_id: string; notes: RefinementNote[]; source: "user" | "auto" } | null {
  const region = session.candidates.regions.find((r) => r.id === regionId);
  if (!region) return null;
  const decision = session.decisions.get(regionId);
  if (decision) {
    return { candidate_id: decision.candidate_id, notes: decision.notes, source: "user" };
  }
  const autoNotes = region.candidate_notes[region.auto_picked] ?? [];
  return {
    candidate_id: region.auto_picked,
    notes: autoNotes,
    source: "auto",
  };
}

/**
 * Set the user's pick for a region in the in-memory session. Caller is
 * responsible for calling `saveDecisions` to persist.
 */
export function pickCandidateForRegion(
  session: RefineSession,
  regionId: string,
  candidateId: string,
): boolean {
  const region = session.candidates.regions.find((r) => r.id === regionId);
  if (!region) return false;
  const notes = region.candidate_notes[candidateId];
  if (!notes) return false;
  session.decisions.set(regionId, {
    region_id: regionId,
    candidate_id: candidateId,
    notes,
    edited_at: new Date().toISOString(),
  });
  return true;
}

/**
 * Forget the user's pick for a region (revert to auto-picked).
 */
export function clearDecisionForRegion(
  session: RefineSession,
  regionId: string,
): boolean {
  return session.decisions.delete(regionId);
}
