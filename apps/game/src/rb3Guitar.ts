/**
 * Fender Squier (Rock Band 3 pro guitar) over MIDI.
 *
 * The Squier is not a MIDI guitar in the Jamstik sense. Its frets are
 * switches, and it reports them in its own SysEx, the same family the RB3
 * Mustang uses (`F0 08 40 0A <type> ... F7`). Read from a recorded session
 * (tests/fixtures/rb3-squier-capture.jsonl):
 *
 *   01 <string> <note>    a fret changed on a string, picked or not. `note` is
 *                         the MIDI note of that fret in standard tuning.
 *   05 <string> <vel>     a string was picked. The last byte is velocity.
 *   08 <face> <menu> <dpad> 00
 *                         every button's state: two bitmasks and a d-pad in
 *                         the usual hat encoding (0 up, clockwise, 8 centred).
 *   09 <12 zero bytes>    keep-alive, every ~2.2 s.
 *
 * Strings count the guitarist's way, 1 = high e, matching the MIDI channels
 * the guitar sends its notes on. It also sends a note-on when a string is
 * picked, but nothing when a fret changes without a pick, and nothing at all
 * on a mute: the guitar cannot sense one. So this tracker reads the SysEx for
 * where the hand is, and lets a picked note go after RB3_PICK_RING_MS.
 *
 * What it produces has the shape the guitar views already read from a
 * Jamstik (string 1 on channel 0), plus `picked`: a fretted-but-unpicked
 * string shows where the hand is without counting as a played note.
 */

import type { MidiInputMessageEvent } from "./midiInput";

export type Rb3Message =
  | { kind: "fret"; string: number; note: number }
  | { kind: "pick"; string: number; velocity: number }
  | { kind: "buttons"; face: number; menu: number; dpad: number; extra: number }
  | { kind: "keepalive" };

/** A string's state, as the guitar views read live input. */
export type Rb3LiveNote = {
  pitch: number;
  velocity: number;
  /** 0 = high e, as a Jamstik in Multi-Channel mode sends it. */
  channel: number;
  isPressed: true;
  /** Picked and still (assumed) ringing; false = only fretted. */
  picked: boolean;
};

/** How long a picked note shows. The guitar never reports a mute, so a note just stops counting. */
export const RB3_PICK_RING_MS = 2000;

/** No SysEx for this long and the guitar is taken to be gone (its keep-alive is every ~2.2 s). */
const ACTIVE_TIMEOUT_MS = 6000;

const HEADER = [0xf0, 0x08, 0x40, 0x0a];
/** Standard tuning by string, 1 = high e. The guitar reports notes in it whatever its strings are tuned to. */
const OPEN = [0, 64, 59, 55, 50, 45, 40];

function hasHeader(b: ArrayLike<number>): boolean {
  return b.length > HEADER.length && HEADER.every((x, i) => b[i] === x) && b[b.length - 1] === 0xf7;
}

const isString = (n: number) => Number.isInteger(n) && n >= 1 && n <= 6;
const isData = (n: number) => Number.isInteger(n) && n >= 0 && n <= 127;

/** Decode one SysEx message from the guitar, or null for anything else. */
export function decodeRb3Sysex(bytes: ArrayLike<number>): Rb3Message | null {
  if (!hasHeader(bytes)) return null;
  const body = Array.from(bytes).slice(HEADER.length, bytes.length - 1);
  const [type, ...rest] = body;
  if (type === 0x01 && rest.length === 2 && isString(rest[0]) && isData(rest[1])) {
    return { kind: "fret", string: rest[0], note: rest[1] };
  }
  if (type === 0x05 && rest.length === 2 && isString(rest[0]) && isData(rest[1])) {
    return { kind: "pick", string: rest[0], velocity: rest[1] };
  }
  if (type === 0x08 && rest.length === 4) {
    return { kind: "buttons", face: rest[0], menu: rest[1], dpad: rest[2] & 0x0f, extra: rest[2] & 0x70 };
  }
  if (type === 0x09) return { kind: "keepalive" };
  return null;
}

// Button ids, stable because a learned transport binding stores them:
// 0-6 face bits, 8-14 menu bits, 16-19 d-pad up/right/down/left, 20-22 the
// d-pad byte's upper bits.
const DPAD_UP = 16;
const DPAD_RIGHT = 17;
const DPAD_DOWN = 18;
const DPAD_LEFT = 19;
const EXTRA_BASE = 20;
/** Hat value to directions; 8 (or anything else) is centred. */
const HAT: Record<number, number[]> = {
  0: [DPAD_UP],
  1: [DPAD_UP, DPAD_RIGHT],
  2: [DPAD_RIGHT],
  3: [DPAD_DOWN, DPAD_RIGHT],
  4: [DPAD_DOWN],
  5: [DPAD_DOWN, DPAD_LEFT],
  6: [DPAD_LEFT],
  7: [DPAD_UP, DPAD_LEFT],
};

/** Every button held in one buttons message, as ids. */
export function rb3ButtonsDown(m: Extract<Rb3Message, { kind: "buttons" }>): Set<number> {
  const down = new Set<number>();
  for (let bit = 0; bit < 7; bit++) {
    if (m.face & (1 << bit)) down.add(bit);
    if (m.menu & (1 << bit)) down.add(8 + bit);
  }
  for (const id of HAT[m.dpad] ?? []) down.add(id);
  for (let bit = 4; bit < 7; bit++) if (m.extra & (1 << bit)) down.add(EXTRA_BASE + bit - 4);
  return down;
}

/**
 * A button's name. The guitar's printed labels differ by console, so these
 * follow the bits: learn a transport button by pressing it, not by its name.
 */
export function rb3ButtonLabel(id: number): string {
  if (id >= 0 && id < 7) return `Face ${id + 1}`;
  if (id >= 8 && id < 15) return `Menu ${id - 7}`;
  if (id === DPAD_UP) return "D-pad up";
  if (id === DPAD_RIGHT) return "D-pad right";
  if (id === DPAD_DOWN) return "D-pad down";
  if (id === DPAD_LEFT) return "D-pad left";
  if (id >= EXTRA_BASE && id < EXTRA_BASE + 3) return `Extra ${id - EXTRA_BASE + 1}`;
  return `Button ${id}`;
}

export type Rb3ButtonEdge = { button: number; down: boolean };

/**
 * A button change as an input event, so the transport bindings (which read
 * input events) can learn and match the guitar's buttons like any other.
 */
export function rb3ButtonEvent(edge: Rb3ButtonEdge, timestampUs: number): MidiInputMessageEvent {
  return {
    timestamp_us: timestampUs,
    message_type: "rb3_button",
    status: 0xf0,
    channel: null,
    data1: edge.button,
    data2: edge.down ? 127 : 0,
    bytes: [],
  };
}

const STRING_NAME = ["", "e", "B", "G", "D", "A", "E"];

/** One of the guitar's messages in words, for the MIDI input monitor. */
export function describeRb3Message(m: Rb3Message): string {
  switch (m.kind) {
    case "fret": {
      const fret = m.note - OPEN[m.string];
      return `Squier: string ${m.string} (${STRING_NAME[m.string]}) ${fret === 0 ? "open" : `at fret ${fret}`}`;
    }
    case "pick":
      return `Squier: string ${m.string} (${STRING_NAME[m.string]}) picked, velocity ${m.velocity}`;
    case "buttons": {
      const down = [...rb3ButtonsDown(m)].sort((a, b) => a - b).map(rb3ButtonLabel);
      return `Squier buttons: ${down.length ? down.join(", ") : "none"}`;
    }
    case "keepalive":
      return "Squier: keep-alive";
  }
}

type StringState = { note: number; pickedAtMs: number | null; velocity: number };

/** Where the hand is and what was just picked, from the guitar's messages. */
export class Rb3GuitarTracker {
  private strings: StringState[] = OPEN.map((note) => ({ note, pickedAtMs: null, velocity: 0 }));
  private buttons = new Set<number>();
  private lastSeenMs = -Infinity;

  /** Update from one input message; returns any button presses and releases it carried. */
  apply(ev: MidiInputMessageEvent, nowMs: number): Rb3ButtonEdge[] {
    if (ev.message_type === "note_on" && typeof ev.channel === "number" && typeof ev.data1 === "number") {
      // A pick also arrives as a note-on with the fretted pitch. Take the
      // pitch from it, which keeps the fret right for a finger that was
      // already down before any fret message was seen.
      const s = ev.channel + 1;
      if (isString(s) && isData(ev.data1)) this.strings[s].note = ev.data1;
      return [];
    }
    if (ev.message_type !== "sysex") return [];
    const m = decodeRb3Sysex(ev.bytes);
    if (!m) return [];
    this.lastSeenMs = nowMs;
    if (m.kind === "fret") {
      // The guitar ends the sounding note when the fret under it changes.
      this.strings[m.string] = { note: m.note, pickedAtMs: null, velocity: 0 };
    } else if (m.kind === "pick") {
      const st = this.strings[m.string];
      st.pickedAtMs = nowMs;
      st.velocity = m.velocity;
    } else if (m.kind === "buttons") {
      const now = rb3ButtonsDown(m);
      const edges: Rb3ButtonEdge[] = [];
      for (const b of this.buttons) if (!now.has(b)) edges.push({ button: b, down: false });
      for (const b of now) if (!this.buttons.has(b)) edges.push({ button: b, down: true });
      this.buttons = now;
      return edges;
    }
    return [];
  }

  /** Has the guitar's SysEx arrived recently? */
  isActive(nowMs: number): boolean {
    return nowMs - this.lastSeenMs < ACTIVE_TIMEOUT_MS;
  }

  /**
   * Each string that is either still ringing from a pick or held at a fret,
   * high e first. An open string nobody picked is not shown: it is where
   * every string rests.
   */
  liveNotes(nowMs: number): Rb3LiveNote[] {
    const out: Rb3LiveNote[] = [];
    for (let s = 1; s <= 6; s++) {
      const st = this.strings[s];
      const ringing = st.pickedAtMs !== null && nowMs - st.pickedAtMs < RB3_PICK_RING_MS;
      if (ringing) {
        out.push({ pitch: st.note, velocity: st.velocity, channel: s - 1, isPressed: true, picked: true });
      } else if (st.note > OPEN[s]) {
        out.push({ pitch: st.note, velocity: 0, channel: s - 1, isPressed: true, picked: false });
      }
    }
    return out;
  }

  /** Forget everything, as on a reconnect. */
  clear(): void {
    this.strings = OPEN.map((note) => ({ note, pickedAtMs: null, velocity: 0 }));
    this.buttons.clear();
    this.lastSeenMs = -Infinity;
  }
}
