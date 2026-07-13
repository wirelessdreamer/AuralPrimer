import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { buildVizSongContext } from "../src/vizSongContext";

describe("buildVizSongContext", () => {
  it("preserves drum velocity for visualizer plugins", () => {
    const context = buildVizSongContext({
      drumSelection: {
        mode: "strict",
        reason: "strict_preferred",
        events: [
          { t: 0.5, midi: 36, velocity: 42, lane: "BD", trackIndex: 0, trackName: "Drums" },
        ],
        strictCount: 1,
        relaxedCount: 1,
        strictUniqueLanes: ["BD"],
        relaxedUniqueLanes: ["BD"],
      },
      melodicTracks: [],
      lyrics: null,
      charts: null,
    });

    expect(context.notes).toEqual([
      {
        t_on: 0.5,
        t_off: 0.58,
        pitch: 36,
        velocity: 42,
        role: "drums",
        instrument: "drums",
        channel: 9,
        trackName: "Drums",
      },
    ]);
  });

  it("preserves fretted metadata on melodic notes for visualizer plugins", () => {
    const context = buildVizSongContext({
      drumSelection: null,
      melodicTracks: [
        {
          role: "lead_guitar",
          trackName: "Lead Guitar",
          channel: 2,
          notes: [
            {
              t_on: 0.2,
              t_off: 0.6,
              pitch: 64,
              velocity: 0.8,
              string: 0,
              fret: 24,
            },
          ],
        },
      ],
      lyrics: null,
      charts: null,
    });

    expect(context.notes).toEqual([
      {
        t_on: 0.2,
        t_off: 0.6,
        pitch: 64,
        velocity: 0.8,
        string: 0,
        fret: 24,
        s: undefined,
        f: undefined,
        role: "lead_guitar",
        instrument: "lead_guitar",
        channel: 2,
        trackName: "Lead Guitar",
      },
    ]);
  });

  it("passes vocal pitch artifacts through to visualizer plugins", () => {
    const vocalPitch = { version: 1, notes: [{ t: 0, d: 0.5, midi: 69 }] };
    const vocalPitchContour = { version: 1, samples: [{ t: 0, hz: 440 }] };

    const context = buildVizSongContext({
      drumSelection: null,
      melodicTracks: [],
      lyrics: null,
      vocalPitch,
      vocalPitchContour,
      charts: null,
    });

    expect(context.vocalPitch).toBe(vocalPitch);
    expect(context.vocalPitchContour).toBe(vocalPitchContour);
  });

  it("passes key and harmony artifacts through to visualizer plugins", () => {
    const keys = { version: 1, events: [{ t: 0, key: "C", scale: "major" }] };
    const harmony = { version: 1, key: "C", mode: "major", events: [] };

    const context = buildVizSongContext({
      drumSelection: null,
      melodicTracks: [],
      lyrics: null,
      keys,
      harmony,
      charts: null,
    });

    expect(context.keys).toBe(keys);
    expect(context.harmony).toBe(harmony);
  });

  it("passes song timeline artifacts through to visualizer plugins", () => {
    const songTimeline = { version: 1, beats: [{ time: 0, measure: 1 }] };

    const context = buildVizSongContext({
      drumSelection: null,
      melodicTracks: [],
      lyrics: null,
      songTimeline,
      charts: null,
    });

    expect(context.songTimeline).toBe(songTimeline);
  });

  it("main.ts falls back to legacy feature artifact paths for non-feedpak songs", () => {
    const src = readFileSync(resolve("apps/game/src/main.ts"), "utf-8");

    expect(src).toMatch(/isManifestPack\(containerPath\) \? null : `features\/\$\{legacyFeatureName\}`/);
    expect(src).toContain('readOptionalArtifactJson(containerPath, manifestRaw, "keys", "keys.json")');
    expect(src).toContain('readOptionalArtifactJson(containerPath, manifestRaw, "harmony", "harmony.json")');
    expect(src).toContain('readOptionalArtifactJson(containerPath, manifestRaw, "vocal_pitch", "vocal_pitch.json")');
    expect(src).toContain('"features/pitch_contour.json"');
  });
});
