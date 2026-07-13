/**
 * Cleanup & Edit readiness + stem-detection logic, extracted from main.ts so it
 * can be unit-tested without booting the whole app (importing main.ts runs the
 * entire Studio: DOM grabs, editor construction, listeners — untestable).
 *
 * The only side effects here are Tauri `invoke` reads, which tests mock. This
 * is the logic that has actually broken (defaulting builds to a non-existent
 * "keys" stem; mis-classifying a stem-less pack as a hard "failed"), so it's
 * exactly what needs coverage.
 */

import { invoke } from "@tauri-apps/api/core";
import { featureDir as packFeatureDir, isManifestPack } from "@auralprimer/auralsong/packKind";

// Melodic stem roles we probe for (drums use the lane editor instead).
// One guitar (the lead/rhythm split was a frequency-tilt, not real separation,
// so the two were ~95% identical). Lead/rhythm labels are kept for any legacy
// packs not yet migrated.
export const MELODIC_ROLES = ["keys", "bass", "guitar", "vocals"] as const;
export type MelodicRole = (typeof MELODIC_ROLES)[number];

export const MELODIC_ROLE_LABELS: Record<string, string> = {
  keys: "Keys",
  bass: "Bass",
  guitar: "Guitar",
  lead_guitar: "Lead guitar",
  rhythm_guitar: "Rhythm guitar",
  melodic: "Melodic",
  drums: "Drums",
};

export type RoleReadiness = { spectrogram: boolean; candidates: boolean };
export type RowReady = {
  spec: boolean;
  cand: boolean;
  lyr: boolean;
  /**
   * True when a manifest pack (sloppak/feedpak) declares arrangements but has
   * no derived `aural/notes.mid` yet — the row should offer "Prep notes"
   * before the spectrogram/candidate steps. Optional so legacy consumers
   * (and the sort-by column keys, which only cover spec/cand/lyr) are
   * unaffected.
   */
  prep?: boolean;
};

export type SidecarRunResult = {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  payload?: unknown;
};

export type SpectroOutcome = "ok" | "nostem" | "error";

/**
 * feedpak/sloppak relocate feature artifacts under aural/ (legacy: features/).
 * Re-exported from the shared @auralprimer/auralsong/packKind helper (C2) so
 * every studio site agrees on the mapping.
 */
export function featureDir(containerPath: string): "aural" | "features" {
  return packFeatureDir(containerPath);
}

// Caches keyed by pack (+ role). Invalidated after a build/compute so the
// readout re-checks freshly written artifacts.
const cleanupReadinessCache = new Map<string, RoleReadiness>();
const stemRolesCache = new Map<string, string[]>();
const drumTabRelPathCache = new Map<string, string>();

function cleanupCacheKey(pack: string, role: string): string {
  return `${pack}|${role}`;
}

export function invalidateCleanupCache(pack: string, role?: string): void {
  if (!role) drumTabRelPathCache.delete(pack);
  if (role) {
    cleanupReadinessCache.delete(cleanupCacheKey(pack, role));
    return;
  }
  for (const key of Array.from(cleanupReadinessCache.keys())) {
    if (key.startsWith(`${pack}|`)) cleanupReadinessCache.delete(key);
  }
}

/** Test seam: clears the in-memory caches (so unit tests are independent). */
export function _resetReadinessCachesForTest(): void {
  cleanupReadinessCache.clear();
  stemRolesCache.clear();
  drumTabRelPathCache.clear();
}

/**
 * Shape of `get_auralsong_details` fields this module reads. The Rust lane
 * owns the authoritative struct (CONTRACT C6); we consume every field
 * DEFENSIVELY (all optional, feature-detected) so a rename on the Rust side
 * degrades to "no prep offered" rather than a crash.
 *
 * `has_notes_mid` already exists. For arrangement readiness we accept EITHER
 * a numeric count field (any of the names below) OR a raw `arrangements`
 * list, and finally fall back to `manifest_raw.arrangements`.
 */
export type AuralSongDetails = {
  has_notes_mid?: boolean;
  /** Authoritative field name emitted by the Rust `get_auralsong_details`. */
  arrangements_count?: number;
  arrangement_count?: number;
  arrangements?: unknown;
  /**
   * manifest `lyrics` pointer (rel path); sloppak lyrics live at pack root.
   * `lyrics_rel` is the authoritative Rust field; the others are accepted as
   * fallbacks so a rename degrades gracefully.
   */
  lyrics_rel?: string;
  lyrics_rel_path?: string;
  lyrics_path?: string;
  manifest_raw?: {
    arrangements?: unknown;
    drum_tab?: unknown;
    artifacts?: unknown;
    lyrics?: unknown;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
};

/**
 * Count the arrangements a pack's manifest declares, reading whichever field
 * the Rust `get_auralsong_details` summary exposes (feature-detected): an
 * explicit `arrangement_count`, a top-level `arrangements` array, else the
 * raw manifest's `arrangements`. Returns 0 when none are discoverable.
 */
export function arrangementCount(details: AuralSongDetails | null | undefined): number {
  if (!details) return 0;
  if (typeof details.arrangements_count === "number") return details.arrangements_count;
  if (typeof details.arrangement_count === "number") return details.arrangement_count;
  if (Array.isArray(details.arrangements)) return details.arrangements.length;
  const raw = details.manifest_raw?.arrangements;
  if (Array.isArray(raw)) return raw.length;
  return 0;
}

/**
 * True when a manifest pack declares arrangements but has no derived
 * `aural/notes.mid` yet — i.e. the studio should offer to derive melodic
 * gameplay notes from the arrangement wire JSONs (Phase 5 / the
 * `ingest_prep_arrangements` sidecar command).
 *
 * Only manifest packs (feedpak/sloppak) qualify; legacy `.auralsong` packs
 * have no arrangement wire format. Consumes `details.has_notes_mid`
 * defensively (an absent field is treated as "not present" so prep is
 * offered rather than silently skipped).
 */
export function needsArrangementPrep(
  pack: string,
  details: AuralSongDetails | null | undefined,
): boolean {
  if (!isManifestPack(pack)) return false;
  if (arrangementCount(details) <= 0) return false;
  return details?.has_notes_mid !== true;
}

/** True iff read_auralsong_json resolves for relPath (throws => artifact absent). */
export async function auralsongJsonExists(pack: string, relPath: string): Promise<boolean> {
  try {
    await invoke("read_auralsong_json", { containerPath: pack, relPath });
    return true;
  } catch {
    return false;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringRelPath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export async function drumTabRelPath(pack: string): Promise<string> {
  const cached = drumTabRelPathCache.get(pack);
  if (cached) return cached;
  let relPath = "drum_tab.json";
  if (isManifestPack(pack)) {
    try {
      const details = await invoke<AuralSongDetails>("get_auralsong_details", {
        containerPath: pack,
      });
      const raw = details.manifest_raw;
      const artifacts = isObject(raw?.artifacts) ? raw.artifacts : null;
      relPath =
        stringRelPath(raw?.drum_tab) ??
        stringRelPath(artifacts?.drum_tab) ??
        relPath;
    } catch {
      relPath = "drum_tab.json";
    }
  }
  drumTabRelPathCache.set(pack, relPath);
  return relPath;
}

export async function getRoleReadiness(
  pack: string,
  role: string,
  opts?: { force?: boolean },
): Promise<RoleReadiness> {
  const key = cleanupCacheKey(pack, role);
  if (!opts?.force) {
    const cached = cleanupReadinessCache.get(key);
    if (cached) return cached;
  }
  // feedpak relocates these artifacts under aural/ (legacy: features/).
  const fd = featureDir(pack);
  const drumTabRel = role === "drums" ? await drumTabRelPath(pack) : "drum_tab.json";
  const [spectrogram, candidates] = await Promise.all([
    auralsongJsonExists(pack, `${fd}/spectrogram/${role}/spectrogram.json`),
    // Drums have no candidate system; the manifest-declared drum_tab artifact is
    // the source of truth the lane editor edits, so it plays the "candidates"
    // role here.
    role === "drums"
      ? auralsongJsonExists(pack, drumTabRel)
      : auralsongJsonExists(pack, `${fd}/refine_candidates.${role}.json`),
  ]);
  const readiness: RoleReadiness = { spectrogram, candidates };
  cleanupReadinessCache.set(key, readiness);
  return readiness;
}

/**
 * The melodic stem roles a pack actually has audio for, read from its manifest's
 * `stems` list. Empty when it has none (a mix-only demo, or an old import that
 * was never stem-split). This is what makes a guitar-only song build its guitar
 * stems instead of a bogus "keys".
 */
export async function melodicStemRoles(pack: string): Promise<string[]> {
  const cached = stemRolesCache.get(pack);
  if (cached) return cached;
  let out: string[] = [];
  try {
    const details = await invoke<{ manifest_raw?: unknown }>("get_auralsong_details", {
      containerPath: pack,
    });
    const raw = details.manifest_raw as { stems?: Array<{ id?: string }> } | null | undefined;
    const ids = new Set(
      (raw?.stems ?? [])
        .map((s) => s?.id)
        .filter((x): x is string => typeof x === "string"),
    );
    out = (MELODIC_ROLES as readonly string[]).filter((r) => ids.has(r));
  } catch {
    out = [];
  }
  stemRolesCache.set(pack, out);
  return out;
}

/**
 * Determine a pack's melodic stems. Roles with a built spectrogram/candidate
 * artifact win; otherwise we offer the melodic stems the pack actually has (per
 * its manifest), falling back to "keys" only when it lists none — so the build
 * always targets real audio rather than a hardcoded role.
 */
export async function detectMelodicStems(
  pack: string,
): Promise<{ roles: string[]; primary: string; readiness: Map<string, RoleReadiness> }> {
  const readiness = new Map<string, RoleReadiness>();
  const present: string[] = [];
  await Promise.all(
    MELODIC_ROLES.map(async (role: MelodicRole) => {
      const r = await getRoleReadiness(pack, role);
      readiness.set(role, r);
      if (r.spectrogram || r.candidates) present.push(role);
    }),
  );
  const roles = MELODIC_ROLES.filter((r) => present.includes(r)) as string[];
  // Drums are a first-class cleanup instrument (lane editor over the drums
  // spectrogram) whenever the pack carries a drum tab — no melodic stem needed.
  const drums = await getRoleReadiness(pack, "drums");
  readiness.set("drums", drums);
  const hasDrums = drums.candidates; // manifest drum_tab present
  if (roles.length === 0 && !hasDrums) {
    // No melodic artifacts AND no drums — offer the melodic stems the pack has
    // (or "keys" as a last resort) so a build targets real audio.
    const stemRoles = await melodicStemRoles(pack);
    const chosen = stemRoles.length ? stemRoles : ["keys"];
    for (const role of chosen) {
      roles.push(role);
      if (!readiness.has(role)) {
        readiness.set(role, await getRoleReadiness(pack, role));
      }
    }
  }
  if (hasDrums) roles.push("drums");
  // Prefer a role that actually has content (candidates, or a drum tab) over one
  // that only has a spectrogram — a guitar song whose "keys" stem is just bleed
  // gates to zero notes, so it shouldn't become the primary / default editor.
  const withCandidates = roles.filter((r) => readiness.get(r)?.candidates);
  const pool = withCandidates.length ? withCandidates : roles;
  const primary = pool.includes("keys") ? "keys" : pool[0]!;
  return { roles, primary, readiness };
}

/** Parse the trailing JSON status line from a sidecar run's stdout. */
export function parseSidecarStatusLine(stdout: string): Record<string, unknown> | null {
  try {
    const lines = stdout.trim().split(/\r?\n/);
    const parsed = JSON.parse(lines[lines.length - 1] ?? "{}");
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * Classify a spectrogram build result: "ok" | "nostem" (no separated melodic
 * stem to build from — e.g. a mix-only demo) | "error" (a real failure). The
 * sidecar reports a stem-less pack as ok:false with an empty roles object and
 * no stderr, which we'd otherwise surface as a scary "failed".
 */
export function classifySpectroResult(res: SidecarRunResult): SpectroOutcome {
  if (res.ok) return "ok";
  const parsed = parseSidecarStatusLine(res.stdout);
  const roles = (parsed?.roles ?? {}) as Record<string, unknown>;
  if (Object.keys(roles).length === 0 && !res.stderr.trim()) return "nostem";
  return "error";
}

/** Per-instrument outcome of a refine-candidates run. A silent stem ("no
 * audible content" after the silence gate) is a benign SKIP, not a failure —
 * band songs legitimately carry an empty keys stem, and treating that as an
 * error made "Prep all unbuilt" look broken. */
export type CandidateOutcome = { built: string[]; skipped: string[]; failed: string[] };

export function classifyCandidateResult(
  res: SidecarRunResult,
  requested: string[],
): CandidateOutcome {
  const parsed = parseSidecarStatusLine(res.stdout);
  const instruments = (parsed?.instruments ?? {}) as Record<
    string,
    { ok?: boolean; error?: string }
  >;
  const built: string[] = [];
  const skipped: string[] = [];
  const failed: string[] = [];
  for (const role of requested) {
    const r = instruments[role];
    if (r?.ok) built.push(role);
    else if (typeof r?.error === "string" && /no audible content/i.test(r.error)) skipped.push(role);
    else if (res.ok && !r) built.push(role); // top-level ok, no per-role detail
    else failed.push(role);
  }
  return { built, skipped, failed };
}
