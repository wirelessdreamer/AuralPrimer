/**
 * A/V sync offset store for AuralPrimer (game).
 *
 * Persistence is the shared settings.json (via the Rust get/set_av_calibration
 * commands), NOT localStorage — in the portable both AuralPrimer and
 * AuralStudio resolve the same file, so a calibration done in either app
 * applies to both. Two latencies are stored: audio (output delay) and video
 * (display lag); the falling-notes transport uses the effective offset =
 * audio - video (see @auralprimer/av-sync).
 *
 * Cached in memory; loaded once at startup and broadcast on change.
 */

import { invoke } from "@tauri-apps/api/core";
import { clampOffsetMs, effectiveAvOffsetMs, effectiveAvOffsetSec } from "@auralprimer/av-sync";

export const AV_OFFSET_CHANGED_EVENT = "auralprimer:avoffset";

let audioMs = 0;
let videoMs = 0;

export function getAudioOffsetMs(): number {
  return audioMs;
}

export function getVideoOffsetMs(): number {
  return videoMs;
}

export function getEffectiveOffsetMs(): number {
  return effectiveAvOffsetMs(audioMs, videoMs);
}

/** Effective visual offset in seconds — what the transport applies to notes. */
export function getEffectiveOffsetSec(): number {
  return effectiveAvOffsetSec(audioMs, videoMs);
}

function broadcast(): void {
  try {
    window.dispatchEvent(new CustomEvent(AV_OFFSET_CHANGED_EVENT, { detail: { audioMs, videoMs } }));
  } catch {
    // CustomEvent unsupported — callers that need the readout already have the value.
  }
}

/** Load persisted offsets from the shared settings.json. Call once at startup. */
export async function loadAvCalibration(): Promise<void> {
  try {
    const r = await invoke<{ audio_offset_ms: number; video_offset_ms: number }>("get_av_calibration");
    audioMs = clampOffsetMs(r.audio_offset_ms);
    videoMs = clampOffsetMs(r.video_offset_ms);
  } catch {
    audioMs = 0;
    videoMs = 0;
  }
  broadcast();
}

/** Persist new offsets to the shared settings.json (both apps read it). */
export async function setAvCalibration(nextAudioMs: number, nextVideoMs: number): Promise<void> {
  audioMs = clampOffsetMs(nextAudioMs);
  videoMs = clampOffsetMs(nextVideoMs);
  broadcast();
  try {
    await invoke("set_av_calibration", { audioOffsetMs: audioMs, videoOffsetMs: videoMs });
  } catch {
    // Persist is best-effort; the in-memory value still drives this session.
  }
}
