/**
 * Instrument-hint derivation: given a chart filename, a chart JSON, a mapped
 * role, or the full manifest blob, set the relevant byInstrument flags on
 * the SongCapabilities aggregate so the UI knows which lanes to offer.
 *
 * Pure helpers — no DOM, no Tauri, no module state. Extracted from main.ts
 * as Phase 2.L.
 */

import type { Instrument } from "./instrumentTypes";

/**
 * The subset of SongCapabilities the hint helpers mutate. Kept narrow so
 * this module doesn't pull in the rest of the capabilities type.
 */
export type InstrumentFlags = Partial<Record<Instrument, boolean>>;

export function asObjectRecord(v: unknown): Record<string, unknown> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  return v as Record<string, unknown>;
}

/**
 * Filename-token / lane-name / chart-mode heuristic. Lowercases the token
 * first so the regexes can stay simple.
 */
export function applyInstrumentHintsFromToken(tokenRaw: string, byInstrument: InstrumentFlags): void {
  const token = tokenRaw.toLowerCase();
  if (!token) return;

  if (/rhythm[_\s-]?guitar|guitar[_\s-]?rhythm|rhythm/.test(token)) byInstrument.rhythm_guitar = true;
  if (/lead[_\s-]?guitar|guitar[_\s-]?lead|lead/.test(token)) byInstrument.lead_guitar = true;
  if (/guitar|gtr/.test(token) && !/rhythm/.test(token)) byInstrument.lead_guitar = true;
  if (/bass/.test(token)) byInstrument.bass = true;
  if (/keys|piano|synth/.test(token)) byInstrument.keys = true;
  if (/vocals?|vox|lyrics?/.test(token)) byInstrument.vocals = true;
  if (/drum|kit|percussion|beat|kick|snare|hihat|hat|cym|ride|tom|bd|sd|hh|cy|rd|ht|lt|ft/.test(token)) {
    byInstrument.drums = true;
  }

  // Common five-fret lane naming in some chart formats.
  if (/^(g|r|y|b|o|green|red|yellow|blue|orange)$/.test(token)) {
    byInstrument.lead_guitar = true;
  }
}

/**
 * Walk a chart-spec JSON blob: mode/instrument/instruments[]/targets[].lane/
 * targets[].instrument. Best-effort — anything not a string is ignored.
 */
export function applyInstrumentHintsFromChartJson(chartJson: unknown, byInstrument: InstrumentFlags): void {
  const chart = asObjectRecord(chartJson);
  if (!chart) return;

  if (typeof chart.mode === "string") applyInstrumentHintsFromToken(chart.mode, byInstrument);
  if (typeof chart.instrument === "string") applyInstrumentHintsFromToken(chart.instrument, byInstrument);
  if (Array.isArray(chart.instruments)) {
    for (const item of chart.instruments) {
      if (typeof item === "string") applyInstrumentHintsFromToken(item, byInstrument);
    }
  }

  if (!Array.isArray(chart.targets)) return;
  for (const target of chart.targets) {
    const targetObj = asObjectRecord(target);
    if (!targetObj) continue;
    if (typeof targetObj.lane === "string") applyInstrumentHintsFromToken(targetObj.lane, byInstrument);
    if (typeof targetObj.instrument === "string") applyInstrumentHintsFromToken(targetObj.instrument, byInstrument);
  }
}

/**
 * Direct mapping from a sidecar-emitted role string (drums/bass/lead_guitar/
 * rhythm_guitar/keys/vocals) to the corresponding flag. Unknown roles ignored.
 */
export function applyInstrumentHintsFromMappedRole(roleRaw: string, byInstrument: InstrumentFlags): void {
  switch (roleRaw) {
    case "drums":
      byInstrument.drums = true;
      break;
    case "bass":
      byInstrument.bass = true;
      break;
    case "guitar":
    case "lead_guitar":
      byInstrument.lead_guitar = true;
      break;
    case "rhythm_guitar":
      byInstrument.rhythm_guitar = true;
      break;
    case "keys":
      byInstrument.keys = true;
      break;
    case "vocals":
      byInstrument.vocals = true;
      break;
    default:
      break;
  }
}

/**
 * Walk the full manifest blob: source.parts.mapped_game_roles[] plus
 * assets.midi.tracks[].role. Anything else is ignored.
 */
export function applyInstrumentHintsFromManifestRaw(manifestRaw: unknown, byInstrument: InstrumentFlags): void {
  const manifest = asObjectRecord(manifestRaw);
  if (!manifest) return;

  const source = asObjectRecord(manifest.source);
  const parts = source ? asObjectRecord(source.parts) : null;
  const mappedRoles = Array.isArray(parts?.mapped_game_roles) ? parts?.mapped_game_roles : [];
  for (const role of mappedRoles) {
    if (typeof role === "string") applyInstrumentHintsFromMappedRole(role, byInstrument);
  }

  const assets = asObjectRecord(manifest.assets);
  const midi = assets ? asObjectRecord(assets.midi) : null;
  const midiTracks = Array.isArray(midi?.tracks) ? midi?.tracks : [];
  for (const track of midiTracks) {
    const rec = asObjectRecord(track);
    if (rec && typeof rec.role === "string") applyInstrumentHintsFromMappedRole(rec.role, byInstrument);
  }
}
