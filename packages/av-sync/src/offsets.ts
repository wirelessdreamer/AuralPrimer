/**
 * A/V sync offset math — shared by AuralPrimer (game) and AuralStudio.
 *
 * Two independently-measured latencies:
 *   - audio offset: how long after the clock you actually HEAR the output
 *     (the whole audio stack, including Bluetooth — 150-250ms).
 *   - video offset: how long after the clock the frame is actually SEEN
 *     (render + display lag — a TV not in game mode can add 50-100ms).
 *
 * The visual (falling notes / playhead) should be pushed LATER by the audio
 * delay so it lines up with delayed sound, and EARLIER by the video delay so
 * what you see matches the moment it was meant to render. Net shift applied to
 * the visual = audioMs - videoMs.
 */

export const AV_OFFSET_MAX_MS = 500;

/** Round to whole ms and clamp to the supported range; non-finite -> 0. */
export function clampOffsetMs(ms: number): number {
  if (!Number.isFinite(ms)) return 0;
  return Math.max(-AV_OFFSET_MAX_MS, Math.min(AV_OFFSET_MAX_MS, Math.round(ms)));
}

/** Effective offset (ms) to apply to the visual relative to audio. */
export function effectiveAvOffsetMs(audioMs: number, videoMs: number): number {
  return clampOffsetMs(clampOffsetMs(audioMs) - clampOffsetMs(videoMs));
}

/** Effective offset in seconds — convenience for playhead / transport clocks. */
export function effectiveAvOffsetSec(audioMs: number, videoMs: number): number {
  return effectiveAvOffsetMs(audioMs, videoMs) / 1000;
}
