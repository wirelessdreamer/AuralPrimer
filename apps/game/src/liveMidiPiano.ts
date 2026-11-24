import { MidiInputStateTracker, type MidiInputMessageEvent } from "@auralprimer/midi-input";

/** Live MIDI monitoring owns no hardware port. It consumes the shared input
 * bus, honors sustain, and sounds only in the foreground app. */
export function initLiveMidiPiano(options: {
  isReady: () => boolean;
  prepare: () => Promise<boolean>;
  noteOn: (pitch: number, velocity: number) => void;
  noteOff: (pitch: number) => void;
}): { dispose: () => void } {
  const tracker = new MidiInputStateTracker();
  const sounding = new Map<number, number>();
  let preparing = false;
  let disposed = false;

  function silence(): void {
    tracker.clear();
    for (const pitch of sounding.keys()) options.noteOff(pitch);
    sounding.clear();
  }

  function sync(): void {
    if (disposed || !document.hasFocus()) { silence(); return; }
    const desired = new Map<number, { velocity: number; startedAtUs: number }>();
    for (const note of tracker.snapshot().activeNotes) {
      if (note.channel === 9) continue;
      const previous = desired.get(note.pitch);
      if (!previous || note.startedAtUs > previous.startedAtUs) desired.set(note.pitch, note);
    }
    for (const pitch of sounding.keys()) {
      if (!desired.has(pitch)) { options.noteOff(pitch); sounding.delete(pitch); }
    }
    if (!desired.size) return;
    if (!options.isReady()) {
      if (!preparing) {
        preparing = true;
        void options.prepare().then((ready) => { if (ready) sync(); }, () => {}).finally(() => { preparing = false; });
      }
      return;
    }
    for (const [pitch, note] of desired) {
      const startedAt = sounding.get(pitch);
      if (startedAt === undefined || note.startedAtUs > startedAt) {
        options.noteOn(pitch, note.velocity);
        sounding.set(pitch, note.startedAtUs);
      }
    }
  }

  function onInput(event: Event): void {
    if (!document.hasFocus()) return;
    const message = (event as CustomEvent<MidiInputMessageEvent>).detail;
    if (!message || message.channel === 9) return;
    if (!["note_on", "note_off", "control_change"].includes(message.message_type)) return;
    tracker.apply(message);
    sync();
  }
  window.addEventListener("auralprimer:midi-input", onInput);
  window.addEventListener("auralprimer:midi-reset", silence);
  window.addEventListener("blur", silence);
  return { dispose: () => {
    disposed = true;
    silence();
    window.removeEventListener("auralprimer:midi-input", onInput);
    window.removeEventListener("auralprimer:midi-reset", silence);
    window.removeEventListener("blur", silence);
  } };
}
