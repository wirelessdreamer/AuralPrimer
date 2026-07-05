/**
 * Game-side reader for the pack-root `drum_tab.json`.
 *
 * The Studio's drum-cleanup lane editor (and the import-time
 * `align_drum_tab_to_onsets` onset snapper) write edited drum hits back to
 * `drum_tab.json` at the pack root — NOT into `aural/notes.mid`. Until this
 * module existed the game charted drums exclusively from `notes.mid`, so
 * every lane-editor edit and every onset-aligned hit time was invisible in
 * gameplay. This reader closes that gap: when a pack ships a valid
 * `drum_tab.json`, we chart drums from it; otherwise the caller falls back
 * to the `notes.mid` drum selection unchanged.
 *
 * `drum_tab.json` hit shape is `{ t, p, v? }` where `p` is a lane id string
 * (kick, snare, hihat_closed, …). We convert each hit to the same
 * `DrumChartSelection` shape `selectDrumChart` produces, mapping lanes to
 * canonical GM MIDI numbers so `mapMidiToLane` (and therefore
 * `buildVizSongContext`, the drum highway, velocity, `t_off = t + 0.08`)
 * behave identically to the `notes.mid` path.
 *
 * The lane→GM MIDI table is the inverse of the sidecar's `_GM_DRUM_LANES`
 * (`feedpak_writer.py`), so a hit charted from `drum_tab.json` lands in the
 * exact same lane it would have via `notes.mid`.
 *
 * Everything here is defensive: a missing / malformed / empty
 * `drum_tab.json` yields `null` (never throws), and the caller keeps the
 * `notes.mid` selection.
 */

import { invoke } from "@tauri-apps/api/core";
import { selectDrumChart, type DrumChartSelection, type MidiTrackLike } from "./chartLoader";

/** One hit as stored in `drum_tab.json` (`p` is a lane id, `v` is 1..127). */
type DrumTabHit = { t: number; p: string; v?: number };

/**
 * Lane id → canonical GM percussion MIDI note. The inverse of the sidecar's
 * `_GM_DRUM_LANES` (each lane maps to one representative GM pitch that
 * `chartLoader.mapMidiToLane` classifies back into the intended 8-lane
 * scheme):
 *   kick→BD, snare/clap→SD, hihat_*→HH, crash→CY, ride→RD,
 *   tom_high→HT, tom_mid→LT, tom_low/perc-fallback→FT.
 */
const LANE_TO_GM_MIDI: Record<string, number> = {
  kick: 36, // → BD
  snare: 38, // → SD
  clap: 39, // → SD
  hihat_closed: 42, // → HH
  hihat_pedal: 44, // → HH
  hihat_open: 46, // → HH
  crash: 49, // → CY
  ride: 51, // → RD
  tom_high: 48, // → HT
  tom_mid: 45, // → LT
  tom_low: 41, // → FT
};

/**
 * Fallback GM pitch for any lane not in `LANE_TO_GM_MIDI` (e.g. the sidecar's
 * generic `perc` bucket, or an unknown editor lane). We route it to a snare so
 * the hit still charts + plays rather than silently vanishing.
 */
const FALLBACK_GM_MIDI = 38; // → SD

function laneToMidi(lane: string): number {
  return LANE_TO_GM_MIDI[lane] ?? FALLBACK_GM_MIDI;
}

/**
 * Convert a parsed `drum_tab.json` document to a `DrumChartSelection`.
 *
 * Returns `null` when the document has no usable hits (missing/empty/invalid
 * `hits`) so the caller can fall back to the `notes.mid` selection. Individual
 * malformed hits (non-finite `t`, non-string/empty `p`) are skipped rather than
 * failing the whole tab.
 *
 * Pure — no DOM, no Tauri; unit-tested in tests/drumTabChart.test.ts.
 */
export function drumChartFromTab(doc: unknown): DrumChartSelection | null {
  if (typeof doc !== "object" || doc === null) return null;
  const hits = (doc as { hits?: unknown }).hits;
  if (!Array.isArray(hits) || hits.length === 0) return null;

  // Build a single synthetic drum track and reuse selectDrumChart so lane
  // classification / selection metadata stays identical to the notes.mid path.
  const notes: MidiTrackLike["notes"] = [];
  for (const h of hits as DrumTabHit[]) {
    if (typeof h?.t !== "number" || !Number.isFinite(h.t)) continue;
    if (typeof h?.p !== "string" || h.p.length === 0) continue;
    notes.push({
      t: h.t,
      midi: laneToMidi(h.p),
      channel: 9,
      velocity: typeof h.v === "number" ? h.v : undefined,
    });
  }
  if (notes.length === 0) return null;
  notes.sort((a, b) => a.t - b.t);

  const track: MidiTrackLike = { index: 0, name: "Drums", notes };
  const selection = selectDrumChart([track]);
  if (selection.events.length === 0) return null;
  // The named "Drums" track guarantees a strict selection; override the reason
  // so the caps panel / logs make clear this came from drum_tab.json.
  return { ...selection, reason: "drum_tab" };
}

/**
 * Best-effort load of the pack-root `drum_tab.json` for a feedpak and convert
 * it to a `DrumChartSelection`. Returns `null` when the file is absent,
 * unreadable, or has no usable hits — the caller keeps the notes.mid selection.
 *
 * `read_auralsong_json` explicitly allows the root-level `drum_tab.json`
 * (see lib.rs `is_allowed_feature_rel`).
 */
export async function loadDrumChartFromTab(
  containerPath: string,
): Promise<DrumChartSelection | null> {
  let raw: unknown;
  try {
    raw = await invoke<unknown>("read_auralsong_json", {
      containerPath,
      relPath: "drum_tab.json",
    });
  } catch {
    // Missing file is the common case (no drum cleanup authored); stay silent.
    return null;
  }
  if (raw == null) return null;
  try {
    return drumChartFromTab(raw);
  } catch {
    return null;
  }
}
