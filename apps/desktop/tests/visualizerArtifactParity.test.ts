import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function read(p: string): string {
  return readFileSync(resolve(p), "utf-8");
}

describe("desktop visualizer artifact parity", () => {
  const src = read("apps/desktop/src/main.ts");
  const fingeringLoaderSrc = read("apps/desktop/src/fingeringLoader.ts");

  it("passes vocal pitch artifacts through the visualizer song context", () => {
    expect(src).toMatch(/let currentVocalPitch: unknown \| null = null;/);
    expect(src).toMatch(/let currentVocalPitchContour: unknown \| null = null;/);
    expect(src).toMatch(/vocalPitch: currentVocalPitch \?\? undefined/);
    expect(src).toMatch(/vocalPitchContour: currentVocalPitchContour \?\? undefined/);
  });

  it("passes timeline and harmony artifacts through the visualizer song context", () => {
    expect(src).toMatch(/let currentKeys: unknown \| null = null;/);
    expect(src).toMatch(/let currentHarmony: unknown \| null = null;/);
    expect(src).toMatch(/let currentSongTimeline: unknown \| null = null;/);
    expect(src).toMatch(/songTimeline: currentSongTimeline \?\? undefined/);
    expect(src).toMatch(/keys: currentKeys \?\? undefined/);
    expect(src).toMatch(/harmony: currentHarmony \?\? undefined/);
    expect(src).toMatch(/applySongMeterFromTimeline\(currentSongTimeline\)/);
    expect(src).toMatch(/transportController\.setSongMeter\(songBpm, songTimeSig\)/);
    expect(src).toMatch(/setHudKeyMode\(details\.manifest_raw, \{ keys: currentKeys, harmony: currentHarmony \}\)/);
  });

  it("lets viz-lyrics run on lyrics, vocal_pitch, or vocal_pitch_contour", () => {
    expect(src).toMatch(
      /ok: \(d\) => Boolean\(d\?\.has_lyrics \|\| d\?\.has_vocal_pitch \|\| d\?\.has_vocal_pitch_contour\)/,
    );
    expect(src).toMatch(
      /plugin\.id === "viz-lyrics" && !currentLyrics && !currentVocalPitch && !currentVocalPitchContour/,
    );
  });

  it("loads drum_tab charts before falling back to notes.mid", () => {
    expect(src).toMatch(/import \{ loadDrumChartFromTab \} from "\.\/drumTabChart";/);
    expect(src).toMatch(/if \(details\.has_drum_tab\) \{[\s\S]*?loadDrumChartFromTab\(containerPath, relPath\)/);
    expect(src).toMatch(/selectedDrumChartSelection = await readDrumChartSelection\(containerPath, details\);/);
    expect(src).toMatch(/ok: \(d\) => Boolean\(d\?\.has_notes_mid \|\| d\?\.has_drum_tab\)/);
  });

  it("passes aural fingering notes through the visualizer song context", () => {
    expect(src).toMatch(/loadFingeringForRoles/);
    expect(src).toMatch(/selectMelodicTracksFromMidiBytes/);
    expect(src).toMatch(/manifestArtifactRelPath\(manifestRaw, "aural_notes_mid"\)/);
    expect(src).toMatch(/let currentMelodicTracks: MelodicTrackSelection\[\] = \[\];/);
    expect(src).toMatch(/let currentMelodicNotes: VisualizerSongNote\[\] = \[\];/);
    expect(src).toMatch(/let currentFingeringNotes: VisualizerSongNote\[\] = \[\];/);
    expect(src).toMatch(/currentMelodicTracks = await readMelodicTrackSelection\(containerPath, details\);/);
    expect(src).toMatch(/currentMelodicNotes = melodicTracksToVisualizerNotes\(currentMelodicTracks\);/);
    expect(src).toMatch(/if \(details\.has_aural_fingering\) \{[\s\S]*?fingeringFilesToVisualizerNotes\(fingeringFiles\)/);
    expect(src).toMatch(/if \(currentMelodicTracks\.length === 0\) \{[\s\S]*?fingeringFilesToMelodicTracks\(fingeringFiles\)/);
    expect(src).toMatch(/mergeFingeringIntoVisualizerNotes\(currentMelodicNotes, currentFingeringNotes\)/);
    expect(src).toMatch(/notes: notes\.length > 0 \? notes : undefined/);
    expect(fingeringLoaderSrc).toMatch(/s: note\.string/);
    expect(fingeringLoaderSrc).toMatch(/f: note\.fret/);
    expect(fingeringLoaderSrc).toMatch(/channel,/);
  });

  it("derives desktop instrument availability from parsed MIDI melodic roles", () => {
    expect(src).toMatch(/computeSongCapabilities\([\s\S]*?melodicTracks: readonly MelodicTrackSelection\[\]/);
    expect(src).toMatch(/hasMelodicRole\("lead_guitar"\)/);
    expect(src).toMatch(/hasMelodicRole\("rhythm_guitar"\)/);
    expect(src).toMatch(/hasMelodicRole\("bass"\)/);
    expect(src).toMatch(/hasMelodicRole\("keys"\)/);
    expect(src).toMatch(/hasMelodicRole\("vocals"\)/);
    expect(src).toMatch(/computeSongCapabilities\(details, drumSelection, currentMelodicTracks\)/);
    expect(src).toMatch(/charts\.length > 0 \|\| drumChartAvailable \|\| melodicChartAvailable/);
  });

  it("keeps desktop player state aligned when unavailable instruments are repicked", () => {
    expect(src).toMatch(/const playerId = chip\.getAttribute\("data-player-id"\)/);
    expect(src).toMatch(/players = players\.map\(\(p\) => \(p\.id === playerId \? \{ \.\.\.p, instrument: nextInstrument \} : p\)\)/);
  });
});
