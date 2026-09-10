/**
 * Which hand plays which note.
 *
 * Ported from the ingest pipeline's `piano_reduction.assign_hands`, so the
 * split the player practises against is the same one the importer already
 * uses to decide whether a passage is playable at all. Two hand models that
 * disagree would put a note in the left hand here and the right hand there,
 * and the player would be the one to notice.
 *
 * No pack carries hand data -- the importer computes the split, uses it, and
 * throws it away -- so this runs at load time on the notes we already have.
 * That is what makes the feature work on every pack in the library instead of
 * only on songs imported after it shipped.
 *
 * The rule: group notes that strike together, break each group at its widest
 * interior gap, and give everything below the break to the left hand. A
 * pianist breaks a chord where the gap is, not at a fixed key.
 */

import type { MelodicNote } from "./chartLoader";

export type Hand = "L" | "R";

/** Which hand or hands the player is working on. */
export type HandMode = "both" | "left" | "right";

// Straight from PlayabilityConfig / ReductionConfig. Kept as literals rather
// than imported from anywhere: these are a physical model of a hand, and they
// have not moved since they were measured.
const MAX_HAND_SPAN = 14; // a major ninth
const MAX_NOTES_PER_HAND = 4;
const ONSET_WINDOW_SEC = 0.05;
const HAND_PIVOT = 60; // middle C

/**
 * Divide simultaneous pitches between two non-crossing hands.
 *
 * Returns `[left, right]` for the first feasible break, or `null` when no
 * break satisfies the span and finger limits. Breaks are tried widest-gap
 * first.
 */
export function handSplit(pitches: readonly number[]): [number[], number[]] | null {
  const ordered = [...pitches].map((p) => Math.trunc(p)).sort((a, b) => a - b);
  if (ordered.length === 0) return [[], []];
  if (ordered.length > 2 * MAX_NOTES_PER_HAND) return null;

  const ok = (hand: number[]): boolean => {
    if (hand.length === 0) return true;
    if (hand.length > MAX_NOTES_PER_HAND) return false;
    return hand[hand.length - 1] - hand[0] <= MAX_HAND_SPAN;
  };

  const gapAt = (k: number): number => (k >= 1 && k < ordered.length ? ordered[k] - ordered[k - 1] : 0);
  const candidates = Array.from({ length: ordered.length + 1 }, (_, k) => k)
    .sort((a, b) => gapAt(b) - gapAt(a));

  for (const k of candidates) {
    const left = ordered.slice(0, k);
    const right = ordered.slice(k);
    if (ok(left) && ok(right)) return [left, right];
  }
  return null;
}

/**
 * Highest pitch the left hand takes at this attack, or `null` for all-right.
 *
 * The fallback is the part worth reading twice. `handSplit` parks a run it
 * cannot break in the RIGHT hand, so a lone note always comes back as
 * right-handed. Low on the keyboard that is the wrong hand, and register is
 * the only thing left to decide it by -- without this, every single-note
 * passage in the song lands in the right hand no matter how far below middle
 * C it sits, and the left-hand practice mode shows an empty stave.
 */
export function leftHandCeiling(pitches: readonly number[]): number | null {
  if (pitches.length === 0) return null;
  const split = handSplit(pitches);
  if (split === null) {
    // Unreachable for notes the importer already cut to fit, but a caller may
    // hand us raw ones: fall back to the median so the output is still two
    // hands rather than an exception.
    const ordered = [...pitches].sort((a, b) => a - b);
    return ordered[Math.floor((ordered.length - 1) / 2)];
  }
  const [low] = split;
  if (low.length > 0) return low[low.length - 1];
  const highest = Math.max(...pitches);
  return highest < HAND_PIVOT ? highest : null;
}

/**
 * Chain notes whose consecutive onsets fall inside the onset window.
 *
 * Chained from the PREVIOUS onset, not from the first of the group, which is
 * how the ingest pipeline groups them. Wait mode's own grouping anchors on the
 * first note instead; the two can therefore disagree on a fast run, and this
 * one is the one that has to match the playability model.
 */
function groupByOnset(notes: readonly MelodicNote[]): MelodicNote[][] {
  const sorted = [...notes].sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch);
  const groups: MelodicNote[][] = [];
  let current: MelodicNote[] = [];
  for (const note of sorted) {
    if (current.length > 0 && note.t_on - current[current.length - 1].t_on < ONSET_WINDOW_SEC) {
      current.push(note);
    } else {
      if (current.length > 0) groups.push(current);
      current = [note];
    }
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

/**
 * Tag every note with the hand that plays it.
 *
 * Returns new notes; the input is not mutated. A note's hand is decided at its
 * onset and does not change while it sounds, so a held note keeps one hand --
 * and therefore one colour -- for its whole length.
 */
export function assignHands(notes: readonly MelodicNote[]): MelodicNote[] {
  const out: MelodicNote[] = [];
  for (const group of groupByOnset(notes)) {
    const ceiling = leftHandCeiling(group.map((n) => Math.trunc(n.pitch)));
    for (const note of group) {
      const hand: Hand = ceiling !== null && Math.trunc(note.pitch) <= ceiling ? "L" : "R";
      out.push({ ...note, hand });
    }
  }
  out.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch);
  return out;
}

/**
 * Is this note one the player is being asked to play?
 *
 * Notes with no hand tag always count. Only the piano part is split, so every
 * other instrument answers yes to everything and needs no special case at the
 * call sites.
 */
export function isSelectedHand(note: { hand?: Hand }, mode: HandMode): boolean {
  if (mode === "both") return true;
  if (!note.hand) return true;
  return note.hand === (mode === "left" ? "L" : "R");
}
