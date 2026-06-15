/**
 * Pure-helper tests for the Refine workspace's session/decision model.
 *
 * The Tauri-bound read/write paths (loadCandidates / loadDecisions /
 * saveDecisions) are exercised end-to-end via Studio runtime; here we
 * just lock down the in-memory decision invariants the workspace
 * controller depends on.
 */
import { describe, expect, it } from "vitest";
import {
  activeNotesForRegion,
  clearDecisionForRegion,
  pickCandidateForRegion,
  type RefineSession,
} from "../src/refineCandidatesIo";

function note(t_on: number, pitch: number) {
  return { t_on, t_off: t_on + 0.5, pitch, velocity: 90 };
}

function makeSession(): RefineSession {
  return {
    containerPath: "/tmp/test.auralsong",
    instrument: "keys",
    candidates: {
      version: "0.1.0",
      instrument: "keys",
      song_duration_sec: 8,
      candidates: {
        a: { label: "A", color: "#aabbcc" },
        b: { label: "B", color: "#112233" },
      },
      regions: [
        {
          id: "r0",
          t_start: 0,
          t_end: 4,
          hot_spot_type: "low_confidence",
          confidence: 0.5,
          auto_picked: "a",
          candidate_scores: { a: 0.8, b: 0.3 },
          candidate_notes: {
            a: [note(0.5, 60), note(1.0, 62)],
            b: [note(0.5, 60)],
          },
        },
      ],
    },
    decisions: new Map(),
  };
}

describe("activeNotesForRegion", () => {
  it("returns auto-picked notes when no decision", () => {
    const session = makeSession();
    const active = activeNotesForRegion(session, "r0");
    expect(active?.candidate_id).toBe("a");
    expect(active?.source).toBe("auto");
    expect(active?.notes).toHaveLength(2);
  });

  it("returns user-picked notes when decision exists", () => {
    const session = makeSession();
    pickCandidateForRegion(session, "r0", "b");
    const active = activeNotesForRegion(session, "r0");
    expect(active?.candidate_id).toBe("b");
    expect(active?.source).toBe("user");
    expect(active?.notes).toHaveLength(1);
  });

  it("returns null for unknown region", () => {
    expect(activeNotesForRegion(makeSession(), "ghost")).toBeNull();
  });
});

describe("pickCandidateForRegion", () => {
  it("creates a decision entry", () => {
    const s = makeSession();
    expect(s.decisions.size).toBe(0);
    expect(pickCandidateForRegion(s, "r0", "b")).toBe(true);
    expect(s.decisions.size).toBe(1);
    expect(s.decisions.get("r0")?.candidate_id).toBe("b");
  });

  it("rejects unknown candidate id", () => {
    const s = makeSession();
    expect(pickCandidateForRegion(s, "r0", "ghost")).toBe(false);
    expect(s.decisions.size).toBe(0);
  });

  it("rejects unknown region id", () => {
    const s = makeSession();
    expect(pickCandidateForRegion(s, "ghost", "a")).toBe(false);
  });
});

describe("clearDecisionForRegion", () => {
  it("removes the decision", () => {
    const s = makeSession();
    pickCandidateForRegion(s, "r0", "b");
    expect(clearDecisionForRegion(s, "r0")).toBe(true);
    expect(s.decisions.size).toBe(0);
  });

  it("returns false when no decision existed", () => {
    expect(clearDecisionForRegion(makeSession(), "r0")).toBe(false);
  });
});
