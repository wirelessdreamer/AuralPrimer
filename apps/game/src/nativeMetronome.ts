import type { TransportState } from "@auralprimer/viz-sdk";

export type NativeMetronomeSettings = {
  enabled: boolean;
  volume: number;
  bpm: number;
  beatsPerBar: number;
};

/**
 * The metronome in the native audio engine, which places each click on its
 * beat's song frame itself. This side only tells it what to play: on/off,
 * volume and the meter, sent when any of them changes.
 */
export class NativeMetronome {
  private enabled = false;
  private volume = 0.25;
  private lastSent = "";

  constructor(private readonly send: (s: NativeMetronomeSettings) => void) {}

  setEnabled(v: boolean): void {
    this.enabled = v;
  }

  getEnabled(): boolean {
    return this.enabled;
  }

  setVolume(v: number): void {
    this.volume = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.25;
  }

  /** Send again on the next update, as for an engine that was just recreated. */
  resend(): void {
    this.lastSent = "";
  }

  /** Called once per frame with the transport, whose meter may change under it. */
  update(state: Pick<TransportState, "bpm" | "timeSignature">): void {
    const [n, d] = state.timeSignature;
    // Clicks are quarter notes, so a bar is counted in quarters: 6/8 is 3.
    const quarters = Number.isFinite(n) && Number.isFinite(d) && n > 0 && d > 0 ? (n * 4) / d : 4;
    const settings: NativeMetronomeSettings = {
      enabled: this.enabled,
      volume: this.volume,
      bpm: Number.isFinite(state.bpm) && state.bpm > 0 ? state.bpm : 120,
      beatsPerBar: Math.max(1, Math.round(quarters)),
    };
    const key = JSON.stringify(settings);
    if (key === this.lastSent) return;
    this.lastSent = key;
    this.send(settings);
  }
}
