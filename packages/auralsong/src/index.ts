export * from "./discoverAuralSongs";
export * from "./indexSongLibrary";
export * from "./libraryView";
export * from "./manifest";
export * from "./migrations";
export * from "./readZipManifest";
export * from "./songsFolder";
export * from "./packKind";
export * from "./validateManifest";
export * from "./validateFeatures";
export * from "./validateAuralSong";
export * from "./validateCharts";
export * from "./version";

// Loading
export * from "./loadAuralSong";

// Validation (in-memory)
export * from "./validateLoadedAuralSong";

// Deliverable utilities
export * from "./canonicalJson";
export * from "./canonicalizeAuralSong";
export * from "./buildAuralSongZip";

// Refinement (Studio Refine workspace data contract + game-side overlay)
export {
  applyRefinementOverlay,
  findOverlappingRegions,
  validateRefinement,
  type RefinementFile,
  type RefinementHotSpotType,
  type RefinementInstrument,
  type RefinementNote,
  type RefinementRegion,
  type RefinementSummary,
  type ValidationError,
  type ValidationResult as RefinementValidationResult,
} from "./refinement";
export * from "./refineCandidates";

// feedpak reader (read-only; stage 3 of the .auralsong -> feedpak migration).
// Lives alongside the loadAuralSong reader above; cutover is a later stage.
export * from "./feedpak";
