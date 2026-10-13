// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { clampSongSyncMs, formatSongSyncMs, loadSongSyncMs, saveSongSyncMs } from "../src/songSync";

describe("songSync", () => {
  beforeEach(() => window.localStorage.clear());

  it("remembers a nudge per song, and forgets it at zero", () => {
    saveSongSyncMs("D:/songs/a.feedpak", 300);
    saveSongSyncMs("D:/songs/b.feedpak", -40);
    expect(loadSongSyncMs("D:/songs/a.feedpak")).toBe(300);
    expect(loadSongSyncMs("D:/songs/b.feedpak")).toBe(-40);
    expect(loadSongSyncMs("D:/songs/c.feedpak")).toBe(0);
    saveSongSyncMs("D:/songs/a.feedpak", 0);
    expect(window.localStorage.length).toBe(1);
  });

  it("clamps, rounds and refuses junk", () => {
    expect(clampSongSyncMs(5000)).toBe(2000);
    expect(clampSongSyncMs(-12.6)).toBe(-13);
    expect(clampSongSyncMs(Number.NaN)).toBe(0);
    window.localStorage.setItem("auralprimer.songSyncMs:x", "nope");
    expect(loadSongSyncMs("x")).toBe(0);
    expect(loadSongSyncMs(null)).toBe(0);
  });

  it("reads out signed", () => {
    expect(formatSongSyncMs(40)).toBe("+40 ms");
    expect(formatSongSyncMs(-20)).toBe("\u221220 ms");
    expect(formatSongSyncMs(0)).toBe("0 ms");
  });
});
