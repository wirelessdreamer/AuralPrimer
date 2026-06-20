/**
 * Regression test for the "Backing Vocals beats Vocals" bug we hit on the
 * piano-psalms corpus.
 *
 * The Studio relies on two layers cooperating:
 *
 *   1. apps/desktop/src-tauri/src/raw_song.rs::detect_part_role_from_tokens
 *      tags "Backing Vocals" as detected_role="backing_vocals" (NOT
 *      "vocals"). A Rust unit test in raw_song.rs pins that.
 *
 *   2. apps/desktop/src/main.ts::buildIngestInputStemPaths uses
 *      normalizeInputStemRole to drop "backing_vocals" to null on its
 *      detected_role pass so the lead-vocal stem fills the
 *      input_stem_paths["vocals"] slot first, before the second pass
 *      uses game_role (where backing/lead vocals both collapse to
 *      "vocals").
 *
 * This source-grep test pins step 2: if someone adds "backing_vocals"
 * to the InputStemRole switch in normalizeInputStemRole, OR removes
 * the two-pass detected_role-then-game_role structure in
 * buildIngestInputStemPaths, the priority breaks silently and the
 * Studio re-imports start writing the quieter Backing Vocals stem
 * into input_stem_paths["vocals"], producing nearly-silent vocals in
 * the synthesized mix (same failure mode the Python driver had).
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function read(p: string): string {
  return readFileSync(resolve(p), "utf-8");
}

describe("Suno Backing Vocals priority", () => {
  it("normalizeInputStemRole does NOT accept 'backing_vocals' (kept off the switch on purpose)", () => {
    const src = read("apps/desktop/src/main.ts");

    // The InputStemRole union has exactly the eight canonical roles
    // and "backing_vocals" must NOT appear -- adding it would let
    // backing vocals win Pass 1 of buildIngestInputStemPaths.
    expect(src).toMatch(
      /type InputStemRole = "drums" \| "bass" \| "guitar" \| "lead_guitar" \| "rhythm_guitar" \| "keys" \| "vocals" \| "other";/,
    );

    // The normalizer's switch lists only those eight values + null.
    expect(src).toMatch(
      /function normalizeInputStemRole[\s\S]*?switch \(key\) \{[\s\S]*?case "drums":[\s\S]*?case "bass":[\s\S]*?case "guitar":[\s\S]*?case "lead_guitar":[\s\S]*?case "rhythm_guitar":[\s\S]*?case "keys":[\s\S]*?case "vocals":[\s\S]*?case "other":[\s\S]*?return key;[\s\S]*?default:[\s\S]*?return null;/,
    );

    // Defensive: explicitly assert "backing_vocals" is NOT present
    // in normalizeInputStemRole's body.
    const normalizerStart = src.indexOf("function normalizeInputStemRole");
    const normalizerEnd = src.indexOf("}", src.indexOf("default:", normalizerStart));
    const normalizerBody = src.slice(normalizerStart, normalizerEnd);
    expect(normalizerBody).not.toMatch(/backing_vocals/);
  });

  it("buildIngestInputStemPaths runs the detected_role pass before the game_role pass", () => {
    const src = read("apps/desktop/src/main.ts");
    // The first-wins assign() helper, then a detected_role loop, then
    // a game_role loop. The order matters: detected_role exposes the
    // backing_vocals tag (which normalizes to null and gets skipped),
    // while game_role would collapse both vocal kinds to "vocals".
    expect(src).toMatch(
      /function buildIngestInputStemPaths[\s\S]*?if \(!role \|\| paths\[role\]\) return;[\s\S]*?normalizeInputStemRole\(part\.detected_role\)[\s\S]*?normalizeInputStemRole\(part\.game_role\)/,
    );
  });
});
