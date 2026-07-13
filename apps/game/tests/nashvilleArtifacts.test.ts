import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  createVisualizer,
  harmonyChordsFromSongArtifacts,
  keySignatureFromSongArtifacts,
} from "../../../visualizers/viz-nashville/src/index";

describe("viz-nashville key artifacts", () => {
  it("prefers harmony.json key/mode metadata", () => {
    const key = keySignatureFromSongArtifacts({
      keys: { version: 1, events: [{ t: 0, key: "C", scale: "major" }] },
      harmony: { version: 1, key: "Bb", mode: "minor", confidence: 0.73, events: [] },
    });

    expect(key).toMatchObject({
      tonic: "Bb",
      mode: "minor",
      pitchClass: 10,
      label: "Bb minor",
      noteLabelStyle: "flat",
      confidence: 0.73,
    });
  });

  it("falls back to keys.json events when harmony metadata is absent", () => {
    const key = keySignatureFromSongArtifacts({
      keys: { version: 1, events: [{ t: 0, key: "D", scale: "major", score: 0.91 }] },
    });

    expect(key).toMatchObject({
      tonic: "D",
      mode: "major",
      pitchClass: 2,
      label: "D major",
      noteLabelStyle: "sharp",
      score: 0.91,
    });
  });

  it("extracts display chord labels from harmony events", () => {
    const chords = harmonyChordsFromSongArtifacts({
      harmony: {
        version: 1,
        key: "C",
        mode: "major",
        events: [
          { t: 0, duration: 2, root: "C", quality: "maj", rn: "I" },
          { t: 2, root: "F", quality: "maj", rn: "IV" },
          { t: 4, root: null },
          { t: Number.NaN, root: "G", quality: "7", rn: "V7" },
        ],
      },
    });

    expect(chords).toEqual([
      { t: 0, t_off: 2, label: "I (Cmaj)", root: "C", quality: "maj" },
      { t: 2, t_off: 4, label: "IV (Fmaj)", root: "F", quality: "maj" },
      { t: 4, t_off: 6, label: "N.C.", root: null, quality: undefined },
    ]);
  });

  it("does not hide the harmony chord band for harmony-only packs", () => {
    const src = readFileSync(resolve("visualizers/viz-nashville/src/index.ts"), "utf-8");
    expect(src).toContain("if (this.notes.length === 0 && this.chords.length === 0)");
  });

  it("uses harmony key metadata for harmony-only packs", async () => {
    const labels: string[] = [];
    const ctx2d = {
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 1,
      font: "",
      textAlign: "left",
      textBaseline: "alphabetic",
      clearRect: () => undefined,
      fillRect: () => undefined,
      beginPath: () => undefined,
      moveTo: () => undefined,
      lineTo: () => undefined,
      stroke: () => undefined,
      fill: () => undefined,
      arcTo: () => undefined,
      closePath: () => undefined,
      createLinearGradient: () => ({ addColorStop: () => undefined }),
      fillText: (text: string) => {
        labels.push(text);
      },
    } as unknown as CanvasRenderingContext2D;
    const viz = createVisualizer();

    await viz.init({
      song: {
        harmony: {
          version: 1,
          key: "Bb",
          mode: "minor",
          events: [{ t: 0, duration: 2, root: "Bb", quality: "min", rn: "i" }],
        },
      },
    } as never);
    viz.render({
      ctx2d,
      width: 640,
      height: 240,
      state: { t: 0, bpm: 120, isPlaying: false, scrollSpeedMultiplier: 1 },
    } as never);

    expect(labels.some((label) => label.includes("Bb minor"))).toBe(true);
    expect(labels.some((label) => label.includes("key unknown"))).toBe(false);
  });
});
