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

  it("returns empty selection (no melodic MIDI parse) when notes.mid is absent and no drum_tab.json", async () => {
    // Absent notes.mid: skips read_auralsong_mid, tries the root drum_tab.json.
    // Here that read yields nothing, so the selection is empty.
    invokeMock.mockResolvedValue(null);
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: false },
      consoleBridge: bridge,
    });
    expect(out).toEqual({ drumSelection: null, melodicTracks: [] });
    // Never parses melodic MIDI — the only invoke is the drum_tab.json read.
    expect(invokeMock).not.toHaveBeenCalledWith("read_auralsong_mid", expect.anything());
  });

  it("charts a drums-only sloppak from drum_tab.json when notes.mid is absent", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_json" && args.relPath === "drum_tab.json") {
        return { version: 1, name: "drums", kit: [], hits: [
          { t: 0, p: "kick" }, { t: 0.5, p: "snare" }, { t: 1, p: "hihat_closed" },
        ] };
      }
      return undefined;
    });
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: false },
      consoleBridge: bridge,
    });
    expect(out.melodicTracks).toEqual([]);
    expect(out.drumSelection).not.toBeNull();
    expect(out.drumSelection!.reason).toBe("drum_tab");
    expect(out.drumSelection!.events).toHaveLength(3);
    // Never touches the melodic MIDI path.
    expect(invokeMock).not.toHaveBeenCalledWith("read_auralsong_mid", expect.anything());
    expect(bridge.log).toHaveBeenCalledWith("play", expect.stringContaining("drum_tab.json"));
  });

  it("charts drums from drum_tab.json when the MIDI blob is empty", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") return { bytes: [] };
      if (cmd === "read_auralsong_json" && args.relPath === "drum_tab.json") {
        return { version: 1, name: "drums", kit: [], hits: [{ t: 0, p: "kick" }] };
      }
      return undefined;
    });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: makeBridge(),
    });
    expect(out.melodicTracks).toEqual([]);
    expect(out.drumSelection).not.toBeNull();
    expect(out.drumSelection!.reason).toBe("drum_tab");
  });

  it("returns empty selection for an empty MIDI blob with no drum_tab.json", async () => {
    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "read_auralsong_mid") return { bytes: [] };
      return null; // drum_tab.json read yields nothing
    });
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

  it("falls back to feedpak aural/notes.mid when legacy features/notes.mid is absent", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid" && args.relPath === "features/notes.mid") {
        throw new Error("not found");
      }
      if (cmd === "read_auralsong_mid" && args.relPath === "aural/notes.mid") {
        return { bytes: midiBytesArray() };
      }
      return undefined;
    });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: makeBridge(),
    });

    expect(out.melodicTracks.map((t) => t.role)).toContain("keys");
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "features/notes.mid",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "aural/notes.mid",
    });
  });

  it("uses manifest-declared notes MIDI and drum_tab paths", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid" && args.relPath === "custom/notes.mid") {
        return { bytes: midiBytesArray() };
      }
      if (cmd === "read_auralsong_json" && args.relPath === "custom/drums.json") {
        return { version: 1, name: "drums", kit: [], hits: [{ t: 0, p: "ride" }] };
      }
      throw new Error(`unexpected ${cmd}:${args.relPath}`);
    });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: {
        has_notes_mid: true,
        manifest_raw: { aural_notes_mid: "custom/notes.mid", drum_tab: "custom/drums.json" },
      },
      consoleBridge: makeBridge(),
    });

    expect(out.melodicTracks.map((t) => t.role)).toContain("keys");
    expect(out.drumSelection?.reason).toBe("drum_tab");
    expect(out.drumSelection?.events.map((e) => e.lane)).toEqual(["RD"]);
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "custom/notes.mid",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/c",
      relPath: "custom/drums.json",
    });
  });

  it("falls back to manifest drum_tab when all notes MIDI candidates fail", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") {
        throw new Error(`missing midi: ${args.relPath}`);
      }
      if (cmd === "read_auralsong_json" && args.relPath === "custom/drums.json") {
        return { version: 1, name: "drums", kit: [], hits: [{ t: 0, p: "ride" }] };
      }
      throw new Error(`unexpected ${cmd}:${args.relPath}`);
    });
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: {
        has_notes_mid: true,
        manifest_raw: { aural_notes_mid: "custom/notes.mid", drum_tab: "custom/drums.json" },
      },
      consoleBridge: bridge,
    });

    expect(out.melodicTracks).toEqual([]);
    expect(out.drumSelection?.reason).toBe("drum_tab");
    expect(out.drumSelection?.events.map((e) => e.lane)).toEqual(["RD"]);
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "custom/notes.mid",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "features/notes.mid",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_mid", {
      containerPath: "/c",
      relPath: "aural/notes.mid",
    });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/c",
      relPath: "custom/drums.json",
    });
    expect(bridge.warn).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("trying drum_tab fallback"),
      expect.anything(),
    );
  });

  it("prefers drum_tab.json over the notes.mid drum selection when present", async () => {
    // 1st invoke = notes.mid blob; 2nd invoke = drum_tab.json (root-level read).
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") return { bytes: midiBytesArray() };
      if (cmd === "read_auralsong_json" && args.relPath === "drum_tab.json") {
        return { version: 1, name: "drums", kit: [], hits: [
          { t: 0, p: "kick" }, { t: 0.5, p: "snare" }, { t: 1, p: "hihat_closed" },
        ] };
      }
      return undefined;
    });
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: bridge,
    });
    expect(out.drumSelection).not.toBeNull();
    expect(out.drumSelection!.reason).toBe("drum_tab");
    expect(out.drumSelection!.events).toHaveLength(3);
    expect(bridge.log).toHaveBeenCalledWith(
      "play",
      expect.stringContaining("drum_tab.json"),
    );
  });

  it("falls back to the notes.mid drum selection when drum_tab.json is absent", async () => {
    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "read_auralsong_mid") return { bytes: midiBytesArray() };
      // drum_tab.json read rejects (missing file).
      throw new Error("not found");
    });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true },
      consoleBridge: makeBridge(),
    });
    expect(out.drumSelection).not.toBeNull();
    expect(out.drumSelection!.reason).not.toBe("drum_tab");
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

  it("applies fingering sidecars to parsed melodic tracks", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") return { bytes: midiBytesArray() };
      if (cmd === "read_auralsong_json" && args.relPath === "aural/fingering.keys.json") {
        return {
          version: "1.0.0",
          instrument: "keys",
          notes: [{ t_on: 0, t_off: 0.25, pitch: 60, velocity: 100, string: 1, fret: 5 }],
        };
      }
      return undefined;
    });
    const bridge = makeBridge();
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: { has_notes_mid: true, has_aural_fingering: true },
      consoleBridge: bridge,
    });

    const keysTrack = out.melodicTracks.find((track) => track.role === "keys");
    expect(keysTrack?.notes[0]).toMatchObject({ pitch: 60, string: 1, fret: 5 });
    expect(bridge.log).toHaveBeenCalledWith(
      "play",
      expect.stringContaining("fingering: keys"),
    );
  });

  it("loads fingering from manifest aural_fingering paths", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") return { bytes: midiBytesArray() };
      if (cmd === "read_auralsong_json" && args.relPath === "custom/keys-fingering.json") {
        return {
          version: "1.0.0",
          instrument: "keys",
          notes: [{ t_on: 0, t_off: 0.25, pitch: 60, velocity: 100, string: 2, fret: 7 }],
        };
      }
      throw new Error(`unexpected ${cmd}:${args.relPath}`);
    });
    const out = await readSongChartSelection({
      containerPath: "/c",
      details: {
        has_notes_mid: true,
        manifest_raw: { aural_fingering: { keys: "custom/keys-fingering.json" } },
      },
      consoleBridge: makeBridge(),
    });

    const keysTrack = out.melodicTracks.find((track) => track.role === "keys");
    expect(keysTrack?.notes[0]).toMatchObject({ pitch: 60, string: 2, fret: 7 });
    expect(invokeMock).toHaveBeenCalledWith("read_auralsong_json", {
      containerPath: "/c",
      relPath: "custom/keys-fingering.json",
    });
  });

  it("charts melodic tracks from aural_fingering sidecars when notes.mid is absent", async () => {
    invokeMock.mockImplementation(async (cmd: string, args: { relPath: string }) => {
      if (cmd === "read_auralsong_mid") throw new Error("should not read MIDI");
      if (cmd === "read_auralsong_json" && args.relPath === "drum_tab.json") {
        throw new Error("no drum tab");
      }
      if (cmd === "read_auralsong_json" && args.relPath === "custom/fingering.guitar.json") {
        return {
          version: "1.0.0",
          instrument: "guitar",
          notes: [{ t_on: 0.5, pitch: 55, velocity: 100, string: 3, fret: 0 }],
        };
      }
      throw new Error(`unexpected ${cmd}:${args.relPath}`);
    });
    const bridge = makeBridge();

    const out = await readSongChartSelection({
      containerPath: "/c",
      details: {
        has_notes_mid: false,
        has_aural_fingering: true,
        manifest_raw: { aural_fingering: { guitar: "custom/fingering.guitar.json" } },
      },
      consoleBridge: bridge,
    });

    expect(out.drumSelection).toBeNull();
    expect(out.melodicTracks).toHaveLength(1);
    expect(out.melodicTracks[0]).toMatchObject({
      role: "lead_guitar",
      trackName: "Guitar",
      channel: 2,
      notes: [{ t_on: 0.5, t_off: 0.65, pitch: 55, velocity: 100 / 127, string: 3, fret: 0 }],
    });
    expect(invokeMock).not.toHaveBeenCalledWith("read_auralsong_mid", expect.anything());
    expect(bridge.log).toHaveBeenCalledWith(
      "play",
      expect.stringContaining("charting melodic tracks from fingering: lead_guitar"),
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
      expect.stringContaining("failed to load notes MIDI"),
      expect.anything(),
    );
  });
});
