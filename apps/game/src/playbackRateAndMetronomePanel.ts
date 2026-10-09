/**
 * Playback rate + metronome panel — owns the speed slider on the stage, the
 * rate input + apply button in the rail, and the metronome enable + volume
 * controls.
 *
 * Two unrelated features that share a single row in the controls UI;
 * bundled here only because they were the last two stray button-handler
 * blocks in main.ts. Extracted from main.ts as Phase 2.V.
 *
 * Every rate change, from the slider, the rail box or the - / = keys, goes
 * through one function and on to TransportController.setPlaybackRate, so the
 * transport stays the single source of truth for speed.
 */

import type { TransportController } from "./transportController";
import type { Metronome } from "./metronome";

/** The slider's range and step, as percent of the song's own tempo. */
export const SPEED_MIN_PERCENT = 25;
export const SPEED_MAX_PERCENT = 100;
export const SPEED_STEP_PERCENT = 5;

export type PlaybackRateAndMetronomePanelDeps = {
  /**
   * The transport in use right now, resolved per call: the host replaces the
   * controller when native audio falls back to HTML playback, and a captured
   * reference would keep setting the speed of the disposed one.
   */
  getTransportController: () => Pick<TransportController, "setPlaybackRate">;
  metronome: Metronome;
  setAudioStatus: (msg: string) => void;
  /** Called after every playback-rate change so the host cache stays in sync. */
  onPlaybackRateApplied: (rate: number) => void;
  /** The song's tempo where the playhead is now, or null when the song has no tempo map. */
  getSongBpm?: () => number | null;
};

export type PlaybackRateAndMetronomePanelHandle = {
  /** Read the most recently applied playback rate (host caches it). */
  getPlaybackRate: () => number;
  /** Redraw the speed readout; the song's tempo moves with the playhead. */
  refresh: () => void;
};

/** `75% · 90 BPM (song 120)`, or just the percent for a song with no tempo map. */
export function formatSpeedReadout(rate: number, songBpm: number | null): string {
  const percent = `${Math.round(rate * 100)}%`;
  if (songBpm === null || !Number.isFinite(songBpm) || songBpm <= 0) return percent;
  return `${percent} · ${Math.round(songBpm * rate)} BPM (song ${Math.round(songBpm)})`;
}

/** The slider position for a rate: snapped to its steps, inside its range. */
export function speedSliderPercent(rate: number): number {
  const snapped = Math.round((rate * 100) / SPEED_STEP_PERCENT) * SPEED_STEP_PERCENT;
  return Math.max(SPEED_MIN_PERCENT, Math.min(SPEED_MAX_PERCENT, snapped));
}

function focusIsTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

export function initPlaybackRateAndMetronomePanel(
  deps: PlaybackRateAndMetronomePanelDeps,
): PlaybackRateAndMetronomePanelHandle {
  const rateInput = document.getElementById("playbackRate") as HTMLInputElement | null;
  const rateApplyBtn = document.getElementById("playbackRateApply") as HTMLButtonElement | null;
  const speedSlider = document.getElementById("speedSlider") as HTMLInputElement | null;
  const speedValueEl = document.getElementById("speedValue") as HTMLElement | null;
  const metEnabledInput = document.getElementById("metronomeEnabled") as HTMLInputElement | null;
  const metVolumeInput = document.getElementById("metronomeVolume") as HTMLInputElement | null;
  if (!rateInput || !rateApplyBtn || !speedSlider || !speedValueEl || !metEnabledInput || !metVolumeInput) {
    throw new Error(
      "initPlaybackRateAndMetronomePanel: required DOM (#playbackRate / #playbackRateApply / #speedSlider / #speedValue / #metronomeEnabled / #metronomeVolume) missing",
    );
  }

  let currentRate = 1;

  function refresh(): void {
    const text = formatSpeedReadout(currentRate, deps.getSongBpm?.() ?? null);
    if (speedValueEl!.textContent !== text) speedValueEl!.textContent = text;
  }

  // The one path every rate change takes.
  function applyRate(r: number): void {
    currentRate = r;
    deps.getTransportController().setPlaybackRate(r);
    deps.onPlaybackRateApplied(r);
    // Keep both controls telling the truth whichever one moved. The box can go
    // past 100%, where the slider cannot; the slider then rests at its top
    // and the readout says what is really playing.
    rateInput!.value = String(r);
    speedSlider!.value = String(speedSliderPercent(r));
    refresh();
  }

  rateApplyBtn.addEventListener("click", () => {
    const r = Number(rateInput!.value);
    if (!Number.isFinite(r) || r <= 0) return;
    applyRate(r);
    deps.setAudioStatus(`playbackRate set: ${r.toFixed(2)}x`);
  });

  // Live while dragging, so the song slows under the player's hand.
  speedSlider.addEventListener("input", () => {
    const percent = Number(speedSlider!.value);
    if (!Number.isFinite(percent) || percent <= 0) return;
    applyRate(percent / 100);
  });
  // Hand the keyboard back after a click, like the seek bar: a focused range
  // eats the arrow keys and the transport hotkeys stand down for inputs.
  const release = () => speedSlider!.blur();
  speedSlider.addEventListener("pointerup", release);
  speedSlider.addEventListener("pointercancel", release);

  // - slows the song, = (or +) speeds it back up, a slider step at a time.
  // Ctrl/Meta with these is the webview's zoom, so modified presses are left alone.
  window.addEventListener("keydown", (ev) => {
    if (focusIsTyping(ev.target)) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const dir = ev.key === "-" ? -1 : ev.key === "=" || ev.key === "+" ? 1 : 0;
    if (dir === 0) return;
    ev.preventDefault();
    // Already at or past full speed (the rail box allows up to 2x): faster is
    // the box's job, and stepping "up" to 100% would be a slowdown.
    if (dir > 0 && currentRate * 100 >= SPEED_MAX_PERCENT) return;
    const next = Math.max(
      SPEED_MIN_PERCENT,
      Math.min(SPEED_MAX_PERCENT, speedSliderPercent(currentRate) + dir * SPEED_STEP_PERCENT),
    );
    if (next / 100 !== currentRate) applyRate(next / 100);
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

  refresh();

  return {
    getPlaybackRate: () => currentRate,
    refresh,
  };
}
