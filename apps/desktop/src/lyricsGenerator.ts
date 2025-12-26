export type GeneratedLyricsFile = {
  format: "psalms_karaoke_json_v1";
  // Optional per-schema; we omit it for line-level fallbacks.
  granularity?: "syllable" | "word";
  job_id?: string;
  lines: Array<{ start: number; end: number; text: string; chunks?: never }>;
};

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function normalizeLinesFromText(raw: string): string[] {
  // Basic: split into non-empty lines. (We can later support timestamps, section headers, etc.)
  return raw
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/**
 * MVP lyrics generator.
 *
 * Produces a `features/lyrics.json` compatible with our schema, by distributing lines uniformly
 * across the song duration.
 */
export function generateLyricsJsonFromPlainText(opts: {
  lyricsText: string;
  durationSec: number;
  jobId?: string;
}): GeneratedLyricsFile {
  const durationSec = Number(opts.durationSec);
  if (!Number.isFinite(durationSec) || durationSec <= 0) {
    throw new Error("durationSec must be > 0");
  }

  const lines = normalizeLinesFromText(opts.lyricsText);
  if (!lines.length) {
    throw new Error("lyrics text contained no non-empty lines");
  }

  const perLine = durationSec / lines.length;

  return {
    format: "psalms_karaoke_json_v1",
    job_id: opts.jobId,
    lines: lines.map((text, i) => {
      const start = clamp(i * perLine, 0, durationSec);
      const end = clamp((i + 1) * perLine, 0, durationSec);
      return { start, end, text };
    })
  };
}
