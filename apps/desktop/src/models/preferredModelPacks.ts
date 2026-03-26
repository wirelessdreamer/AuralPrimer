export type PreferredModelPack = {
  id: string;
  version: string;
  /** Direct URL to a .zip that contains modelpack.json at archive root. */
  url?: string;
  /** Optional sha256 of the zip bytes (hex). */
  sha256?: string;
  description?: string;
};

/**
 * Curated model packs the app can install.
 *
 * NOTE: Fill in `url`/`sha256` once you have hosted artifacts.
 */
export const PREFERRED_MODEL_PACKS: PreferredModelPack[] = [
  {
    id: "demucs_6",
    version: "0.0.0",
    description: "6-stem separation (keys, drums, guitar, bass, vocals)",
  },
  {
    id: "mr_mt3",
    version: "0.0.0",
    description: "MR-MT3 local checkpoint pack for drum benchmark evaluation",
  },
  {
    id: "yourmt3",
    version: "0.0.0",
    description: "YourMT3 local checkpoint pack for drum benchmark evaluation",
  },
  {
    id: "basic-transcription",
    version: "0.0.0",
    description: "Baseline transcription model pack",
  },
];
