import { describe, expect, it } from "vitest";
import {
  MidiInputStateTracker,
  formatMidiActiveNotes,
  formatMidiInputMessage,
  midiPitchName,
  type MidiInputMessageEvent,
} from "../src/midiInput";

function ev(partial: Partial<MidiInputMessageEvent>): MidiInputMessageEvent {
  return {
    timestamp_us: 1,
    message_type: "unknown",
    status: 0x90,
    bytes: [],
    ...partial,
  };
}

describe("MidiInputStateTracker", () => {
  it("tracks note on and note off by channel and pitch", () => {
    const tracker = new MidiInputStateTracker();

    let snapshot = tracker.apply(ev({ message_type: "note_on", channel: 0, data1: 60, data2: 96, bytes: [0x90, 60, 96] }));
    expect(snapshot.activeNotes).toHaveLength(1);
    expect(snapshot.activeNotes[0]).toMatchObject({
      channel: 0,
      pitch: 60,
      noteName: "C4",
      velocity: 96,
      isPressed: true,
      heldBySustain: false,
    });

    snapshot = tracker.apply(ev({ message_type: "note_off", channel: 0, data1: 60, data2: 64, bytes: [0x80, 60, 64] }));
    expect(snapshot.activeNotes).toHaveLength(0);
  });

  it("treats zero-velocity note_on as note off for browser/synthetic events", () => {
    const tracker = new MidiInputStateTracker();
    tracker.apply(ev({ message_type: "note_on", channel: 1, data1: 64, data2: 100, bytes: [0x91, 64, 100] }));

    const snapshot = tracker.apply(ev({ message_type: "note_on", channel: 1, data1: 64, data2: 0, bytes: [0x91, 64, 0] }));
    expect(snapshot.activeNotes).toHaveLength(0);
  });

  it("keeps released notes visible while sustain pedal is down", () => {
    const tracker = new MidiInputStateTracker();
    tracker.apply(ev({ message_type: "control_change", channel: 0, data1: 64, data2: 127, bytes: [0xb0, 64, 127] }));
    tracker.apply(ev({ message_type: "note_on", channel: 0, data1: 67, data2: 80, bytes: [0x90, 67, 80] }));

    let snapshot = tracker.apply(ev({ message_type: "note_off", channel: 0, data1: 67, data2: 0, bytes: [0x80, 67, 0] }));
    expect(snapshot.activeNotes).toHaveLength(1);
    expect(snapshot.activeNotes[0]).toMatchObject({ isPressed: false, heldBySustain: true });
    expect(snapshot.heldBySustainCount).toBe(1);

    snapshot = tracker.apply(ev({ message_type: "control_change", channel: 0, data1: 64, data2: 0, bytes: [0xb0, 64, 0] }));
    expect(snapshot.activeNotes).toHaveLength(0);
  });

  it("clears notes on MIDI panic/all-notes-off controller messages", () => {
    const tracker = new MidiInputStateTracker();
    tracker.apply(ev({ message_type: "note_on", channel: 2, data1: 48, data2: 90, bytes: [0x92, 48, 90] }));
    tracker.apply(ev({ message_type: "note_on", channel: 3, data1: 52, data2: 90, bytes: [0x93, 52, 90] }));

    const snapshot = tracker.apply(ev({ message_type: "control_change", channel: 2, data1: 123, data2: 0, bytes: [0xb2, 123, 0] }));
    expect(snapshot.activeNotes.map((note) => note.pitch)).toEqual([52]);
  });
});

describe("MIDI input formatting", () => {
  it("formats note names and raw bytes for monitor output", () => {
    expect(midiPitchName(61)).toBe("C#4");
    expect(formatMidiInputMessage(ev({ message_type: "note_on", channel: 0, data1: 61, data2: 127, bytes: [0x90, 61, 127] }))).toContain(
      "note_on ch1 C#4"
    );
  });

  it("summarizes active note snapshots for the UI", () => {
    const tracker = new MidiInputStateTracker();
    const snapshot = tracker.apply(ev({ message_type: "note_on", channel: 0, data1: 60, data2: 100, bytes: [0x90, 60, 100] }));

    expect(formatMidiActiveNotes(snapshot)).toContain("active notes: 1");
    expect(formatMidiActiveNotes(snapshot)).toContain("ch1 C4 (60)");
  });
});
