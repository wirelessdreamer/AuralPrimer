// @vitest-environment jsdom
/**
 * Execution tests for the lyrics playback overlay panel.
 *
 * Imports + runs initLyricsPanel and drives render()/clear() across the
 * preview / active-line / karaoke-split / post-line-hold / hidden states,
 * plus the chunk-based highlight path and the viz-lyrics skip branch.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { initLyricsPanel } from "../src/lyricsPanel";
import type { LyricsFile } from "../src/lyricsGenerator";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="playLyrics" hidden>' +
    '<div id="playLyricsCurrent"></div>' +
    '<div id="playLyricsNext"></div>' +
    "</div>";
}

const container = () => document.getElementById("playLyrics") as HTMLDivElement;
const current = () => document.getElementById("playLyricsCurrent") as HTMLDivElement;
const next = () => document.getElementById("playLyricsNext") as HTMLDivElement;

function simpleLyrics(): LyricsFile {
  return {
    lines: [
      { start: 10, end: 12, text: "First line" },
      { start: 13, end: 15, text: "Second line" },
    ],
  } as unknown as LyricsFile;
}

function chunkLyrics(): LyricsFile {
  return {
    lines: [
      {
        start: 0,
        end: 4,
        text: "Hello world",
        chunks: [
          { start: 0, end: 2, text: "Hello", char_start: 0, char_end: 5 },
          { start: 2, end: 4, text: " world", char_start: 5, char_end: 11 },
        ],
      },
      { start: 5, end: 6, text: "Next" },
    ],
  } as unknown as LyricsFile;
}

describe("lyricsPanel", () => {
  beforeEach(() => {
    stageDom();
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initLyricsPanel()).toThrow(/required DOM/);
  });

  it("clear() hides the overlay and is a no-op when already hidden", () => {
    const panel = initLyricsPanel();
    // Already hidden: early return, no error.
    panel.clear();
    // Render an active line, then clear to flip it hidden.
    panel.render(11, simpleLyrics(), null);
    expect(container().hidden).toBe(false);
    panel.clear();
    expect(container().hidden).toBe(true);
    expect(current().innerHTML).toBe("");
    expect(next().textContent).toBe("");
    // Second clear is the early-return path.
    panel.clear();
    expect(container().hidden).toBe(true);
  });

  it("hides for null / empty lyrics", () => {
    const panel = initLyricsPanel();
    panel.render(0, null, null);
    expect(container().hidden).toBe(true);
    panel.render(0, { lines: [] } as unknown as LyricsFile, null);
    expect(container().hidden).toBe(true);
  });

  it("skips overlay when viz-lyrics is the active plugin", () => {
    const panel = initLyricsPanel();
    panel.render(11, simpleLyrics(), "viz-lyrics");
    expect(container().hidden).toBe(true);
  });

  it("stays hidden when the first line is too far in the future", () => {
    const panel = initLyricsPanel();
    // t=0, first line starts at 10 → 10s > 3s preview lead → hidden.
    panel.render(0, simpleLyrics(), null);
    expect(container().hidden).toBe(true);
  });

  it("shows the preview line within the lead window", () => {
    const panel = initLyricsPanel();
    // t=8, first line starts at 10 → 2s lead ≤ 3s → preview.
    panel.render(8, simpleLyrics(), null);
    expect(container().hidden).toBe(false);
    expect(next().textContent).toBe("First line");
    expect(current().innerHTML).toBe("");
    // Repeat same preview → cached, returns early without throwing.
    panel.render(8.2, simpleLyrics(), null);
    expect(next().textContent).toBe("First line");
  });

  it("renders the active line with a karaoke split + next preview", () => {
    const panel = initLyricsPanel();
    panel.render(11, simpleLyrics(), null);
    expect(container().hidden).toBe(false);
    expect(current().innerHTML).toContain("playLyricsDone");
    expect(current().innerHTML).toContain("playLyricsRest");
    expect(next().textContent).toBe("Second line");
    // Same frame → cached early return.
    const html = current().innerHTML;
    panel.render(11, simpleLyrics(), null);
    expect(current().innerHTML).toBe(html);
  });

  it("uses chunk timing for the highlight split index", () => {
    const panel = initLyricsPanel();
    // t=1 falls inside chunk 0 (0..2).
    panel.render(1, chunkLyrics(), null);
    expect(current().innerHTML).toContain("playLyricsDone");
    // t=3 falls inside chunk 1 (2..4).
    panel.render(3, chunkLyrics(), null);
    expect(current().innerHTML).toContain("playLyricsRest");
    // t=4.5 past all chunks but still inside the line window? line ends at 4,
    // so this is post-line; exercise t just before end with a gap chunk.
    panel.render(2, chunkLyrics(), null);
    expect(container().hidden).toBe(false);
  });

  it("hides after the last line plus post-line hold", () => {
    const panel = initLyricsPanel();
    // Last line ends at 15; hold is 1.5s. t=17 > 16.5 → hidden.
    panel.render(17, simpleLyrics(), null);
    expect(container().hidden).toBe(true);
  });
});
