import { promises as fs } from "node:fs";
import path from "node:path";
import { zipSync, type ZipOptions, type Zippable, type ZippableFile } from "fflate";

import { canonicalJsonStringify, type CanonicalJsonOptions } from "./canonicalJson";
import { validateSongPack } from "./validateSongPack";

export interface BuildSongPackZipOptions {
  /**
   * When true (default), validate the input directory SongPack before zipping.
   *
   * Note: current validation focuses on JSON-schema validity.
   */
  validate?: boolean;

  /**
   * Canonicalize any `*.json` file by parsing + re-stringifying with stable key ordering.
   *
   * Default: true.
   */
  canonicalizeJson?: boolean;

  /** Options passed to canonical JSON serialization. */
  json?: CanonicalJsonOptions;

  /**
   * Zip compression level.
   *
   * Defaults to 9.
   */
  compressionLevel?: ZipOptions["level"];

  /**
   * Zip entry modification time.
   *
   * ZIP timestamps are limited to years 1980-2099; we default to a fixed
   * in-range value for determinism.
   */
  mtime?: ZipOptions["mtime"];
}

async function* walkFiles(rootDir: string, relDir = ""): AsyncGenerator<string> {
  const absDir = path.join(rootDir, relDir);
  const ents = await fs.readdir(absDir, { withFileTypes: true });

  // Deterministic traversal order.
  ents.sort((a, b) => a.name.localeCompare(b.name));

  for (const ent of ents) {
    const rel = relDir ? path.posix.join(relDir.split(path.sep).join(path.posix.sep), ent.name) : ent.name;
    const abs = path.join(rootDir, rel);

    if (ent.isDirectory()) {
      yield* walkFiles(rootDir, rel);
    } else if (ent.isFile()) {
      // Always use POSIX separators in zip paths.
      const relPosix = rel.split(path.sep).join(path.posix.sep);
      yield relPosix;
    }
  }
}

function defaultJsonOpts(): CanonicalJsonOptions {
  return { indent: 2, trailingNewline: true, floatEpsilon: 1e-6 };
}

/**
 * Build a deterministic `.songpack` zip (as bytes) from a directory SongPack.
 */
export async function buildSongPackZipFromDirectory(
  songPackDir: string,
  opts: BuildSongPackZipOptions = {}
): Promise<Uint8Array> {
  const validate = opts.validate ?? true;
  const canonicalizeJson = opts.canonicalizeJson ?? true;
  const compressionLevel = opts.compressionLevel ?? 9;
  // Use a local-time timestamp (no trailing Z) so it doesn't underflow into 1979
  // in timezones behind UTC.
  const mtime = opts.mtime ?? "1980-01-01T00:00:00";
  const jsonOpts: CanonicalJsonOptions = { ...defaultJsonOpts(), ...(opts.json ?? {}) };

  const zipFileOpts: ZipOptions = {
    level: compressionLevel,
    mtime
  };

  // Ensure it's a directory.
  const st = await fs.stat(songPackDir);
  if (!st.isDirectory()) {
    throw new Error(`buildSongPackZipFromDirectory expects a directory: ${songPackDir}`);
  }

  if (validate) {
    const res = await validateSongPack(songPackDir);
    if (!res.ok) {
      const msg = res.issues.map((i) => `${i.code}:${i.path}`).join(", ");
      throw new Error(`SongPack validation failed (${res.containerKind}): ${msg}`);
    }
  }

  const relPaths: string[] = [];
  for await (const rel of walkFiles(songPackDir)) relPaths.push(rel);
  relPaths.sort();

  const z: Zippable = {};
  for (const rel of relPaths) {
    const abs = path.join(songPackDir, rel);

    // JSON canonicalization for determinism.
    if (canonicalizeJson && rel.toLowerCase().endsWith(".json")) {
      const raw = await fs.readFile(abs, "utf-8");
      const parsed = JSON.parse(raw);
      const canonical = canonicalJsonStringify(parsed, jsonOpts);
      const data = new TextEncoder().encode(canonical);

      (z as any)[rel] = [data, zipFileOpts] satisfies ZippableFile;
      continue;
    }

    const data = new Uint8Array(await fs.readFile(abs));
    (z as any)[rel] = [data, zipFileOpts] satisfies ZippableFile;
  }

  // zipSync iterates object keys; ensure deterministic key order by insertion order.
  // We inserted relPaths in sorted order.
  return zipSync(z, { level: compressionLevel });
}

/**
 * Convenience: build + write to disk.
 */
export async function writeSongPackZipFromDirectory(
  songPackDir: string,
  outSongPackZipPath: string,
  opts: BuildSongPackZipOptions = {}
): Promise<void> {
  const bytes = await buildSongPackZipFromDirectory(songPackDir, opts);
  await fs.writeFile(outSongPackZipPath, Buffer.from(bytes));
}
