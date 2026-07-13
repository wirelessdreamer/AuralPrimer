/**
 * Unit tests for the Cleanup & Edit readiness + stem-detection logic. These
 * cover the exact bugs that previously slipped to the user: builds defaulting
 * to a non-existent "keys" stem on guitar songs, and a stem-less pack being
 * mis-reported as a hard "failed". Tauri `invoke` is mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invokeMock(...a) }));

import {
  featureDir,
  drumTabRelPath,
  getRoleReadiness,
  melodicStemRoles,
  detectMelodicStems,
  classifySpectroResult,
  classifyCandidateResult,
  parseSidecarStatusLine,
  needsArrangementPrep,
  arrangementCount,
  _resetReadinessCachesForTest,
  type SidecarRunResult,
  type AuralSongDetails,
} from "../src/cleanupReadiness";

beforeEach(() => {
  vi.clearAllMocks();
  invokeMock.mockReset();
  _resetReadinessCachesForTest();
});

function res(partial: Partial<SidecarRunResult>): SidecarRunResult {
  return { ok: false, exit_code: 0, stdout: "", stderr: "", ...partial };
}

/** Mock the existence probes (read_auralsong_json) + manifest (get_auralsong_details). */
function mockBackend(opts: { exists?: (rel: string) => boolean; stems?: string[]; drumTabRel?: string }): void {
  invokeMock.mockImplementation((cmd: string, args: { relPath?: string }) => {
    if (cmd === "read_auralsong_json") {
      const ok = opts.exists ? opts.exists(args.relPath ?? "") : false;
      return ok ? Promise.resolve({}) : Promise.reject(new Error("not found"));
    }
    if (cmd === "get_auralsong_details") {
      return Promise.resolve({
        manifest_raw: {
          stems: (opts.stems ?? []).map((id) => ({ id })),
          ...(opts.drumTabRel ? { drum_tab: opts.drumTabRel } : {}),
        },
      });
    }
    return Promise.reject(new Error(`unexpected ${cmd}`));
  });
}

describe("featureDir", () => {
  it("maps .feedpak -> aural/ and legacy -> features/", () => {
    expect(featureDir("/songs/x.feedpak")).toBe("aural");
    expect(featureDir("/songs/x.auralsong")).toBe("features");
  });
  it("maps .sloppak -> aural/ (a manifest pack, C2)", () => {
    expect(featureDir("/songs/x.sloppak")).toBe("aural");
    expect(featureDir("/data/songs/minimal.sloppak")).toBe("aural");
  });
});

describe("arrangementCount", () => {
  it("prefers an explicit numeric arrangement_count field", () => {
    expect(arrangementCount({ arrangement_count: 3 })).toBe(3);
  });
  it("falls back to a top-level arrangements array length", () => {
    expect(arrangementCount({ arrangements: [{ id: "lead" }, { id: "bass" }] })).toBe(2);
  });
  it("falls back to the raw manifest arrangements", () => {
    expect(arrangementCount({ manifest_raw: { arrangements: [{ id: "lead" }] } })).toBe(1);
  });
  it("is 0 when nothing is discoverable", () => {
    expect(arrangementCount({})).toBe(0);
    expect(arrangementCount(null)).toBe(0);
    expect(arrangementCount(undefined)).toBe(0);
  });
});

describe("needsArrangementPrep", () => {
  // Mirrors the minimal.sloppak fixture: manifest has 2 arrangements (lead, bass).
  const sloppakDetails: AuralSongDetails = {
    manifest_raw: { arrangements: [{ id: "lead" }, { id: "bass" }] },
  };

  it("is true for a sloppak with arrangements and no derived notes.mid", () => {
    expect(needsArrangementPrep("/songs/minimal.sloppak", sloppakDetails)).toBe(true);
  });
  it("is false once notes.mid exists", () => {
    expect(
      needsArrangementPrep("/songs/minimal.sloppak", { ...sloppakDetails, has_notes_mid: true }),
    ).toBe(false);
  });
  it("is true for a feedpak with arrangements and no notes.mid (same rule)", () => {
    expect(needsArrangementPrep("/songs/x.feedpak", { arrangement_count: 1 })).toBe(true);
  });
  it("is false for a legacy .auralsong (not a manifest pack)", () => {
    expect(needsArrangementPrep("/songs/x.auralsong", sloppakDetails)).toBe(false);
  });
  it("is false when the pack declares no arrangements", () => {
    expect(needsArrangementPrep("/songs/x.sloppak", { arrangement_count: 0 })).toBe(false);
    expect(needsArrangementPrep("/songs/x.sloppak", {})).toBe(false);
  });
  it("degrades to false on missing/undefined details rather than throwing", () => {
    expect(needsArrangementPrep("/songs/x.sloppak", null)).toBe(false);
    expect(needsArrangementPrep("/songs/x.sloppak", undefined)).toBe(false);
  });
});

describe("classifySpectroResult", () => {
  it("ok when the build succeeded", () => {
    expect(classifySpectroResult(res({ ok: true }))).toBe("ok");
  });
  it("nostem when ok:false with empty roles + no stderr (mix-only pack)", () => {
    expect(classifySpectroResult(res({ ok: false, stdout: '{"ok":false,"roles":{}}' }))).toBe("nostem");
  });
  it("error when ok:false with stderr", () => {
    expect(classifySpectroResult(res({ ok: false, stderr: "boom", stdout: "{}" }))).toBe("error");
  });
  it("error when ok:false but some roles actually built", () => {
    expect(classifySpectroResult(res({ ok: false, stdout: '{"roles":{"keys":{"ok":true}}}' }))).toBe("error");
  });
});

describe("classifyCandidateResult", () => {
  it("counts every requested instrument as built when all succeed", () => {
    const r = res({ ok: true, stdout: '{"instruments":{"bass":{"ok":true},"guitar":{"ok":true}},"ok":true}' });
    expect(classifyCandidateResult(r, ["bass", "guitar"])).toEqual({
      built: ["bass", "guitar"],
      skipped: [],
      failed: [],
    });
  });

  it("treats a silent stem ('no audible content') as a skip, not a failure", () => {
    // The band-song case: keys stem is silent, bass built fine. Top-level ok
    // is false, but that must NOT count keys as a failure.
    const r = res({
      ok: false,
      stdout:
        '{"instruments":{"keys":{"ok":false,"error":"no audible content for instrument=\'keys\' after the silence gate; skipping candidates for this stem"},"bass":{"ok":true}},"ok":false}',
    });
    expect(classifyCandidateResult(r, ["keys", "bass"])).toEqual({
      built: ["bass"],
      skipped: ["keys"],
      failed: [],
    });
  });

  it("counts a real per-instrument error as failed", () => {
    const r = res({ ok: false, stdout: '{"instruments":{"keys":{"ok":false,"error":"model crashed"}},"ok":false}' });
    expect(classifyCandidateResult(r, ["keys"])).toEqual({ built: [], skipped: [], failed: ["keys"] });
  });

  it("falls back to built on top-level ok with no per-instrument detail", () => {
    expect(classifyCandidateResult(res({ ok: true, stdout: "{}" }), ["keys"])).toEqual({
      built: ["keys"],
      skipped: [],
      failed: [],
    });
  });
});

describe("parseSidecarStatusLine", () => {
  it("parses the trailing JSON line", () => {
    expect(parseSidecarStatusLine("log line\nmore log\n{\"a\":1}")).toEqual({ a: 1 });
  });
  it("returns null on non-JSON", () => {
    expect(parseSidecarStatusLine("not json at all")).toBeNull();
  });
});

describe("getRoleReadiness", () => {
  it("probes the aural/ paths for a feedpak and reports spectrogram + candidates", async () => {
    const seen: string[] = [];
    invokeMock.mockImplementation((_cmd: string, args: { relPath?: string }) => {
      seen.push(args.relPath ?? "");
      return (args.relPath ?? "").includes("spectrogram")
        ? Promise.resolve({})
        : Promise.reject(new Error("nf"));
    });
    const r = await getRoleReadiness("/x.feedpak", "keys");
    expect(r.spectrogram).toBe(true);
    expect(r.candidates).toBe(false);
    expect(seen).toContain("aural/spectrogram/keys/spectrogram.json");
    expect(seen).toContain("aural/refine_candidates.keys.json");
  });

  it("probes vocal spectrogram and refine-candidate artifacts", async () => {
    const seen: string[] = [];
    invokeMock.mockImplementation((_cmd: string, args: { relPath?: string }) => {
      const rel = args.relPath ?? "";
      seen.push(rel);
      return rel.includes("spectrogram/vocals/") || rel.endsWith("refine_candidates.vocals.json")
        ? Promise.resolve({})
        : Promise.reject(new Error("nf"));
    });

    const r = await getRoleReadiness("/x.feedpak", "vocals");

    expect(r).toEqual({ spectrogram: true, candidates: true });
    expect(seen).toContain("aural/spectrogram/vocals/spectrogram.json");
    expect(seen).toContain("aural/refine_candidates.vocals.json");
  });

  it("for drums, the drum_tab.json plays the 'candidates' role (no refine_candidates file)", async () => {
    const seen: string[] = [];
    invokeMock.mockImplementation((_cmd: string, args: { relPath?: string }) => {
      const rel = args.relPath ?? "";
      seen.push(rel);
      return rel === "drum_tab.json" || rel.includes("spectrogram/drums/")
        ? Promise.resolve({})
        : Promise.reject(new Error("nf"));
    });
    const r = await getRoleReadiness("/x.feedpak", "drums");
    expect(r).toEqual({ spectrogram: true, candidates: true });
    expect(seen).toContain("drum_tab.json");
    expect(seen).not.toContain("aural/refine_candidates.drums.json");
  });

  it("for drums, manifest drum_tab paths play the 'candidates' role", async () => {
    const seen: string[] = [];
    mockBackend({
      drumTabRel: "custom/drums.json",
      exists: (rel) => {
        seen.push(rel);
        return rel === "custom/drums.json" || rel.includes("spectrogram/drums/");
      },
    });

    expect(await drumTabRelPath("/x.feedpak")).toBe("custom/drums.json");
    const r = await getRoleReadiness("/x.feedpak", "drums");

    expect(r).toEqual({ spectrogram: true, candidates: true });
    expect(seen).toContain("custom/drums.json");
    expect(seen).not.toContain("drum_tab.json");
    expect(seen).not.toContain("aural/refine_candidates.drums.json");
  });

  it("caches results, re-probing only when forced", async () => {
    mockBackend({ exists: () => false });
    await getRoleReadiness("/x.feedpak", "keys");
    const after1 = invokeMock.mock.calls.length;
    await getRoleReadiness("/x.feedpak", "keys"); // served from cache
    expect(invokeMock.mock.calls.length).toBe(after1);
    await getRoleReadiness("/x.feedpak", "keys", { force: true }); // re-probes
    expect(invokeMock.mock.calls.length).toBeGreaterThan(after1);
  });
});

describe("melodicStemRoles", () => {
  it("returns only the melodic roles present in the manifest stems", async () => {
    mockBackend({ stems: ["guitar", "lead_guitar", "rhythm_guitar", "guitar_split_source", "drums", "vocals"] });
    // Collapsed to a single guitar; the legacy lead/rhythm splits aren't probed.
    expect(await melodicStemRoles("/x.feedpak")).toEqual(["guitar", "vocals"]);
  });
  it("is empty for a mix-only pack", async () => {
    mockBackend({ stems: ["mix"] });
    expect(await melodicStemRoles("/x.feedpak")).toEqual([]);
  });
});

describe("detectMelodicStems", () => {
  it("REGRESSION: builds the guitar stem a pack actually has, not a hardcoded 'keys'", async () => {
    // No built artifacts yet; the manifest lists a guitar stem (+ legacy splits).
    mockBackend({ exists: () => false, stems: ["guitar", "lead_guitar", "rhythm_guitar"] });
    const { roles, primary } = await detectMelodicStems("/beat_it.feedpak");
    expect(roles).toEqual(["guitar"]);
    expect(primary).toBe("guitar"); // no keys present -> first real stem
  });

  it("falls back to 'keys' only when the pack lists no melodic stem", async () => {
    mockBackend({ exists: () => false, stems: ["mix"] });
    const { roles, primary } = await detectMelodicStems("/demo.feedpak");
    expect(roles).toEqual(["keys"]);
    expect(primary).toBe("keys");
  });

  it("prefers roles that already have built artifacts", async () => {
    mockBackend({
      exists: (rel) => rel.includes("/keys/") || rel.includes(".keys."),
      stems: [],
    });
    const { roles, primary } = await detectMelodicStems("/x.feedpak");
    expect(roles).toEqual(["keys"]);
    expect(primary).toBe("keys");
  });

  it("REGRESSION: a drums-only pack opens without any melodic stem (no bogus 'keys')", async () => {
    mockBackend({
      exists: (rel) => rel === "drum_tab.json" || rel.includes("spectrogram/drums/"),
      stems: ["drums"],
    });
    const { roles, primary, readiness } = await detectMelodicStems("/drumsonly.feedpak");
    expect(roles).toEqual(["drums"]);
    expect(primary).toBe("drums");
    expect(readiness.get("drums")).toEqual({ spectrogram: true, candidates: true });
  });

  it("offers drums alongside melodic instruments when the pack has both", async () => {
    mockBackend({
      exists: (rel) =>
        rel.includes("/keys/") || rel.includes(".keys.") || rel === "drum_tab.json" || rel.includes("spectrogram/drums/"),
      stems: ["keys", "drums"],
    });
    const { roles, primary } = await detectMelodicStems("/both.feedpak");
    expect(roles).toContain("keys");
    expect(roles).toContain("drums");
    expect(primary).toBe("keys"); // melodic stays the default; drums is a switch in the editor
  });

  it("offers vocals when vocal cleanup artifacts are present", async () => {
    mockBackend({
      exists: (rel) => rel.includes("/vocals/") || rel.includes(".vocals."),
      stems: ["vocals"],
    });
    const { roles, primary, readiness } = await detectMelodicStems("/vocals.feedpak");
    expect(roles).toContain("vocals");
    expect(primary).toBe("vocals");
    expect(readiness.get("vocals")).toEqual({ spectrogram: true, candidates: true });
  });
});
