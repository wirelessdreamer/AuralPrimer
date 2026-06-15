/**
 * Tests for the refinement overlay wiring on the game side.
 *
 * - Behaviour tests cover `applyRefinementsToMelodicTracks` (pure function in
 *   chartLoader): role-matching, multi-instrument refinement files, untouched
 *   tracks, no mutation of inputs.
 * - Source-level pins catch regressions in main.ts wiring (the import,
 *   the loader call, and the apply step that together let the game prefer
 *   refinement.json when present). They run without spinning up Tauri.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  applyRefinementsToMelodicTracks,
  type MelodicTrackSelection,
  type MelodicNote,
} from "../src/chartLoader";
import type { RefinementFile } from "@auralprimer/auralsong/refinement";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
function read(rel: string): string {
  return readFileSync(resolve(REPO_ROOT, rel), "utf-8");
}

function n(t_on: number, t_off: number, pitch: number, velocity = 80): MelodicNote {
  return { t_on, t_off, pitch, velocity };
}

function track(
  role: MelodicTrackSelection["role"],
  notes: MelodicNote[],
): MelodicTrackSelection {
  return { role, trackName: role, channel: 0, notes };
}

function refinement(
  instrument: RefinementFile["instrument"],
  regions: RefinementFile["regions"],
): RefinementFile {
  return { version: "1.0.0", instrument, regions };
}

function region(
  t_start: number,
  t_end: number,
  notes: MelodicNote[],
): RefinementFile["regions"][number] {
  return {
    id: `r-${t_start}`,
    t_start,
    t_end,
    accepted_candidate: "consensus_default",
    accepted_at: "2026-05-30T00:00:00Z",
    notes,
  };
}

describe("applyRefinementsToMelodicTracks", () => {
  const keys = track("keys", [n(0.5, 0.9, 60), n(1.2, 1.5, 62), n(1.6, 1.9, 64), n(3.0, 3.3, 67)]);
  const bass = track("bass", [n(0.5, 0.9, 40), n(2.0, 2.3, 43)]);

  it("returns a deep-copied passthrough when refinements is empty", () => {
    const out = applyRefinementsToMelodicTracks([keys, bass], []);
    expect(out).toEqual([keys, bass]);
    expect(out[0]).not.toBe(keys);
    expect(out[0].notes).not.toBe(keys.notes);
  });

  it("applies a keys refinement to the keys track only", () => {
    const ref = refinement("keys", [region(1.0, 2.0, [n(1.5, 1.8, 70)])]);
    const out = applyRefinementsToMelodicTracks([keys, bass], [ref]);
    // keys: notes at t_on=1.2 and 1.6 removed (inside [1,2)), n(1.5,1.8,70) added; others kept.
    expect(out[0].role).toBe("keys");
    expect(out[0].notes).toEqual([n(0.5, 0.9, 60), n(1.5, 1.8, 70), n(3.0, 3.3, 67)]);
    // bass: untouched.
    expect(out[1]).toEqual(bass);
  });

  it("applies separate refinements per instrument", () => {
    const refKeys = refinement("keys", [region(0.0, 1.0, [n(0.6, 0.9, 71)])]);
    const refBass = refinement("bass", [region(1.5, 2.5, [n(2.1, 2.2, 45)])]);
    const out = applyRefinementsToMelodicTracks([keys, bass], [refKeys, refBass]);
    expect(out[0].notes[0]).toEqual(n(0.6, 0.9, 71));
    expect(out[1].notes).toEqual([n(0.5, 0.9, 40), n(2.1, 2.2, 45)]);
  });

  it("ignores refinements whose instrument matches no track", () => {
    const refUnused = refinement("drums", [region(0.0, 5.0, [])]);
    const out = applyRefinementsToMelodicTracks([keys, bass], [refUnused]);
    expect(out).toEqual([keys, bass]);
  });

  it("does not mutate the input tracks", () => {
    const keysCopy = JSON.parse(JSON.stringify(keys));
    const ref = refinement("keys", [region(1.0, 2.0, [n(1.5, 1.8, 70)])]);
    applyRefinementsToMelodicTracks([keys], [ref]);
    expect(keys).toEqual(keysCopy);
  });

  it("composes when two refinements target the same instrument", () => {
    // Edge case: AuralSong convention is one file per instrument, but the loader
    // must be deterministic when several arrive together. Later refinements
    // operate on the result of earlier ones.
    const refA = refinement("keys", [region(0.0, 1.0, [n(0.6, 0.9, 71)])]);
    const refB = refinement("keys", [region(2.5, 3.5, [n(3.1, 3.2, 75)])]);
    const out = applyRefinementsToMelodicTracks([keys], [refA, refB]);
    expect(out[0].notes.map((m) => m.pitch)).toEqual([71, 62, 64, 75]);
  });
});

describe("main.ts wiring (source-level pin)", () => {
  // Phase 2.T moved the chart-load + refinement-apply pipeline into
  // songChartLoader.ts. main.ts now just calls readSongChartSelection and
  // writes the returned drumSelection + melodicTracks. The
  // applyRefinementsToMelodicTracks + loadRefinementsForRoles imports +
  // calls all live inside songChartLoader.ts.
  const main = read("apps/game/src/main.ts");
  const chartSrc = read("apps/game/src/songChartLoader.ts");

  it("songChartLoader imports applyRefinementsToMelodicTracks from chartLoader", () => {
    expect(chartSrc).toMatch(/applyRefinementsToMelodicTracks/);
    expect(chartSrc).toMatch(/from\s+["']\.\/chartLoader["']/);
  });

  it("songChartLoader imports loadRefinementsForRoles from its extracted module", () => {
    expect(chartSrc).toMatch(/import\s*\{\s*loadRefinementsForRoles\s*\}\s*from\s*["']\.\/refinementLoader["']/);
    expect(chartSrc).not.toMatch(/from\s+["']@auralprimer\/auralsong\/refinement["']/);
  });

  it("songChartLoader calls loadRefinementsForRoles + applies overlay into melodicTracks", () => {
    // Compose-and-return shape (Phase 2.T) — caller assigns the result.
    expect(chartSrc).toMatch(
      /(?:const|let)\s+melodicTracks\s*=\s*applyRefinementsToMelodicTracks\s*\(\s*baseMelodicTracks\s*,\s*refinements\s*\)/
    );
    // The loader call must pass a warn callback so invalid files don't get
    // silently swallowed (they shouldn't be fatal, but they must surface).
    expect(chartSrc).toMatch(
      /loadRefinementsForRoles\s*\([\s\S]*?warn\s*:[\s\S]*?\)/
    );
  });

  it("main.ts wires readSongChartSelection's result into selectedDrumChartSelection + selectedMelodicTracks", () => {
    // Pins the host's contract: the new pipeline returns both pieces;
    // main.ts must write both into its module state (no side-effect mutation).
    expect(main).toMatch(/import\s*\{\s*readSongChartSelection\s*\}\s*from\s*["']\.\/songChartLoader["']/);
    expect(main).toMatch(/selectedDrumChartSelection\s*=\s*chartSelection\.drumSelection/);
    expect(main).toMatch(/selectedMelodicTracks\s*=\s*chartSelection\.melodicTracks/);
  });

  it("refinementLoader module wires the read_auralsong_json + validateRefinement flow", () => {
    const loaderSrc = read("apps/game/src/refinementLoader.ts");
    expect(loaderSrc).toMatch(/async\s+function\s+loadRefinementsForRoles\s*\(/);
    expect(loaderSrc).toMatch(/read_auralsong_json/);
    expect(loaderSrc).toMatch(/features\/refinement\.\$\{role\}\.json/);
    expect(loaderSrc).toMatch(/validateRefinement\s*\(\s*raw\s*\)/);
    // Invalid files warn + continue, never throw.
    expect(loaderSrc).toMatch(/result\.ok/);
    expect(loaderSrc).toMatch(/deps\.warn\s*\(/);
  });

  it("refinementLoader uses the browser-safe deep import (not the auralsong barrel)", () => {
    const loaderSrc = read("apps/game/src/refinementLoader.ts");
    expect(loaderSrc).toMatch(/from\s+["']@auralprimer\/auralsong\/refinement["']/);
    expect(loaderSrc).not.toMatch(/from\s+["']@auralprimer\/auralsong["']/);
  });
});
