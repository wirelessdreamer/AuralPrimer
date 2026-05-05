import { beforeAll, bench, describe } from "vitest";
import { createVisualizer as createBeatsVisualizer } from "@auralprimer/viz-beats";
import { createVisualizer as createDrumHighwayVisualizer } from "@auralprimer/viz-drum-highway";
import {
  parseMidiTracksFromBytes,
  selectDrumChart,
  selectMelodicTracks,
  selectMelodicTracksFromMidiBytes,
  type MidiTrackLike,
} from "../src/chartLoader";
import { inferKeySignature } from "../src/tabRenderer";
import type { FrameContext, TransportState, VizInitContext } from "@auralprimer/viz-sdk";

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

function u32(value: number): number[] {
  return [(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff];
}

function trackChunk(name: string, channel: number, pitches: number[], noteCount: number): number[] {
  const body: number[] = [
    ...encodeVarLen(0),
    0xff,
    0x03,
    ...encodeVarLen(name.length),
    ...asciiBytes(name),
  ];
  if (channel === 0) {
    body.push(...encodeVarLen(0), 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20);
  }

  let tick = 0;
  for (let idx = 0; idx < noteCount; idx += 1) {
    const startTick = idx * 120;
    const pitch = pitches[idx % pitches.length];
    body.push(...encodeVarLen(startTick - tick), 0x90 | channel, pitch, 96);
    tick = startTick;
    body.push(...encodeVarLen(72), 0x80 | channel, pitch, 0);
    tick += 72;
  }
  body.push(...encodeVarLen(0), 0xff, 0x2f, 0x00);
  return [0x4d, 0x54, 0x72, 0x6b, ...u32(body.length), ...body];
}

function buildLargeMidi(): Uint8Array {
  const tracks = [
    trackChunk("Conductor", 0, [60], 1),
    trackChunk("Drums", 9, [36, 42, 38, 46, 49, 51, 45, 41], 1_600),
    trackChunk("Bass", 0, [36, 38, 40, 43, 45, 47], 1_200),
    trackChunk("Rhythm Guitar", 1, [52, 55, 59, 64, 67, 71], 1_200),
    trackChunk("Lead Guitar", 2, [64, 67, 69, 71, 74, 76], 900),
    trackChunk("Keys", 3, [48, 52, 55, 60, 64, 67, 72, 76], 1_400),
  ];
  return Uint8Array.from([
    0x4d,
    0x54,
    0x68,
    0x64,
    0x00,
    0x00,
    0x00,
    0x06,
    0x00,
    0x01,
    (tracks.length >>> 8) & 0xff,
    tracks.length & 0xff,
    0x01,
    0xe0,
    ...tracks.flat(),
  ]);
}

function buildTrackFixture(): MidiTrackLike[] {
  const notes = Array.from({ length: 6_000 }, (_, idx) => ({
    t: idx * 0.06,
    t_off: idx * 0.06 + 0.08,
    midi: [36, 42, 38, 46, 49, 51, 45, 41, 60, 64, 67][idx % 11],
    channel: idx % 11 < 8 ? 9 : 3,
    velocity: 96,
  }));
  return [
    { index: 0, name: "Drums", notes: notes.slice(0, 4_800) },
    { index: 1, name: "Keys", notes: notes.slice(4_800) },
  ];
}

function buildPianoNotes() {
  return Array.from({ length: 4_096 }, (_, idx) => {
    const root = [48, 50, 53, 55, 57, 60, 62, 65][idx % 8];
    return {
      t_on: idx * 0.08,
      t_off: idx * 0.08 + 0.18 + (idx % 5) * 0.04,
      pitch: root + [0, 4, 7, 12][Math.floor(idx / 8) % 4],
      velocity: 0.55 + (idx % 6) * 0.06,
    };
  });
}

function fakeCanvasContext(): CanvasRenderingContext2D {
  const gradient = { addColorStop: () => {} };
  return new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === "canvas") return { width: 1280, height: 720 };
        if (prop === "measureText") return () => ({ width: 42 });
        if (prop === "createLinearGradient" || prop === "createRadialGradient") return () => gradient;
        return () => {};
      },
      set() {
        return true;
      },
    },
  ) as unknown as CanvasRenderingContext2D;
}

const midiBytes = buildLargeMidi();
const parsedTracks = parseMidiTracksFromBytes(midiBytes);
const trackFixture = buildTrackFixture();
const pianoNotes = buildPianoNotes();
const canvas = { width: 1280, height: 720 } as HTMLCanvasElement;
const ctx2d = fakeCanvasContext();
const state: TransportState = {
  t: 12,
  isPlaying: true,
  playbackRate: 1,
  bpm: 120,
  timeSignature: [4, 4],
};

describe("frontend parser and mapping hot paths", () => {
  bench("parse MIDI tracks from 6-track synthetic SongPack MIDI", () => {
    parseMidiTracksFromBytes(midiBytes);
  });

  bench("select drum chart from parsed tracks", () => {
    selectDrumChart(parsedTracks);
  });

  bench("select melodic tracks from parsed tracks", () => {
    selectMelodicTracks(parsedTracks);
  });

  bench("parse MIDI and select melodic tracks end-to-end", () => {
    selectMelodicTracksFromMidiBytes(midiBytes);
  });

  bench("select drum chart from dense in-memory track fixture", () => {
    selectDrumChart(trackFixture);
  });

  bench("infer key signature from dense piano track", () => {
    inferKeySignature(pianoNotes);
  });
});

describe("visualizer update and render loops", () => {
  const frame: FrameContext = {
    canvas,
    ctx2d,
    width: 1280,
    height: 720,
    dpr: 1,
    state,
  };
  const initContext: VizInitContext = {
    canvas,
    ctx2d,
    song: {
      notes: Array.from({ length: 2_400 }, (_, idx) => ({
        t_on: idx * 0.08,
        t_off: idx * 0.08 + 0.09,
        pitch: [36, 42, 38, 46, 49, 51, 45, 41][idx % 8],
        velocity: 80 + (idx % 40),
      })),
    },
  };
  const beats = createBeatsVisualizer();
  const drums = createDrumHighwayVisualizer();

  beforeAll(async () => {
    await beats.init(initContext);
    beats.onResize(frame.width, frame.height, frame.dpr);
    await drums.init(initContext);
    drums.onResize(frame.width, frame.height, frame.dpr);
  });

  bench("beats visualizer update+render 240 frames", () => {
    for (let idx = 0; idx < 240; idx += 1) {
      const t = idx / 60;
      const frameState = { ...state, t };
      beats.update(1 / 60, frameState);
      beats.render({ ...frame, state: frameState });
    }
  });

  bench("drum highway visualizer update+render 240 frames", () => {
    for (let idx = 0; idx < 240; idx += 1) {
      const t = idx / 60;
      const frameState = { ...state, t };
      drums.update(1 / 60, frameState);
      drums.render({ ...frame, state: frameState });
    }
  });
});
