import type { TransportState } from "@auralprimer/viz-sdk";

/**
 * Minimal audio + timebase contract the host transport needs.
 *
 * This is intentionally small so we can:
 * - unit test transport logic in jsdom/node without WebAudio
 * - swap implementations later (HTMLMediaElement, WebAudio, sidecar, etc.)
 */
export interface TransportTimebase {
  /** Load a new track and reset playback position to 0. */
  load(source: { blob: Blob; mime: string }): Promise<void>;

  /**
   * Optional: load a track directly from a SongPack path.
   *
   * Desktop (Tauri) can implement this to avoid transferring large audio blobs
   * over the JS<->Rust IPC boundary.
   */
  loadFromSongPack?(containerPath: string): Promise<{ mime: string; durationSec: number } | void>;

  play(): Promise<void>;
  pause(): void;
  stop(): void;

  seek(tSec: number): void;

  /** Optional loop region. Implementations should clamp/loop as precisely as possible. */
  setLoop(loop?: { t0: number; t1: number }): void;

  /** Playback speed multiplier. 1.0 = normal. */
  setPlaybackRate(rate: number): void;
  getPlaybackRate(): number;

  /** Seconds. Null means unknown/not-yet-loaded. */
  getDurationSec(): number | null;

  /** Seconds; should be monotonic while playing. */
  getCurrentTimeSec(): number;

  getIsPlaying(): boolean;

  /** Best-effort latency estimate (seconds) for sync/compensation. */
  getOutputLatencySec?(): number | undefined;

  dispose(): void;
}

export function clampLoop(loop: { t0: number; t1: number }): { t0: number; t1: number } {
  const t0 = Math.max(0, Math.min(loop.t0, loop.t1));
  const t1 = Math.max(0, Math.max(loop.t0, loop.t1));
  return { t0, t1 };
}

export function clampToLoop(t: number, loop?: TransportState["loop"]): number {
  if (!loop) return t;
  if (t < loop.t0) return loop.t0;
  if (t > loop.t1) return loop.t1;
  return t;
}
