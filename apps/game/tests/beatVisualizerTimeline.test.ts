import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { timelineBeatGridLines } from "../../../visualizers/viz-beats/src/index";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");

function read(rel: string): string {
  return readFileSync(resolve(REPO_ROOT, rel), "utf-8");
}

describe("viz-beats timeline support", () => {
  it("builds visible grid lines from feedpak song_timeline beats", () => {
    const lines = timelineBeatGridLines(
      {
        version: 1,
        beats: [
          { time: 0, measure: 1 },
          { time: 0.5, measure: 1 },
          { time: 1.0, measure: 2 },
          { time: 1.5, measure: 2 },
          { time: 3.0, measure: 3 },
        ],
      },
      0.75,
      1.0,
    );

    expect(lines).toEqual([
      { tSec: 1.0, x01: 0.25, isDownbeat: true, barIndex: 1 },
      { tSec: 1.5, x01: 0.75, isDownbeat: false, barIndex: 1 },
    ]);
  });

  it("falls back to transport bpm and time signature instead of a fixed 60 bpm grid", () => {
    const src = read("visualizers/viz-beats/src/index.ts");
    expect(src).toMatch(/timelineBeatGridLines\(this\.songTimeline,\s*t,\s*windowSec\)/);
    expect(src).toMatch(/bpm:\s*frame\.state\.bpm/);
    expect(src).toMatch(/beatsPerBar:\s*frame\.state\.timeSignature\[0\]/);
    expect(src).not.toMatch(/bpm:\s*60/);
  });
});
