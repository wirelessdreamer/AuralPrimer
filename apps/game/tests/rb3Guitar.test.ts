import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { MidiInputMessageEvent } from "../src/midiInput";
import {
  RB3_PICK_RING_MS,
  Rb3GuitarTracker,
  decodeRb3Sysex,
  describeRb3Message,
  rb3ButtonEvent,
  rb3ButtonLabel,
  rb3ButtonsDown,
} from "../src/rb3Guitar";

/**
 * A recorded session from a Fender Squier (Rock Band 3 pro guitar) through a
 * DUO-CAPTURE EX, 2026-10-14: each open string low to high, fret 1 on each,
 * fret 5 on A held without picking, low E fret 5 picked, then the face
 * buttons and d-pad. One JSON line per message: seconds, type, raw bytes.
 */
const CAPTURE = readFileSync(join(__dirname, "fixtures", "rb3-squier-capture.jsonl"), "utf8")
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as { t: number; bytes: number[] });

/** The event the Rust side would emit for these bytes (midi_clock_input.rs). */
function toEvent(t: number, bytes: number[]): MidiInputMessageEvent {
  const status = bytes[0];
  const hi = status & 0xf0;
  const base = { timestamp_us: Math.round(t * 1e6), status, bytes };
  if (status === 0xf0) return { ...base, message_type: "sysex" };
  if (hi === 0x90 || hi === 0x80) {
    const off = hi === 0x80 || bytes[2] === 0;
    return { ...base, message_type: off ? "note_off" : "note_on", channel: status & 0x0f, data1: bytes[1], data2: bytes[2] };
  }
  if (hi === 0xc0) return { ...base, message_type: "program_change", channel: status & 0x0f, data1: bytes[1] };
  return { ...base, message_type: "unknown" };
}

/** Replay the capture up to `untilSec`, returning the tracker and every button edge seen. */
function replay(untilSec: number) {
  const tracker = new Rb3GuitarTracker();
  const edges: Array<{ t: number; button: number; down: boolean }> = [];
  for (const m of CAPTURE) {
    if (m.t > untilSec) break;
    for (const e of tracker.apply(toEvent(m.t, m.bytes), m.t * 1000)) edges.push({ t: m.t, ...e });
  }
  return { tracker, edges };
}

/** What the guitar views get at `sec`: [string as the guitar numbers it, MIDI note, picked]. */
const live = (tracker: Rb3GuitarTracker, sec: number) =>
  tracker.liveNotes(sec * 1000).map((n) => [n.channel + 1, n.pitch, n.picked]);

describe("decodeRb3Sysex", () => {
  it("reads a fret change: string (1 = high e) and the MIDI note of the fret", () => {
    // A string, fret 5, held without picking.
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x01, 0x05, 0x32, 0xf7])).toEqual({ kind: "fret", string: 5, note: 50 });
  });

  it("reads a pick: string and velocity -- the last byte is velocity, not a note", () => {
    // Open low E picked; the note-on beside it in the capture has velocity 110 too.
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x05, 0x06, 0x6e, 0xf7])).toEqual({ kind: "pick", string: 6, velocity: 110 });
  });

  it("reads the buttons and the d-pad, and the keep-alive", () => {
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x08, 0x04, 0x00, 0x08, 0x00, 0xf7])).toEqual({ kind: "buttons", face: 4, menu: 0, dpad: 8, extra: 0 });
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x08, 0x00, 0x01, 0x48, 0x00, 0xf7])).toEqual({ kind: "buttons", face: 0, menu: 1, dpad: 8, extra: 0x40 });
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x09, ...new Array(12).fill(0), 0xf7])).toEqual({ kind: "keepalive" });
  });

  it("ignores anything else: other SysEx, a wrong length, a string out of range", () => {
    expect(decodeRb3Sysex([0xf0, 0x7e, 0x7f, 0x06, 0x01, 0xf7])).toBeNull();
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x01, 0x05, 0xf7])).toBeNull();
    expect(decodeRb3Sysex([0xf0, 0x08, 0x40, 0x0a, 0x01, 0x07, 0x32, 0xf7])).toBeNull();
    expect(decodeRb3Sysex([0x90, 40, 100])).toBeNull();
  });
});

describe("Rb3GuitarTracker, replaying a real Squier", () => {
  it("is not active until it has seen the guitar's SysEx", () => {
    const t = new Rb3GuitarTracker();
    expect(t.isActive(0)).toBe(false);
    t.apply(toEvent(0.5, [0x95, 40, 110]), 500); // a bare note proves nothing
    expect(t.isActive(500)).toBe(false);
    t.apply(toEvent(0.6, [0xf0, 0x08, 0x40, 0x0a, 0x05, 0x06, 0x6e, 0xf7]), 600);
    expect(t.isActive(600)).toBe(true);
    // The guitar sends a keep-alive every ~2.2 s; silence for longer means it's gone.
    expect(t.isActive(600 + 10_000)).toBe(false);
  });

  it("shows each open string as it is picked, low E first", () => {
    const { tracker } = replay(0.6);
    expect(live(tracker, 0.6)).toEqual([[6, 40, true]]);
    const later = replay(6.42).tracker;
    // All six picked within the last ~6 s; only those inside the ring window still show.
    expect(live(later, 6.42).map((x) => x[0])).toEqual(
      [6, 5, 4, 3, 2, 1].filter((s) => {
        const pickedAt = { 6: 0.58, 5: 1.74, 4: 2.87, 3: 4.02, 2: 5.23, 1: 6.42 }[s]!;
        return (6.42 - pickedAt) * 1000 < RB3_PICK_RING_MS;
      }).sort((a, b) => a - b),
    );
  });

  it("lets a picked note go after the ring window, since the guitar cannot report a mute", () => {
    const { tracker } = replay(0.6);
    expect(live(tracker, 0.6 + (RB3_PICK_RING_MS - 50) / 1000)).toEqual([[6, 40, true]]);
    expect(live(tracker, 0.6 + (RB3_PICK_RING_MS + 50) / 1000)).toEqual([]);
  });

  it("shows the fretting hand before a pick: A string held at fret 5, not picked", () => {
    const { tracker } = replay(21.0);
    expect(live(tracker, 21.0)).toEqual([[5, 50, false]]);
    // Released at 24.8 s: back to open, and an open string unpicked shows nothing.
    expect(live(replay(25.0).tracker, 25.0)).toEqual([]);
  });

  it("picks a fretted note: low E at fret 5, picked at 30.1 s", () => {
    const { tracker } = replay(30.2);
    expect(live(tracker, 30.2)).toEqual([[6, 45, true]]);
    expect(tracker.liveNotes(30.2 * 1000)[0].velocity).toBe(119);
  });

  it("ends a picked note when its string's fret changes", () => {
    // Low E fret 5 picked at 30.1, released to open at 31.52.
    const { tracker } = replay(31.6);
    expect(live(tracker, 31.6)).toEqual([]);
  });

  it("turns the button bitmask into press and release edges, one per button", () => {
    const { edges } = replay(48);
    const presses = edges.filter((e) => e.down).map((e) => rb3ButtonLabel(e.button));
    // Four face buttons in order, then the d-pad left, right, down, up, then the menu buttons.
    expect(presses.slice(0, 8)).toEqual([
      "Face 1", "Face 2", "Face 3", "Face 4", "D-pad left", "D-pad right", "D-pad down", "D-pad up",
    ]);
    expect(presses).toContain("Menu 2");
    expect(presses).toContain("Menu 1");
    // Every press is released again before the capture's quiet end.
    expect(edges.filter((e) => e.down).length).toBe(edges.filter((e) => !e.down).length);
  });
});

describe("rb3ButtonsDown", () => {
  it("reads a diagonal on the d-pad as both of its directions", () => {
    const down = rb3ButtonsDown({ kind: "buttons", face: 0, menu: 0, dpad: 1, extra: 0 });
    expect([...down].map(rb3ButtonLabel).sort()).toEqual(["D-pad right", "D-pad up"]);
  });
});

describe("rb3ButtonEvent and describeRb3Message", () => {
  it("makes a button change into an input event the transport bindings read", () => {
    expect(rb3ButtonEvent({ button: 16, down: true }, 1234)).toMatchObject({
      timestamp_us: 1234, message_type: "rb3_button", data1: 16, data2: 127, channel: null,
    });
    expect(rb3ButtonEvent({ button: 16, down: false }, 1234).data2).toBe(0);
  });

  it("reads the guitar's messages out in words for the MIDI monitor", () => {
    expect(describeRb3Message({ kind: "fret", string: 5, note: 50 })).toBe("Squier: string 5 (A) at fret 5");
    expect(describeRb3Message({ kind: "fret", string: 5, note: 45 })).toBe("Squier: string 5 (A) open");
    expect(describeRb3Message({ kind: "pick", string: 6, velocity: 110 })).toBe("Squier: string 6 (E) picked, velocity 110");
    expect(describeRb3Message({ kind: "buttons", face: 1, menu: 0, dpad: 0, extra: 0 })).toBe("Squier buttons: Face 1, D-pad up");
    expect(describeRb3Message({ kind: "buttons", face: 0, menu: 0, dpad: 8, extra: 0 })).toBe("Squier buttons: none");
  });
});
