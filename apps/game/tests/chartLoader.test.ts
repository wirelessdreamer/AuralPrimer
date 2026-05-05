import { describe, expect, it } from "vitest";
import { selectDrumChart, selectMelodicTracksFromMidiBytes, type MidiTrackLike } from "../src/chartLoader";

function encodeVarLen(value: number): number[] {
  let buffer = value & 0x7f;
  const out: number[] = [];
  while ((value >>= 7) > 0) {
    buffer <<= 8;
    buffer |= (value & 0x7f) | 0x80;
  }
  while (true) {
    out.push(buffer & 0xff);
    if ((buffer & 0x80) !== 0) {
      buffer >>= 8;
      continue;
    }
    break;
  }
  return out;
}

function asciiBytes(text: string): number[] {
  return Array.from(Buffer.from(text, "ascii"));
}

function buildTestMidi(events: number[]): Uint8Array {
  const trackBytes = Uint8Array.from(events);
  const header = Uint8Array.from([
    0x4d, 0x54, 0x68, 0x64,
    0x00, 0x00, 0x00, 0x06,
    0x00, 0x00,
    0x00, 0x01,
    0x01, 0xe0,
    0x4d, 0x54, 0x72, 0x6b,
    (trackBytes.length >>> 24) & 0xff,
    (trackBytes.length >>> 16) & 0xff,
    (trackBytes.length >>> 8) & 0xff,
    trackBytes.length & 0xff,
  ]);

  const bytes = new Uint8Array(header.length + trackBytes.length);
  bytes.set(header, 0);
  bytes.set(trackBytes, header.length);
  return bytes;
}

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

describe("selectMelodicTracksFromMidiBytes", () => {
  it("preserves note-off timing for keys tracks", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(4), ...asciiBytes("Keys"),
      ...encodeVarLen(0), 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20,
      ...encodeVarLen(0), 0x93, 60, 100,
      ...encodeVarLen(480), 0x83, 60, 0,
      ...encodeVarLen(0), 0x93, 63, 80,
      ...encodeVarLen(240), 0x83, 63, 0,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];

    const tracks = selectMelodicTracksFromMidiBytes(buildTestMidi(events));
    expect(tracks).toHaveLength(1);
    expect(tracks[0].role).toBe("keys");
    expect(tracks[0].notes).toHaveLength(2);
    expect(tracks[0].notes[0].t_on).toBeCloseTo(0, 6);
    expect(tracks[0].notes[0].t_off).toBeCloseTo(0.5, 6);
    expect(tracks[0].notes[0].velocity).toBeCloseTo(100 / 127, 6);
    expect(tracks[0].notes[1].t_on).toBeCloseTo(0.5, 6);
    expect(tracks[0].notes[1].t_off).toBeCloseTo(0.75, 6);
  });
});
