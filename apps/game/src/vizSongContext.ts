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
  string?: number;
  fret?: number;
  s?: number;
  f?: number;
  role?: MelodicTrackSelection["role"] | "drums";
  instrument?: string;
  channel?: number;
  trackName?: string;
};

export type VizSongContext = {
  lyrics?: unknown;
  vocalPitch?: unknown;
  vocalPitchContour?: unknown;
  songTimeline?: unknown;
  keys?: unknown;
  harmony?: unknown;
  charts?: AuralSongChartsByPath;
  notes?: VizNote[];
};

export type BuildVizSongContextInput = {
  drumSelection: DrumChartSelection | null;
  melodicTracks: MelodicTrackSelection[];
  lyrics: unknown | null;
  vocalPitch?: unknown | null;
  vocalPitchContour?: unknown | null;
  songTimeline?: unknown | null;
  keys?: unknown | null;
  harmony?: unknown | null;
  charts: AuralSongChartsByPath | null;
};

export function buildVizSongContext(input: BuildVizSongContextInput): VizSongContext {
  const drumNotes: VizNote[] =
    input.drumSelection?.events.map((ev) => ({
      t_on: ev.t,
      t_off: ev.t + 0.08,
      pitch: ev.midi,
      velocity: ev.velocity ?? 100,
      role: "drums",
      instrument: "drums",
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
      string: n.string,
      fret: n.fret,
      s: n.s,
      f: n.f,
      role: track.role,
      instrument: track.role,
      channel: track.channel,
      trackName: track.trackName,
    })),
  );

  const allNotes = [...drumNotes, ...melodicNotes];
  allNotes.sort((a, b) => a.t_on - b.t_on);

  return {
    lyrics: input.lyrics ?? undefined,
    vocalPitch: input.vocalPitch ?? undefined,
    vocalPitchContour: input.vocalPitchContour ?? undefined,
    songTimeline: input.songTimeline ?? undefined,
    keys: input.keys ?? undefined,
    harmony: input.harmony ?? undefined,
    charts: input.charts ?? undefined,
    notes: allNotes.length > 0 ? allNotes : undefined,
  };
}
