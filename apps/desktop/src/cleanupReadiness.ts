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

// Melodic stem roles we probe for (drums/vocals are never melodic targets).
export const MELODIC_ROLES = ["keys", "bass", "lead_guitar", "rhythm_guitar"] as const;
export type MelodicRole = (typeof MELODIC_ROLES)[number];

export const MELODIC_ROLE_LABELS: Record<string, string> = {
  keys: "Keys",
  bass: "Bass",
  lead_guitar: "Lead guitar",
  rhythm_guitar: "Rhythm guitar",
  melodic: "Melodic",
};

export type RoleReadiness = { spectrogram: boolean; candidates: boolean };
export type RowReady = { spec: boolean; cand: boolean; lyr: boolean };

export type SidecarRunResult = {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  payload?: unknown;
};

export type SpectroOutcome = "ok" | "nostem" | "error";

/** feedpak relocates feature artifacts under aural/ (legacy: features/). */
export function featureDir(containerPath: string): "aural" | "features" {
  return containerPath.endsWith(".feedpak") ? "aural" : "features";
}

// Caches keyed by pack (+ role). Invalidated after a build/compute so the
// readout re-checks freshly written artifacts.
const cleanupReadinessCache = new Map<string, RoleReadiness>();
const stemRolesCache = new Map<string, string[]>();

function cleanupCacheKey(pack: string, role: string): string {
  return `${pack}|${role}`;
}

export function invalidateCleanupCache(pack: string, role?: string): void {
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
  const [spectrogram, candidates] = await Promise.all([
    auralsongJsonExists(pack, `${fd}/spectrogram/${role}/spectrogram.json`),
    auralsongJsonExists(pack, `${fd}/refine_candidates.${role}.json`),
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
  if (roles.length === 0) {
    const stemRoles = await melodicStemRoles(pack);
    const chosen = stemRoles.length ? stemRoles : ["keys"];
    for (const role of chosen) {
      roles.push(role);
      if (!readiness.has(role)) {
        readiness.set(role, await getRoleReadiness(pack, role));
      }
    }
  }
  const primary = roles.includes("keys") ? "keys" : roles[0]!;
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
