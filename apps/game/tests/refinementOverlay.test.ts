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
import type { RefinementFile } from "@auralprimer/songpack/refinement";

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
    // Edge case: SongPack convention is one file per instrument, but the loader
    // must be deterministic when several arrive together. Later refinements
    // operate on the result of earlier ones.
    const refA = refinement("keys", [region(0.0, 1.0, [n(0.6, 0.9, 71)])]);
    const refB = refinement("keys", [region(2.5, 3.5, [n(3.1, 3.2, 75)])]);
    const out = applyRefinementsToMelodicTracks([keys], [refA, refB]);
    expect(out[0].notes.map((m) => m.pitch)).toEqual([71, 62, 64, 75]);
  });
});

describe("main.ts wiring (source-level pin)", () => {
  const src = read("apps/game/src/main.ts");

  it("imports applyRefinementsToMelodicTracks from chartLoader", () => {
    expect(src).toMatch(/applyRefinementsToMelodicTracks/);
    expect(src).toMatch(/from\s+["']\.\/chartLoader["']/);
  });

  it("imports validateRefinement + RefinementFile from songpack/refinement (deep, browser-safe)", () => {
    expect(src).toMatch(/validateRefinement[^\n]*RefinementFile/);
    // Must NOT use the top-level barrel — that pulls discoverSongPacks (which
    // imports node:fs) into the game bundle and breaks Vite build. The deep
    // path bypasses the barrel.
    expect(src).toMatch(/from\s+["']@auralprimer\/songpack\/refinement["']/);
  });

  it("defines loadRefinementsForRoles using read_songpack_json", () => {
    expect(src).toMatch(/async\s+function\s+loadRefinementsForRoles\s*\(/);
    // Plumbs through the existing Tauri command + the per-instrument filename convention.
    expect(src).toMatch(/read_songpack_json/);
    expect(src).toMatch(/features\/refinement\.\$\{role\}\.json/);
  });

  it("applies the refinements before assigning selectedMelodicTracks", () => {
    // The notes.mid load must call applyRefinementsToMelodicTracks so the
    // base track is overlaid before being handed to the rest of the app.
    expect(src).toMatch(
      /selectedMelodicTracks\s*=\s*applyRefinementsToMelodicTracks\s*\(\s*baseMelodicTracks\s*,\s*refinements\s*\)/
    );
  });

  it("invalid refinement files are caught and skipped, not fatal", () => {
    // The loader must use validateRefinement and warn on .ok === false, never
    // throw — a broken refinement should never block the base notes.mid track.
    expect(src).toMatch(/validateRefinement\s*\(\s*raw\s*\)/);
    expect(src).toMatch(/result\.ok/);
  });
});
