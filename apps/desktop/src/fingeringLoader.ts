import { invoke } from "@tauri-apps/api/core";
import type { InstrumentRole, MelodicTrackSelection } from "./chartLoader";

export type FingeringRole = "bass" | "guitar" | "rhythm_guitar" | "lead_guitar" | "keys" | "vocals" | "melodic";

export const FINGERING_ROLES: readonly FingeringRole[] = [
  "bass",
  "guitar",
  "rhythm_guitar",
  "lead_guitar",
  "keys",
  "vocals",
  "melodic",
] as const;

const VALID_ROLES = new Set<FingeringRole>(FINGERING_ROLES);

export type FingeringNote = {
  t_on: number;
  t_off?: number;
  pitch: number;
  velocity?: number;
  string: number;
  fret: number;
};

export type FingeringFile = {
  version?: string;
  instrument: FingeringRole;
  notes: FingeringNote[];
};

export type VisualizerSongNote = {
  t_on: number;
  t_off?: number;
  pitch: number;
  velocity?: number;
  string?: number;
  fret?: number;
  s?: number;
  f?: number;
  role?: string;
  instrument?: string;
  channel?: number;
  trackName?: string;
};

type ValidationResult =
  | { ok: true; value: FingeringFile }
  | { ok: false; errors: Array<{ path: string; message: string }> };

type FingeringLoaderDeps = {
  warn: (channel: "play", message: string, detail?: unknown) => void;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function intInRange(value: unknown, min: number, max: number): number | null {
  const n = numberOrNull(value);
  if (n === null || !Number.isInteger(n) || n < min || n > max) return null;
  return n;
}

function roleOrNull(value: unknown): FingeringRole | null {
  return typeof value === "string" && VALID_ROLES.has(value as FingeringRole) ? (value as FingeringRole) : null;
}

function isGuitarRole(role: string): boolean {
  return role === "lead_guitar" || role === "rhythm_guitar";
}

function fingeringRoleMatchesExpected(role: FingeringRole, expectedRole: FingeringRole): boolean {
  return role === expectedRole || (role === "guitar" && isGuitarRole(expectedRole));
}

function candidateRoleKeys(role: FingeringRole): FingeringRole[] {
  return isGuitarRole(role) ? [role, "guitar"] : [role];
}

function manifestFingeringPath(manifestRaw: unknown, role: FingeringRole): string | null {
  if (!isObject(manifestRaw)) return null;
  const paths = manifestRaw.aural_fingering;
  if (!isObject(paths)) return null;
  const relPath = paths[role];
  return typeof relPath === "string" && relPath.trim() ? relPath : null;
}

export function fingeringRolesFromManifest(manifestRaw: unknown): FingeringRole[] {
  if (!isObject(manifestRaw) || !isObject(manifestRaw.aural_fingering)) return [];
  return Object.keys(manifestRaw.aural_fingering)
    .map(roleOrNull)
    .filter((role): role is FingeringRole => role !== null);
}

export function candidateFingeringPaths(role: FingeringRole, manifestRaw?: unknown): string[] {
  const out: string[] = [];
  const roleKeys = candidateRoleKeys(role);
  for (const key of roleKeys) {
    const manifestPath = manifestFingeringPath(manifestRaw, key);
    if (manifestPath) out.push(manifestPath);
  }
  for (const key of roleKeys) {
    out.push(`aural/fingering.${key}.json`, `features/fingering.${key}.json`);
  }
  return [...new Set(out)];
}

export function validateFingering(raw: unknown, expectedRole?: FingeringRole): ValidationResult {
  const errors: Array<{ path: string; message: string }> = [];
  if (!isObject(raw)) {
    return { ok: false, errors: [{ path: "$", message: "expected object" }] };
  }

  if (raw.version !== undefined && typeof raw.version !== "string") {
    errors.push({ path: "$.version", message: "expected string" });
  }

  const instrument = roleOrNull(raw.instrument);
  if (!instrument) {
    errors.push({ path: "$.instrument", message: "unknown instrument" });
  } else if (expectedRole !== undefined && !fingeringRoleMatchesExpected(instrument, expectedRole)) {
    errors.push({ path: "$.instrument", message: `expected ${expectedRole}` });
  }

  const rawNotes = Array.isArray(raw.notes) ? raw.notes : undefined;
  if (rawNotes === undefined) {
    errors.push({ path: "$.notes", message: "expected array" });
  }
  if (errors.length > 0 || rawNotes === undefined || !instrument) return { ok: false, errors };

  const notes: FingeringNote[] = [];
  for (let i = 0; i < rawNotes.length; i += 1) {
    const item = rawNotes[i];
    const path = `$.notes[${i}]`;
    if (!isObject(item)) {
      errors.push({ path, message: "expected object" });
      continue;
    }

    const tOn = numberOrNull(item.t_on);
    const tOff = item.t_off === undefined ? undefined : numberOrNull(item.t_off);
    const pitch = intInRange(item.pitch, 0, 127);
    const velocity = item.velocity === undefined ? undefined : intInRange(item.velocity, 0, 127);
    const stringIdx = intInRange(item.string ?? item.s, 0, 8);
    const fret = intInRange(item.fret ?? item.f, 0, 36);

    if (tOn === null || tOn < 0) errors.push({ path: `${path}.t_on`, message: "expected number >= 0" });
    if (tOff === null) errors.push({ path: `${path}.t_off`, message: "expected number" });
    if (tOn !== null && typeof tOff === "number" && tOff <= tOn) {
      errors.push({ path: `${path}.t_off`, message: "must be > t_on" });
    }
    if (pitch === null) errors.push({ path: `${path}.pitch`, message: "expected integer 0..127" });
    if (item.velocity !== undefined && velocity === null) {
      errors.push({ path: `${path}.velocity`, message: "expected integer 0..127" });
    }
    if (stringIdx === null) errors.push({ path: `${path}.string`, message: "expected integer 0..8" });
    if (fret === null) errors.push({ path: `${path}.fret`, message: "expected integer 0..36" });

    if (tOn !== null && pitch !== null && stringIdx !== null && fret !== null) {
      notes.push({
        t_on: tOn,
        t_off: typeof tOff === "number" ? tOff : undefined,
        pitch,
        velocity: velocity ?? undefined,
        string: stringIdx,
        fret,
      });
    }
  }

  if (errors.length > 0) return { ok: false, errors };
  notes.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch || a.string - b.string || a.fret - b.fret);
  return {
    ok: true,
    value: {
      version: typeof raw.version === "string" ? raw.version : undefined,
      instrument,
      notes,
    },
  };
}

export async function loadFingeringForRoles(
  containerPath: string,
  roles: readonly FingeringRole[],
  deps: FingeringLoaderDeps,
  manifestRaw?: unknown,
): Promise<FingeringFile[]> {
  const out: FingeringFile[] = [];
  for (const role of [...new Set(roles)]) {
    let raw: unknown;
    for (const relPath of candidateFingeringPaths(role, manifestRaw)) {
      try {
        raw = await invoke<unknown>("read_auralsong_json", { containerPath, relPath });
        break;
      } catch {
        raw = undefined;
      }
    }
    if (raw == null) continue;

    const result = validateFingering(raw, role);
    if (!result.ok) {
      deps.warn("play", `fingering.${role}.json failed validation; ignoring`, result.errors);
      continue;
    }
    out.push(result.value);
  }
  return out;
}

function roleTrackName(role: FingeringRole): string {
  switch (role) {
    case "bass":
      return "Bass";
    case "guitar":
      return "Guitar";
    case "rhythm_guitar":
      return "Rhythm Guitar";
    case "lead_guitar":
      return "Lead Guitar";
    case "keys":
      return "Keys";
    case "vocals":
      return "Vocals";
    default:
      return "Melodic";
  }
}

function roleChannel(role: FingeringRole): number {
  switch (role) {
    case "bass":
      return 0;
    case "guitar":
      return 2;
    case "rhythm_guitar":
      return 1;
    case "lead_guitar":
      return 2;
    case "keys":
      return 3;
    case "vocals":
      return 5;
    default:
      return 4;
  }
}

function concreteTrackRole(role: FingeringRole): InstrumentRole {
  return role === "guitar" ? "lead_guitar" : role;
}

export function fingeringFilesToMelodicTracks(files: readonly FingeringFile[]): MelodicTrackSelection[] {
  const byRole = new Map<InstrumentRole, MelodicTrackSelection>();
  for (const file of files) {
    const role = concreteTrackRole(file.instrument);
    const existing = byRole.get(role);
    const track =
      existing ??
      {
        role,
        trackName: roleTrackName(file.instrument),
        channel: roleChannel(file.instrument),
        notes: [],
      };

    for (const note of file.notes) {
      track.notes.push({
        t_on: note.t_on,
        t_off: Math.max(note.t_on + 0.06, typeof note.t_off === "number" ? note.t_off : note.t_on + 0.15),
        pitch: note.pitch,
        velocity: Math.max(0, Math.min(1, (note.velocity ?? 100) / 127)),
        string: note.string,
        fret: note.fret,
        s: note.string,
        f: note.fret,
      });
    }

    byRole.set(role, track);
  }

  const order: InstrumentRole[] = ["bass", "rhythm_guitar", "lead_guitar", "keys", "melodic", "vocals"];
  const tracks = [...byRole.values()];
  for (const track of tracks) {
    track.notes.sort(
      (a, b) =>
        a.t_on - b.t_on ||
        a.pitch - b.pitch ||
        (a.string ?? 0) - (b.string ?? 0) ||
        (a.fret ?? 0) - (b.fret ?? 0),
    );
  }
  tracks.sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role));
  return tracks.filter((track) => track.notes.length > 0);
}

export function fingeringFilesToVisualizerNotes(files: readonly FingeringFile[]): VisualizerSongNote[] {
  const out: VisualizerSongNote[] = [];
  for (const file of files) {
    const trackName = roleTrackName(file.instrument);
    const channel = roleChannel(file.instrument);
    for (const note of file.notes) {
      out.push({
        t_on: note.t_on,
        t_off: note.t_off,
        pitch: note.pitch,
        velocity: note.velocity,
        string: note.string,
        fret: note.fret,
        s: note.string,
        f: note.fret,
        role: file.instrument,
        instrument: file.instrument,
        channel,
        trackName,
      });
    }
  }
  out.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch || (a.string ?? 0) - (b.string ?? 0) || (a.fret ?? 0) - (b.fret ?? 0));
  return out;
}

function noteMergeKey(note: VisualizerSongNote): string {
  return `${note.channel ?? -1}:${Math.round(note.t_on * 10_000)}:${note.pitch}`;
}

export function mergeFingeringIntoVisualizerNotes(
  baseNotes: readonly VisualizerSongNote[],
  fingeringNotes: readonly VisualizerSongNote[],
): VisualizerSongNote[] {
  if (fingeringNotes.length === 0) return [...baseNotes];
  if (baseNotes.length === 0) return [...fingeringNotes];

  const byKey = new Map<string, VisualizerSongNote[]>();
  for (const note of fingeringNotes) {
    const key = noteMergeKey(note);
    const list = byKey.get(key);
    if (list) list.push(note);
    else byKey.set(key, [note]);
  }

  const merged = baseNotes.map((note) => {
    const match = byKey.get(noteMergeKey(note))?.shift();
    if (!match) return { ...note };
    return {
      ...note,
      string: match.string,
      fret: match.fret,
      s: match.s,
      f: match.f,
    };
  });

  for (const remaining of byKey.values()) {
    merged.push(...remaining);
  }
  merged.sort((a, b) => a.t_on - b.t_on || a.pitch - b.pitch || (a.channel ?? -1) - (b.channel ?? -1));
  return merged;
}
