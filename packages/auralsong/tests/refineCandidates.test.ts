import { describe, expect, it } from "vitest";
import {
  validateRefineCandidates,
  type RefineCandidatesFile,
} from "../src/refineCandidates";

const MIN: RefineCandidatesFile = {
  version: "1.0.0",
  instrument: "keys",
  song_duration_sec: 180,
  candidates: {
    stem_only: { label: "Stem-only", color: "#9BA3AC" },
    consensus_default: { label: "Consensus 100/2", color: "#3EE6A8" },
  },
  regions: [],
};

describe("validateRefineCandidates", () => {
  it("accepts a minimal valid file", () => {
    expect(validateRefineCandidates(MIN).ok).toBe(true);
  });

  it("accepts vocals as a candidate instrument", () => {
    expect(validateRefineCandidates({ ...MIN, instrument: "vocals" }).ok).toBe(true);
  });

  it("rejects bad version", () => {
    const r = validateRefineCandidates({ ...MIN, version: "1.0" });
    expect(r.ok).toBe(false);
  });

  it("rejects unknown instrument", () => {
    const r = validateRefineCandidates({ ...MIN, instrument: "tuba" });
    expect(r.ok).toBe(false);
  });

  it("rejects non-positive song_duration_sec", () => {
    const r = validateRefineCandidates({ ...MIN, song_duration_sec: 0 });
    expect(r.ok).toBe(false);
  });

  it("requires at least one candidate", () => {
    const r = validateRefineCandidates({ ...MIN, candidates: {} });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.some((e) => e.path === "$.candidates")).toBe(true);
    }
  });

  it("rejects a candidate with no label", () => {
    const r = validateRefineCandidates({
      ...MIN,
      candidates: { only: { label: "", color: "#000000" } },
    });
    expect(r.ok).toBe(false);
  });

  it("rejects a candidate with a non-hex color", () => {
    const r = validateRefineCandidates({
      ...MIN,
      candidates: { only: { label: "x", color: "blue" } },
    });
    expect(r.ok).toBe(false);
  });

  it("rejects auto_picked that isn't declared in candidates", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "r1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "octave_ghost",
          confidence: 0.6,
          auto_picked: "not_a_real_candidate",
          candidate_scores: { stem_only: 0.5 },
          candidate_notes: { stem_only: [] },
        },
      ],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects candidate_scores referencing unknown candidates", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "r1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "octave_ghost",
          confidence: 0.6,
          auto_picked: "stem_only",
          candidate_scores: { stem_only: 0.5, ghost_candidate: 0.7 },
          candidate_notes: { stem_only: [] },
        },
      ],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects scores outside 0..1", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "r1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "octave_ghost",
          confidence: 0.6,
          auto_picked: "stem_only",
          candidate_scores: { stem_only: 1.5 },
          candidate_notes: { stem_only: [] },
        },
      ],
    });
    expect(r.ok).toBe(false);
  });

  it("rejects unknown hot_spot_type", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "r1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "made_up",
          confidence: 0.6,
          auto_picked: "stem_only",
          candidate_scores: { stem_only: 0.5 },
          candidate_notes: { stem_only: [] },
        },
      ],
    });
    expect(r.ok).toBe(false);
  });

  it("accepts a fully populated region", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "verse1-bar3",
          t_start: 14.85,
          t_end: 18.23,
          section_label: "Verse 1",
          hot_spot_type: "octave_ghost",
          confidence: 0.62,
          chord: "Bb",
          auto_picked: "consensus_default",
          candidate_scores: { stem_only: 0.4, consensus_default: 0.78 },
          candidate_notes: {
            stem_only: [{ t_on: 15.0, t_off: 15.4, pitch: 60, velocity: 80 }],
            consensus_default: [{ t_on: 15.0, t_off: 15.4, pitch: 60, velocity: 80 }],
          },
        },
      ],
    });
    expect(r.ok).toBe(true);
  });

  it("accepts candidate notes with string/fret metadata", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "riff1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "clean",
          confidence: 0.9,
          auto_picked: "stem_only",
          candidate_scores: { stem_only: 0.9 },
          candidate_notes: {
            stem_only: [{ t_on: 1.1, t_off: 1.5, pitch: 64, velocity: 90, string: 2, fret: 7, s: 2, f: 7 }],
          },
        },
      ],
    });

    expect(r.ok).toBe(true);
  });

  it("rejects candidate note string/fret metadata outside the supported range", () => {
    const r = validateRefineCandidates({
      ...MIN,
      regions: [
        {
          id: "riff1",
          t_start: 1,
          t_end: 2,
          hot_spot_type: "clean",
          confidence: 0.9,
          auto_picked: "stem_only",
          candidate_scores: { stem_only: 0.9 },
          candidate_notes: {
            stem_only: [{ t_on: 1.1, t_off: 1.5, pitch: 64, velocity: 90, string: 9, fret: 37 }],
          },
        },
      ],
    });

    expect(r.ok).toBe(false);
  });
});
