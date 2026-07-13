import { describe, expect, it, beforeEach, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";

import {
  candidateFingeringPaths,
  fingeringFilesToMelodicTracks,
  fingeringFilesToVisualizerNotes,
  fingeringRolesFromManifest,
  loadFingeringForRoles,
  mergeFingeringIntoVisualizerNotes,
  validateFingering,
  type FingeringFile,
} from "../src/fingeringLoader";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const invokeMock = invoke as unknown as ReturnType<typeof vi.fn>;

describe("desktop fingeringLoader", () => {
  beforeEach(() => {
    invokeMock.mockReset();
  });

  it("validates explicit string/fret metadata and compact aliases", () => {
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

  it("extracts manifest roles and prefers manifest-declared paths", () => {
    const manifest = {
      aural_fingering: {
        lead_guitar: "custom/fret-map.lead.json",
        guitar: "custom/fret-map.guitar.json",
        drums: "custom/ignored-drums.json",
        bass: "custom/fret-map.bass.json",
      },
    };

    expect(fingeringRolesFromManifest(manifest)).toEqual(["lead_guitar", "guitar", "bass"]);
    expect(candidateFingeringPaths("lead_guitar", manifest)).toEqual([
      "custom/fret-map.lead.json",
      "custom/fret-map.guitar.json",
      "aural/fingering.lead_guitar.json",
      "features/fingering.lead_guitar.json",
      "aural/fingering.guitar.json",
      "features/fingering.guitar.json",
    ]);
  });

  it("loads manifest-declared fingering before conventional fallbacks", async () => {
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

  it("falls back from aural to legacy features sidecars", async () => {
    invokeMock.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath === "aural/fingering.bass.json") throw new Error("missing");
      if (args.relPath === "features/fingering.bass.json") {
        return {
          version: "1.0.0",
          instrument: "bass",
          notes: [{ t_on: 0.5, pitch: 40, string: 0, fret: 12 }],
        };
      }
      throw new Error(`unexpected ${args.relPath}`);
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles("/pack.auralsong", ["bass"], { warn });

    expect(files).toHaveLength(1);
    expect(files[0]!.notes[0]).toMatchObject({ pitch: 40, string: 0, fret: 12 });
    expect(warn).not.toHaveBeenCalled();
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.auralsong",
      relPath: "aural/fingering.bass.json",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/pack.auralsong",
      relPath: "features/fingering.bass.json",
    });
  });

  it("loads generic guitar fingering for a concrete guitar role", async () => {
    invokeMock.mockImplementation(async (_cmd: string, args: { relPath: string }) => {
      if (args.relPath === "aural/fingering.lead_guitar.json") throw new Error("missing lead");
      if (args.relPath === "features/fingering.lead_guitar.json") throw new Error("missing legacy lead");
      if (args.relPath === "aural/fingering.guitar.json") {
        return {
          version: "1.0.0",
          instrument: "guitar",
          notes: [{ t_on: 0.25, pitch: 55, string: 3, fret: 0 }],
        };
      }
      throw new Error(`unexpected ${args.relPath}`);
    });
    const warn = vi.fn();

    const files = await loadFingeringForRoles("/pack.feedpak", ["lead_guitar"], { warn });

    expect(files).toHaveLength(1);
    expect(files[0]!.instrument).toBe("guitar");
    expect(files[0]!.notes[0]).toMatchObject({ pitch: 55, string: 3, fret: 0 });
    expect(warn).not.toHaveBeenCalled();
  });

  it("converts fingering files into host-provided visualizer notes", () => {
    const files: FingeringFile[] = [
      {
        version: "1.0.0",
        instrument: "guitar",
        notes: [{ t_on: 0.1, pitch: 55, string: 3, fret: 0 }],
      },
      {
        version: "1.0.0",
        instrument: "lead_guitar",
        notes: [{ t_on: 0.5, t_off: 0.75, pitch: 64, velocity: 96, string: 1, fret: 5 }],
      },
      {
        version: "1.0.0",
        instrument: "bass",
        notes: [{ t_on: 0.25, pitch: 40, string: 0, fret: 12 }],
      },
    ];

    expect(fingeringFilesToVisualizerNotes(files)).toEqual([
      {
        t_on: 0.1,
        t_off: undefined,
        pitch: 55,
        velocity: undefined,
        string: 3,
        fret: 0,
        s: 3,
        f: 0,
        role: "guitar",
        instrument: "guitar",
        channel: 2,
        trackName: "Guitar",
      },
      {
        t_on: 0.25,
        t_off: undefined,
        pitch: 40,
        velocity: undefined,
        string: 0,
        fret: 12,
        s: 0,
        f: 12,
        role: "bass",
        instrument: "bass",
        channel: 0,
        trackName: "Bass",
      },
      {
        t_on: 0.5,
        t_off: 0.75,
        pitch: 64,
        velocity: 96,
        string: 1,
        fret: 5,
        s: 1,
        f: 5,
        role: "lead_guitar",
        instrument: "lead_guitar",
        channel: 2,
        trackName: "Lead Guitar",
      },
    ]);
  });

  it("converts fingering-only files into melodic track selections", () => {
    const tracks = fingeringFilesToMelodicTracks([
      {
        version: "1.0.0",
        instrument: "guitar",
        notes: [{ t_on: 0.1, pitch: 55, string: 3, fret: 0 }],
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
      notes: [{ t_on: 0.1, t_off: 0.25, pitch: 55, string: 3, fret: 0, s: 3, f: 0 }],
    });
  });

  it("merges sidecar fingering onto matching MIDI-derived visualizer notes", () => {
    const merged = mergeFingeringIntoVisualizerNotes(
      [
        { t_on: 0.5, t_off: 0.75, pitch: 64, velocity: 0.8, channel: 2, trackName: "Lead Guitar" },
        { t_on: 1.0, t_off: 1.25, pitch: 67, velocity: 0.7, channel: 2, trackName: "Lead Guitar" },
      ],
      [
        { t_on: 0.5, t_off: 0.75, pitch: 64, string: 1, fret: 5, s: 1, f: 5, channel: 2, trackName: "Lead Guitar" },
      ],
    );

    expect(merged).toEqual([
      {
        t_on: 0.5,
        t_off: 0.75,
        pitch: 64,
        velocity: 0.8,
        string: 1,
        fret: 5,
        s: 1,
        f: 5,
        channel: 2,
        trackName: "Lead Guitar",
      },
      { t_on: 1.0, t_off: 1.25, pitch: 67, velocity: 0.7, channel: 2, trackName: "Lead Guitar" },
    ]);
  });
});
