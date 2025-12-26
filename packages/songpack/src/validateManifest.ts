import Ajv, { type ErrorObject } from "ajv";
import manifestSchema from "../schemas/manifest.schema.json";
import type { SongPackManifest } from "./manifest";

export interface ValidationResult<T> {
  ok: boolean;
  value?: T;
  errors?: ErrorObject[];
}

const ajv = new Ajv({ allErrors: true, strict: false });
const validate = ajv.compile<SongPackManifest>(manifestSchema as any);

export function validateManifest(json: unknown): ValidationResult<SongPackManifest> {
  const ok = validate(json);
  if (ok) {
    return { ok: true, value: json as SongPackManifest };
  }
  return { ok: false, errors: validate.errors ?? [] };
}
