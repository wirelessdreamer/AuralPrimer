import Ajv, { type ErrorObject } from "ajv";

import chartSchema from "../schemas/chart.schema.json";

export interface ChartValidationResult {
  ok: boolean;
  value?: unknown;
  errors?: ErrorObject[];
}

const ajv = new Ajv({ allErrors: true, strict: false });
const validate = ajv.compile(chartSchema as any);

/**
 * Validate a `charts/*.json` file.
 */
export function validateChart(json: unknown): ChartValidationResult {
  const ok = validate(json);
  if (ok) return { ok: true, value: json };
  return { ok: false, errors: validate.errors ?? [] };
}
