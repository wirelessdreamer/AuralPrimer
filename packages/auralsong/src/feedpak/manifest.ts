/**
 * Typed view of a feedpak `manifest.yaml` (validated as JSON).
 *
 * Only the fields AuralPrimer reads are typed explicitly; the index
 * signature `[k: string]: unknown` lets every other spec field AND the
 * AuralPrimer extension keys (`aural_*`) survive a parse round-trip
 * untouched, so a Reader never silently drops data it doesn't model.
 *
 * Spec: feedpak v1.11.0 — see packages/feedpak/schemas/manifest.schema.json.
 */

/** A single arrangement entry (`arrangements[]`). */
export interface FeedpakArrangement {
  id: string;
  name?: string;
  /** Arrangement type hint, e.g. "piano", "guitar". */
  type?: string;
  /** Relative path to an arrangement file (POSIX, inside the package). */
  file?: string;
  /** Relative path to a notation document (POSIX). */
  notation?: string;
  /** MIDI note per string, low→high. */
  tuning?: number[];
  capo?: number;
  centOffset?: number;

  [k: string]: unknown;
}

/** A single stem entry (`stems[]`). */
export interface FeedpakStem {
  id: string;
  /** Relative path to the stem audio (POSIX). */
  file: string;
  codec?: string;
  language?: string;
  /** Spec allows boolean or a truthy/falsy string. */
  default?: boolean | string;

  [k: string]: unknown;
}

/** A single lyric track entry (`lyric_tracks[]`). */
export interface FeedpakLyricTrack {
  id: string;
  /** Relative path to the lyric file (POSIX). */
  file: string;
  /** BCP 47 language tag. */
  language: string;
  /** Track kind: "original" | "transliteration" | "translation" | (open). */
  kind: string;
  lyrics_source?: "authored" | "transcribed" | "user";
  stem?: string;
  name?: string;

  [k: string]: unknown;
}

/**
 * The feedpak manifest.
 *
 * Documented fields cover what AuralPrimer consumes plus the common spec
 * fields; the index signature preserves everything else, including the
 * `aural_*` extension keys this package cares about.
 */
export interface FeedpakManifest {
  /** Format version (semver). Absent is treated as "1.0.0" by the spec. */
  feedpak_version?: string;
  title: string;
  artist: string;
  album?: string;
  year?: number;
  language?: string;
  duration: number;

  arrangements: FeedpakArrangement[];
  stems: FeedpakStem[];

  /** Relative path to the song timeline document (POSIX). */
  song_timeline?: string;

  lyrics?: string;
  lyric_tracks?: FeedpakLyricTrack[];
  vocal_pitch?: string;
  pitch_extraction?: Record<string, unknown>;
  vocal_pitch_contour?: string;
  drum_tab?: string;
  keys?: string;
  harmony?: string;

  // --- AuralPrimer extension keys (preserved, never stripped) ---
  /** Relative path to transcribed notes MIDI. */
  aural_notes_mid?: string;
  /** Relative path to a precomputed spectrogram asset. */
  aural_spectrogram?: string;
  /** Map of refine role -> relative candidate path. */
  aural_refine_candidates?: Record<string, string>;
  /** Map of fretted role -> relative fingering metadata path. */
  aural_fingering?: Record<string, string>;
  /** Relative path to a benchmark/eval artifact. */
  aural_benchmark?: string;
  /** Embedded pipeline-config object. */
  aural_pipeline?: Record<string, unknown>;

  [k: string]: unknown;
}

/**
 * Structural guard for a feedpak manifest. Checks only the fields the
 * spec marks required (`title`, `artist`, `duration`, `arrangements`,
 * `stems`); use {@link validateFeedpakManifest} for full schema validation.
 */
export function isFeedpakManifest(x: unknown): x is FeedpakManifest {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.title === "string" &&
    typeof o.artist === "string" &&
    typeof o.duration === "number" &&
    Array.isArray(o.arrangements) &&
    Array.isArray(o.stems)
  );
}
