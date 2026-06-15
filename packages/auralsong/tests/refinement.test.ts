import { describe, expect, it } from "vitest";
import {
  applyRefinementOverlay,
  findOverlappingRegions,
  validateRefinement,
  type RefinementFile,
  type RefinementNote,
} from "../src/refinement";

function n(t_on: number, t_off: number, pitch: number, velocity = 80): RefinementNote {
  return { t_on, t_off, pitch, velocity };
}

const MIN_REFINEMENT: RefinementFile = {
  version: "1.0.0",
  instrument: "keys",
  regions: [],
};

function regionWith(
  t_start: number,
  t_end: number,
  notes: RefinementNote[],
  id = `r-${t_start}`,
): RefinementFile["regions"][number] {
  return {
    id,
    t_start,
    t_end,
    accepted_candidate: "consensus_default",
    accepted_at: "2026-05-30T00:00:00Z",
    notes,
  };
}

describe("validateRefinement", () => {
  it("accepts a minimal valid file with empty regions", () => {
    const r = validateRefinement(MIN_REFINEMENT);
    expect(r.ok).toBe(true);
  });

  it("rejects a non-object payload", () => {
    expect(validateRefinement(null).ok).toBe(false);
    expect(validateRefinement("nope").ok).toBe(false);
    expect(validateRefinement([] as unknown).ok).toBe(false);
  });

  it("rejects bad version strings", () => {
    const r = validateRefinement({ ...MIN_REFINEMENT, version: "1.0" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.some((e) => e.path === "$.version")).toBe(true);
    }
  });

  it("rejects unknown instruments", () => {
    const r = validateRefinement({ ...MIN_REFINEMENT, instrument: "tuba" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.some((e) => e.path === "$.instrument")).toBe(true);
    }
  });

  it("validates a region's notes", () => {
    const bad = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1, 2, [{ t_on: 0.5, t_off: 0.4, pitch: 60, velocity: 80 }])],
    };
    const r = validateRefinement(bad);
    expect(r.ok).toBe(false);
  });

  it("rejects t_end <= t_start", () => {
    const bad = { ...MIN_REFINEMENT, regions: [regionWith(2, 2, [])] };
    const r = validateRefinement(bad);
    expect(r.ok).toBe(false);
  });

  it("rejects missing accepted_candidate", () => {
    const region = { ...regionWith(1, 2, []) };
    delete (region as { accepted_candidate?: string }).accepted_candidate;
    const r = validateRefinement({ ...MIN_REFINEMENT, regions: [region] });
    expect(r.ok).toBe(false);
  });

  it("rejects out-of-range MIDI pitch", () => {
    const r = validateRefinement({
      ...MIN_REFINEMENT,
      regions: [regionWith(1, 2, [{ t_on: 1.1, t_off: 1.4, pitch: 200, velocity: 80 }])],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects out-of-range velocity", () => {
    const r = validateRefinement({
      ...MIN_REFINEMENT,
      regions: [regionWith(1, 2, [{ t_on: 1.1, t_off: 1.4, pitch: 60, velocity: 0 }])],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects confidence outside 0..1", () => {
    const r = validateRefinement({
      ...MIN_REFINEMENT,
      regions: [{ ...regionWith(1, 2, []), confidence: 1.5 }],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects unknown hot_spot_type", () => {
    const r = validateRefinement({
      ...MIN_REFINEMENT,
      regions: [{ ...regionWith(1, 2, []), hot_spot_type: "made_up" }],
    });
    expect(r.ok).toBe(false);
  });
});

describe("applyRefinementOverlay", () => {
  const base = [n(0.5, 0.9, 60), n(1.2, 1.5, 62), n(1.6, 1.9, 64), n(3.0, 3.3, 67)];

  it("returns a copy of base when refinement is null", () => {
    const out = applyRefinementOverlay(base, null);
    expect(out).toEqual(base);
    expect(out).not.toBe(base); // copy, not same reference
  });

  it("returns a copy of base when refinement has no regions", () => {
    const out = applyRefinementOverlay(base, MIN_REFINEMENT);
    expect(out).toEqual(base);
  });

  it("removes base notes whose t_on falls inside a region and adds the region's notes", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1.0, 2.0, [n(1.2, 1.5, 65), n(1.6, 1.9, 67)])],
    };
    const out = applyRefinementOverlay(base, ref);
    // Base notes at t_on = 1.2, 1.6 (inside [1.0, 2.0)) removed; 0.5, 3.0 kept.
    // Region adds 1.2 (pitch 65) + 1.6 (pitch 67).
    expect(out).toEqual([
      n(0.5, 0.9, 60),
      n(1.2, 1.5, 65),
      n(1.6, 1.9, 67),
      n(3.0, 3.3, 67),
    ]);
  });

  it("keeps notes whose t_on equals the region t_end (half-open interval [t_start, t_end))", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1.0, 1.2, [n(1.05, 1.18, 70)])],
    };
    const out = applyRefinementOverlay([n(1.2, 1.4, 62)], ref);
    // n(1.2) is NOT in [1.0, 1.2) so kept; region adds 1.05.
    expect(out).toEqual([n(1.05, 1.18, 70), n(1.2, 1.4, 62)]);
  });

  it("handles a region with an empty notes array (= pure deletion)", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1.0, 2.0, [])],
    };
    const out = applyRefinementOverlay(base, ref);
    expect(out).toEqual([n(0.5, 0.9, 60), n(3.0, 3.3, 67)]);
  });

  it("merges multiple disjoint regions", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [
        regionWith(0.0, 1.0, [n(0.55, 0.85, 72)], "r0"),
        regionWith(2.8, 3.5, [n(2.95, 3.30, 75)], "r1"),
      ],
    };
    const out = applyRefinementOverlay(base, ref);
    // 0.5 is in [0, 1) -> removed; 3.0 in [2.8, 3.5) -> removed.
    // Both region notes added; 1.2, 1.6 untouched.
    expect(out).toEqual([
      n(0.55, 0.85, 72),
      n(1.2, 1.5, 62),
      n(1.6, 1.9, 64),
      n(2.95, 3.30, 75),
    ]);
  });

  it("does not mutate the base array", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1.0, 2.0, [n(1.5, 1.8, 70)])],
    };
    const snapshot = base.map((x) => ({ ...x }));
    applyRefinementOverlay(base, ref);
    expect(base).toEqual(snapshot);
  });

  it("returns notes sorted by t_on regardless of input order", () => {
    const unsorted = [n(3.0, 3.3, 67), n(0.5, 0.9, 60)];
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(1.0, 2.0, [n(1.9, 1.95, 70), n(1.0, 1.5, 65)])],
    };
    const out = applyRefinementOverlay(unsorted, ref);
    expect(out.map((x) => x.t_on)).toEqual([0.5, 1.0, 1.9, 3.0]);
  });

  it("preserves extra fields on base notes (caller's note type can extend RefinementNote)", () => {
    type Ext = RefinementNote & { color: string };
    const ext: Ext[] = [{ ...n(0.5, 0.9, 60), color: "green" }];
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(2.0, 3.0, [])],
    };
    const out = applyRefinementOverlay(ext, ref);
    expect(out[0].color).toBe("green");
  });
});

describe("findOverlappingRegions", () => {
  it("returns empty for disjoint regions", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(0, 1, [], "a"), regionWith(2, 3, [], "b")],
    };
    expect(findOverlappingRegions(ref)).toEqual([]);
  });

  it("returns adjacent (touching) regions as NOT overlapping", () => {
    // [0, 1) and [1, 2) are adjacent but disjoint
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(0, 1, [], "a"), regionWith(1, 2, [], "b")],
    };
    expect(findOverlappingRegions(ref)).toEqual([]);
  });

  it("flags actual overlap", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(0, 1.5, [], "a"), regionWith(1.0, 2.0, [], "b")],
    };
    const pairs = findOverlappingRegions(ref);
    expect(pairs).toHaveLength(1);
    expect(pairs[0].a.id).toBe("a");
    expect(pairs[0].b.id).toBe("b");
  });

  it("flags a region wholly inside another", () => {
    const ref: RefinementFile = {
      ...MIN_REFINEMENT,
      regions: [regionWith(0, 5, [], "outer"), regionWith(2, 3, [], "inner")],
    };
    const pairs = findOverlappingRegions(ref);
    expect(pairs).toHaveLength(1);
  });
});
