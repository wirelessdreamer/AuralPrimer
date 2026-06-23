/**
 * Read-only feedpak reader for AuralPrimer.
 *
 * Stage 3 of migrating the native song format from `.auralsong` to
 * feedpak. This barrel exposes the manifest types/guard, the YAML parser,
 * the ajv-2020 schema validator, and the directory/zip loaders. It lives
 * alongside the existing `loadAuralSong` reader; the cutover that removes
 * the old path is a later stage.
 */
export * from "./manifest";
export * from "./parseManifest";
export * from "./validateManifest";
export * from "./loadFeedpak";
