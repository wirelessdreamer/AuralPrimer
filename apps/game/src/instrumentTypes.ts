/**
 * Shared instrument-related types used by main.ts, playersPanel.ts, and any
 * other module that needs the same vocabulary. Kept tiny on purpose — only
 * the names that cross module boundaries.
 */

export type Instrument =
  | "lead_guitar"
  | "rhythm_guitar"
  | "bass"
  | "drums"
  | "keys"
  | "vocals";

export const INSTRUMENT_LABELS: Record<Instrument, string> = {
  drums: "Drums",
  bass: "Bass",
  lead_guitar: "Lead Guitar",
  rhythm_guitar: "Rhythm Guitar",
  keys: "Keys",
  vocals: "Vocals",
};
