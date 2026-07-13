export type KeyMode = {
  key: string;
  mode: string;
};

export type KeyModeArtifacts = {
  keys?: unknown | null;
  harmony?: unknown | null;
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

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function resolveCandidate(key: unknown, mode: unknown): KeyMode | null {
  const normalizedKey = normalizeKey(key);
  const normalizedMode = normalizeMode(mode);
  if (!normalizedKey && !normalizedMode) return null;
  return {
    key: normalizedKey ?? DEFAULT_KEY_MODE.key,
    mode: normalizedMode ?? DEFAULT_KEY_MODE.mode,
  };
}

function keyModeFromManifestObject(manifestRaw: unknown): KeyMode | null {
  if (!isObject(manifestRaw)) return null;
  const harmony = isObject(manifestRaw.harmony) ? manifestRaw.harmony : null;
  const scale = isObject(manifestRaw.scale) ? manifestRaw.scale : null;
  return (
    resolveCandidate(manifestRaw.key ?? manifestRaw.tonic, manifestRaw.mode) ??
    resolveCandidate(harmony?.key ?? harmony?.tonic, harmony?.mode) ??
    resolveCandidate(undefined, scale?.mode)
  );
}

function keyModeFromHarmonyDoc(harmonyDoc: unknown): KeyMode | null {
  if (!isObject(harmonyDoc)) return null;
  return resolveCandidate(
    harmonyDoc.key ?? harmonyDoc.tonic,
    harmonyDoc.mode ?? harmonyDoc.scale,
  );
}

function keyModeFromKeysDoc(keysDoc: unknown): KeyMode | null {
  if (!isObject(keysDoc) || !Array.isArray(keysDoc.events)) return null;
  for (const event of keysDoc.events) {
    if (!isObject(event)) continue;
    const km = resolveCandidate(event.key ?? event.tonic, event.scale ?? event.mode);
    if (km) return km;
  }
  return null;
}

/**
 * Extract key/mode from an AuralSong manifest (best-effort).
 *
 * Prefers explicit key/harmony artifacts when loaded, then falls back to a
 * stable default for legacy packs with no harmonic metadata.
 */
export function extractKeyModeFromManifest(
  manifestRaw: unknown,
  artifacts?: KeyModeArtifacts | null,
): KeyMode {
  return (
    keyModeFromManifestObject(manifestRaw) ??
    keyModeFromHarmonyDoc(artifacts?.harmony) ??
    keyModeFromKeysDoc(artifacts?.keys) ??
    DEFAULT_KEY_MODE
  );
}

export function hasExplicitKeyModeInManifest(
  manifestRaw: unknown,
  artifacts?: KeyModeArtifacts | null,
): boolean {
  return Boolean(
    keyModeFromManifestObject(manifestRaw) ??
      keyModeFromHarmonyDoc(artifacts?.harmony) ??
      keyModeFromKeysDoc(artifacts?.keys),
  );
}

export function formatKeyMode(km: KeyMode): string {
  return `${km.key} ${km.mode}`;
}
