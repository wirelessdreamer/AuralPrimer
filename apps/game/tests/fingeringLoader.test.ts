import { describe, expect, it, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";

import {
  applyFingeringToMelodicTracks,
  candidateFingeringPaths,
  fingeringFilesToMelodicTracks,
  fingeringRolesFromManifest,
  loadFingeringForRoles,
  validateFingering,
  type FingeringFile,
} from "../src/fingeringLoader";
import type { MelodicTrackSelection } from "../src/chartLoader";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = invoke as unknown as ReturnType<typeof vi.fn>;

describe("fingeringLoader", () => {
  beforeEach(() => {
    invokeMock.mockReset();
  });

  it("validates explicit string/fret metadata and compact s/f aliases", () => {
    const result = validateFingering({
      version: "1.0.0",
      instrument: "lead_guitar",
      notes: [
        { t_on: 0.5, t_off: 0.75, pitch: 64, velocity: 96, string: 1, fret: 5 },
        { t_on: 0.1, pitch: 60, s: 2, f: 8 },
      ],
    });

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("validation failed");
    expect(result.value.notes.map((n) => [n.t_on, n.pitch, n.string, n.fret])).toEqual([
      [0.1, 60, 2, 8],
      [0.5, 64, 1, 5],
    ]);
  });

  it("merges matching fingering notes onto melodic tracks without mutating timing", () => {
    const tracks: MelodicTrackSelection[] = [
      {
        role: "lead_guitar",
        trackName: "Lead Guitar",
        channel: 2,
        notes: [
          { t_on: 0.10000001, t_off: 0.35, pitch: 60, velocity: 0.7 },
          { t_on: 0.5, t_off: 0.75, pitch: 64, velocity: 0.8 },
          { t_on: 0.8, t_off: 1.0, pitch: 67, velocity: 0.6 },
        ],
      },
    ];
    const fingering: FingeringFile[] = [
      {
        version: "1.0.0",
        instrument: "lead_guitar",
        notes: [
          { t_on: 0.1, pitch: 60, string: 2, fret: 8 },
          { t_on: 0.5, pitch: 64, string: 1, fret: 5 },
        ],
      },
    ];

    const out = applyFingeringToMelodicTracks(tracks, fingering);

    expect(out[0]!.notes.map((n) => ({ t_on: n.t_on, pitch: n.pitch, string: n.string, fret: n.fret }))).toEqual([
      { t_on: 0.10000001, pitch: 60, string: 2, fret: 8 },
      { t_on: 0.5, pitch: 64, string: 1, fret: 5 },
      { t_on: 0.8, pitch: 67, string: undefined, fret: undefined },
    ]);
    expect(tracks[0]!.notes[0]!.string).toBeUndefined();
  });

  it("accepts generic guitar fingering for concrete guitar tracks", () => {
    const tracks: MelodicTrackSelection[] = [
      {
        role: "rhythm_guitar",
        trackName: "Rhythm Guitar",
        channel: 1,
        notes: [{ t_on: 0.25, t_off: 0.5, pitch: 55, velocity: 0.7 }],
      },
    ];
    const fingering: FingeringFile[] = [
      {
        version: "1.0.0",
        instrument: "guitar",
        notes: [{ t_on: 0.25, pitch: 55, string: 3, fret: 0 }],
      },
    ];

    const out = applyFingeringToMelodicTracks(tracks, fingering);

    expect(validateFingering(fingering[0], "rhythm_guitar").ok).toBe(true);
    expect(out[0]!.notes[0]).toMatchObject({ pitch: 55, string: 3, fret: 0 });
  });

  it("extracts manifest roles and includes generic guitar fallbacks for concrete guitar roles", () => {
    const manifest = {
      aural_fingering: {
        lead_guitar: "custom/lead.json",
        guitar: "custom/guitar.json",
        drums: "custom/ignored-drums.json",
        bass: "custom/bass.json",
      },
    };

    expect(fingeringRolesFromManifest(manifest)).toEqual(["lead_guitar", "guitar", "bass"]);
    expect(candidateFingeringPaths("lead_guitar", manifest)).toEqual([
      "custom/lead.json",
      "custom/guitar.json",
      "aural/fingering.lead_guitar.json",
      "features/fingering.lead_guitar.json",
      "aural/fingering.guitar.json",
      "features/fingering.guitar.json",
    ]);
  });

  it("converts fingering-only sidecars into melodic track selections", () => {
    const tracks = fingeringFilesToMelodicTracks([
      {
        version: "1.0.0",
        instrument: "guitar",
        notes: [{ t_on: 0.5, pitch: 55, string: 3, fret: 0 }],
      },
      {
        version: "1.0.0",
        instrument: "bass",
        notes: [{ t_on: 0.25, t_off: 0.5, pitch: 40, velocity: 64, string: 0, fret: 12 }],
      },
    ]);

    expect(tracks.map((track) => track.role)).toEqual(["bass", "lead_guitar"]);
    expect(tracks[0]).toMatchObject({
      role: "bass",
      trackName: "Bass",
      channel: 0,
      notes: [{ t_on: 0.25, t_off: 0.5, pitch: 40, velocity: 64 / 127, string: 0, fret: 12, s: 0, f: 12 }],
    });
    expect(tracks[1]).toMatchObject({
      role: "lead_guitar",
      trackName: "Guitar",
      channel: 2,
      notes: [{ t_on: 0.5, t_off: 0.65, pitch: 55, string: 3, fret: 0, s: 3, f: 0 }],
    });
  });

  it("loads aural sidecars and falls back to legacy features sidecars", async () => {
    invokeMock.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath === "aural/fingering.lead_guitar.json") throw new Error("missing");
      if (args.relPath === "features/fingering.lead_guitar.json") {
        return {
          version: "1.0.0",
          instrument: "lead_guitar",
          notes: [{ t_on: 0.5, pitch: 64, string: 1, fret: 5 }],
        };
      }
      throw new Error(`unexpected ${args.relPath}`);
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles("/pack.auralsong", ["lead_guitar"], { warn });

    expect(files).toHaveLength(1);
    expect(files[0]!.notes[0]).toMatchObject({ pitch: 64, string: 1, fret: 5 });
    expect(warn).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.auralsong",
      relPath: "aural/fingering.lead_guitar.json",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.auralsong",
      relPath: "features/fingering.lead_guitar.json",
    });
  });

  it("loads manifest-declared aural_fingering paths before conventional paths", async () => {
    invokeMock.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath === "custom/fret-map.lead.json") {
        return {
          version: "1.0.0",
          instrument: "lead_guitar",
          notes: [{ t_on: 0.25, pitch: 67, string: 0, fret: 3 }],
        };
      }
      throw new Error(`unexpected ${args.relPath}`);
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles(
      "/pack.feedpak",
      ["lead_guitar"],
      { warn },
      { aural_fingering: { lead_guitar: "custom/fret-map.lead.json" } },
    );

    expect(files).toHaveLength(1);
    expect(files[0]!.notes[0]).toMatchObject({ pitch: 67, string: 0, fret: 3 });
    expect(warn).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.feedpak",
      relPath: "custom/fret-map.lead.json",
    });
  });

  it("falls back to generic guitar manifest paths for concrete guitar roles", async () => {
    invokeMock.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath === "custom/fret-map.guitar.json") {
        return {
          version: "1.0.0",
          instrument: "guitar",
          notes: [{ t_on: 0.25, pitch: 55, string: 3, fret: 0 }],
        };
      }
      throw new Error(`unexpected ${args.relPath}`);
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles(
      "/pack.feedpak",
      ["lead_guitar"],
      { warn },
      { aural_fingering: { guitar: "custom/fret-map.guitar.json" } },
    );

    expect(files).toHaveLength(1);
    expect(files[0]!.instrument).toBe("guitar");
    expect(files[0]!.notes[0]).toMatchObject({ pitch: 55, string: 3, fret: 0 });
    expect(warn).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.feedpak",
      relPath: "custom/fret-map.guitar.json",
    });
  });

  it("warns and skips invalid sidecars", async () => {
    invokeMock.mockResolvedValue({
      version: "1.0.0",
      instrument: "lead_guitar",
      notes: [{ t_on: 0.5, pitch: 64, string: 99, fret: 5 }],
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles("/pack.feedpak", ["lead_guitar"], { warn });

    expect(files).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      "play",
      "fingering.lead_guitar.json failed validation; ignoring",
      expect.any(Array),
    );
  });
});
