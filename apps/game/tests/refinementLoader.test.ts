// @vitest-environment jsdom
/**
 * Execution tests for loadRefinementsForRoles. Exercises the per-role
 * best-effort fetch loop: success accumulation, missing-file (invoke throws)
 * skip, null-result skip, and validation-failure warn+skip paths.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { RefinementFile } from "@auralprimer/auralsong/refinement";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

const validateRefinement = vi.fn();
vi.mock("@auralprimer/auralsong/refinement", () => ({
  validateRefinement: (raw: unknown) => validateRefinement(raw),
}));

import { loadRefinementsForRoles } from "../src/refinementLoader";
import type { InstrumentRole } from "../src/chartLoader";

const ROLES: InstrumentRole[] = ["bass", "keys"];

function fakeRefinement(instrument: string): RefinementFile {
  return {
    version: "1.0.0",
    instrument: instrument as RefinementFile["instrument"],
    regions: [],
  };
}

describe("loadRefinementsForRoles", () => {
  beforeEach(() => {
    invoke.mockReset();
    validateRefinement.mockReset();
  });

  it("returns validated refinements for every role that has a valid file", async () => {
    invoke.mockImplementation(async (_cmd: string, args: { relPath: string }) => ({
      relPath: args.relPath,
    }));
    validateRefinement.mockImplementation((raw: { relPath: string }) => ({
      ok: true,
      value: fakeRefinement(raw.relPath),
    }));
    const warn = vi.fn();

    const out = await loadRefinementsForRoles("/song", ROLES, { warn });

    expect(out).toHaveLength(2);
    expect(invoke).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/song",
      relPath: "features/refinement.bass.json",
    });
    expect(invoke).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/song",
      relPath: "features/refinement.keys.json",
    });
    expect(warn).not.toHaveBeenCalled();
  });

  it("silently skips a role whose file is missing (invoke throws)", async () => {
    invoke.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath.includes("bass")) throw new Error("ENOENT");
      return { ok: 1 };
    });
    validateRefinement.mockReturnValue({ ok: true, value: fakeRefinement("keys") });
    const warn = vi.fn();

    const out = await loadRefinementsForRoles("/song", ROLES, { warn });

    expect(out).toHaveLength(1);
    expect(out[0].instrument).toBe("keys");
    expect(warn).not.toHaveBeenCalled();
  });

  it("skips a role when the read returns null (no validation attempted)", async () => {
    invoke.mockResolvedValue(null);
    const warn = vi.fn();

    const out = await loadRefinementsForRoles("/song", ROLES, { warn });

    expect(out).toHaveLength(0);
    expect(validateRefinement).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  it("warns and skips a role whose file fails validation", async () => {
    invoke.mockResolvedValue({ bad: true });
    validateRefinement.mockReturnValue({
      ok: false,
      errors: [{ path: "version", message: "missing" }],
    });
    const warn = vi.fn();

    const out = await loadRefinementsForRoles("/song", ["bass"], { warn });

    expect(out).toHaveLength(0);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      "play",
      "refinement.bass.json failed validation; ignoring",
      [{ path: "version", message: "missing" }],
    );
  });

  it("returns an empty array when given no roles", async () => {
    const out = await loadRefinementsForRoles("/song", [], { warn: vi.fn() });
    expect(out).toEqual([]);
    expect(invoke).not.toHaveBeenCalled();
  });

  it("mixes valid, invalid, missing and null roles in one pass", async () => {
    const roles: InstrumentRole[] = ["bass", "keys", "lead_guitar", "rhythm_guitar"];
    invoke.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath.includes("bass")) return { tag: "bass" };
      if (args.relPath.includes("keys")) throw new Error("missing");
      if (args.relPath.includes("lead_guitar")) return null;
      return { tag: "rhythm" };
    });
    validateRefinement.mockImplementation((raw: { tag: string }) =>
      raw.tag === "bass"
        ? { ok: true, value: fakeRefinement("bass") }
        : { ok: false, errors: [{ path: "x", message: "y" }] },
    );
    const warn = vi.fn();

    const out = await loadRefinementsForRoles("/song", roles, { warn });

    expect(out).toHaveLength(1);
    expect(out[0].instrument).toBe("bass");
    // only rhythm_guitar reached validation and failed -> exactly one warn
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      "play",
      "refinement.rhythm_guitar.json failed validation; ignoring",
      [{ path: "x", message: "y" }],
    );
  });
});
