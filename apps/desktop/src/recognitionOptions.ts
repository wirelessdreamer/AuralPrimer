/** MuScriptor's MT3_FULL_PLUS vocabulary. Values are model labels, not game roles. */
export const RECOGNITION_GROUPS = [
  ["Keys and synths", ["acoustic_piano", "electric_piano", "organ", "synth_lead", "synth_pad"]],
  ["Guitars and bass", ["acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar", "acoustic_bass", "electric_bass"]],
  ["Strings", ["violin", "viola", "cello", "contrabass", "orchestral_harp", "string_ensemble", "synth_strings"]],
  ["Brass", ["trumpet", "trombone", "tuba", "french_horn", "brass_section"]],
  ["Woodwinds", ["soprano_and_alto_sax", "tenor_sax", "baritone_sax", "oboe", "english_horn", "bassoon", "clarinet", "flutes"]],
  ["Voice and percussion", ["voice", "drums", "chromatic_percussion", "timpani", "orchestra_hit"]],
] as const;

export const SOURCE_STEM_ROLES = ["keys", "guitar", "lead_guitar", "rhythm_guitar", "bass", "drums", "vocals", "other"] as const;
export const instrumentLabel = (name: string) => name.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export function validateInstrumentSelection(names: string[]): string[] {
  const allowed = new Set<string>(RECOGNITION_GROUPS.flatMap(([, items]) => [...items]));
  if (!names.length) throw new Error("Choose at least one instrument to recognize, or use Automatic.");
  if (names.some((name) => !allowed.has(name))) throw new Error("Unknown recognition instrument.");
  return [...new Set(names)].sort();
}
