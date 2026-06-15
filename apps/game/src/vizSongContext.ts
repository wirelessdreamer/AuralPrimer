/**
 * Build the "song context" object passed to every Visualizer plugin's
 * init/render — composes the loaded AuralSong's lyrics, chart spec JSON,
 * and a merged + time-sorted notes array (drums + per-instrument melodic).
 *
 * Pure compute: zero state, zero DOM. Extracted from main.ts as Phase 2.S.
 */

import type { DrumChartSelection, MelodicTrackSelection } from "./chartLoader";
import type { AuralSongChartsByPath } from "./capsPanel";

export type VizNote = {
  t_on: number;
  t_off?: number;
  pitch: number;
  velocity?: number;
  channel?: number;
  trackName?: string;
};

export type VizSongContext = {
  lyrics?: unknown;
  charts?: AuralSongChartsByPath;
  notes?: VizNote[];
};

export type BuildVizSongContextInput = {
  drumSelection: DrumChartSelection | null;
  melodicTracks: MelodicTrackSelection[];
  lyrics: unknown | null;
  charts: AuralSongChartsByPath | null;
};

export function buildVizSongContext(input: BuildVizSongContextInput): VizSongContext {
  const drumNotes: VizNote[] =
    input.drumSelection?.events.map((ev) => ({
      t_on: ev.t,
      t_off: ev.t + 0.08,
      pitch: ev.midi,
      velocity: 100,
      channel: 9,
      trackName: ev.trackName,
    })) ?? [];

  // Include melodic instrument notes for visualizer plugins.
  const melodicNotes: VizNote[] = input.melodicTracks.flatMap((track) =>
    track.notes.map((n) => ({
      t_on: n.t_on,
      t_off: n.t_off,
      pitch: n.pitch,
      velocity: n.velocity,
      channel: track.channel,
      trackName: track.trackName,
    })),
  );

  const allNotes = [...drumNotes, ...melodicNotes];
  allNotes.sort((a, b) => a.t_on - b.t_on);

  return {
    lyrics: input.lyrics ?? undefined,
    charts: input.charts ?? undefined,
    notes: allNotes.length > 0 ? allNotes : undefined,
  };
}
