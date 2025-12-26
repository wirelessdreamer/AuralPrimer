import { validateBeats, validateEvents, validateSections, validateTempoMap } from "../src/validateFeatures";

describe("validateFeatures", () => {
  it("validates beats.json", () => {
    expect(
      validateBeats({
        beats_version: "1.0.0",
        beats: [{ t: 0.5, bar: 0, beat: 0, strength: 1.0 }]
      }).ok
    ).toBe(true);

    expect(
      validateBeats({
        beats_version: "1.0.0",
        beats: [{ t: -1, bar: 0, beat: 0 }]
      }).ok
    ).toBe(false);
  });

  it("validates tempo_map.json", () => {
    expect(
      validateTempoMap({
        tempo_version: "1.0.0",
        segments: [{ t0: 0, bpm: 120, time_signature: "4/4" }]
      }).ok
    ).toBe(true);

    expect(
      validateTempoMap({
        tempo_version: "1.0.0",
        segments: [{ t0: 0, bpm: 0, time_signature: "4/4" }]
      }).ok
    ).toBe(false);
  });

  it("validates sections.json", () => {
    expect(
      validateSections({
        sections_version: "1.0.0",
        sections: [{ t0: 0, t1: 10, label: "intro" }]
      }).ok
    ).toBe(true);

    expect(
      validateSections({
        sections_version: "1.0.0",
        sections: [{ t0: 0, t1: 10 }]
      }).ok
    ).toBe(false);
  });

  it("validates events.json (minimal)", () => {
    expect(
      validateEvents({
        events_version: "1.0.0",
        tracks: [{ track_id: "t1", role: "guitar", name: "Guitar" }],
        notes: []
      }).ok
    ).toBe(true);

    expect(
      validateEvents({
        tracks: []
      }).ok
    ).toBe(false);
  });
});
