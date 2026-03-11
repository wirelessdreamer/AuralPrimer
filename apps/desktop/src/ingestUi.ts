import type { IngestImportRequest, IngestSubcommand } from "./ingestClient";

export type IngestFormState = {
  sourcePath: string;
  mode: IngestSubcommand;
  outSongpackPath?: string;
  profile?: string;
  config?: string;
  title?: string;
  artist?: string;
  drumFilter?: string;
  melodicMethod?: string;
  shiftsText?: string;
  multiFilter: boolean;
};

function nonEmpty(value?: string): string | undefined {
  const s = (value ?? "").trim();
  return s ? s : undefined;
}

function parseShifts(shiftsText?: string): number {
  const raw = (shiftsText ?? "").trim();
  if (!raw) return 1;
  if (!/^\d+$/.test(raw)) throw new Error("shifts must be an integer >= 1");
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n) || n < 1) throw new Error("shifts must be an integer >= 1");
  return n;
}

function basenameFromPath(path: string): string {
  const trimmed = path.trim().replace(/[\\/]+$/g, "");
  if (!trimmed) return "";
  const slash = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
}

function stripKnownIngestExtension(name: string): string {
  return name.replace(/\.(wav|mp3|ogg|flac|m4a|aif|aiff|aac|opus|wma|dtx)$/i, "");
}

function normalizeFilenameStem(raw: string): string {
  return raw
    .replace(/_/g, " ")
    .replace(/^\s*\d{1,3}\s*[-_. )]+\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function stripTrailingTag(raw: string): string {
  return raw
    .replace(/\s*\[[^\]]+\]\s*$/g, "")
    .replace(/\s*\([^)]+\)\s*$/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function isTrackIndexToken(raw: string): boolean {
  return /^(track\s*)?\d+$/i.test(raw.trim());
}

export function inferIngestTitleArtistFromSourcePath(sourcePath: string): {
  title?: string;
  artist?: string;
} {
  const base = basenameFromPath(sourcePath);
  if (!base) return {};

  const normalized = normalizeFilenameStem(stripKnownIngestExtension(base));
  if (!normalized) return {};

  const parts = normalized
    .split(/\s+-\s+/)
    .map(stripTrailingTag)
    .filter((p) => p.length > 0);

  if (parts.length >= 2) {
    if (isTrackIndexToken(parts[0]) && parts.length >= 3) {
      const artist = parts[1];
      const title = parts.slice(2).join(" - ").trim();
      return {
        title: title || undefined,
        artist: artist || undefined
      };
    }

    const artist = parts[0];
    const title = parts.slice(1).join(" - ").trim();
    return {
      title: title || undefined,
      artist: artist || undefined
    };
  }

  return { title: stripTrailingTag(normalized) || undefined };
}

export function buildIngestRequestFromForm(state: IngestFormState): IngestImportRequest {
  const sourcePath = state.sourcePath.trim();
  if (!sourcePath) throw new Error("source path is required");

  const req: IngestImportRequest = {
    source_path: sourcePath,
    subcommand: state.mode,
    out_songpack_path: nonEmpty(state.outSongpackPath),
    profile: nonEmpty(state.profile) ?? "full",
    config: nonEmpty(state.config),
    title: nonEmpty(state.title),
    artist: nonEmpty(state.artist),
    drum_filter: nonEmpty(state.drumFilter),
    melodic_method: nonEmpty(state.melodicMethod),
    shifts: parseShifts(state.shiftsText),
    multi_filter: state.multiFilter
  };

  return req;
}

