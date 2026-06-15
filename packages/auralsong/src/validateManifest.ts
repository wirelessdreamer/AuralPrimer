import Ajv, { type ErrorObject } from "ajv";
import manifestSchema from "../schemas/manifest.schema.json";
import type { AuralSongManifest } from "./manifest";

export interface ValidationResult<T> {
  ok: boolean;
  value?: T;
  errors?: ErrorObject[];
}

const ajv = new Ajv({ allErrors: true, strict: false });
const validate = ajv.compile<AuralSongManifest>(manifestSchema as any);

export function validateManifest(json: unknown): ValidationResult<AuralSongManifest> {
  const ok = validate(json);
  if (ok) {
    return { ok: true, value: json as AuralSongManifest };
  }
  return { ok: false, errors: validate.errors ?? [] };
}
