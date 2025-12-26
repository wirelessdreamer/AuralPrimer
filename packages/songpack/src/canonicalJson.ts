export interface CanonicalJsonOptions {
  /**
   * Number of spaces for indentation.
   *
   * Defaults to 2 for human readability.
   */
  indent?: number;

  /**
   * Quantize all finite numbers to this step before serialization.
   *
   * Example: `1e-6` makes floating outputs stable across platforms.
   *
   * If omitted, numbers are not modified.
   */
  floatEpsilon?: number;

  /** Append a trailing `\n` to the output. Defaults to true. */
  trailingNewline?: boolean;
}

function quantizeNumber(n: number, eps: number): number {
  if (!Number.isFinite(n)) return n;
  if (eps <= 0) return n;

  // Preserve -0, but avoid producing -0 after rounding.
  const q = Math.round(n / eps) * eps;
  return Object.is(q, -0) ? 0 : q;
}

function canonicalizeValue(value: unknown, eps?: number): unknown {
  if (value === null) return null;

  const t = typeof value;
  if (t === "number") {
    return eps ? quantizeNumber(value as number, eps) : value;
  }

  if (t === "string" || t === "boolean") return value;

  if (Array.isArray(value)) {
    return value.map((v) => canonicalizeValue(v, eps));
  }

  if (t === "object") {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(obj).sort()) {
      const v = obj[k];
      // Preserve JSON.stringify behavior: omit undefined.
      if (v === undefined) continue;
      out[k] = canonicalizeValue(v, eps);
    }
    return out;
  }

  // For JSON-unsupported values (undefined, function, symbol), let JSON.stringify drop them.
  return value;
}

/**
 * Deterministic JSON serialization:
 * - stable object key ordering (recursive)
 * - optional float quantization (recursive)
 */
export function canonicalJsonStringify(value: unknown, opts: CanonicalJsonOptions = {}): string {
  const indent = opts.indent ?? 2;
  const eps = opts.floatEpsilon;
  const trailingNewline = opts.trailingNewline ?? true;

  const canonical = canonicalizeValue(value, eps);
  const json = JSON.stringify(canonical, null, indent);
  return trailingNewline ? `${json}\n` : json;
}
