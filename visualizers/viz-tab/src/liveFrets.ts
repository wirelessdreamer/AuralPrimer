/**
 * What the player is fretting, read from a MIDI guitar.
 *
 * A Jamstik in Multi-Channel mode sends each string on its own MIDI channel,
 * so a note arrives with its string, and pitch plus string is one place on
 * the neck. Jamstik counts strings the guitarist's way, string 1 the high E
 * on channel 1; the charts count from the low E as string 0. This file is
 * the one place that flips them -- a mirrored neck looks plausible, which is
 * why it lives here with a test and nowhere else.
 *
 * The guitar is taken to be in standard tuning for now.
 */

import { visibleNotes, type FretChart } from "./fretted";

/** A held note as the MIDI input tracker reports it. */
export type LiveInputNote = {
  pitch: number;
  velocity?: number;
  /** 0-15, as on the wire: Jamstik's channel 1 is 0. */
  channel?: number;
  isPressed?: boolean;
  /**
   * False when the string is only fretted, not picked: a Rock Band 3 Squier
   * reports where the fretting hand is before (and between) picks. Absent
   * means picked, which is all a Jamstik ever sends.
   */
  picked?: boolean;
};

/** A held note placed on the neck. */
export type LiveFret = { s: number; f: number; midi: number; velocity: number; picked: boolean };

/** A six-string guitar in standard tuning, lowest string first. */
const STANDARD_GUITAR = [40, 45, 50, 55, 59, 64];

const MAX_FRET = 24;

/**
 * Place each held note on the neck from its channel: `firstChannel` (0 on
 * the wire) carries the high E. Multi-Channel mode starts at 0, MPE at 1.
 * A note that cannot be on its string (an unknown channel, below the open
 * note, past the last fret) is dropped rather than guessed at.
 */
export function liveFrets(notes: ReadonlyArray<LiveInputNote>, firstChannel = 0): LiveFret[] {
  const n = STANDARD_GUITAR.length;
  const out: LiveFret[] = [];
  for (const x of notes) {
    if (x.isPressed === false || x.channel === undefined) continue;
    const s = n - 1 - (x.channel - firstChannel);
    if (s < 0 || s >= n) continue;
    const midi = Math.round(x.pitch);
    const f = midi - STANDARD_GUITAR[s];
    if (f < 0 || f > MAX_FRET) continue;
    out.push({ s, f, midi, velocity: x.velocity ?? 100, picked: x.picked !== false });
  }
  return out.sort((a, b) => a.s - b.s);
}

/**
 * True when two held notes share a channel: the guitar is in Single Channel
 * mode (or this is not a MIDI guitar), and the strings cannot be told apart.
 */
export function sharesChannel(notes: ReadonlyArray<LiveInputNote>): boolean {
  const seen = new Set<number>();
  for (const x of notes) {
    if (x.isPressed === false || x.channel === undefined) continue;
    if (seen.has(x.channel)) return true;
    seen.add(x.channel);
  }
  return false;
}

/**
 * How a held note answers the chart: on the chart's string and fret (hit),
 * the right pitch somewhere else on the neck (other, with where the chart
 * has it), or neither (off). A string that is only fretted is not judged
 * (fretted): it shows where the hand is, not a note played.
 */
export type JudgedFret = LiveFret & { kind: "hit" | "other" | "off" | "fretted"; target: { s: number; f: number } | null };

/** How early a note may be played and still count, and how late after it stops ringing (seconds). */
const JUDGE_SLACK_SEC = 0.08;

/** Judge each held note against the chart notes due at `now`. */
export function judgeLive(chart: FretChart, live: ReadonlyArray<LiveFret>, now: number): JudgedFret[] {
  const due = visibleNotes(chart.notes, now - JUDGE_SLACK_SEC, now + JUDGE_SLACK_SEC).filter(
    (x) => !x.tech.mt && now >= x.t - JUDGE_SLACK_SEC && now <= x.t + Math.max(x.sus, 0.14) + JUDGE_SLACK_SEC,
  );
  // A bass part sounds an octave below the guitar's same string and fret.
  const lift = chart.isBass ? 12 : 0;
  return live.map((x) => {
    if (!x.picked) return { ...x, kind: "fretted", target: null };
    if (due.some((d) => d.s === x.s && d.f === x.f)) return { ...x, kind: "hit", target: null };
    const there = due.find((d) => d.midi + lift === x.midi);
    if (there) return { ...x, kind: "other", target: { s: there.s, f: there.f } };
    return { ...x, kind: "off", target: null };
  });
}
