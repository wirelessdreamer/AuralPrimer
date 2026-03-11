import { selectDrumChart, type MidiTrackLike } from "../src/chartLoader";

describe("chartLoader King in Zion regression", () => {
  it("does not drop sparse dedicated drums track when relaxed pass is denser", () => {
    const tracks: MidiTrackLike[] = [
      {
        index: 0,
        name: "Drums",
        notes: [
          { t: 0.0, midi: 36, channel: 1 },
          { t: 0.25, midi: 38, channel: 1 },
          { t: 0.5, midi: 42, channel: 1 }
        ]
      },
      {
        index: 1,
        name: "Keys",
        notes: [
          { t: 0.0, midi: 41, channel: 1 },
          { t: 0.05, midi: 46, channel: 1 },
          { t: 0.1, midi: 49, channel: 1 },
          { t: 0.15, midi: 50, channel: 1 },
          { t: 0.2, midi: 51, channel: 1 },
          { t: 0.25, midi: 47, channel: 1 },
          { t: 0.3, midi: 41, channel: 1 }
        ]
      }
    ];

    const out = selectDrumChart(tracks);
    expect(out.mode).toBe("strict");
    expect(out.reason).toBe("dedicated_drum_track_guard");
    expect(out.events).toHaveLength(3);
    expect(out.strictUniqueLanes).toEqual(["BD", "SD", "HH"]);
  });
});
