export interface AuralSongManifest {
  schema_version: string;
  song_id: string;
  title: string;
  artist: string;
  duration_sec: number;

  // allow forward-compat without typing the entire schema yet
  [k: string]: unknown;
}

export function isAuralSongManifest(x: unknown): x is AuralSongManifest {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.schema_version === "string" &&
    typeof o.song_id === "string" &&
    typeof o.title === "string" &&
    typeof o.artist === "string" &&
    typeof o.duration_sec === "number"
  );
}
