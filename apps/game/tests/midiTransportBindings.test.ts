// @vitest-environment jsdom
/**
 * Unit tests for the transport binding model — matching, Learn capture, and
 * persistence. These are the parts that decide whether a controller's buttons
 * are recognised at all, so they carry the edge cases: note-on-with-velocity-0
 * as a release, channel scoping, and tolerating junk in localStorage.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  CC_PRESS_THRESHOLD,
  STORAGE_KEY,
  bindingFromMessage,
  defaultBindings,
  describeBinding,
  describeMessage,
  loadBindings,
  bindingsConflict,
  matchBinding,
  parseBindings,
  saveBindings,
  type MidiBinding,
} from "../src/midiTransportBindings";
import type { MidiInputMessageEvent } from "../src/midiInput";

function msg(over: Partial<MidiInputMessageEvent>): MidiInputMessageEvent {
  return {
    timestamp_us: 0,
    message_type: "control_change",
    status: 0xb0,
    channel: 0,
    data1: 31,
    data2: 127,
    bytes: [],
    ...over,
  } as MidiInputMessageEvent;
}

const ccBinding: MidiBinding = { kind: "cc", number: 31, channel: null };
const noteBinding: MidiBinding = { kind: "note", number: 60, channel: 0 };

describe("matchBinding", () => {
  it("reads a CC at or above the threshold as a press", () => {
    expect(matchBinding(ccBinding, msg({ data2: CC_PRESS_THRESHOLD }))).toBe("press");
    expect(matchBinding(ccBinding, msg({ data2: 127 }))).toBe("press");
  });

  it("reads a CC below the threshold as a release", () => {
    expect(matchBinding(ccBinding, msg({ data2: CC_PRESS_THRESHOLD - 1 }))).toBe("release");
    expect(matchBinding(ccBinding, msg({ data2: 0 }))).toBe("release");
  });

  it("ignores a different CC number", () => {
    expect(matchBinding(ccBinding, msg({ data1: 32 }))).toBeNull();
  });

  it("ignores a note that happens to share the CC number", () => {
    expect(matchBinding(ccBinding, msg({ message_type: "note_on", data1: 31 }))).toBeNull();
  });

  it("reads note-on as press and note-off as release", () => {
    expect(matchBinding(noteBinding, msg({ message_type: "note_on", data1: 60, data2: 100 }))).toBe("press");
    expect(matchBinding(noteBinding, msg({ message_type: "note_off", data1: 60, data2: 0 }))).toBe("release");
  });

  it("treats note-on with velocity 0 as a release, as many keyboards send", () => {
    expect(matchBinding(noteBinding, msg({ message_type: "note_on", data1: 60, data2: 0 }))).toBe("release");
  });

  it("scopes a channel-pinned binding to that channel", () => {
    expect(matchBinding(noteBinding, msg({ message_type: "note_on", data1: 60, data2: 90, channel: 1 }))).toBeNull();
    expect(matchBinding(noteBinding, msg({ message_type: "note_on", data1: 60, data2: 90, channel: 0 }))).toBe("press");
  });

  it("matches any channel when the binding is not pinned", () => {
    expect(matchBinding(ccBinding, msg({ channel: 9 }))).toBe("press");
  });

  it("ignores an unassigned binding", () => {
    expect(matchBinding(null, msg({}))).toBeNull();
  });

  it("ignores malformed data bytes", () => {
    expect(matchBinding(ccBinding, msg({ data1: null }))).toBeNull();
    expect(matchBinding(ccBinding, msg({ data2: null }))).toBeNull();
  });
});

describe("bindingFromMessage (Learn capture)", () => {
  it("captures a CC press", () => {
    expect(bindingFromMessage(msg({ data1: 44, data2: 127, channel: 2 }))).toEqual({
      kind: "cc",
      number: 44,
      channel: 2,
    });
  });

  it("refuses a CC release, so it can't overwrite what the press just set", () => {
    expect(bindingFromMessage(msg({ data1: 44, data2: 0 }))).toBeNull();
  });

  it("captures a note press and pins its channel", () => {
    expect(bindingFromMessage(msg({ message_type: "note_on", data1: 36, data2: 88, channel: 9 }))).toEqual({
      kind: "note",
      number: 36,
      channel: 9,
    });
  });

  it("refuses a note release", () => {
    expect(bindingFromMessage(msg({ message_type: "note_on", data1: 36, data2: 0 }))).toBeNull();
    expect(bindingFromMessage(msg({ message_type: "note_off", data1: 36, data2: 0 }))).toBeNull();
  });

  it("ignores message types that aren't buttons", () => {
    expect(bindingFromMessage(msg({ message_type: "pitch_bend" }))).toBeNull();
    expect(bindingFromMessage(msg({ message_type: "clock" }))).toBeNull();
  });
});

describe("describe helpers", () => {
  it("names bindings, including the unassigned case", () => {
    expect(describeBinding(ccBinding)).toBe("CC 31 (any ch)");
    expect(describeBinding(noteBinding)).toBe("Note 60 (ch 1)");
    expect(describeBinding(null)).toBe("unassigned");
  });

  it("summarises incoming messages for the monitor", () => {
    expect(describeMessage(msg({ data1: 31, data2: 127 }))).toBe("CC 31 = 127 ch1");
    expect(describeMessage(msg({ message_type: "note_on", data1: 60, data2: 90 }))).toBe("Note 60 on, vel 90 ch1");
    expect(describeMessage(msg({ message_type: "note_off", data1: 60 }))).toBe("Note 60 off ch1");
  });
});

describe("bindingsConflict", () => {
  it("flags the same button on the same channel", () => {
    expect(bindingsConflict({ kind: "cc", number: 31, channel: 0 }, { kind: "cc", number: 31, channel: 0 })).toBe(true);
  });

  it("flags an any-channel binding against a pinned one on the same number", () => {
    // The case that matters: a learned button (pinned) must displace an
    // any-channel default, or one press drives two actions.
    expect(bindingsConflict({ kind: "cc", number: 31, channel: null }, { kind: "cc", number: 31, channel: 3 })).toBe(true);
    expect(bindingsConflict({ kind: "cc", number: 31, channel: 3 }, { kind: "cc", number: 31, channel: null })).toBe(true);
  });

  it("does not flag different channels, numbers, or kinds", () => {
    expect(bindingsConflict({ kind: "cc", number: 31, channel: 1 }, { kind: "cc", number: 31, channel: 2 })).toBe(false);
    expect(bindingsConflict({ kind: "cc", number: 31, channel: null }, { kind: "cc", number: 32, channel: null })).toBe(false);
    expect(bindingsConflict({ kind: "cc", number: 31, channel: null }, { kind: "note", number: 31, channel: null })).toBe(false);
  });

  it("never flags an unassigned binding", () => {
    expect(bindingsConflict(null, { kind: "cc", number: 31, channel: null })).toBe(false);
    expect(bindingsConflict({ kind: "cc", number: 31, channel: null }, null)).toBe(false);
  });
});

describe("persistence", () => {
  beforeEach(() => window.localStorage.clear());

  it("falls back to defaults with nothing stored", () => {
    expect(parseBindings(null)).toEqual(defaultBindings());
  });

  it("falls back to defaults on unparseable JSON", () => {
    expect(parseBindings("{not json")).toEqual(defaultBindings());
  });

  it("round-trips through localStorage", () => {
    const next = { ...defaultBindings(), play: { kind: "note", number: 36, channel: 9 } as MidiBinding };
    saveBindings(next);
    expect(loadBindings()).toEqual(next);
  });

  it("keeps an explicit null as a deliberate unassignment", () => {
    expect(parseBindings(JSON.stringify({ play: null })).play).toBeNull();
  });

  it("ignores an invalid entry without discarding the valid ones", () => {
    const parsed = parseBindings(
      JSON.stringify({
        play: { kind: "cc", number: 999, channel: null }, // out of range
        stop: { kind: "note", number: 40, channel: 3 },
      }),
    );
    expect(parsed.play).toEqual(defaultBindings().play); // fell back
    expect(parsed.stop).toEqual({ kind: "note", number: 40, channel: 3 });
  });

  it("survives a corrupt stored value", () => {
    window.localStorage.setItem(STORAGE_KEY, "[]");
    expect(loadBindings()).toEqual(defaultBindings());
  });
});
