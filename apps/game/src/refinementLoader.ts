/**
 * Per-instrument refinement overlay loader.
 *
 * For each `InstrumentRole`, best-effort fetches
 * `aural/refine_candidates.<role>.json` from a feedpak via the existing
 * `read_auralsong_json` Tauri command. Missing files are silent (the
 * common case — no refinement has been authored). Invalid files are
 * surfaced via the caller-provided `warn` callback and skipped, so a
 * broken refinement file never prevents the base `notes.mid` track from
 * rendering.
 *
 * Extracted from main.ts as part of the Phase 2 decomposition. The
 * caller is responsible for applying the returned refinements to the
 * melodic tracks via `applyRefinementsToMelodicTracks`.
 */

import { invoke } from "@tauri-apps/api/core";

import { validateRefinement, type RefinementFile } from "@auralprimer/auralsong/refinement";
import type { InstrumentRole } from "./chartLoader";

export type RefinementLoaderDeps = {
  /** Warning sink — typically `warnConsole` from the host. */
  warn: (channel: string, message: string, detail?: unknown) => void;
};

export async function loadRefinementsForRoles(
  containerPath: string,
  roles: ReadonlyArray<InstrumentRole>,
  deps: RefinementLoaderDeps,
): Promise<RefinementFile[]> {
  const out: RefinementFile[] = [];
  for (const role of roles) {
    const relPath = `aural/refine_candidates.${role}.json`;
    let raw: unknown;
    try {
      raw = await invoke<unknown>("read_auralsong_json", { containerPath, relPath });
    } catch {
      // Missing file is the common case; do not log.
      continue;
    }
    if (raw == null) continue;
    const result = validateRefinement(raw);
    if (!result.ok) {
      deps.warn(
        "play",
        `refine_candidates.${role}.json failed validation; ignoring`,
        result.errors,
      );
      continue;
    }
    out.push(result.value);
  }
  return out;
}
