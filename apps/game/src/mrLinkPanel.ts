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

export type MrLinkStatus = { running: boolean; host?: string };

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

export function initMrLinkPanel(): MrLinkPanelHandle {
  const toggle = document.getElementById("mrLinkEnabled") as HTMLInputElement | null;
  const statusEl = document.getElementById("mrLinkStatus");

  let enabled = readEnabled();
  let lastPublish = 0;
  let lastChartJson: string | null = null;

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

  async function apply(): Promise<void> {
    try {
      if (enabled) {
        await invoke("mr_link_start");
        // Re-push whatever we already know, so a link started mid-session is
        // not blank until the next song change.
        if (lastChartJson) await invoke("mr_link_set_chart", { chartJson: lastChartJson });
      } else {
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
