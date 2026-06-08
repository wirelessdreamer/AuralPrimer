/**
 * Playback rate + metronome panel — owns the rate input + apply button
 * and the metronome enable + volume controls.
 *
 * Two unrelated features that share a single row in the controls UI;
 * bundled here only because they were the last two stray button-handler
 * blocks in main.ts. Extracted from main.ts as Phase 2.V.
 */

import type { TransportController } from "./transportController";
import type { Metronome } from "./metronome";

export type PlaybackRateAndMetronomePanelDeps = {
  transportController: TransportController;
  metronome: Metronome;
  setAudioStatus: (msg: string) => void;
  /** Called after every playback-rate change so the host cache stays in sync. */
  onPlaybackRateApplied: (rate: number) => void;
};

export type PlaybackRateAndMetronomePanelHandle = {
  /** Read the most recently applied playback rate (host caches it). */
  getPlaybackRate: () => number;
};

export function initPlaybackRateAndMetronomePanel(
  deps: PlaybackRateAndMetronomePanelDeps,
): PlaybackRateAndMetronomePanelHandle {
  const rateInput = document.getElementById("playbackRate") as HTMLInputElement | null;
  const rateApplyBtn = document.getElementById("playbackRateApply") as HTMLButtonElement | null;
  const metEnabledInput = document.getElementById("metronomeEnabled") as HTMLInputElement | null;
  const metVolumeInput = document.getElementById("metronomeVolume") as HTMLInputElement | null;
  if (!rateInput || !rateApplyBtn || !metEnabledInput || !metVolumeInput) {
    throw new Error(
      "initPlaybackRateAndMetronomePanel: required DOM (#playbackRate / #playbackRateApply / #metronomeEnabled / #metronomeVolume) missing",
    );
  }

  let currentRate = 1;

  rateApplyBtn.addEventListener("click", () => {
    const r = Number(rateInput!.value);
    if (!Number.isFinite(r) || r <= 0) return;
    currentRate = r;
    deps.transportController.setPlaybackRate(r);
    deps.onPlaybackRateApplied(r);
    deps.setAudioStatus(`playbackRate set: ${r.toFixed(2)}x`);
  });

  metEnabledInput.addEventListener("change", () => {
    deps.metronome.setEnabled(metEnabledInput!.checked);
    deps.setAudioStatus(`metronome: ${deps.metronome.getEnabled() ? "on" : "off"}`);
  });

  metVolumeInput.addEventListener("input", () => {
    const v = Number(metVolumeInput!.value);
    if (!Number.isFinite(v)) return;
    deps.metronome.setVolume(v);
  });

  return {
    getPlaybackRate: () => currentRate,
  };
}
