// @vitest-environment jsdom
/**
 * Execution tests for the instrument-hint pure helpers. These have no DOM or
 * Tauri coupling, so the tests just import each exported function and exercise
 * every regex / switch / walk branch against representative inputs.
 */
import { describe, it, expect } from "vitest";
import {
  asObjectRecord,
  applyInstrumentHintsFromToken,
  applyInstrumentHintsFromChartJson,
  applyInstrumentHintsFromMappedRole,
  applyInstrumentHintsFromManifestRaw,
  type InstrumentFlags,
} from "../src/instrumentHints";

describe("instrumentHints", () => {
  describe("asObjectRecord", () => {
    it("returns the record for a plain object", () => {
      const o = { a: 1 };
      expect(asObjectRecord(o)).toBe(o);
    });
    it("returns null for null / non-object / array", () => {
      expect(asObjectRecord(null)).toBeNull();
      expect(asObjectRecord(undefined)).toBeNull();
      expect(asObjectRecord(5)).toBeNull();
      expect(asObjectRecord("x")).toBeNull();
      expect(asObjectRecord([1, 2])).toBeNull();
    });
  });

  describe("applyInstrumentHintsFromToken", () => {
    it("does nothing for an empty token", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromToken("", f);
      expect(f).toEqual({});
    });

    it("flags rhythm guitar", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromToken("Rhythm_Guitar", f);
      expect(f.rhythm_guitar).toBe(true);
      // 'guitar' present but 'rhythm' also present -> lead NOT set by the gtr rule.
      expect(f.lead_guitar).toBeUndefined();
    });

    it("flags lead guitar from explicit lead and from gtr token", () => {
      const f1: InstrumentFlags = {};
      applyInstrumentHintsFromToken("lead-guitar", f1);
      expect(f1.lead_guitar).toBe(true);

      const f2: InstrumentFlags = {};
      applyInstrumentHintsFromToken("gtr", f2);
      expect(f2.lead_guitar).toBe(true);
    });

    it("flags bass, keys, vocals, drums", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromToken("bass", f);
      applyInstrumentHintsFromToken("piano", f);
      applyInstrumentHintsFromToken("vox", f);
      applyInstrumentHintsFromToken("snare", f);
      expect(f.bass).toBe(true);
      expect(f.keys).toBe(true);
      expect(f.vocals).toBe(true);
      expect(f.drums).toBe(true);
    });

    it("flags lead guitar for a single five-fret lane letter", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromToken("green", f);
      expect(f.lead_guitar).toBe(true);
    });

    it("leaves unrelated tokens untouched", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromToken("xyzzy", f);
      expect(f).toEqual({});
    });
  });

  describe("applyInstrumentHintsFromChartJson", () => {
    it("ignores non-object json", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromChartJson(null, f);
      applyInstrumentHintsFromChartJson("nope", f);
      expect(f).toEqual({});
    });

    it("walks mode / instrument / instruments[] / targets[]", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromChartJson(
        {
          mode: "drums",
          instrument: "bass",
          instruments: ["keys", 42],
          targets: [
            { lane: "vox" },
            { instrument: "lead_guitar" },
            "not-an-object",
            { other: 1 },
          ],
        },
        f,
      );
      expect(f.drums).toBe(true);
      expect(f.bass).toBe(true);
      expect(f.keys).toBe(true);
      expect(f.vocals).toBe(true);
      expect(f.lead_guitar).toBe(true);
    });

    it("returns early when targets is not an array", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromChartJson({ mode: "bass" }, f);
      expect(f.bass).toBe(true);
    });
  });

  describe("applyInstrumentHintsFromMappedRole", () => {
    it("maps each known role and ignores unknown", () => {
      const f: InstrumentFlags = {};
      for (const r of ["drums", "bass", "lead_guitar", "rhythm_guitar", "keys", "vocals", "mystery"]) {
        applyInstrumentHintsFromMappedRole(r, f);
      }
      expect(f).toEqual({
        drums: true,
        bass: true,
        lead_guitar: true,
        rhythm_guitar: true,
        keys: true,
        vocals: true,
      });
    });
  });

  describe("applyInstrumentHintsFromManifestRaw", () => {
    it("ignores a non-object manifest", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromManifestRaw(null, f);
      expect(f).toEqual({});
    });

    it("walks mapped_game_roles and midi.tracks[].role", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromManifestRaw(
        {
          source: { parts: { mapped_game_roles: ["drums", 1] } },
          assets: { midi: { tracks: [{ role: "bass" }, "bad", { role: 3 }] } },
        },
        f,
      );
      expect(f.drums).toBe(true);
      expect(f.bass).toBe(true);
    });

    it("tolerates missing source/assets sub-objects", () => {
      const f: InstrumentFlags = {};
      applyInstrumentHintsFromManifestRaw({}, f);
      expect(f).toEqual({});
    });
  });
});
