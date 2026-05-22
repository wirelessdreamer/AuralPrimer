/**
 * Source-level regression tests for per-player visualizer stages.
 *
 * Goal: when the user adds Player 2 (or beyond), each extra player gets
 * their own visualizer canvas inside `#playerStages`, runs its own plugin
 * instance, and stays tempo-locked to Player 1 via the shared transport.
 *
 * These are pure source-level pins. The exact pixel layout is exercised
 * by the runtime jsdom integration check
 * (distBundleSongLibrary.integration.test.ts pattern) -- this file just
 * catches a future refactor that drops the secondary-stage wiring from
 * main.ts (which would silently put us back into the "I added a Player 2
 * but they didn't show up" bug class).
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");

function read(rel: string): string {
  return readFileSync(resolve(REPO_ROOT, rel), "utf-8");
}

describe("per-player visualizer stages (Player 2..N)", () => {
  const mainSrc = read("apps/game/src/main.ts");
  const styleSrc = read("apps/game/src/style.css");

  it("template wraps #viz in a #playerStages grid container", () => {
    // The static canvas stays for Player 1; the wrapper is what new
    // sibling canvases get appended into for Player 2..N.
    expect(mainSrc).toMatch(/id="playerStages"\s+class="playerStages"/);
    expect(mainSrc).toMatch(/id="viz"[^>]*data-stage-index="0"/);
  });

  it("main.ts captures a playerStagesEl handle and parents playSurface around it", () => {
    expect(mainSrc).toMatch(
      /const\s+playerStagesEl\s*=\s*document\.getElementById\(\s*["']playerStages["']\s*\)/
    );
    // playSurface must wrap the stages wrapper (not the bare canvas), so
    // adding canvases at runtime keeps them inside the styled play surface.
    expect(mainSrc).toMatch(/playSurfaceEl\.append\(\s*playerStagesEl\s*,/);
  });

  it("main.ts has a SecondaryStage type + secondaryStages array", () => {
    expect(mainSrc).toMatch(/type\s+SecondaryStage\s*=\s*\{/);
    expect(mainSrc).toMatch(/let\s+secondaryStages\s*:\s*SecondaryStage\s*\[\s*\]\s*=\s*\[\s*\]/);
  });

  it("defaultPluginIdForInstrument maps each instrument to a visualizer", () => {
    expect(mainSrc).toMatch(/function\s+defaultPluginIdForInstrument\s*\(/);
    // Spot-check the obvious mappings so a future refactor can't silently
    // collapse them to a single fallback.
    expect(mainSrc).toMatch(/case\s+"drums"\s*:\s*\n?\s*return\s+DRUM_HIGHWAY_PLUGIN_ID/);
    expect(mainSrc).toMatch(/case\s+"vocals"\s*:\s*\n?\s*return\s+LYRICS_PLUGIN_ID/);
  });

  it("buildSecondaryStages exists and iterates players past Player 1", () => {
    expect(mainSrc).toMatch(/async\s+function\s+buildSecondaryStages\s*\(/);
    expect(mainSrc).toMatch(/players\.slice\(\s*1\s*\)/);
  });

  it("each secondary stage gets its own canvas appended to playerStagesEl", () => {
    expect(mainSrc).toMatch(/document\.createElement\(\s*["']canvas["']\s*\)/);
    expect(mainSrc).toMatch(/playerStagesEl\.appendChild\(\s*canvas\s*\)/);
    expect(mainSrc).toMatch(/canvas\.className\s*=\s*["']playerStage__canvas["']/);
  });

  it("primary stage init now passes ONLY Player 1 (not the full players array)", () => {
    // Each stage renders one player's lane. The primary stage gets
    // players[0]; secondary stages get [player] in buildSecondaryStages.
    expect(mainSrc).toMatch(/players:\s*players\.slice\(\s*0\s*,\s*1\s*\)\.map/);
    expect(mainSrc).toMatch(
      /players:\s*\[\{\s*id:\s*player\.id\s*,\s*name:\s*player\.name\s*,\s*instrument:\s*player\.instrument\s*\}\s*\]/
    );
  });

  it("tick loop renders every secondary stage with the shared transport", () => {
    expect(mainSrc).toMatch(/for\s*\(\s*const\s+stage\s+of\s+secondaryStages\s*\)/);
    // The state arg must be the same `transport` so all stages stay tempo-locked.
    expect(mainSrc).toMatch(/stage\.viz\.render\(\s*\{[\s\S]*?state:\s*transport/);
  });

  it("stopVisualizer disposes secondary stages", () => {
    expect(mainSrc).toMatch(/disposeSecondaryStages\s*\(\s*\)\s*;/);
  });

  it("rerenderPlayersAndApplyAvailability rebuilds stages while viz is running", () => {
    // Adding / removing a player mid-session must rebuild secondary stages
    // immediately, not require the user to stop+restart the visualizer.
    expect(mainSrc).toMatch(
      /if\s*\(\s*viz\s*\)[\s\S]{0,200}?buildSecondaryStages\s*\(\s*\)/
    );
  });

  it("style.css defines .playerStages grid with multi-player breakpoint", () => {
    expect(styleSrc).toMatch(/\.playerStages\s*\{[\s\S]*?display:\s*grid/);
    // Widescreen two-column layout when 2 players are active.
    expect(styleSrc).toMatch(
      /@media\s*\([^)]*min-width:\s*1300px[^)]*\)[\s\S]*?\.playerStages\[data-player-count="2"\][\s\S]*?grid-template-columns:\s*1fr\s+1fr/
    );
  });

  it("style.css extends per-canvas styling to .playerStage__canvas siblings", () => {
    // The original #viz rules previously targeted only #viz; secondary
    // canvases need the same border/background/min-height styling so they
    // don't render as bare unstyled rectangles.
    expect(styleSrc).toMatch(/#viz\s*,\s*\n?\s*\.playerStage__canvas\s*\{/);
    expect(styleSrc).toMatch(/\.bandSetupStage\s+#viz\s*,\s*\n?\s*\.bandSetupStage\s+\.playerStage__canvas/);
  });
});
