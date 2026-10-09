/**
 * fretted.ts - a fretted part, read once into the shape the guitar views draw.
 *
 * Pure: no canvas and no DOM, so all of it is testable. The views in
 * guitarViews.ts only ever read a FretChart; everything that has to be worked
 * out from the tab -- which string and fret, what a chord is called, where the
 * hand is when the tab does not say, what the tuning is called -- happens here,
 * once per track, rather than every frame.
 *
 * String index 0 is the LOWEST string throughout, as in the sidecar and the
 * importer. Views that put the high string on top invert it when they draw,
 * and nowhere else.
 */

import { nameChord } from "@auralprimer/core-music";
import { triadOf, type Triad } from "./fretNumbers";
import { clampScrollSpeedMultiplier } from "@auralprimer/viz-sdk";
import {
  noteToFretPosition,
  TUNING_BASS_STANDARD,
  TUNING_GUITAR_STANDARD,
  type InstrumentRole,
  type MelodicTrackSelection,
} from "./index";

export type FrettedRole = "bass" | "rhythm_guitar" | "lead_guitar";

export function isFrettedRole(role: string | null | undefined): role is FrettedRole {
  return role === "bass" || role === "rhythm_guitar" || role === "lead_guitar";
}

/** Technique keys present on a note (feedpak: pm, ho, po, sl, bn, vb, hm, ...). */
export type FretTech = Record<string, number | boolean>;
/** A hand position: index finger at `fret`, spanning `width` frets, from `t`. */
export type FretAnchor = { t: number; fret: number; width: number };
/** A chord shape held from `t` to `t_end`; lowest string first, -1 = unused. */
export type FretHandshape = { t: number; t_end: number; name: string; frets: number[]; fingers: number[] };

/** One struck chord, shared by every note in it. */
export type FretChord = {
  t: number;
  name: string;
  size: number;
  fingers: number[];
  /** Root and quality read from the notes, for chord numbers and chord tones; null if no triad fits. */
  triad: Triad | null;
};

export type FretNote = {
  t: number;
  /** Seconds the note rings. */
  sus: number;
  s: number;
  f: number;
  midi: number;
  tech: FretTech;
  /** The chord this note is part of, or null for a single note. */
  chord: FretChord | null;
  /** Fretting finger 1-4, or 0 when the tab does not say. */
  finger: number;
};

/** A chord shape coming up, for the coach's cards. */
export type ChordChange = { t: number; end: number; name: string; frets: number[]; fingers: number[]; triad: Triad | null };

export type FretChart = {
  isBass: boolean;
  /** Number of strings. */
  n: number;
  /** Open strings as MIDI, lowest first. */
  open: number[];
  /** Sorted by time, then string. */
  notes: FretNote[];
  /** Sorted by time; never empty. */
  anchors: FretAnchor[];
  changes: ChordChange[];
  maxFret: number;
  tuningName: string;
};

/** A beat from the song timeline; `m` is its bar number. */
export type GridBeat = { t: number; down: boolean; m: number };
export type GridSection = { t: number; name: string };

/** Notes struck closer together than this are one chord. */
const CHORD_TOLERANCE_SEC = 0.03;

// Mixed spelling on purpose: the names a guitarist reads off a chart (Eb, Bb,
// F#), not a key-aware spelling the tab has no key to drive.
const NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

export function noteName(midi: number): string {
  return NOTE_NAMES[((midi % 12) + 12) % 12];
}

// Technique keys the views draw. Others ride along in the sidecar and are
// ignored here.
const TECH_KEYS = ["pm", "mt", "fhm", "ho", "po", "tp", "sl", "slu", "bn", "vb", "tr", "ac", "hm", "hp"];
// Keys whose value is a target, not a flag: a slide to fret N, a bend of N
// semitones. Zero means unset for these.
const VALUED_TECH = new Set(["sl", "slu", "bn"]);

export function normalizeTech(raw: Record<string, number | boolean> | undefined): FretTech {
  const out: FretTech = {};
  if (!raw) return out;
  for (const key of TECH_KEYS) {
    const v = raw[key];
    if (v === true) out[key] = true;
    else if (typeof v === "number" && Number.isFinite(v) && (VALUED_TECH.has(key) ? v > 0 : v !== 0)) out[key] = v;
  }
  return out;
}

/** Open strings for a part: the sidecar's own tuning when it has one, else standard. */
export function openStrings(role: InstrumentRole, tuning?: number[]): number[] {
  if (Array.isArray(tuning) && tuning.length > 0 && tuning.every((m) => Number.isInteger(m))) return tuning.slice();
  return (role === "bass" ? TUNING_BASS_STANDARD : TUNING_GUITAR_STANDARD).strings.slice();
}

export function tuningName(open: number[], isBass: boolean): string {
  const base = (isBass ? TUNING_BASS_STANDARD : TUNING_GUITAR_STANDARD).strings;
  if (open.length === base.length) {
    const off = open.map((m, i) => m - base[i]);
    const same = off.every((v) => v === off[0]);
    if (same && off[0] === 0) return "E standard";
    if (same && off[0] === -1) return "E♭ standard";
    if (same && off[0] === -2) return "D standard";
    if (off[0] === -2 && off.slice(1).every((v) => v === 0)) return "Drop D";
  }
  return open.map(noteName).join(" ");
}

/** The name a player calls string `s`: the top string of a guitar is "e", not "E". */
export function stringLabel(open: number[], s: number): string {
  const name = noteName(open[s]);
  return s === open.length - 1 && s > 0 && name === noteName(open[0]) ? name.toLowerCase() : name;
}

/**
 * Where the hand is when the tab does not say.
 *
 * A four-fret window that stays put until a note falls outside it, then moves
 * just far enough to cover it -- the laziest hand that still reaches every
 * note, which is roughly what a player does. Open strings never move it.
 */
export function deriveAnchors(notes: ReadonlyArray<{ t: number; f: number }>): FretAnchor[] {
  const out: FretAnchor[] = [];
  let cur = 1;
  for (const x of notes) {
    if (x.f <= 0) continue;
    let next = cur;
    if (x.f < cur) next = x.f;
    else if (x.f > cur + 3) next = x.f - 3;
    if (next !== cur || out.length === 0) {
      cur = next;
      out.push({ t: x.t - 0.25, fret: cur, width: 4 });
    }
  }
  return out.length > 0 ? out : [{ t: 0, fret: 1, width: 4 }];
}

/** Read a track into what the guitar views draw. */
export function buildFretChart(track: MelodicTrackSelection): FretChart {
  const isBass = track.role === "bass";
  const open = openStrings(track.role, track.tuning);
  const n = open.length;
  const tuning = { name: "", strings: open };

  // Strings and frets: the tab's own when it has them, else the lowest fret
  // that plays the pitch.
  // The tab's chord name for a note, kept beside the note rather than on it:
  // the chord a view draws is the group's, not any one member's.
  const tabChordName = new Map<FretNote, string>();
  const notes: FretNote[] = [];
  for (const note of track.notes) {
    const pos = noteToFretPosition(note, tuning);
    if (!pos) continue;
    const x: FretNote = {
      t: note.t_on,
      sus: Math.max(0, note.t_off - note.t_on),
      s: pos.string,
      f: pos.fret,
      midi: Math.round(note.pitch),
      tech: normalizeTech(note.tech),
      chord: null,
      finger: note.finger ?? 0,
    };
    if (note.chord?.trim()) tabChordName.set(x, note.chord.trim());
    notes.push(x);
  }
  notes.sort((a, b) => a.t - b.t || a.s - b.s);

  // Chords: notes struck together on different strings. Named by the tab when
  // it names them, from the pitches when it does not.
  const changes: ChordChange[] = [];
  for (let i = 0; i < notes.length; ) {
    let j = i + 1;
    while (j < notes.length && notes[j].t - notes[i].t <= CHORD_TOLERANCE_SEC) j += 1;
    const group = notes.slice(i, j);
    const strings = new Set(group.map((x) => x.s));
    if (group.length >= 2 && strings.size === group.length) {
      const fingers = Array<number>(n).fill(-1);
      const frets = Array<number>(n).fill(-1);
      for (const x of group) {
        frets[x.s] = x.f;
        if (x.finger > 0) fingers[x.s] = x.finger;
      }
      const authored = group.map((x) => tabChordName.get(x)).find(Boolean);
      const name = authored || nameChord(group.map((x) => x.midi)) || "";
      const triad = triadOf(group.map((x) => x.midi));
      const chord: FretChord = { t: group[0].t, name, size: group.length, fingers, triad };
      for (const x of group) x.chord = chord;
      changes.push({ t: chord.t, end: chord.t, name, frets, fingers, triad });
    }
    i = j;
  }

  // Shapes the tab holds over time, arpeggios included.
  for (const h of track.handshapes ?? []) {
    const frets = Array.from({ length: n }, (_, s) => h.frets[s] ?? -1);
    const fingers = Array.from({ length: n }, (_, s) => h.fingers[s] ?? -1);
    const pitches = frets.flatMap((f, s) => (f >= 0 ? [open[s] + f] : []));
    const name = h.name || nameChord(pitches) || "";
    changes.push({ t: h.t, end: h.t_end, name, frets, fingers, triad: triadOf(pitches) });
  }

  // One card per change: a chord restruck, or struck inside the shape it came
  // from, extends the card it is already on.
  changes.sort((a, b) => a.t - b.t);
  const merged: ChordChange[] = [];
  for (const c of changes) {
    const prev = merged[merged.length - 1];
    if (prev && prev.name === c.name && prev.frets.join() === c.frets.join()) {
      prev.end = Math.max(prev.end, c.end);
      continue;
    }
    merged.push({ ...c, name: c.name || "?" });
  }

  const authored = (track.anchors ?? [])
    .filter((a) => Number.isFinite(a.t))
    .map((a) => ({ t: a.t, fret: Math.max(1, a.fret || 1), width: Math.max(2, a.width || 4) }))
    .sort((a, b) => a.t - b.t);

  return {
    isBass,
    n,
    open,
    notes,
    anchors: authored.length > 0 ? authored : deriveAnchors(notes),
    changes: merged,
    maxFret: notes.reduce((m, x) => Math.max(m, x.f), 0),
    tuningName: tuningName(open, isBass),
  };
}

// --- Lookups and layout maths shared by the views ---------------------------

/** Index of the first item at or after `t`. */
export function lowerBound(items: ReadonlyArray<{ t: number }>, t: number): number {
  let lo = 0;
  let hi = items.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The last item at or before `t`, or null when there is none yet. */
export function lastAtOrBefore<T extends { t: number }>(items: ReadonlyArray<T>, t: number): T | null {
  const i = lowerBound(items, t + 1e-9);
  return i > 0 ? items[i - 1] : null;
}

/** Where the hand is at `t`; before the first position, the first (it has to be somewhere). */
export function anchorAt(chart: FretChart, t: number): FretAnchor {
  return lastAtOrBefore(chart.anchors, t) ?? chart.anchors[0];
}

/** Notes on screen between t0 and t1, including ones still ringing into t0. */
export function visibleNotes(notes: ReadonlyArray<FretNote>, t0: number, t1: number): FretNote[] {
  const out: FretNote[] = [];
  for (let i = lowerBound(notes, t0 - 6); i < notes.length && notes[i].t <= t1; i += 1) {
    const x = notes[i];
    if (x.t >= t0 || x.t + x.sus >= t0) out.push(x);
  }
  return out;
}

/** True while the note is sounding; a short note still lights for a moment. */
export function isSounding(note: FretNote, t: number): boolean {
  return t >= note.t && t <= note.t + Math.max(note.sus, 0.14);
}

/**
 * Seconds of song a view shows ahead of now, given the host's note spacing.
 *
 * More spacing spreads the notes further apart by showing less time in the
 * same space -- the same rule the piano roll follows -- so `[` and `]` move
 * every view the same way.
 */
export function lookAheadSec(baseSec: number, scrollSpeedMultiplier: number | undefined): number {
  return baseSec / clampScrollSpeedMultiplier(scrollSpeedMultiplier);
}

/** Frets the coach's neck shows: the whole part, at least twelve, at most 24. */
export function neckFretCount(maxFret: number): number {
  return Math.max(12, Math.min(24, maxFret + 1));
}

/**
 * Where fret wire `b` sits along a neck showing `frets` frets, 0 (nut) to 1.
 *
 * Real spacing: each fret is 2^(-1/12) the width of the one before it, so the
 * hand window is wider low on the neck than high up, as it is on the guitar.
 */
export function fretWireFraction(b: number, frets: number): number {
  return (1 - 2 ** (-b / 12)) / (1 - 2 ** (-frets / 12));
}

/** The tab mark for a bend of `semitones`: ½ and full, else in whole steps. */
export function bendLabel(semitones: number): string {
  if (semitones === 1) return "½";
  if (semitones === 2) return "full";
  return String(semitones / 2);
}

/** The fret number, or × for a muted note: the default label on a gem or dot. */
export function fretLabel(note: FretNote): string {
  return note.tech.mt ? "×" : String(note.f);
}

/**
 * A beat grid at one tempo, for songs with no timeline to draw bar lines from.
 */
export function constantBeats(bpm: number, beatsPerBar: number, t0: number, t1: number): GridBeat[] {
  const spb = 60 / (Number.isFinite(bpm) && bpm > 0 ? bpm : 120);
  const per = Math.max(1, Math.round(beatsPerBar) || 4);
  const out: GridBeat[] = [];
  for (let i = Math.max(0, Math.floor(t0 / spb)); i * spb <= t1; i += 1) {
    out.push({ t: i * spb, down: i % per === 0, m: Math.floor(i / per) + 1 });
  }
  return out;
}

/**
 * Where the tab staff sits in a canvas `height` tall.
 *
 * Fifty pixels above it hold the HUD, the section boxes and the chord names;
 * twenty-six below hold the palm-mute brackets. The strings share the rest,
 * 10-30 px apart and centred, so the old 160 px tab strip still fits a full
 * six-string staff with every mark.
 */
export function tabStaffLayout(height: number, strings: number): { gap: number; yTop: number; yBot: number } {
  const above = 50;
  const below = 26;
  const n = Math.max(2, strings);
  const room = height - above - below;
  const gap = Math.max(10, Math.min(30, room / (n - 1)));
  const staff = gap * (n - 1);
  const yTop = above + Math.max(0, (room - staff) / 2);
  return { gap, yTop, yBot: yTop + staff };
}
