// @vitest-environment jsdom
/**
 * Execution tests for readSongChartSelection. Mocks the Tauri `invoke`
 * (notes.mid blob), the refinement loader, and the consoleBridge, and
 * drives the no-notes / empty-blob / parse / refinement / error branches.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { readSongChartSelection } from "../src/songChartLoader";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const loadRefinementsForRoles = vi.fn();
vi.mock("../src/refinementLoader", () => ({
  loadRefinementsForRoles: (...a: unknown[]) => loadRefinementsForRoles(...a),
}));

const invokeMock = invoke as unknown as ReturnType<typeof vi.fn>;

// ─── MIDI byte builders (one named "Keys" melodic track + a Drums track) ─────

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

function buildMidiMultiTrack(tracks: number[][]): number[] {
  const header = [
    0x4d, 0x54, 0x68, 0x64, 0x00, 0x00, 0x00, 0x06,
    0x00, 0x01, 0x00, tracks.length & 0xff, 0x01, 0xe0,
  ];
  const out = [...header];
  for (const ev of tracks) {
    out.push(
      0x4d, 0x54, 0x72, 0x6b,
      (ev.length >>> 24) & 0xff,
      (ev.length >>> 16) & 0xff,
      (ev.length >>> 8) & 0xff,
      ev.length & 0xff,
    );
    out.push(...ev);
  }
  return out;
}

function midiBytesArray(): number[] {
  const keysTrack = [
    ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(4), ...asciiBytes("Keys"),
    ...encodeVarLen(0), 0x93, 60, 100,
    ...encodeVarLen(240), 0x83, 60, 0,
    ...encodeVarLen(0), 0xff, 0x2f, 0x00,
  ];
  const drumsTrack = [
    ...encodeVarLen(0), 0xff, 0x03, ...encodeVarLen(5), ...asciiBytes("Drums"),
    ...encodeVarLen(0), 0x99, 36, 100,
    ...encodeVarLen(0), 0xff, 0x2f, 0x00,
  ];
  return buildMidiMultiTrack([keysTrack, drumsTrack]);
}

function makeBridge() {
  return { log: vi.fn(), warn: vi.fn() } as any;
}

describe("readSongChartSelection", () => {
  beforeEach(() => {
    invokeMock.mockReset();
    loadRefinementsForRoles.mockReset();
    loadRefinementsForRoles.mockResolvedValue([]);
  });

  it("returns empty selection without invoking when notes.mid is absent", async () => {
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: false },
      consoleBridge: bridge,
    });
    expect(out).toEqual({ drumSelection: null, melodicTracks: [] });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("returns empty selection for an empty MIDI blob", async () => {
    invokeMock.mockResolvedValueOnce({ bytes: [] });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: makeBridge(),
    });
    expect(out).toEqual({ drumSelection: null, melodicTracks: [] });
  });

  it("parses drums + melodic tracks and logs them", async () => {
    invokeMock.mockResolvedValueOnce({ bytes: midiBytesArray() });
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: bridge,
    });
    expect(out.drumSelection).not.toBeNull();
    expect(out.melodicTracks.map((t) => t.role)).toContain("keys");
    expect(bridge.log).toHaveBeenCalledWith(
      "play",
      expect.stringContaining("melodic track"),
    );
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "features/notes.mid",
    });
  });

  it("applies refinement overlays and appends a refinement suffix to the log", async () => {
    invokeMock.mockResolvedValueOnce({ bytes: midiBytesArray() });
    loadRefinementsForRoles.mockResolvedValueOnce([
      { version: "0.1.0", instrument: "keys", regions: [] },
    ]);
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: bridge,
    });
    expect(out.melodicTracks.length).toBeGreaterThan(0);
    expect(bridge.log).toHaveBeenCalledWith(
      "play",
      expect.stringContaining("refinement: keys"),
    );
  });

  it("forwards a warn callback to the refinement loader", async () => {
    invokeMock.mockResolvedValueOnce({ bytes: midiBytesArray() });
    let captured: { warn: (c: string, m: string, d?: unknown) => void } | undefined;
    loadRefinementsForRoles.mockImplementationOnce(async (_p, _roles, hooks: any) => {
      captured = hooks;
      return [];
    });
    const bridge = makeBridge();
    await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: bridge,
    });
    captured!.warn("debugging", "hi", { a: 1 });
    expect(bridge.warn).toHaveBeenCalledWith("debugging", "hi", { a: 1 });
  });

  it("returns empty selection + warns when invoke rejects", async () => {
    invokeMock.mockRejectedValueOnce(new Error("read failed"));
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: bridge,
    });
    expect(out).toEqual({ drumSelection: null, melodicTracks: [] });
    expect(bridge.warn).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("failed to load/parse"),
      expect.anything(),
    );
  });
});
