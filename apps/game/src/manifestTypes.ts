/**
 * Shared TS types for AuralSong manifest data crossing module boundaries.
 *
 * Extracted from main.ts so that decomposed feature modules
 * (`songLibraryPanel`, etc.) can share these types without circular
 * imports or duplicated definitions. Keep this file minimal — only the
 * types that actually need to be shared across modules.
 *
 * The Rust backend returns these shapes verbatim from `scan_auralsongs`,
 * `get_auralsong_details`, etc.
 */

export type ManifestSummary = {
  schema_version?: string;
  song_id?: string;
  title?: string;
  artist?: string;
  genre?: string;
  duration_sec?: number;
};
