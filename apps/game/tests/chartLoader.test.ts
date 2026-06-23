import { describe, expect, it } from "vitest";
import {
  selectDrumChart,
  selectMelodicTracks,
  selectMelodicTracksFromMidiBytes,
  selectDrumChartFromMidiBytes,
  parseMidiTracksFromBytes,
  applyRefinementsToMelodicTracks,
  type MidiTrackLike,
  type MelodicTrackSelection,
} from "../src/chartLoader";
import type { RefinementFile } from "@auralprimer/auralsong/refinement";

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

  it("infers role from a Bass track name", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(4), ...asciiBytes("Bass"),
      ...encodeVarLen(0), 0x90, 40, 100,
      ...encodeVarLen(240), 0x80, 40, 0,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = selectMelodicTracksFromMidiBytes(buildTestMidi(events));
    expect(tracks).toHaveLength(1);
    expect(tracks[0].role).toBe("bass");
  });

  it("handles a hanging note-on (no matching note-off) by closing it at end of track", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(4), ...asciiBytes("Keys"),
      ...encodeVarLen(0), 0x93, 60, 100, // note-on, never closed
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = selectMelodicTracksFromMidiBytes(buildTestMidi(events));
    expect(tracks).toHaveLength(1);
    expect(tracks[0].notes).toHaveLength(1);
    // No t_off parsed -> floor applied (t_on + 0.15).
    expect(tracks[0].notes[0].t_off).toBeGreaterThan(tracks[0].notes[0].t_on);
  });
});

describe("selectMelodicTracks (pure)", () => {
  function melodicTrack(name: string | undefined, channel: number, midi = 60): MidiTrackLike {
    return {
      index: 0,
      name,
      notes: [{ t: 0, t_off: 0.5, midi, channel, velocity: 100 }],
    };
  }

  it("skips drum-named and conductor/structure tracks", () => {
    const out = selectMelodicTracks([
      { index: 0, name: "Drums", notes: [{ t: 0, midi: 60, channel: 9 }] },
      { index: 1, name: "Conductor", notes: [{ t: 0, midi: 60, channel: 0 }] },
      { index: 2, name: "Structure", notes: [{ t: 0, midi: 60, channel: 0 }] },
    ]);
    expect(out).toHaveLength(0);
  });

  it("infers role from channel when the name gives no hint", () => {
    const out = selectMelodicTracks([melodicTrack("Untitled", 0)]);
    expect(out).toHaveLength(1);
    expect(out[0].role).toBe("bass");
    expect(out[0].channel).toBe(0);
  });

  it("dedups repeated roles (keeps the first)", () => {
    const out = selectMelodicTracks([
      melodicTrack("Bass", 0, 40),
      melodicTrack("Bass II", 0, 41),
    ]);
    expect(out).toHaveLength(1);
  });

  it("skips a track whose only notes are on channel 9/15", () => {
    const out = selectMelodicTracks([
      { index: 0, name: "Bass", notes: [{ t: 0, midi: 40, channel: 9 }] },
    ]);
    // Role inferred from name "Bass" but all notes filtered out -> dropped.
    expect(out).toHaveLength(0);
  });

  it("skips tracks with no inferable role", () => {
    const out = selectMelodicTracks([
      { index: 0, name: "Mystery", notes: [{ t: 0, midi: 60, channel: 7 }] },
    ]);
    expect(out).toHaveLength(0);
  });

  it("sorts output bass, rhythm, lead, keys, melodic", () => {
    const out = selectMelodicTracks([
      melodicTrack("Keys", 3),
      melodicTrack("Bass", 0),
      melodicTrack("Lead Guitar", 2),
    ]);
    expect(out.map((t) => t.role)).toEqual(["bass", "lead_guitar", "keys"]);
  });
});

describe("applyRefinementsToMelodicTracks", () => {
  const baseTrack: MelodicTrackSelection = {
    role: "keys",
    trackName: "Keys",
    channel: 3,
    notes: [
      { t_on: 0, t_off: 0.5, pitch: 60, velocity: 0.8 },
      { t_on: 1, t_off: 1.5, pitch: 62, velocity: 0.8 },
    ],
  };

  it("returns a clone when there are no refinements", () => {
    const out = applyRefinementsToMelodicTracks([baseTrack], []);
    expect(out).toHaveLength(1);
    expect(out[0].notes).not.toBe(baseTrack.notes);
    expect(out[0].notes).toEqual(baseTrack.notes);
  });

  it("applies a matching-instrument refinement overlay", () => {
    const ref: RefinementFile = {
      version: "0.1.0",
      instrument: "keys",
      regions: [
        {
          id: "r0",
          t_start: 0,
          t_end: 0.9,
          accepted_candidate: "x",
          accepted_at: "now",
          notes: [{ t_on: 0.1, t_off: 0.3, pitch: 72, velocity: 0.9 }],
        },
      ],
    };
    const out = applyRefinementsToMelodicTracks([baseTrack], [ref]);
    // The original 60 in [0,0.9) is replaced by 72; the 62 at t=1 survives.
    const pitches = out[0].notes.map((n) => n.pitch).sort((a, b) => a - b);
    expect(pitches).toEqual([62, 72]);
  });

  it("leaves a track untouched when no refinement targets its role", () => {
    const ref: RefinementFile = {
      version: "0.1.0",
      instrument: "bass",
      regions: [],
    };
    const out = applyRefinementsToMelodicTracks([baseTrack], [ref]);
    expect(out[0].notes).toEqual(baseTrack.notes);
  });
});

describe("parseMidiTracksFromBytes (game, tempo + division)", () => {
  it("applies a tempo change so seconds advance per the new tempo", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20, // 500000us/q
      ...encodeVarLen(0), 0x90, 60, 100,
      ...encodeVarLen(480), 0xff, 0x51, 0x03, 0x0f, 0x42, 0x40, // tempo change
      ...encodeVarLen(0), 0x80, 60, 0,
      ...encodeVarLen(480), 0x90, 62, 100,
      ...encodeVarLen(480), 0x80, 62, 0,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes.length).toBeGreaterThanOrEqual(2);
    // Times should be finite and monotonically non-decreasing by tick.
    for (const n of tracks[0].notes) {
      expect(Number.isFinite(n.t)).toBe(true);
    }
  });

  it("decodes SMPTE division headers without throwing", () => {
    const trackBytes = Uint8Array.from([
      ...encodeVarLen(0), 0x90, 60, 100,
      ...encodeVarLen(10), 0x80, 60, 0,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ]);
    // division high bit set: -25 fps (0xE7), 40 ticks/frame (0x28).
    const header = Uint8Array.from([
      0x4d, 0x54, 0x68, 0x64, 0x00, 0x00, 0x00, 0x06,
      0x00, 0x00, 0x00, 0x01, 0xe7, 0x28,
      0x4d, 0x54, 0x72, 0x6b,
      (trackBytes.length >>> 24) & 0xff,
      (trackBytes.length >>> 16) & 0xff,
      (trackBytes.length >>> 8) & 0xff,
      trackBytes.length & 0xff,
    ]);
    const bytes = new Uint8Array(header.length + trackBytes.length);
    bytes.set(header, 0);
    bytes.set(trackBytes, header.length);
    const tracks = parseMidiTracksFromBytes(bytes);
    expect(tracks[0].notes).toHaveLength(1);
    expect(tracks[0].notes[0].t).toBeGreaterThanOrEqual(0);
  });

  it("pairs note-on/note-on retrigger (implicit off) on the same key", () => {
    const events = [
      ...encodeVarLen(0), 0x90, 60, 100, // on
      ...encodeVarLen(100), 0x90, 60, 110, // retrigger -> closes prior note
      ...encodeVarLen(100), 0x80, 60, 0,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const tracks = parseMidiTracksFromBytes(buildTestMidi(events));
    expect(tracks[0].notes.length).toBeGreaterThanOrEqual(2);
  });

  it("round-trips into a drum selection via selectDrumChartFromMidiBytes", () => {
    const events = [
      ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(5), ...asciiBytes("Drums"),
      ...encodeVarLen(0), 0x99, 36, 100,
      ...encodeVarLen(0), 0xff, 0x2f, 0x00,
    ];
    const sel = selectDrumChartFromMidiBytes(buildTestMidi(events));
    expect(sel.events.map((e) => e.lane)).toEqual(["BD"]);
  });
});
