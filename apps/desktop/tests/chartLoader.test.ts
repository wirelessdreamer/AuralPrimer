// @vitest-environment jsdom
/**
 * Execution tests for the desktop chartLoader: the MIDI byte parser
 * (`parseMidiTracksFromBytes`) and the strict/relaxed drum-chart
 * selection state machine (`selectDrumChart` /
 * `selectDrumChartFromMidiBytes`).
 */
import { describe, expect, it } from "vitest";
import {
  parseMidiTracksFromBytes,
  selectDrumChart,
  selectDrumChartFromMidiBytes,
  type MidiTrackLike,
} from "../src/chartLoader";

// ─── MIDI byte-builder helpers ──────────────────────────────────────────────

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

function buildTestMidi(events: number[], trackCount = 1): Uint8Array {
  const trackBytes = Uint8Array.from(events);
  const header = Uint8Array.from([
    0x4d, 0x54, 0x68, 0x64,
    0x00, 0x00, 0x00, 0x06,
    0x00, 0x00,
    0x00, trackCount & 0xff,
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
          { t: 0.5, midi: 38, channel: 9 },
        ],
      },
      {
        index: 1,
        name: "Rhythm Guitar",
        notes: [
          { t: 0.0, midi: 45, channel: 1 },
          { t: 0.5, midi: 47, channel: 1 },
        ],
      },
    ];

    const selection = selectDrumChart(tracks);

    expect(selection.reason).toBe("dedicated_drum_track_guard");
    expect(selection.events.map((event) => event.trackName)).toEqual(["Drums", "Drums"]);
    expect(selection.events.map((event) => event.lane)).toEqual(["BD", "SD"]);
  });

  it("falls back to relaxed when no strict events exist", () => {
    const tracks: MidiTrackLike[] = [
      {
        index: 0,
        name: "Synth",
        // No channel-9, not a named drum track -> no strict source.
        notes: [
          { t: 0.0, midi: 36, channel: 1 },
          { t: 0.5, midi: 42, channel: 1 },
        ],
      },
    ];
    const selection = selectDrumChart(tracks);
    expect(selection.reason).toBe("strict_empty");
    expect(selection.mode).toBe("relaxed");
    expect(selection.strictCount).toBe(0);
    expect(selection.relaxedCount).toBe(2);
    expect(selection.relaxedUniqueLanes.sort()).toEqual(["BD", "HH"]);
  });

  it("prefers strict (channel9) when relaxed is not appreciably richer", () => {
    const tracks: MidiTrackLike[] = [
      {
        index: 0,
        name: "Track",
        notes: [
          { t: 0.0, midi: 36, channel: 9 },
          { t: 0.5, midi: 38, channel: 9 },
        ],
      },
    ];
    const selection = selectDrumChart(tracks);
    expect(selection.reason).toBe("strict_preferred");
    expect(selection.mode).toBe("strict");
    expect(selection.strictUniqueLanes.sort()).toEqual(["BD", "SD"]);
  });

  it("chooses relaxed_richer when relaxed has >=1.4x count and more lanes", () => {
    const tracks: MidiTrackLike[] = [
      {
        index: 0,
        name: "Track",
        // one strict (channel 9) note
        notes: [{ t: 0.0, midi: 36, channel: 9 }],
      },
      {
        index: 1,
        name: "Other",
        // several relaxed-only notes spanning more lanes
        notes: [
          { t: 0.1, midi: 38, channel: 1 },
          { t: 0.2, midi: 42, channel: 1 },
          { t: 0.3, midi: 49, channel: 1 },
          { t: 0.4, midi: 45, channel: 1 },
        ],
      },
    ];
    const selection = selectDrumChart(tracks);
    expect(selection.reason).toBe("relaxed_richer");
    expect(selection.mode).toBe("relaxed");
    expect(selection.relaxedCount).toBeGreaterThanOrEqual(Math.ceil(selection.strictCount * 1.4));
  });

  it("ignores notes outside the GM drum map", () => {
    const tracks: MidiTrackLike[] = [
      { index: 0, name: "Drums", notes: [{ t: 0, midi: 200, channel: 9 }] },
    ];
    const selection = selectDrumChart(tracks);
    expect(selection.events).toHaveLength(0);
    expect(selection.reason).toBe("strict_empty");
  });

  it("maps every documented GM lane", () => {
    const midis = [35, 36, 37, 38, 39, 40, 42, 44, 46, 49, 52, 55, 57, 51, 53, 59, 48, 50, 45, 47, 41, 43];
    const tracks: MidiTrackLike[] = [
      { index: 0, name: "Drums", notes: midis.map((midi, i) => ({ t: i, midi, channel: 9 })) },
    ];
    const selection = selectDrumChart(tracks);
    const lanes = new Set(selection.events.map((e) => e.lane));
    expect(lanes).toEqual(new Set(["BD", "SD", "HH", "CY", "RD", "HT", "LT", "FT"]));
  });
});

describe("parseMidiTracksFromBytes", () => {
  it("returns [] for too-short buffers", () => {
    expect(parseMidiTracksFromBytes(new Uint8Array(4))).toEqual([]);
  });

  it("returns [] when the MThd magic is wrong", () => {
    const bytes = new Uint8Array(20);
    bytes[0] = 0x00;
    expect(parseMidiTracksFromBytes(bytes)).toEqual([]);
  });

  it("returns [] when the header length is invalid", () => {
    const bytes = buildTestMidi([0x00, 0xff, 0x2f, 0x00]);
    // Corrupt header length to < 6.
    bytes[7] = 0x02;
    expect(parseMidiTracksFromBytes(bytes)).toEqual([]);
  });

  it("parses a named track with note-on events and tempo meta", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(5), ...asciiBytes("Drums"),
      ...encodeVarLen(0), 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20,
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(480), 0x99, 38, 90,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks).toHaveLength(1);
    expect(tracks[0].name).toBe("Drums");
    expect(tracks[0].notes).toHaveLength(2);
    expect(tracks[0].notes[0].midi).toBe(36);
    expect(tracks[0].notes[0].channel).toBe(9);
  });

  it("ignores note-on with velocity 0 (treated as note-off)", () => {
    const events = [
      ...encodeVarLen(0), 0x99, 36, 0, // vel 0 -> not a note
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes).toHaveLength(0);
  });

  it("handles running status", () => {
    const events = [
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(10), 38, 90, // running status reuses 0x99
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes).toHaveLength(2);
    expect(tracks[0].notes[1].midi).toBe(38);
  });

  it("skips sysex events", () => {
    const events = [
      ...encodeVarLen(0), 0xf0, ...encodeVarLen(2), 0x7e, 0xf7,
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes).toHaveLength(1);
  });

  it("handles a program-change (1 data byte) message", () => {
    const events = [
      ...encodeVarLen(0), 0xc0, 5, // program change
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes).toHaveLength(1);
  });

  it("stops parsing tracks when the MTrk magic is wrong", () => {
    const events = [...encodeVarLen(0), 0xff, 0x2f, 0x00];
    const bytes = buildTestMidi(events, 2); // claims 2 tracks, only 1 present
    const tracks = parseMidiTracksFromBytes(bytes);
    expect(tracks).toHaveLength(1);
  });

  it("round-trips through selectDrumChartFromMidiBytes", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(5), ...asciiBytes("Drums"),
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const selection = selectDrumChartFromMidiBytes(buildTestMidi(events));
    expect(selection.events.map((e) => e.lane)).toEqual(["BD"]);
  });
});
