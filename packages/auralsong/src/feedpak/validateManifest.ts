import Ajv2020, { type ErrorObject } from "ajv/dist/2020";

import feedpakManifestSchema from "../../../feedpak/schemas/manifest.schema.json";
import type { FeedpakManifest } from "./manifest";

/**
 * Result of validating a value against the feedpak manifest schema.
 *
 * Distinct name from the auralsong `ValidationResult` so both can be
 * re-exported from the package root without clashing.
 */
export interface FeedpakValidationResult<T> {
  ok: boolean;
  value?: T;
  errors?: ErrorObject[];
}

// The vendored manifest schema is JSON Schema draft 2020-12, so it must be
// compiled with the 2020 Ajv build. All `$ref`s in the schema are internal
// (`#/$defs/...`), so it is self-contained and needs no extra schemas.
const ajv = new Ajv2020({ allErrors: true, strict: false });
const validate = ajv.compile<FeedpakManifest>(feedpakManifestSchema as object);

/**
 * Validate a parsed manifest object against the feedpak v1 manifest schema.
 */
export function validateFeedpakManifest(json: unknown): FeedpakValidationResult<FeedpakManifest> {
  const ok = validate(json);
  if (ok) {
    return { ok: true, value: json as FeedpakManifest };
  }
  return { ok: false, errors: validate.errors ?? [] };
}
