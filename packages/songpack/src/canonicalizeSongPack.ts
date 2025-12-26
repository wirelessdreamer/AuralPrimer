import { promises as fs } from "node:fs";
import path from "node:path";
import { unzipSync, zipSync, strFromU8, strToU8, type ZipOptions, type Zippable, type ZippableFile } from "fflate";

import { canonicalJsonStringify, type CanonicalJsonOptions } from "./canonicalJson";

export type CanonicalizeSongPackOptions = {
  /** Canonical JSON serialization options. */
  json?: CanonicalJsonOptions;

  /** Zip compression level when output is zip bytes. Default 9. */
  compressionLevel?: ZipOptions["level"];

  /** Deterministic mtime used for zip entries. */
  mtime?: ZipOptions["mtime"];
};

function defaultJsonOpts(): CanonicalJsonOptions {
  return { indent: 2, trailingNewline: true, floatEpsilon: 1e-6 };
}

function normalizeRelPath(relPath: string): string {
  return relPath.split(path.sep).join(path.posix.sep);
}

async function* walkFiles(rootDir: string, relDir = ""): AsyncGenerator<string> {
  const absDir = path.join(rootDir, relDir);
  const ents = await fs.readdir(absDir, { withFileTypes: true });

  ents.sort((a, b) => a.name.localeCompare(b.name));

  for (const ent of ents) {
    const rel = relDir ? path.posix.join(relDir.split(path.sep).join(path.posix.sep), ent.name) : ent.name;
    const abs = path.join(rootDir, rel);

    if (ent.isDirectory()) {
      yield* walkFiles(rootDir, rel);
    } else if (ent.isFile()) {
      yield rel.split(path.sep).join(path.posix.sep);
    }
  }
}

/**
 * Canonicalize JSON files in-place in a directory SongPack.
 *
 * This is a deterministic normalization step: stable key ordering + float quantization.
 */
export async function canonicalizeSongPackDirectory(songPackDir: string, opts: CanonicalizeSongPackOptions = {}): Promise<void> {
  const jsonOpts: CanonicalJsonOptions = { ...defaultJsonOpts(), ...(opts.json ?? {}) };

  for await (const rel of walkFiles(songPackDir)) {
    if (!rel.toLowerCase().endsWith(".json")) continue;

    const abs = path.join(songPackDir, rel.split(path.posix.sep).join(path.sep));
    const raw = await fs.readFile(abs, "utf-8");
    const parsed = JSON.parse(raw);
    const canonical = canonicalJsonStringify(parsed, jsonOpts);
    await fs.writeFile(abs, canonical, "utf-8");
  }
}

/**
 * Create a deterministic zip from a zip SongPack by reserializing JSON entries canonically.
 */
export async function canonicalizeSongPackZipBytes(zipBytes: Uint8Array, opts: CanonicalizeSongPackOptions = {}): Promise<Uint8Array> {
  const jsonOpts: CanonicalJsonOptions = { ...defaultJsonOpts(), ...(opts.json ?? {}) };
  const compressionLevel = opts.compressionLevel ?? 9;
  const mtime = opts.mtime ?? "1980-01-01T00:00:00";
  const zipFileOpts: ZipOptions = { level: compressionLevel, mtime };

  const files = unzipSync(zipBytes) as Record<string, Uint8Array>;
  const relPaths = Object.keys(files).sort();

  const z: Zippable = {};

  for (const rel0 of relPaths) {
    const rel = normalizeRelPath(rel0);
    const data = files[rel0];
    if (!data) continue;

    if (rel.toLowerCase().endsWith(".json")) {
      const parsed = JSON.parse(strFromU8(data));
      const canonical = canonicalJsonStringify(parsed, jsonOpts);
      (z as any)[rel] = [strToU8(canonical), zipFileOpts] satisfies ZippableFile;
    } else {
      (z as any)[rel] = [data, zipFileOpts] satisfies ZippableFile;
    }
  }

  return zipSync(z, { level: compressionLevel });
}

/**
 * Read a zip SongPack from disk, canonicalize it, and write it back.
 */
export async function canonicalizeSongPackZipInPlace(zipSongPackPath: string, opts: CanonicalizeSongPackOptions = {}): Promise<void> {
  const raw = await fs.readFile(zipSongPackPath);
  const out = await canonicalizeSongPackZipBytes(new Uint8Array(raw), opts);
  await fs.writeFile(zipSongPackPath, Buffer.from(out));
}
