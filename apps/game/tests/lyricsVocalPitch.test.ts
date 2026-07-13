import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  parseVocalPitchNotes,
  parseVocalPitchSamples,
} from "../../../visualizers/viz-lyrics/src/index";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");

function read(rel: string): string {
  return readFileSync(resolve(REPO_ROOT, rel), "utf-8");
}

describe("viz-lyrics vocal pitch artifacts", () => {
  it("parses and sorts schema-valid vocal pitch notes", () => {
    expect(
      parseVocalPitchNotes({
        version: 1,
        notes: [
          { t: 1.0, d: 0.25, midi: 72 },
          { t: 0.5, d: 0.5, midi: 69 },
          { t: 0.25, d: 0, midi: 67 },
          { t: 0.75, d: 0.5, midi: "bad" },
        ],
      }),
    ).toEqual([
      { t: 0.5, d: 0.5, midi: 69 },
      { t: 1.0, d: 0.25, midi: 72 },
    ]);
  });

  it("parses and sorts schema-valid vocal pitch contour samples", () => {
    expect(
      parseVocalPitchSamples({
        version: 1,
        samples: [
          { t: 0.5, hz: 440 },
          { t: 0.25, hz: 220 },
          { t: 0.75, hz: 0 },
          { t: "bad", hz: 330 },
        ],
      }),
    ).toEqual([
      { t: 0.25, hz: 220 },
      { t: 0.5, hz: 440 },
    ]);
  });

  it("reads vocal pitch artifacts from song context and draws before missing-lyrics fallback", () => {
    const src = read("visualizers/viz-lyrics/src/index.ts");
    expect(src).toMatch(/parseVocalPitchNotes\(ctx\.song\?\.vocalPitch\)/);
    expect(src).toMatch(/parseVocalPitchSamples\(ctx\.song\?\.vocalPitchContour\)/);
    expect(src.indexOf("this.drawVocalPitchLane(frame)")).toBeLessThan(
      src.indexOf("No lyrics loaded"),
    );
  });
});
