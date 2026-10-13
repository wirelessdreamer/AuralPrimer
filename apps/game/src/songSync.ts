/**
 * Per-song chart sync, nudged by the player and remembered per song.
 *
 * Some packs' tabs sit hundreds of milliseconds off their own audio, and by a
 * different amount in each song, so the device's A/V calibration cannot fix
 * them. Measuring it from the audio is unreliable on steady, repeating parts
 * (a shift of a beat matches almost as well as the truth), so the player sets
 * it by ear: positive makes the notes come later. Kept in localStorage, keyed
 * by the song's path; every read and write tolerates storage being missing.
 */

export const SONG_SYNC_STEP_MS = 20;
export const SONG_SYNC_LIMIT_MS = 2000;
const KEY_PREFIX = "auralprimer.songSyncMs:";

export function clampSongSyncMs(ms: number): number {
  if (!Number.isFinite(ms)) return 0;
  return Math.max(-SONG_SYNC_LIMIT_MS, Math.min(SONG_SYNC_LIMIT_MS, Math.round(ms)));
}

export function loadSongSyncMs(songPath: string | null): number {
  if (!songPath) return 0;
  try {
    const raw = window.localStorage.getItem(KEY_PREFIX + songPath);
    return raw === null ? 0 : clampSongSyncMs(Number(raw));
  } catch {
    return 0;
  }
}

export function saveSongSyncMs(songPath: string | null, ms: number): void {
  if (!songPath) return;
  try {
    const value = clampSongSyncMs(ms);
    if (value === 0) window.localStorage.removeItem(KEY_PREFIX + songPath);
    else window.localStorage.setItem(KEY_PREFIX + songPath, String(value));
  } catch {
    // No storage: the nudge still applies for this session.
  }
}

/** "+40 ms", "−20 ms", "0 ms". */
export function formatSongSyncMs(ms: number): string {
  return ms > 0 ? `+${ms} ms` : ms < 0 ? `\u2212${-ms} ms` : "0 ms";
}
