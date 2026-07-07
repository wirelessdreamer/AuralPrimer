/**
 * Pack-kind dispatch: the single place the ".sloppak" / ".feedpak" /
 * ".auralsong" extension strings live on the TypeScript side.
 *
 * A ".feedpak" and a ".sloppak" are container siblings — both carry a
 * `manifest.yaml` index and relocate AuralPrimer feature artifacts under
 * `aural/`. A legacy ".auralsong" carries `manifest.json` and keeps its
 * features under `features/`. These helpers encode CONTRACT C2 (the feature
 * dir rule) so every studio site agrees.
 */

/**
 * True for a manifest-indexed container pack (feedpak OR sloppak). These
 * share the `manifest.yaml` + `aural/` conventions. A legacy `.auralsong`
 * (manifest.json, `features/`) is NOT a manifest pack.
 *
 * Matching is case-insensitive on the trailing extension; a trailing path
 * separator (a directory-form pack passed with a slash) is tolerated.
 */
export function isManifestPack(path: string): boolean {
  const p = path.replace(/[\\/]+$/, "");
  return /\.(feedpak|sloppak)$/i.test(p);
}

/**
 * The in-container subdir AuralPrimer feature artifacts live under.
 * CONTRACT C2: "aural" for a pack path ending .feedpak OR .sloppak; else
 * "features" (legacy .auralsong).
 */
export function featureDir(path: string): "aural" | "features" {
  return isManifestPack(path) ? "aural" : "features";
}

/**
 * A readable display name for a pack: drop the directory and the
 * `.feedpak` / `.sloppak` / `.auralsong` extension, and normalize
 * `ingest_`-prefixed underscore-separated names into spaces.
 */
export function packDisplayName(path: string): string {
  const base = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? path;
  return (
    base
      .replace(/\.(feedpak|sloppak|auralsong)$/i, "")
      .replace(/^ingest_/, "")
      .replace(/_/g, " ")
      .trim() || base
  );
}
