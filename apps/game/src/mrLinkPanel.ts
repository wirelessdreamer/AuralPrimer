/**
 * MR headset link — Configure panel + per-frame publishing.
 *
 * The host owns MIDI, song position and audio; the headset renders. This module
 * is the desktop end of that: it starts the link, and feeds it what the headset
 * needs (see `docs/mr-link-protocol.md`).
 *
 * Defaults to ON. The whole reason the MR client exists is that the headset
 * connects to this app, so making the user hunt through Configure to enable it
 * would just be a discoverability trap — the same one the note-spacing and
 * wait-mode controls fell into.
 */

import { invoke } from "@tauri-apps/api/core";

import type { MelodicTrackSelection } from "./chartLoader";

const STORAGE_KEY = "auralprimer.mrLinkEnabled";
/** Publishing every frame would be wasteful; the headset disciplines its own
 * clock from these, and 30 Hz is far more than that needs. */
const PUBLISH_INTERVAL_MS = 33;
/** How often to ask whether the headset picked a song. Polled rather than
 * pushed because the link runs on its own thread in Rust with no channel
 * back into the webview; twice a second is imperceptible for a menu press
 * and costs a directory scan nobody notices. */
const SELECTION_POLL_MS = 500;

export type MrLinkStatus = { running: boolean; host?: string };

/** What the headset says its keyboard can physically play (protocol §7). */
export type MrKeyboardLayout = {
  lowestPitch: number;
  highestPitch: number;
  dropOutOfRange: boolean;
};

export type MrLinkPanelHandle = {
  /** Feed the link the current transport + held notes. Cheap, rate-limited,
   * and a no-op when the link is off. */
  publish: (songTimeSec: number, playing: boolean, heldNotes: { pitch: number; velocity?: number }[]) => void;
  /** Push the chart for a newly loaded song, or clear it. */
  setChart: (chart: unknown | null) => void;
  setAudioOffsetSec: (offsetSec: number) => void;
  isEnabled: () => boolean;
};

function readEnabled(): boolean {
  try {
    // Absent means first run: default on.
    return window.localStorage.getItem(STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

/**
 * Build the CHART payload (protocol §4) from a loaded melodic track.
 *
 * Notes go out sorted by onset so the client can advance a cursor instead of
 * scanning, and velocity is normalised 0..1 to match the desktop's own
 * MelodicNote — the headset should never have to guess which convention it got.
 */
export function buildChart(
  songId: string,
  title: string,
  track: MelodicTrackSelection | null,
  bpm: number,
  beatsPerBar: number,
): unknown | null {
  if (!track || track.notes.length === 0) return null;

  const notes = [...track.notes]
    .sort((a, b) => a.t_on - b.t_on)
    .map((n) => ({
      on: n.t_on,
      off: n.t_off,
      pitch: n.pitch,
      vel: n.velocity,
    }));

  return {
    songId,
    title,
    durationSec: notes.length ? notes[notes.length - 1].off : 0,
    tempoMap: [{ tSec: 0, bpm, beatsPerBar }],
    role: track.role,
    notes,
  };
}

/**
 *  onSongRequested Called with a container path when the headset picks
 *   a song from its Songs menu. Already validated host-side against the real
 *   library, so it is safe to load directly.
 */
export function initMrLinkPanel(
  onSongRequested?: (containerPath: string) => void,
  onKeyboardLayout?: (layout: MrKeyboardLayout | null) => void,
): MrLinkPanelHandle {
  const toggle = document.getElementById("mrLinkEnabled") as HTMLInputElement | null;
  const statusEl = document.getElementById("mrLinkStatus");

  let enabled = readEnabled();
  let lastPublish = 0;
  let lastChartJson: string | null = null;
  let selectionTimer: ReturnType<typeof setInterval> | null = null;

  function setStatus(text: string): void {
    if (statusEl && statusEl.textContent !== text) statusEl.textContent = text;
  }

  async function refreshStatus(): Promise<void> {
    try {
      const status = await invoke<MrLinkStatus>("mr_link_status");
      setStatus(
        status.running
          ? `serving as "${status.host}" — open AuralPrimer on the headset and it will find this PC`
          : "off",
      );
    } catch (e) {
      setStatus(`unavailable: ${String(e)}`);
    }
  }

  function pollSelection(): void {
    if (selectionTimer !== null) return;
    selectionTimer = setInterval(() => {
      void invoke<string | null>("mr_link_take_selection")
        .then((containerPath) => {
          if (containerPath) onSongRequested?.(containerPath);
        })
        .catch(() => {
          // The link may be stopping; the next tick either works or the
          // timer is cleared. Not worth a log line twice a second.
        });

      // Standing state rather than a one-shot, so this reads it every tick
      // and lets the handler ignore an unchanged value. A recalibration in
      // the headset has to reach us without anything being re-selected.
      void invoke<MrKeyboardLayout | null>("mr_link_keyboard_layout")
        .then((layout) => onKeyboardLayout?.(layout ?? null))
        .catch(() => {});
    }, SELECTION_POLL_MS);
  }

  function stopPollingSelection(): void {
    if (selectionTimer === null) return;
    clearInterval(selectionTimer);
    selectionTimer = null;
  }

  async function apply(): Promise<void> {
    try {
      if (enabled) {
        await invoke("mr_link_start");
        // Re-push whatever we already know, so a link started mid-session is
        // not blank until the next song change.
        if (lastChartJson) await invoke("mr_link_set_chart", { chartJson: lastChartJson });
        pollSelection();
      } else {
        stopPollingSelection();
        await invoke("mr_link_stop");
      }
    } catch (e) {
      setStatus(`failed: ${String(e)}`);
      return;
    }
    await refreshStatus();
  }

  if (toggle) {
    toggle.checked = enabled;
    toggle.addEventListener("change", () => {
      enabled = toggle.checked;
      try {
        window.localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
      } catch {
        // Session-only persistence is acceptable.
      }
      void apply();
    });
  }

  void apply();

  return {
    isEnabled: () => enabled,

    publish(songTimeSec, playing, heldNotes) {
      if (!enabled) return;
      const now = performance.now();
      if (now - lastPublish < PUBLISH_INTERVAL_MS) return;
      lastPublish = now;

      // Pairs of [pitch, velocity 0-127]. Velocity is optional upstream, so
      // default rather than sending NaN into a byte.
      const held = heldNotes.map((n) => [
        Math.max(0, Math.min(127, Math.trunc(n.pitch))),
        Math.max(0, Math.min(127, Math.trunc(n.velocity ?? 100))),
      ]);

      void invoke("mr_link_publish", {
        songTimeSec,
        playing,
        heldNotes: held,
      }).catch(() => {
        // Publishing is best-effort and happens continuously; a failed frame is
        // not worth a log line every 33 ms.
      });
    },

    setChart(chart) {
      const json = chart === null ? null : JSON.stringify(chart);
      if (json === lastChartJson) return;
      lastChartJson = json;
      if (!enabled) return;
      void invoke("mr_link_set_chart", { chartJson: json }).catch(() => {});
    },

    setAudioOffsetSec(offsetSec) {
      if (!enabled) return;
      void invoke("mr_link_set_audio_offset", { offsetSec }).catch(() => {});
    },
  };
}
