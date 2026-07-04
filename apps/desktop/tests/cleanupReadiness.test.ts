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
  getRoleReadiness,
  melodicStemRoles,
  detectMelodicStems,
  classifySpectroResult,
  classifyCandidateResult,
  parseSidecarStatusLine,
  _resetReadinessCachesForTest,
  type SidecarRunResult,
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
function mockBackend(opts: { exists?: (rel: string) => boolean; stems?: string[] }): void {
  invokeMock.mockImplementation((cmd: string, args: { relPath?: string }) => {
    if (cmd === "read_auralsong_json") {
      const ok = opts.exists ? opts.exists(args.relPath ?? "") : false;
      return ok ? Promise.resolve({}) : Promise.reject(new Error("not found"));
    }
    if (cmd === "get_auralsong_details") {
      return Promise.resolve({ manifest_raw: { stems: (opts.stems ?? []).map((id) => ({ id })) } });
    }
    return Promise.reject(new Error(`unexpected ${cmd}`));
  });
}

describe("featureDir", () => {
  it("maps .feedpak -> aural/ and legacy -> features/", () => {
    expect(featureDir("/songs/x.feedpak")).toBe("aural");
    expect(featureDir("/songs/x.auralsong")).toBe("features");
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
    // Collapsed to a single guitar; the legacy lead/rhythm splits aren't melodic roles anymore.
    expect(await melodicStemRoles("/x.feedpak")).toEqual(["guitar"]);
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
});
