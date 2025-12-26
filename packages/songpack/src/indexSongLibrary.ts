import { promises as fs } from "node:fs";
import path from "node:path";
import { discoverSongPacks } from "./discoverSongPacks";
import { type SongPackManifest } from "./manifest";
import { readZipManifest } from "./readZipManifest";
import { validateManifest } from "./validateManifest";

export type LibraryEntryKind = "directory" | "zip";

export interface LibraryEntryBase {
  kind: LibraryEntryKind;
  /** Absolute path to the .songpack file or directory */
  path: string;
  /** Basename (e.g., "MySong.songpack") */
  name: string;
}

export interface ParsedLibraryEntry extends LibraryEntryBase {
  parsed: true;
  manifest: SongPackManifest;
}

export interface UnparsedLibraryEntry extends LibraryEntryBase {
  parsed: false;
  reason: "missing_manifest" | "invalid_manifest" | "read_error";
}

export type LibraryEntry = ParsedLibraryEntry | UnparsedLibraryEntry;

export interface IndexSongLibraryOptions {
  /** If true, walk subdirectories. Default false. */
  recursive?: boolean;
}

async function readJsonFile(p: string): Promise<unknown> {
  const raw = await fs.readFile(p, "utf-8");
  return JSON.parse(raw);
}

/**
 * Build a library index by scanning a songs folder for SongPacks.
 *
 * - Directory SongPacks: attempts to read and validate `manifest.json`.
 * - Zip SongPacks: included as entries but not parsed yet.
 */
export async function indexSongLibrary(
  songsFolder: string,
  opts: IndexSongLibraryOptions = {}
): Promise<LibraryEntry[]> {
  const discovered = await discoverSongPacks(songsFolder, { recursive: opts.recursive });

  const out: LibraryEntry[] = [];

  for (const sp of discovered) {
    // Zip SongPack
    if (sp.kind === "zip") {
      try {
        const json = await readZipManifest(sp.path);
        const v = validateManifest(json);
        if (!v.ok) {
          out.push({ kind: "zip", name: sp.name, path: sp.path, parsed: false, reason: "invalid_manifest" });
          continue;
        }
        out.push({ kind: "zip", name: sp.name, path: sp.path, parsed: true, manifest: v.value! });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "read_error";
        if (msg === "missing_manifest") {
          out.push({ kind: "zip", name: sp.name, path: sp.path, parsed: false, reason: "missing_manifest" });
        } else {
          out.push({ kind: "zip", name: sp.name, path: sp.path, parsed: false, reason: "read_error" });
        }
      }
      continue;
    }

    // directory SongPack: expect manifest.json at root
    const manifestPath = path.join(sp.path, "manifest.json");

    try {
      const stat = await fs.stat(manifestPath).catch(() => null);
      if (!stat || !stat.isFile()) {
        out.push({ kind: "directory", name: sp.name, path: sp.path, parsed: false, reason: "missing_manifest" });
        continue;
      }

      const json = await readJsonFile(manifestPath);
      const v = validateManifest(json);
      if (!v.ok) {
        out.push({ kind: "directory", name: sp.name, path: sp.path, parsed: false, reason: "invalid_manifest" });
        continue;
      }

      out.push({ kind: "directory", name: sp.name, path: sp.path, parsed: true, manifest: v.value! });
    } catch {
      out.push({ kind: "directory", name: sp.name, path: sp.path, parsed: false, reason: "read_error" });
    }
  }

  // deterministic ordering
  out.sort((a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path));

  return out;
}

export function isParsedEntry(e: LibraryEntry): e is ParsedLibraryEntry {
  return e.parsed === true;
}
