/**
 * The song's key for the guitar views' Nashville numbers.
 *
 * The pack's own key first -- harmony.json, then keys.json, read exactly the
 * way viz-nashville reads them -- and only then a guess from the notes. Until
 * now the guitar parts had no key at all: inference only ever ran for the
 * piano roll. Null when neither says, and then the views show frets rather
 * than numbers counted from nowhere.
 */

import { keySignatureFromSongArtifacts } from "@auralprimer/viz-nashville";
import { inferKeySignature, type MelodicNote, type SongKey } from "@auralprimer/viz-tab";

/** A resolved key. `inferred` marks a guess from the notes, for anything that must say so. */
export type ResolvedSongKey = SongKey & { inferred?: boolean };

const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

/** Duration-weighted pitch-class totals. */
function pitchWeights(notes: MelodicNote[]): number[] {
  const w = Array<number>(12).fill(0);
  for (const n of notes) w[((n.pitch % 12) + 12) % 12] += Math.max(0.06, n.t_off - n.t_on);
  return w;
}

/**
 * The note the bass line leans on, when it clearly leans on one: the most
 * weight, at least 1.2 times the next. Null without a bass or a clear lean.
 */
function bassRoot(bassNotes: MelodicNote[]): number | null {
  if (bassNotes.length === 0) return null;
  const w = pitchWeights(bassNotes);
  const order = w.map((v, pc) => [v, pc] as const).sort((a, b) => b[0] - a[0]);
  return order[0][0] > 0 && order[0][0] >= 1.2 * order[1][0] ? order[0][1] : null;
}

/**
 * `bassNotes`, when the song has a bass part, settles the root. A modal song
 * fools a major/minor template: a funk tune in E dorian uses the D major
 * scale's notes, so the notes alone say "D major" and every E -- the song's
 * home -- would read as a 2. The bass sits on the home note, so when it
 * clearly leans on one that is the root, and the third above it says major or
 * minor.
 */
export function songKeyFor(
  artifacts: { harmony?: unknown; keys?: unknown },
  notes: MelodicNote[],
  bassNotes: MelodicNote[] = [],
): ResolvedSongKey | null {
  const authored = keySignatureFromSongArtifacts({ harmony: artifacts.harmony, keys: artifacts.keys });
  if (authored) return { pitchClass: authored.pitchClass, mode: authored.mode, label: authored.label };
  const inferred = notes.length > 0 ? inferKeySignature(notes) : null;
  const root = bassRoot(bassNotes);
  if (inferred && root !== null && root !== inferred.pitchClass) {
    const w = pitchWeights(notes);
    const mode = w[(root + 3) % 12] >= w[(root + 4) % 12] ? "minor" : "major";
    const names = inferred.noteLabelStyle === "flat" ? FLAT_NAMES : SHARP_NAMES;
    return { pitchClass: root, mode, label: `${names[root]} ${mode} (from the notes)`, inferred: true };
  }
  if (inferred) {
    return {
      pitchClass: inferred.pitchClass,
      mode: inferred.mode,
      label: `${inferred.label} (from the notes)`,
      inferred: true,
    };
  }
  return null;
}
