import { describe, expect, it } from "vitest";
import { NativeMetronome, type NativeMetronomeSettings } from "../src/nativeMetronome";

describe("NativeMetronome", () => {
  const meter = { bpm: 96, timeSignature: [4, 4] as [number, number] };

  it("sends settings only when they change", () => {
    const sent: NativeMetronomeSettings[] = [];
    const m = new NativeMetronome((s) => sent.push(s));
    m.update(meter);
    m.update(meter);
    expect(sent).toEqual([{ enabled: false, volume: 0.25, bpm: 96, beatsPerBar: 4 }]);
    m.setEnabled(true);
    m.setVolume(0.6);
    m.update(meter);
    expect(sent.at(-1)).toEqual({ enabled: true, volume: 0.6, bpm: 96, beatsPerBar: 4 });
    expect(sent).toHaveLength(2);
  });

  it("counts a bar in quarter notes", () => {
    const sent: NativeMetronomeSettings[] = [];
    const m = new NativeMetronome((s) => sent.push(s));
    m.update({ bpm: 120, timeSignature: [6, 8] });
    expect(sent[0].beatsPerBar).toBe(3);
  });

  it("sends again after resend, for a new engine", () => {
    const sent: NativeMetronomeSettings[] = [];
    const m = new NativeMetronome((s) => sent.push(s));
    m.update(meter);
    m.resend();
    m.update(meter);
    expect(sent).toHaveLength(2);
  });
});
