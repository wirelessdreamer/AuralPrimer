// @vitest-environment jsdom
/**
 * Tests for the Refine workspace's session/decision model.
 *
 * In-memory decision invariants are pinned directly. The Tauri-bound
 * read/write paths (loadCandidates / loadDecisions / loadSession /
 * saveDecisions) are exercised against a mocked `invoke` so load/save/
 * error/not-found branches are covered without a real Tauri backend.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import {
  activeNotesForRegion,
  clearDecisionForRegion,
  pickCandidateForRegion,
  loadCandidates,
  loadDecisions,
  loadSession,
  saveDecisions,
  type RefineSession,
} from "../src/refineCandidatesIo";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = invoke as unknown as ReturnType<typeof vi.fn>;

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

describe("activeNotesForRegion (auto-picked fallback to empty)", () => {
  it("returns empty notes when the auto_picked candidate has no notes entry", () => {
    const s = makeSession();
    // auto_picked points at "a" but drop its notes entry.
    delete (s.candidates.regions[0].candidate_notes as Record<string, unknown>).a;
    const active = activeNotesForRegion(s, "r0");
    expect(active?.source).toBe("auto");
    expect(active?.notes).toEqual([]);
  });
});

// ─── Tauri-bound I/O ────────────────────────────────────────────────────────

function candidatesFile() {
  return makeSession().candidates;
}

function refinementFile() {
  return {
    version: "0.1.0",
    instrument: "keys",
    regions: [
      {
        id: "r0",
        t_start: 0,
        t_end: 4,
        accepted_candidate: "b",
        accepted_at: "2024-01-01T00:00:00.000Z",
        source_candidate_id: "b",
        edited_at: "2024-01-01T00:00:00.000Z",
        notes: [note(0.5, 60)],
      },
    ],
  };
}

describe("loadCandidates", () => {
  beforeEach(() => invokeMock.mockReset());

  it("returns the validated file on success", async () => {
    invokeMock.mockResolvedValueOnce(candidatesFile());
    const got = await loadCandidates("/c", "keys");
    expect(got?.instrument).toBe("keys");
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/c",
      relPath: "features/refine_candidates.keys.json",
    });
  });

  it("returns null when the file is not found", async () => {
    invokeMock.mockRejectedValueOnce(new Error("file not found"));
    expect(await loadCandidates("/c", "keys")).toBeNull();
  });

  it("throws a wrapped error for other read failures", async () => {
    invokeMock.mockRejectedValueOnce(new Error("permission denied"));
    await expect(loadCandidates("/c", "keys")).rejects.toThrow(/failed to read/);
  });

  it("throws when the candidates file is malformed", async () => {
    invokeMock.mockResolvedValueOnce({ version: "bad", instrument: "keys" });
    await expect(loadCandidates("/c", "keys")).rejects.toThrow(/malformed/);
  });

  it("reads from aural/ for a feedpak container", async () => {
    invokeMock.mockResolvedValueOnce(candidatesFile());
    await loadCandidates("/songs/x.feedpak", "keys");
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/songs/x.feedpak",
      relPath: "aural/refine_candidates.keys.json",
    });
  });
});

describe("loadDecisions", () => {
  beforeEach(() => invokeMock.mockReset());

  it("maps regions into decisions keyed by id", async () => {
    invokeMock.mockResolvedValueOnce(refinementFile());
    const decisions = await loadDecisions("/c", "keys");
    expect(decisions.size).toBe(1);
    expect(decisions.get("r0")?.candidate_id).toBe("b");
    expect(decisions.get("r0")?.edited_at).toBe("2024-01-01T00:00:00.000Z");
  });

  it("defaults candidate_id to 'manual' and fills edited_at when absent", async () => {
    const file = refinementFile();
    delete (file.regions[0] as Record<string, unknown>).source_candidate_id;
    delete (file.regions[0] as Record<string, unknown>).edited_at;
    invokeMock.mockResolvedValueOnce(file);
    const decisions = await loadDecisions("/c", "keys");
    expect(decisions.get("r0")?.candidate_id).toBe("manual");
    expect(typeof decisions.get("r0")?.edited_at).toBe("string");
  });

  it("returns an empty map when the refinement file is missing", async () => {
    invokeMock.mockRejectedValueOnce(new Error("NotFound"));
    expect((await loadDecisions("/c", "keys")).size).toBe(0);
  });

  it("throws a wrapped error for other read failures", async () => {
    invokeMock.mockRejectedValueOnce(new Error("disk error"));
    await expect(loadDecisions("/c", "keys")).rejects.toThrow(/failed to read/);
  });

  it("throws when the refinement file is malformed", async () => {
    invokeMock.mockResolvedValueOnce({ version: "0.1.0", instrument: "keys", regions: [{}] });
    await expect(loadDecisions("/c", "keys")).rejects.toThrow(/malformed/);
  });
});

describe("loadSession", () => {
  beforeEach(() => invokeMock.mockReset());

  it("returns null when candidates are missing", async () => {
    invokeMock.mockRejectedValueOnce(new Error("not found"));
    expect(await loadSession("/c", "keys")).toBeNull();
  });

  it("builds a session from candidates + decisions", async () => {
    invokeMock
      .mockResolvedValueOnce(candidatesFile()) // loadCandidates
      .mockResolvedValueOnce(refinementFile()); // loadDecisions
    const session = await loadSession("/c", "keys");
    expect(session?.containerPath).toBe("/c");
    expect(session?.decisions.size).toBe(1);
  });

  it("falls back to empty decisions when refinement.json is corrupt", async () => {
    invokeMock
      .mockResolvedValueOnce(candidatesFile()) // loadCandidates ok
      .mockResolvedValueOnce({ version: "0.1.0", instrument: "keys", regions: [{}] }); // bad refinement
    const session = await loadSession("/c", "keys");
    expect(session).not.toBeNull();
    expect(session?.decisions.size).toBe(0);
  });
});

describe("saveDecisions", () => {
  beforeEach(() => invokeMock.mockReset());

  it("writes only regions with decisions, sorted by t_start", async () => {
    invokeMock.mockResolvedValueOnce(undefined);
    const s = makeSession();
    // Add a second region so we can confirm sort + decision filtering.
    s.candidates.regions.push({
      id: "r1",
      t_start: 5,
      t_end: 8,
      hot_spot_type: "low_confidence",
      confidence: 0.4,
      auto_picked: "a",
      candidate_scores: { a: 0.6 },
      candidate_notes: { a: [note(5.5, 64)] },
    });
    pickCandidateForRegion(s, "r1", "a");
    pickCandidateForRegion(s, "r0", "b");

    await saveDecisions(s);

    expect(invokeMock).toHaveBeenCalledWith(
      "write_auralsong_features_json",
      expect.objectContaining({
        containerPath: "/tmp/test.auralsong",
        relPath: "features/refinement.keys.json",
      }),
    );
    const written = invokeMock.mock.calls[0][1].value as { regions: Array<{ id: string; t_start: number }> };
    expect(written.regions.map((r) => r.id)).toEqual(["r0", "r1"]);
    expect(written.regions[0].t_start).toBeLessThan(written.regions[1].t_start);
  });

  it("writes an empty regions array when there are no decisions", async () => {
    invokeMock.mockResolvedValueOnce(undefined);
    await saveDecisions(makeSession());
    const written = invokeMock.mock.calls[0][1].value as { regions: unknown[] };
    expect(written.regions).toEqual([]);
  });

  it("writes refinement under aural/ for a feedpak container", async () => {
    invokeMock.mockResolvedValueOnce(undefined);
    const s = makeSession();
    s.containerPath = "/songs/x.feedpak";
    await saveDecisions(s);
    expect(invokeMock).toHaveBeenCalledWith(
      "write_auralsong_features_json",
      expect.objectContaining({
        containerPath: "/songs/x.feedpak",
        relPath: "aural/refinement.keys.json",
      }),
    );
  });
});
