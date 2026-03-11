import {
  parseMidiTracksFromBytes,
  selectDrumChart,
  selectDrumChartFromMidiBytes,
  type MidiTrackLike
} from "../src/chartLoader";

function t(index: number, name: string, notes: Array<[number, number, number?]>): MidiTrackLike {
  return {
    index,
    name,
    notes: notes.map(([time, midi, channel]) => ({ t: time, midi, channel }))
  };
}

function asBytes(text: string): number[] {
  return Array.from(new TextEncoder().encode(text));
}

function u32be(v: number): number[] {
  return [(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff];
}

function varLen(v: number): number[] {
  if (v < 0 || !Number.isFinite(v)) return [0];
  let value = v >>> 0;
  const out = [value & 0x7f];
  value >>>= 7;
  while (value > 0) {
    out.unshift((value & 0x7f) | 0x80);
    value >>>= 7;
  }
  return out;
}

function makeMidiWithNamedDrumTrack(): Uint8Array {
  const name = asBytes("Drums");
  const events = [
    0x00, 0xff, 0x03, name.length, ...name,
    0x00, 0x90, 36, 110,
    ...varLen(120), 0x90, 38, 110,
    0x00, 0xff, 0x2f, 0x00
  ];

  const out = [
    ...asBytes("MThd"),
    ...u32be(6),
    0x00, 0x01, // format 1
    0x00, 0x01, // one track
    0x01, 0xe0, // division 480
    ...asBytes("MTrk"),
    ...u32be(events.length),
    ...events
  ];
  return new Uint8Array(out);
}

function makeMidiWithTempoChange(): Uint8Array {
  const tempoTrack = [
    0x00, 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20, // 120 BPM at t=0
    ...varLen(480), 0xff, 0x51, 0x03, 0x0f, 0x42, 0x40, // 60 BPM at tick 480
    0x00, 0xff, 0x2f, 0x00
  ];
  const name = asBytes("Drums");
  const drumTrack = [
    0x00, 0xff, 0x03, name.length, ...name,
    ...varLen(960), 0x90, 36, 110,
    0x00, 0xff, 0x2f, 0x00
  ];

  const out = [
    ...asBytes("MThd"),
    ...u32be(6),
    0x00, 0x01, // format 1
    0x00, 0x02, // two tracks
    0x01, 0xe0, // division 480
    ...asBytes("MTrk"),
    ...u32be(tempoTrack.length),
    ...tempoTrack,
    ...asBytes("MTrk"),
    ...u32be(drumTrack.length),
    ...drumTrack
  ];
  return new Uint8Array(out);
}

describe("chartLoader strict/relaxed selection", () => {
  it("falls back to relaxed when strict pass is empty", () => {
    const tracks: MidiTrackLike[] = [
      t(0, "Keys", [
        [0.0, 36, 1],
        [0.1, 42, 1],
        [0.2, 38, 1]
      ])
    ];

    const out = selectDrumChart(tracks);
    expect(out.mode).toBe("relaxed");
    expect(out.reason).toBe("strict_empty");
    expect(out.events).toHaveLength(3);
  });

  it("keeps strict when channel-9 drums are present and relaxed is not much richer", () => {
    const tracks: MidiTrackLike[] = [
      t(0, "Track0", [
        [0.0, 36, 9],
        [0.1, 38, 9],
        [0.2, 42, 9]
      ]),
      t(1, "Guitar", [[0.15, 36, 0]])
    ];

    const out = selectDrumChart(tracks);
    expect(out.mode).toBe("strict");
    expect(out.reason).toBe("strict_preferred");
    expect(out.strictCount).toBe(3);
  });

  it("switches to relaxed when it is substantially richer", () => {
    const tracks: MidiTrackLike[] = [
      t(0, "Track0", [
        [0.0, 36, 9],
        [0.1, 38, 9],
        [0.2, 42, 9]
      ]),
      t(1, "Noise", [
        [0.0, 41, 1],
        [0.05, 46, 1],
        [0.1, 49, 1],
        [0.15, 50, 1],
        [0.2, 51, 1]
      ])
    ];

    const out = selectDrumChart(tracks);
    expect(out.mode).toBe("relaxed");
    expect(out.reason).toBe("relaxed_richer");
    expect(out.relaxedCount).toBeGreaterThan(out.strictCount);
    expect(out.relaxedUniqueLanes.length).toBeGreaterThan(out.strictUniqueLanes.length);
  });

  it("preserves sparse dedicated drum track via named-track guard", () => {
    const tracks: MidiTrackLike[] = [
      t(0, "Drums Dedicated", [
        [0.0, 36, 1],
        [0.2, 38, 1],
        [0.4, 42, 1]
      ]),
      t(1, "Keys Dense", [
        [0.0, 41, 1],
        [0.05, 46, 1],
        [0.1, 49, 1],
        [0.15, 50, 1],
        [0.2, 51, 1],
        [0.25, 47, 1]
      ])
    ];

    const out = selectDrumChart(tracks);
    expect(out.mode).toBe("strict");
    expect(out.reason).toBe("dedicated_drum_track_guard");
    expect(out.events.every((e) => e.trackIndex === 0)).toBe(true);
  });

  it("parses named track + note-on events from MIDI bytes", () => {
    const tracks = parseMidiTracksFromBytes(makeMidiWithNamedDrumTrack());
    expect(tracks).toHaveLength(1);
    expect(tracks[0].name).toBe("Drums");
    expect(tracks[0].notes.map((n) => n.midi)).toEqual([36, 38]);
    expect(tracks[0].notes.map((n) => n.channel)).toEqual([0, 0]);
    expect(tracks[0].notes[0].t).toBeCloseTo(0, 6);
    expect(tracks[0].notes[1].t).toBeCloseTo(0.125, 6);
  });

  it("converts MIDI ticks to seconds using tempo changes", () => {
    const tracks = parseMidiTracksFromBytes(makeMidiWithTempoChange());
    expect(tracks).toHaveLength(2);
    expect(tracks[1].name).toBe("Drums");
    expect(tracks[1].notes).toHaveLength(1);
    expect(tracks[1].notes[0].t).toBeCloseTo(1.5, 6);
  });

  it("selects strict drums from MIDI bytes with named-track guard", () => {
    const out = selectDrumChartFromMidiBytes(makeMidiWithNamedDrumTrack());
    expect(out.mode).toBe("strict");
    expect(out.reason).toBe("dedicated_drum_track_guard");
    expect(out.events).toHaveLength(2);
  });
});
