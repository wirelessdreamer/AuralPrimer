import type { ManifestSummary } from "./manifestTypes";

export type AuralSongDetails = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest_summary?: ManifestSummary;
  manifest_raw?: unknown;
  has_beats: boolean;
  has_tempo_map: boolean;
  has_sections: boolean;
  has_events: boolean;
  has_lyrics?: boolean;
  has_notes_mid?: boolean;
  has_song_timeline?: boolean;
  has_drum_tab?: boolean;
  has_keys?: boolean;
  has_harmony?: boolean;
  has_vocal_pitch?: boolean;
  has_vocal_pitch_contour?: boolean;
  has_aural_fingering?: boolean;
  has_mix_mp3: boolean;
  has_mix_ogg: boolean;
  has_mix_wav?: boolean;
  charts: string[];
  error?: string;
};
