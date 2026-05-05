import { describe, expect, it } from "vitest";
import { selectDrumChart, type MidiTrackLike } from "../src/chartLoader";

describe("selectDrumChart", () => {
  it("prefers a dedicated Drums track over relaxed drum-like notes from other tracks", () => {
    const tracks: MidiTrackLike[] = [
      {
        index: 0,
        name: "Drums",
        notes: [
          { t: 0.0, midi: 36, channel: 9 },
          { t: 0.5, midi: 38, channel: 9 }
        ]
      },
      {
        index: 1,
        name: "Rhythm Guitar",
        notes: [
          { t: 0.0, midi: 45, channel: 1 },
          { t: 0.5, midi: 47, channel: 1 }
        ]
      }
    ];

    const selection = selectDrumChart(tracks);

    expect(selection.reason).toBe("dedicated_drum_track_guard");
    expect(selection.events.map((event) => event.trackName)).toEqual(["Drums", "Drums"]);
    expect(selection.events.map((event) => event.lane)).toEqual(["BD", "SD"]);
  });
});
