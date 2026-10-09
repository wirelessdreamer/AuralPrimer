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

import type { InstrumentRole, MelodicTrackSelection } from "./chartLoader";
import type { DrumTabChart } from "./drumTabChart";

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
/**
 * Which MIDI note each placed pad on the headset's kit sends.
 *
 * Kits disagree about note numbers, so each pad's was learned by striking it
 * during calibration. Without this the host would wait for a General MIDI kick
 * on a kit that sends something else entirely.
 */
export type MrKitLayout = { pieces: { id: string; note: number }[] };

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
  /** Hand the headset the drum chart, or null when the song has none. */
  setDrumChart: (chart: unknown | null) => void;
  /** Report drum strikes as they happen, for the headset to place in time. */
  reportDrumHits: (hits: [number, number][]) => void;
  setAudioOffsetSec: (offsetSec: number) => void;
  /** The desktop's note-spacing multiplier, pushed to the headset on change (protocol §7c). */
  setNoteSpacing: (multiplier: number) => void;
  /** The desktop's playback rate, pushed to the headset on change (protocol §7c). */
  setPlaybackRate: (rate: number) => void;
  isEnabled: () => boolean;
};

/** Parts a fretted chart is built for: these carry strings and frets. */
const FRETTED_ROLES: ReadonlySet<InstrumentRole> = new Set<InstrumentRole>([
  "bass",
  "lead_guitar",
  "rhythm_guitar",
]);

/**
 * The melodic track the headset's chart is built from (protocol §7b).
 *
 * `requested` is the role the headset asked for with INSTRUMENT, or null for
 * a headset that never asks -- which gets keys first, exactly as before.
 * "guitar" means lead, else rhythm. A part the song does not have falls back
 * the same way rather than sending nothing, since sending nothing would leave
 * the headset drawing the previous song: the chart's `role` says what was
 * actually sent, and the headset can tell the player the part is missing.
 */
export function pickMrTrack(
  tracks: readonly MelodicTrackSelection[],
  requested: string | null,
): MelodicTrackSelection | null {
  const wanted: string[] =
    requested === "guitar" ? ["lead_guitar", "rhythm_guitar"] : requested ? [requested] : [];
  for (const role of wanted) {
    const track = tracks.find((t) => t.role === role);
    if (track) return track;
  }
  return tracks.find((t) => t.role === "keys") ?? tracks[0] ?? null;
}

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
  keySignature?: { pitchClass: number; mode: string } | null,
  nashville?: boolean,
  handMode?: "both" | "left" | "right",
  showOtherHand?: boolean,
  fretted?: boolean,
): unknown | null {
  if (!track || track.notes.length === 0) return null;

  // Strings and frets go only to a headset that asked for an instrument
  // (protocol §7b): one that never asks predates them, and is sent exactly
  // what it always was.
  const withFrets = Boolean(fretted) && FRETTED_ROLES.has(track.role);

  const notes = [...track.notes]
    .sort((a, b) => a.t_on - b.t_on)
    .map((n) => ({
      on: n.t_on,
      off: n.t_off,
      pitch: n.pitch,
      vel: n.velocity,
      // 0 = left, 1 = right, omitted when the part was never split. A number
      // rather than "L"/"R" because the headset scans this JSON with a numeric
      // field reader; a string would need a second parser for one bit.
      ...(n.hand ? { hand: n.hand === "L" ? 0 : 1 } : {}),
      ...(withFrets ? frettedFields(n) : {}),
    }));

  return {
    songId,
    title,
    durationSec: notes.length ? notes[notes.length - 1].off : 0,
    // The protocol has always specified this field and the host has never
    // filled it in, so the headset had no way to know what key it was in --
    // which is why it could not number its keys the way the desktop does.
    // Omitted rather than faked when the key is unknown: a wrong tonic numbers
    // every key on the board wrongly, which is worse than numbering none.
    keySignature: keySignature
      ? { tonic: ((keySignature.pitchClass % 12) + 12) % 12, mode: keySignature.mode }
      : null,
    // Sent with the chart, so the headset follows the desktop's toggle. It is
    // a per-song snapshot: toggling mid-song does not reach the headset until
    // the next chart, which is a real limit rather than an oversight.
    nashville: Boolean(nashville),
    // The hand choice lives on the desktop and the headset follows it. Unlike
    // the Nashville flag this is re-sent the moment it changes, so switching
    // hands reaches the headset mid-song instead of at the next one.
    handMode: handMode ?? "both",
    showOtherHand: showOtherHand !== false,
    tempoMap: [{ tSec: 0, bpm, beatsPerBar }],
    role: track.role,
    ...(withFrets && track.tuning ? { tuning: track.tuning } : {}),
    ...(withFrets && track.anchors?.length ? { anchors: track.anchors } : {}),
    notes,
  };
}

/**
 * A note's place on the neck, for a fretted chart: `s` (0 = lowest string)
 * and `f`, plus the tab's finger, chord name and technique keys when it has
 * them. A note the fingering never placed carries none of it, and the headset
 * leaves it off the neck rather than guessing a position.
 */
function frettedFields(n: MelodicTrackSelection["notes"][number]): Record<string, unknown> {
  const s = n.string ?? n.s;
  const f = n.fret ?? n.f;
  if (typeof s !== "number" || typeof f !== "number") return {};
  const techKeys = n.tech ? Object.keys(n.tech) : [];
  return {
    s,
    f,
    ...(n.finger ? { finger: n.finger } : {}),
    ...(n.chord ? { chord: n.chord } : {}),
    // Key names only. The values (bend amounts, slide targets) would need a
    // nested object the headset's reader has no use for yet.
    ...(techKeys.length > 0 ? { tech: techKeys } : {}),
  };
}

/**
 * Build the DRUM CHART payload (protocol §4a) from the pack's drum tab.
 *
 * Lane ids travel exactly as `drum_tab.json` spells them. The desktop's own
 * drum path maps them to GM pitches and back into eight lanes, which collapses
 * hihat_closed, hihat_open and hihat_pedal into one -- on Fire In My Bones
 * that is 650 open hits against 8 closed, an articulation the pack records and
 * the game discards at load. The headset draws cues on real pads, where those
 * are three separate pieces of hardware, so it gets the tab as written.
 */
export function buildDrumChart(
  songId: string,
  title: string,
  tab: DrumTabChart | null,
  bpm: number,
  beatsPerBar: number,
): unknown | null {
  if (!tab || tab.hits.length === 0) return null;
  return {
    songId,
    title,
    durationSec: tab.hits[tab.hits.length - 1].t,
    tempoMap: [{ tSec: 0, bpm, beatsPerBar }],
    kit: tab.kit,
    hits: tab.hits,
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
  onKitLayout?: (kit: MrKitLayout | null) => void,
  onInstrument?: (role: string | null) => void,
): MrLinkPanelHandle {
  const toggle = document.getElementById("mrLinkEnabled") as HTMLInputElement | null;
  const statusEl = document.getElementById("mrLinkStatus");

  let enabled = readEnabled();
  let lastPublish = 0;
  let lastChartJson: string | null = null;
  let lastDrumChartJson: string | null = null;
  // The headset's copy of the desktop's spacing and speed. Kept here so a link
  // started mid-session, or restarted, sends the current values at once.
  const settings = { noteSpacing: 1, playbackRate: 1 };
  let lastSettingsJson: string | null = null;
  let selectionTimer: ReturnType<typeof setInterval> | null = null;

  function pushSettings(): void {
    const json = JSON.stringify(settings);
    if (json === lastSettingsJson) return;
    lastSettingsJson = json;
    if (!enabled) return;
    void invoke("mr_link_set_settings", { ...settings }).catch(() => {});
  }

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

      // Same reasoning for the kit: a pad re-learned in the headset changes
      // which note the host should wait for, and nothing gets re-selected to
      // carry the news.
      void invoke<MrKitLayout | null>("mr_link_kit_layout")
        .then((kit) => onKitLayout?.(kit ?? null))
        .catch(() => {});

      // And the instrument: the player can switch from keys to guitar in the
      // headset without anything on the desktop changing.
      void invoke<{ role?: unknown } | null>("mr_link_instrument")
        .then((v) => onInstrument?.(typeof v?.role === "string" ? v.role : null))
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
        if (lastDrumChartJson) {
          await invoke("mr_link_set_drum_chart", { chartJson: lastDrumChartJson });
        }
        // A freshly started link holds no settings, so always send them.
        await invoke("mr_link_set_settings", { ...settings });
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

    reportDrumHits(hits) {
      if (hits.length === 0) return;
      void invoke("mr_link_drum_hits", { hits }).catch(() => {
        // Best-effort, like publishing: a dropped strike is not worth a log
        // line in the middle of a fill.
      });
    },
    setDrumChart(chart) {
      const json = chart == null ? null : JSON.stringify(chart);
      lastDrumChartJson = json;
      void invoke("mr_link_set_drum_chart", { chartJson: json }).catch(() => {});
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

    setNoteSpacing(multiplier) {
      if (!Number.isFinite(multiplier) || multiplier <= 0) return;
      settings.noteSpacing = multiplier;
      pushSettings();
    },

    setPlaybackRate(rate) {
      if (!Number.isFinite(rate) || rate <= 0) return;
      settings.playbackRate = rate;
      pushSettings();
    },
  };
}
