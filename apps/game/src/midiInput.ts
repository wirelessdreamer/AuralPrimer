export type MidiInputMessageType =
  | "note_on"
  | "note_off"
  | "control_change"
  | "pitch_bend"
  | "poly_aftertouch"
  | "program_change"
  | "channel_pressure"
  | "clock"
  | "start"
  | "continue"
  | "stop"
  | "song_position_pointer"
  | "sysex"
  | "unknown"
  | string;

export type MidiInputMessageEvent = {
  timestamp_us: number;
  message_type: MidiInputMessageType;
  status: number;
  channel?: number | null;
  data1?: number | null;
  data2?: number | null;
  value14?: number | null;
  value_signed?: number | null;
  bytes: number[];
};

export type MidiActiveNote = {
  pitch: number;
  noteName: string;
  channel: number;
  velocity: number;
  velocityUnit: number;
  startedAtUs: number;
  lastEventUs: number;
  isPressed: boolean;
  heldBySustain: boolean;
};

export type MidiInputSnapshot = {
  activeNotes: MidiActiveNote[];
  pressedCount: number;
  heldBySustainCount: number;
  sustainChannels: number[];
};

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function isMidiDataByte(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 127;
}

function isMidiChannel(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 15;
}

function activeNoteKey(channel: number, pitch: number): string {
  return `${channel}:${pitch}`;
}

export function midiPitchName(pitch: number): string {
  const p = Math.trunc(pitch);
  if (!Number.isFinite(p)) return "?";
  const pitchClass = ((p % 12) + 12) % 12;
  const octave = Math.floor(p / 12) - 1;
  return `${NOTE_NAMES[pitchClass]}${octave}`;
}

export function midiVelocityUnit(velocity: number): number {
  return clamp(velocity / 127, 0, 1);
}

export class MidiInputStateTracker {
  private active = new Map<string, MidiActiveNote>();
  private sustainByChannel = new Map<number, boolean>();

  apply(ev: MidiInputMessageEvent): MidiInputSnapshot {
    const channel = ev.channel;
    const data1 = ev.data1;
    const data2 = ev.data2;

    if (ev.message_type === "note_on" && isMidiChannel(channel) && isMidiDataByte(data1) && isMidiDataByte(data2)) {
      if (data2 === 0) {
        this.noteOff(channel, data1, ev.timestamp_us);
      } else {
        this.active.set(activeNoteKey(channel, data1), {
          pitch: data1,
          noteName: midiPitchName(data1),
          channel,
          velocity: data2,
          velocityUnit: midiVelocityUnit(data2),
          startedAtUs: ev.timestamp_us,
          lastEventUs: ev.timestamp_us,
          isPressed: true,
          heldBySustain: false,
        });
      }
    } else if (ev.message_type === "note_off" && isMidiChannel(channel) && isMidiDataByte(data1)) {
      this.noteOff(channel, data1, ev.timestamp_us);
    } else if (ev.message_type === "control_change" && isMidiChannel(channel) && isMidiDataByte(data1) && isMidiDataByte(data2)) {
      this.applyControlChange(channel, data1, data2, ev.timestamp_us);
    }

    return this.snapshot();
  }

  clear(channel?: number): MidiInputSnapshot {
    if (isMidiChannel(channel)) {
      this.clearChannel(channel);
      this.sustainByChannel.delete(channel);
    } else {
      this.active.clear();
      this.sustainByChannel.clear();
    }
    return this.snapshot();
  }

  snapshot(): MidiInputSnapshot {
    const activeNotes = Array.from(this.active.values()).sort((a, b) => {
      if (a.channel !== b.channel) return a.channel - b.channel;
      return a.pitch - b.pitch;
    });

    return {
      activeNotes,
      pressedCount: activeNotes.filter((note) => note.isPressed).length,
      heldBySustainCount: activeNotes.filter((note) => note.heldBySustain).length,
      sustainChannels: Array.from(this.sustainByChannel.entries())
        .filter(([, enabled]) => enabled)
        .map(([channel]) => channel)
        .sort((a, b) => a - b),
    };
  }

  private noteOff(channel: number, pitch: number, timestampUs: number): void {
    const key = activeNoteKey(channel, pitch);
    const existing = this.active.get(key);
    if (!existing) return;

    if (this.sustainByChannel.get(channel)) {
      this.active.set(key, {
        ...existing,
        lastEventUs: timestampUs,
        isPressed: false,
        heldBySustain: true,
      });
      return;
    }

    this.active.delete(key);
  }

  private applyControlChange(channel: number, controller: number, value: number, timestampUs: number): void {
    if (controller === 64) {
      const sustainDown = value >= 64;
      this.sustainByChannel.set(channel, sustainDown);
      if (!sustainDown) {
        for (const [key, note] of this.active.entries()) {
          if (note.channel === channel && !note.isPressed && note.heldBySustain) {
            this.active.delete(key);
          }
        }
      }
      return;
    }

    if (controller === 120 || controller === 123) {
      this.clearChannel(channel);
      return;
    }

    if (controller === 121) {
      this.sustainByChannel.set(channel, false);
      for (const [key, note] of this.active.entries()) {
        if (note.channel === channel && note.heldBySustain) {
          if (note.isPressed) {
            this.active.set(key, { ...note, heldBySustain: false, lastEventUs: timestampUs });
          } else {
            this.active.delete(key);
          }
        }
      }
    }
  }

  private clearChannel(channel: number): void {
    for (const [key, note] of this.active.entries()) {
      if (note.channel === channel) {
        this.active.delete(key);
      }
    }
  }
}

export function formatMidiInputMessage(ev: MidiInputMessageEvent): string {
  const ch = typeof ev.channel === "number" ? ` ch${ev.channel + 1}` : "";
  const d1 = typeof ev.data1 === "number" ? ` d1=${ev.data1}` : "";
  const d2 = typeof ev.data2 === "number" ? ` d2=${ev.data2}` : "";
  const bend = typeof ev.value_signed === "number" ? ` bend=${ev.value_signed}` : "";
  const note =
    (ev.message_type === "note_on" || ev.message_type === "note_off") && typeof ev.data1 === "number"
      ? ` ${midiPitchName(ev.data1)}`
      : "";
  const sustain =
    ev.message_type === "control_change" && ev.data1 === 64 && typeof ev.data2 === "number"
      ? ` sustain=${ev.data2 >= 64 ? "down" : "up"}`
      : "";
  const hex = ev.bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  return `${ev.message_type}${ch}${note}${d1}${d2}${sustain}${bend} [${hex}]`;
}

export function formatMidiActiveNotes(snapshot: MidiInputSnapshot): string {
  const notes = snapshot.activeNotes;
  if (!notes.length) {
    const sustain = snapshot.sustainChannels.length
      ? `\nsustain down: ${snapshot.sustainChannels.map((channel) => `ch${channel + 1}`).join(", ")}`
      : "";
    return `(no active notes)${sustain}`;
  }

  const summary = `active notes: ${notes.length} pressed=${snapshot.pressedCount} sustain-held=${snapshot.heldBySustainCount}`;
  const sustain = snapshot.sustainChannels.length
    ? `sustain down: ${snapshot.sustainChannels.map((channel) => `ch${channel + 1}`).join(", ")}`
    : "sustain down: none";
  const lines = notes.map((note) => {
    const state = note.heldBySustain && !note.isPressed ? " held" : note.heldBySustain ? " pressed+sustain" : "";
    return `ch${note.channel + 1} ${note.noteName} (${note.pitch}) vel=${note.velocity}${state}`;
  });

  return [summary, sustain, ...lines].join("\n");
}
