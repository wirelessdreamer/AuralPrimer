export type RefineWaveform = { duration_sec: number; peaks: number[] };

/** Peak-preserving resampling into the visible canvas, including windows
 * shorter than a bucket and empty portions beyond a shorter source stem. */
export function visibleWaveformPeaks(waveform: RefineWaveform, start: number, end: number, columns: number): number[] {
  if (columns <= 0 || end <= start || waveform.duration_sec <= 0 || !waveform.peaks.length) return [];
  const result = new Array<number>(columns).fill(0);
  const perSecond = waveform.peaks.length / waveform.duration_sec;
  for (let x = 0; x < columns; x++) {
    const from = (start + (end - start) * x / columns) * perSecond;
    const through = (start + (end - start) * (x + 1) / columns) * perSecond;
    if (through <= 0 || from >= waveform.peaks.length) continue;
    for (let i = Math.max(0, Math.floor(from)); i < Math.min(waveform.peaks.length, Math.max(Math.floor(from) + 1, Math.ceil(through))); i++) {
      result[x] = Math.max(result[x], waveform.peaks[i]);
    }
  }
  return result;
}
