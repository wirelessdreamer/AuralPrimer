/**
 * Loads + parses the selected pack's notes MIDI and applies any
 * per-instrument refinement overlays. Returns BOTH the drum chart selection
 * AND the melodic tracks so the caller can write both into its state in one
 * shot.
 *
 * Pure (no module state) plus a Tauri invoke for the AuralSong MIDI blob.
 * Extracted from main.ts as Phase 2.T (was the old `readDrumChartSelection`
 * which mutated `selectedMelodicTracks` as a side effect).
 */

import { invoke } from "@tauri-apps/api/core";
import {
  selectDrumChartFromMidiBytes,
  selectMelodicTracksFromMidiBytes,
  applyRefinementsToMelodicTracks,
  type DrumChartSelection,
  type MelodicTrackSelection,
} from "./chartLoader";
import { loadRefinementsForRoles } from "./refinementLoader";
import {
  FINGERING_ROLES,
  applyFingeringToMelodicTracks,
  fingeringFilesToMelodicTracks,
  fingeringRolesFromManifest,
  loadFingeringForRoles,
} from "./fingeringLoader";
import { loadDrumChartFromTab } from "./drumTabChart";
import type { ConsoleBridge, ConsoleLogCategory } from "./consoleBridge";

type MidiBlob = { bytes: number[] };
type ResolvedMidiBlob = { midi: MidiBlob; relPath: string };

/** Minimum AuralSongDetails subset readDrumChartSelection needs. */
export type SongChartLoaderDetails = {
  has_notes_mid?: boolean;
  has_aural_fingering?: boolean;
  manifest_raw?: unknown;
};

export type SongChartSelection = {
  drumSelection: DrumChartSelection | null;
  melodicTracks: MelodicTrackSelection[];
};

export type ReadSongChartSelectionArgs = {
  containerPath: string;
  details: SongChartLoaderDetails;
  consoleBridge: ConsoleBridge;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function manifestStringPath(manifestRaw: unknown, key: string): string | null {
  if (!isObject(manifestRaw)) return null;
  const raw = manifestRaw[key];
  return typeof raw === "string" && raw.trim() ? raw : null;
}

function uniquePaths(paths: Array<string | null | undefined>): string[] {
  const out: string[] = [];
  for (const path of paths) {
    if (typeof path !== "string" || !path.trim()) continue;
    if (!out.includes(path)) out.push(path);
  }
  return out;
}

function notesMidCandidatePaths(details: SongChartLoaderDetails): string[] {
  return uniquePaths([
    manifestStringPath(details.manifest_raw, "aural_notes_mid"),
    "features/notes.mid",
    "aural/notes.mid",
  ]);
}

function drumTabRelPath(details: SongChartLoaderDetails): string {
  return manifestStringPath(details.manifest_raw, "drum_tab") ?? "drum_tab.json";
}

function hasFingeringSidecar(details: SongChartLoaderDetails): boolean {
  return Boolean(details.has_aural_fingering || fingeringRolesFromManifest(details.manifest_raw).length > 0);
}

function fingeringRolesForStandaloneTracks(details: SongChartLoaderDetails): typeof FINGERING_ROLES[number][] {
  const manifestRoles = fingeringRolesFromManifest(details.manifest_raw);
  return manifestRoles.length > 0 ? manifestRoles : [...FINGERING_ROLES];
}

async function readFirstNotesMid(containerPath: string, relPaths: string[]): Promise<ResolvedMidiBlob> {
  let lastError: unknown;
  for (const relPath of relPaths) {
    try {
      const midi = await invoke<MidiBlob>("read_auralsong_mid", { containerPath, relPath });
      if (!midi || !Array.isArray(midi.bytes)) {
        lastError = new Error(`invalid notes MIDI response for ${relPath}`);
        continue;
      }
      return { midi, relPath };
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError ?? new Error("no notes.mid candidate paths");
}

/**
 * Drums-only path: no melodic notes.mid, so chart drums from the pack-root
 * drum_tab.json alone (a drums-only sloppak). Returns an empty melodic list
 * plus whatever drum_tab.json yields (null when absent/empty/invalid). Never
 * throws — loadDrumChartFromTab is fully defensive.
 */
async function drumsOnlySelection(
  containerPath: string,
  details: SongChartLoaderDetails,
  consoleBridge: ConsoleBridge,
): Promise<SongChartSelection> {
  const tabRelPath = drumTabRelPath(details);
  const tabDrumSelection = await loadDrumChartFromTab(containerPath, tabRelPath);
  let melodicTracks: MelodicTrackSelection[] = [];
  if (tabDrumSelection) {
    consoleBridge.log(
      "play",
      `no notes.mid; charting drums from ${tabRelPath} (${tabDrumSelection.events.length} hit(s))`,
    );
  }
  if (hasFingeringSidecar(details)) {
    const fingeringFiles = await loadFingeringForRoles(
      containerPath,
      fingeringRolesForStandaloneTracks(details),
      { warn: (cat, msg, det) => consoleBridge.warn(cat as ConsoleLogCategory, msg, det) },
      details.manifest_raw,
    );
    melodicTracks = fingeringFilesToMelodicTracks(fingeringFiles);
    if (melodicTracks.length > 0) {
      consoleBridge.log(
        "play",
        `no notes.mid; charting melodic tracks from fingering: ${melodicTracks.map((t) => t.role).join(", ")}`,
      );
    }
  }
  return { drumSelection: tabDrumSelection, melodicTracks };
}

/**
 * Reads aural/notes.mid from a feedpak, extracts drum + melodic
 * tracks, and applies refinement overlays if present.
 *
 *  - No notes.mid / empty blob → drums-only fallback: still tries the pack-root
 *    drum_tab.json (a drums-only sloppak has no melodic arrangements, so prep
 *    writes no notes.mid) and returns that drumSelection with no melodic tracks.
 *  - Unexpected read/parse failure → warn, then still try drum_tab.json before
 *    returning an empty chart.
 *  - Refinement overlay missing/invalid → logged, base tracks rendered.
 */
export async function readSongChartSelection(args: ReadSongChartSelectionArgs): Promise<SongChartSelection> {
  const { containerPath, details, consoleBridge } = args;
  if (!details.has_notes_mid) {
    // No melodic MIDI (e.g. a drums-only sloppak). Chart drums straight from
    // the root drum_tab.json rather than returning empty.
    return await drumsOnlySelection(containerPath, details, consoleBridge);
  }

  let resolvedMidi: ResolvedMidiBlob;
  try {
    resolvedMidi = await readFirstNotesMid(containerPath, notesMidCandidatePaths(details));
  } catch (e) {
    consoleBridge.warn(
      "debugging",
      `failed to load notes MIDI from ${containerPath}; trying drum_tab fallback`,
      e,
    );
    return await drumsOnlySelection(containerPath, details, consoleBridge);
  }

  try {
    const { midi } = resolvedMidi;
    if (!midi.bytes.length) {
      return await drumsOnlySelection(containerPath, details, consoleBridge);
    }
    const midiBytes = new Uint8Array(midi.bytes);

    // Extract melodic instrument tracks alongside drums.
    const baseMelodicTracks = selectMelodicTracksFromMidiBytes(midiBytes);
    // Apply per-instrument refinement overlays from aural/refinement.<role>.json
    // (the file the Studio writes user picks to) if present. Best-effort:
    // missing or invalid refinement files are logged and skipped; the base
    // notes.mid track is rendered unchanged.
    const refinements = await loadRefinementsForRoles(
      containerPath,
      baseMelodicTracks.map((t) => t.role),
      { warn: (cat, msg, det) => consoleBridge.warn(cat as ConsoleLogCategory, msg, det) },
    );
    const refinedMelodicTracks = applyRefinementsToMelodicTracks(baseMelodicTracks, refinements);
    const fingeringFiles = hasFingeringSidecar(details)
      ? await loadFingeringForRoles(
          containerPath,
          refinedMelodicTracks.length > 0
            ? refinedMelodicTracks.map((t) => t.role)
            : fingeringRolesForStandaloneTracks(details),
          { warn: (cat, msg, det) => consoleBridge.warn(cat as ConsoleLogCategory, msg, det) },
          details.manifest_raw,
        )
      : [];
    const melodicTracks =
      refinedMelodicTracks.length > 0
        ? applyFingeringToMelodicTracks(refinedMelodicTracks, fingeringFiles)
        : fingeringFilesToMelodicTracks(fingeringFiles);
    if (melodicTracks.length > 0) {
      const refRoles = refinements.map((r) => r.instrument);
      const fingeringRoles = fingeringFiles.map((f) => f.instrument);
      const suffixParts = [
        refRoles.length > 0 ? `refinement: ${refRoles.join(", ")}` : "",
        fingeringRoles.length > 0 ? `fingering: ${fingeringRoles.join(", ")}` : "",
      ].filter(Boolean);
      const suffix = suffixParts.length > 0 ? ` (${suffixParts.join("; ")})` : "";
      consoleBridge.log(
        "play",
        `found ${melodicTracks.length} melodic track(s): ${melodicTracks.map((t) => t.role).join(", ")}${suffix}`,
      );
    }

    // Prefer the pack-root drum_tab.json when present: it carries the Studio
    // drum-cleanup edits AND the import-time onset-aligned hit times, neither
    // of which are written back into notes.mid. Fall back to the notes.mid
    // drum selection when drum_tab.json is absent / empty / invalid.
    const midiDrumSelection = selectDrumChartFromMidiBytes(midiBytes);
    const tabRelPath = drumTabRelPath(details);
    const tabDrumSelection = await loadDrumChartFromTab(containerPath, tabRelPath);
    const drumSelection = tabDrumSelection ?? midiDrumSelection;
    if (tabDrumSelection) {
      consoleBridge.log(
        "play",
        `charting drums from ${tabRelPath} (${tabDrumSelection.events.length} hit(s))`,
      );
    }

    return {
      drumSelection,
      melodicTracks,
    };
  } catch (e) {
    consoleBridge.warn(
      "debugging",
      `failed to parse notes MIDI from ${containerPath}; trying drum_tab fallback`,
      e,
    );
    return await drumsOnlySelection(containerPath, details, consoleBridge);
  }
}
