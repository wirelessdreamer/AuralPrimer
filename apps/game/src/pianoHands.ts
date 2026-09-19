/**
 * Which hand plays which note.
 *
 * No pack carries hand data -- the importer computes a split, uses it to check
 * the music is playable, and throws it away -- so this runs at load time on
 * the notes we already have. That is what makes the feature work on every pack
 * in the library instead of only on songs imported after it shipped.
 *
 * Two different questions live in this file, and conflating them is what made
 * the first attempt unusable:
 *
 *   `handSplit` asks whether two hands can REACH a chord. That is about one
 *   instant, it is the importer's feasibility test, and it is ported from
 *   `piano_playability.hand_split` exactly -- verified against the Python on
 *   four thousand random chords.
 *
 *   `assignHands` asks which hand a pianist USES. That is a path, not an
 *   instant. Answering it with the feasibility rule put the two hands 24 to 41
 *   semitones inside each other on real packs, because three attacks in four
 *   are single notes and a single note has no gap to break at -- so it fell
 *   through to a fixed middle-C pivot and a line walking across that pivot
 *   changed hands on every note.
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

/** Can one hand take these pitches at once? */
function fits(hand: readonly number[]): boolean {
  if (hand.length === 0) return true;
  if (hand.length > MAX_NOTES_PER_HAND) return false;
  return hand[hand.length - 1] - hand[0] <= MAX_HAND_SPAN;
}

/** How far either side of a note to look when deciding where the hands are. */
const CONTEXT_SEC = 2.5;

/**
 * How strongly the split point resists moving, per attack.
 *
 * High on purpose. The split is a thing the player has to hold in their head
 * while sight-reading, so it is worth more as a line that stays put through a
 * section than as one that is locally optimal and never in the same place
 * twice. It still follows a song that changes register, just slowly.
 */
const BOUNDARY_INERTIA = 0.94;

/** Below this spread there is only one hand's worth of music to divide. */
const MIN_SPREAD_SEMITONES = 12;


/**
 * The pitch that best divides these notes into a low group and a high one.
 *
 * Otsu's method on the pitch histogram: pick the threshold that maximises the
 * separation between the two groups it makes. Returns null when the notes are
 * all in one register and there is nothing to divide -- a monophonic melody
 * has no left hand in it, and inventing a boundary through the middle of one
 * line is what produces a part that alternates hands.
 */
function bestBoundary(pitches: readonly number[]): number | null {
  if (pitches.length < 2) return null;
  const lo = Math.min(...pitches);
  const hi = Math.max(...pitches);
  if (hi - lo < MIN_SPREAD_SEMITONES) return null;

  let bestScore = -1;
  const winners: number[] = [];
  for (let cut = lo + 1; cut <= hi; cut++) {
    const low = pitches.filter((p) => p < cut);
    const high = pitches.filter((p) => p >= cut);
    if (low.length === 0 || high.length === 0) continue;
    const wLow = low.length / pitches.length;
    const wHigh = high.length / pitches.length;
    const diff = mean(high) - mean(low);
    const score = wLow * wHigh * diff * diff; // between-class variance
    if (score > bestScore + 1e-9) {
      bestScore = score;
      winners.length = 0;
      winners.push(cut);
    } else if (score > bestScore - 1e-9) {
      winners.push(cut);
    }
  }
  if (winners.length === 0) return null;

  // The middle of the winning range, not its bottom. Every cut across a gap
  // between two registers scores the same, and taking the first of them put
  // the line hard against the top of the lower part -- so a part that rose by
  // a semitone crossed it, and a register change the boundary was still
  // catching up with swept a whole hand to the wrong side.
  return winners[Math.floor(winners.length / 2)];
}

const mean = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;

/**
 * Tag every note with the hand that plays it.
 *
 * Returns new notes; the input is not mutated. A note's hand is decided at its
 * onset and does not change while it sounds, so a held note keeps one hand --
 * and therefore one colour -- for its whole length.
 *
 * The split is a boundary across the keyboard that moves slowly, not a pair of
 * hands that roam. Two earlier rules failed in opposite ways and both are worth
 * remembering, because each looks right on paper:
 *
 *   Breaking each chord at its widest gap -- the importer's feasibility rule --
 *   cannot answer for a single note, and three attacks in four are single
 *   notes, so it collapsed onto a fixed middle-C pivot and a line walking
 *   across that pivot changed hands on every note.
 *
 *   Giving each hand a position and dealing every attack to the nearer one
 *   fixed the note-by-note flipping and replaced it with drift: over a song
 *   both hands wander the whole keyboard, so the same pitch belongs to
 *   whichever hand happened to be near it, and the two overlap by three
 *   octaves.
 *
 * A held chord in the left hand under a melody in the right is the commonest
 * piano texture there is, and note length was tried as a tie-break near the
 * boundary on exactly that reasoning. It made the split measurably worse on
 * all three test packs -- overlap 16/19/19 semitones went to 19/23/24 -- which
 * is a fact about the transcriptions rather than about piano music: whole-mix
 * transcription fabricates durations, and one of these packs has its notes
 * quantised onto a fifth-of-a-second grid. The signal is real; it is just not
 * in this data.
 *
 * What a player needs is neither -- it is to know, while reading, which notes
 * are theirs. So the boundary is found from the notes around each moment and
 * then heavily damped: it follows a song that changes register and holds still
 * through a section. Where the music is a single line with no second part in
 * it, there is no boundary to find and it stays where it was, rather than
 * splitting one melody down the middle.
 */
export function assignHands(notes: readonly MelodicNote[]): MelodicNote[] {
  const sorted = [...notes].sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch);
  if (sorted.length === 0) return [];

  // Seed from the whole song, so the first bar is divided the same way the
  // rest of it will be rather than by whatever the opening chord happens to be.
  let boundary = bestBoundary(sorted.map((n) => Math.trunc(n.pitch))) ?? HAND_PIVOT;

  const out: MelodicNote[] = [];
  let lo = 0;
  let hi = 0;

  for (const group of groupByOnset(sorted)) {
    const t = group[0].t_on;
    while (lo < sorted.length && sorted[lo].t_on < t - CONTEXT_SEC) lo++;
    while (hi < sorted.length && sorted[hi].t_on <= t + CONTEXT_SEC) hi++;

    const context: number[] = [];
    for (let i = lo; i < hi; i++) context.push(Math.trunc(sorted[i].pitch));
    const candidate = bestBoundary(context);
    if (candidate !== null) {
      boundary = BOUNDARY_INERTIA * boundary + (1 - BOUNDARY_INERTIA) * candidate;
    }

    // Rounded before it is used, so the line sits between two keys instead of
    // inside one -- a fractional boundary would put the same pitch on either
    // side of itself as it drifted by hundredths.
    const cut = Math.round(boundary);
    for (const note of group) {
      out.push({ ...note, hand: Math.trunc(note.pitch) < cut ? "L" : "R" });
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
