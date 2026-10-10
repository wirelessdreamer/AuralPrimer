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

export function songKeyFor(artifacts: { harmony?: unknown; keys?: unknown }, notes: MelodicNote[]): ResolvedSongKey | null {
  const authored = keySignatureFromSongArtifacts({ harmony: artifacts.harmony, keys: artifacts.keys });
  if (authored) return { pitchClass: authored.pitchClass, mode: authored.mode, label: authored.label };
  const inferred = notes.length > 0 ? inferKeySignature(notes) : null;
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
