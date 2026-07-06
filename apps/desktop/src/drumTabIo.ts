/**
 * Drum-tab <-> lane-note conversion for the Refine workspace's drum-cleanup
 * mode. The pack's root `drum_tab.json` (`{version, name, kit, hits}` with
 * hits `{t, p, v}`) is the single source of truth the game charts from; the
 * editor works on SpectroNote-shaped hits where `pitch` is a LANE INDEX
 * (bottom lane 0), and saving converts back — preserving version/name/kit and
 * any unknown top-level fields.
 *
 * Pure logic, no DOM/Tauri — unit-tested in tests/drumTabIo.test.ts.
 */
import type { SpectroNote } from "./spectrogramEditor";

export type DrumTabHit = { t: number; p: string; v?: number; [k: string]: unknown };

export type DrumTabFile = {
  version?: unknown;
  name?: unknown;
  kit?: Array<{ id?: string }>;
  hits?: DrumTabHit[];
  [k: string]: unknown;
};

/**
 * Canonical bottom-to-top lane order, roughly by the frequency band each
 * voice occupies — so lanes land near their energy in the spectrogram behind
 * them (kick at the bottom, cymbals at the top).
 */
export const DRUM_LANE_CANONICAL = [
  "kick",
  "tom_low",
  "tom_mid",
  "tom_high",
  "snare",
  "hihat_closed",
  "hihat_open",
  "ride",
  "crash",
] as const;

export const DRUM_LANE_LABELS: Record<string, string> = {
  kick: "Kick",
  tom_low: "Tom Lo",
  tom_mid: "Tom Mid",
  tom_high: "Tom Hi",
  snare: "Snare",
  hihat_closed: "Hi-hat",
  hihat_open: "HH Open",
  ride: "Ride",
  crash: "Crash",
};

/** Display length of a hit in the editor (hits are instants; this is width). */
export const DRUM_HIT_DISPLAY_SEC = 0.08;

/** Default velocity for user-added hits (matches the transcriber's output). */
export const DRUM_HIT_DEFAULT_VELOCITY = 127;

export function drumLaneLabel(lane: string): string {
  return DRUM_LANE_LABELS[lane] ?? lane;
}

/**
 * Lane order (bottom -> top) for a tab: the canonical order filtered to the
 * voices the tab actually declares (kit ids ∪ hit voices), with any unknown
 * voices appended alphabetically so no hit is ever dropped.
 */
export function laneOrderForTab(tab: DrumTabFile): string[] {
  const present = new Set<string>();
  for (const k of tab.kit ?? []) {
    if (typeof k?.id === "string" && k.id) present.add(k.id);
  }
  for (const h of tab.hits ?? []) {
    if (typeof h?.p === "string" && h.p) present.add(h.p);
  }
  const ordered = DRUM_LANE_CANONICAL.filter((l) => present.has(l));
  const unknown = [...present].filter((l) => !(DRUM_LANE_CANONICAL as readonly string[]).includes(l)).sort();
  return [...ordered, ...unknown];
}

/** Tab hits -> editable lane notes (pitch = lane index; skips unknown lanes). */
export function hitsToNotes(tab: DrumTabFile, lanes: string[]): SpectroNote[] {
  const laneIndex = new Map(lanes.map((l, i) => [l, i]));
  const out: SpectroNote[] = [];
  for (const h of tab.hits ?? []) {
    const idx = laneIndex.get(h?.p ?? "");
    if (idx === undefined || typeof h.t !== "number" || !Number.isFinite(h.t)) continue;
    // Split {t,p,v} (structural) from every other hit field (ghost `g`, flam
    // `f`, choke `k`, unknown future keys) so the extras survive a round-trip
    // instead of being silently dropped on save.
    const { t, p, v, ...rest } = h;
    void t;
    void p;
    void v;
    const note: SpectroNote = {
      t_on: h.t,
      t_off: h.t + DRUM_HIT_DISPLAY_SEC,
      pitch: idx,
      velocity: typeof h.v === "number" ? h.v : DRUM_HIT_DEFAULT_VELOCITY,
    };
    if (Object.keys(rest).length > 0) note.extra = rest;
    out.push(note);
  }
  out.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch);
  return out;
}

/**
 * Edited lane notes -> a full drum-tab file. Every top-level field of the
 * original (version, name, kit, anything unknown) is preserved verbatim; only
 * `hits` is replaced. Hits are sorted by time then lane for stable diffs.
 */
export function notesToTab(notes: SpectroNote[], lanes: string[], original: DrumTabFile): DrumTabFile {
  const hits: DrumTabHit[] = [];
  for (const n of notes) {
    const lane = lanes[n.pitch];
    if (!lane || !Number.isFinite(n.t_on)) continue;
    // Spread the carried extras FIRST, then write t/p/v LAST so a stale/hostile
    // extra key (t/p/v) can never shadow the structural fields — the current
    // note's time/lane/velocity always win. hitsToNotes never routes t/p/v into
    // extra, so in practice `rest` is g/f/k/unknowns only; this ordering just
    // makes the "extras can never shadow t/p/v" guarantee robust. A hit dragged
    // to another lane keeps its extras (they ride on the note object).
    hits.push({
      ...(n.extra ?? {}),
      t: n.t_on,
      p: lane,
      v: typeof n.velocity === "number" ? n.velocity : DRUM_HIT_DEFAULT_VELOCITY,
    });
  }
  hits.sort((a, b) => a.t - b.t || a.p.localeCompare(b.p));
  return { ...original, hits };
}
