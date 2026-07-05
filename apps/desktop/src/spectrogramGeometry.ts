/**
 * Pure coordinate math for the spectrogram editor — extracted so the
 * time->frame->pixel mapping and the drum-lane marker placement can be
 * unit-tested without a WebGL/canvas context. The editor (spectrogramEditor.ts)
 * is the only caller; keeping these here means a regression in e.g. drum-marker
 * centering fails a fast unit test instead of only being visible by eye.
 */

/** Song time (seconds) -> CQT frame (content X). Linear: a hit at time T maps
 * to frame T*fps, which is exactly the column its energy occupies. */
export function secToFrame(timeSec: number, framesPerSec: number): number {
  return timeSec * framesPerSec;
}

/** Content-X (frame) -> normalized [0..1] across the visible window. */
export function frameToNorm(frame: number, originX: number, spanX: number): number {
  return (frame - originX) / spanX;
}

/** Content rows per lane — lane mode divides the n_bins evenly among lanes. */
export function laneBandRows(nBins: number, laneCount: number): number {
  return laneCount > 0 ? nBins / laneCount : 1;
}

/** The content-row band a lane occupies (bottom lane = index 0). */
export function laneRowBand(
  laneIndex: number,
  nBins: number,
  laneCount: number,
): { rLo: number; rHi: number } {
  const rpl = laneBandRows(nBins, laneCount);
  return { rLo: laneIndex * rpl, rHi: (laneIndex + 1) * rpl };
}

/**
 * A fixed-width lane marker CENTERED on the onset pixel. A drum hit is an
 * instant, so the marker must be symmetric about timeToPx(t_on) and its width
 * must NOT depend on t_off — otherwise the marker drifts off / balloons.
 */
export function laneMarkerXSpan(
  centerPx: number,
  markCssPx: number,
  dpr: number,
): { xLeft: number; xRight: number } {
  const half = (markCssPx * dpr) / 2;
  return { xLeft: centerPx - half, xRight: centerPx + half };
}

/** A pitch-mode note's px span t_on..t_off (min 1px, order-independent). */
export function noteBodyXSpan(x0: number, x1: number): { xLeft: number; xRight: number } {
  const left = Math.min(x0, x1);
  return { xLeft: left, xRight: Math.max(left + 1, Math.max(x0, x1)) };
}
