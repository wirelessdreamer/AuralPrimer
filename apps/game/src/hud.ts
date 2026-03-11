export type KeyMode = {
  key: string;
  mode: string;
};

const DEFAULT_KEY_MODE: KeyMode = { key: "C", mode: "major" };

function normalizeKey(key: unknown): string | null {
  if (typeof key !== "string") return null;
  const k = key.trim();
  if (!k) return null;
  // Keep permissive for now; later we can constrain to [A-G][b#]?
  return k;
}

function normalizeMode(mode: unknown): string | null {
  if (typeof mode !== "string") return null;
  const m = mode.trim().toLowerCase();
  if (!m) return null;

  // Normalize common values.
  if (m === "maj") return "major";
  if (m === "min") return "minor";
  return m;
}

/**
 * Extract key/mode from a SongPack manifest (best-effort).
 *
 * For now our fixtures don’t include harmonic metadata, so this intentionally
 * falls back to a stable placeholder.
 */
export function extractKeyModeFromManifest(manifestRaw: unknown): KeyMode {
  if (!manifestRaw || typeof manifestRaw !== "object") return DEFAULT_KEY_MODE;
  const m = manifestRaw as any;

  // Future-proof: support a few likely locations.
  const key = normalizeKey(m.key) ?? normalizeKey(m.tonic) ?? normalizeKey(m.harmony?.key) ?? normalizeKey(m.harmony?.tonic);
  const mode = normalizeMode(m.mode) ?? normalizeMode(m.harmony?.mode) ?? normalizeMode(m.scale?.mode);

  return {
    key: key ?? DEFAULT_KEY_MODE.key,
    mode: mode ?? DEFAULT_KEY_MODE.mode,
  };
}

export function formatKeyMode(km: KeyMode): string {
  return `${km.key} ${km.mode}`;
}
