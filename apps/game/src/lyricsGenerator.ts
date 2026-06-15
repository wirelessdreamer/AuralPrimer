// Lyrics generation (creating features/lyrics.json from a plain-text file) is
// content creation and now lives in AuralStudio. AuralPrimer (the gameplay
// app) renders lyrics from features/lyrics.json when present and otherwise
// renders without lyrics. See `spec.md` section 1.1.

export type LyricsFile = {
  format: string;
  granularity?: string;
  job_id?: string;
  lines: Array<{
    start: number;
    end: number;
    text: string;
    chunks?: Array<{
      start: number;
      end: number;
      text: string;
      char_start: number;
      char_end: number;
    }>;
  }>;
};
