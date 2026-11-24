import { describe, expect, it } from "vitest";
import { visibleWaveformPeaks } from "../src/refineWaveform";

describe("source waveform viewport", () => {
  it("keeps narrow attacks when zooming out and aligns the visible time window", () => {
    const waveform = { duration_sec: 4, peaks: [0, 0.8, 0.2, 1] };
    expect(visibleWaveformPeaks(waveform, 0, 4, 2)).toEqual([0.8, 1]);
    expect(visibleWaveformPeaks(waveform, 2, 4, 2)).toEqual([0.2, 1]);
    expect(visibleWaveformPeaks(waveform, 0, 1, 4)).toEqual([0, 0, 0, 0]);
  });
  it("leaves silence beyond a shorter stem and handles an unavailable waveform", () => {
    expect(visibleWaveformPeaks({ duration_sec: 2, peaks: [0.5, 1] }, 0, 4, 4)).toEqual([0.5, 1, 0, 0]);
    expect(visibleWaveformPeaks({ duration_sec: 0, peaks: [] }, 0, 10, 100)).toEqual([]);
  });
});
