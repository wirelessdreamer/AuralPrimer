/**
 * Loads + parses aural/notes.mid for a selected feedpak and applies
 * any per-instrument refinement overlays. Returns BOTH the drum chart
 * selection AND the melodic tracks so the caller can write both into its
 * state in one shot.
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
import { loadDrumChartFromTab } from "./drumTabChart";
import type { ConsoleBridge, ConsoleLogCategory } from "./consoleBridge";

type MidiBlob = { bytes: number[] };

/** Minimum AuralSongDetails subset readDrumChartSelection needs. */
export type SongChartLoaderDetails = {
  has_notes_mid?: boolean;
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

/**
 * Reads aural/notes.mid from a feedpak, extracts drum + melodic
 * tracks, and applies refinement overlays if present.
 *
 *  - No notes.mid → returns `{ drumSelection: null, melodicTracks: [] }`.
 *  - Parse failure or empty blob → same, plus a warn-level log.
 *  - Refinement overlay missing/invalid → logged, base tracks rendered.
 */
export async function readSongChartSelection(args: ReadSongChartSelectionArgs): Promise<SongChartSelection> {
  const { containerPath, details, consoleBridge } = args;
  if (!details.has_notes_mid) {
    return { drumSelection: null, melodicTracks: [] };
  }

  try {
    const midi = await invoke<MidiBlob>("read_auralsong_mid", {
      containerPath,
      relPath: "aural/notes.mid",
    });
    if (!midi.bytes.length) {
      return { drumSelection: null, melodicTracks: [] };
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
    const melodicTracks = applyRefinementsToMelodicTracks(baseMelodicTracks, refinements);
    if (melodicTracks.length > 0) {
      const refRoles = refinements.map((r) => r.instrument);
      const suffix = refRoles.length > 0 ? ` (refinement: ${refRoles.join(", ")})` : "";
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
    const tabDrumSelection = await loadDrumChartFromTab(containerPath);
    const drumSelection = tabDrumSelection ?? midiDrumSelection;
    if (tabDrumSelection) {
      consoleBridge.log(
        "play",
        `charting drums from drum_tab.json (${tabDrumSelection.events.length} hit(s))`,
      );
    }

    return {
      drumSelection,
      melodicTracks,
    };
  } catch (e) {
    consoleBridge.warn("debugging", `failed to load/parse aural/notes.mid from ${containerPath}`, e);
    return { drumSelection: null, melodicTracks: [] };
  }
}
