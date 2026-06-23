import { load as loadYaml } from "js-yaml";

import type { FeedpakManifest } from "./manifest";

/**
 * Parse a feedpak `manifest.yaml` document into a {@link FeedpakManifest}.
 *
 * This performs structural YAML parsing only — it does NOT validate
 * against the schema (use {@link validateFeedpakManifest} for that). The
 * returned object preserves every key present in the document, including
 * spec fields this package doesn't model and the `aural_*` extension keys.
 */
export function parseFeedpakManifest(yamlText: string): FeedpakManifest {
  const doc = loadYaml(yamlText);
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
    throw new Error("feedpak manifest.yaml must be a YAML mapping");
  }
  return doc as FeedpakManifest;
}
