import { promises as fs } from "node:fs";
import path from "node:path";
import { discoverAuralSongs } from "./discoverAuralSongs";
import { type LibraryItem } from "./libraryView";
import { type AuralSongManifest } from "./manifest";
import { readZipManifest } from "./readZipManifest";
import { validateManifest } from "./validateManifest";

export type LibraryEntryKind = "directory" | "zip";

export interface LibraryEntryBase {
  kind: LibraryEntryKind;
  /** Absolute path to the .auralsong file or directory */
  path: string;
  /** Basename (e.g., "MySong.auralsong") */
  name: string;
  /**
   * Epoch ms the pack landed in the songs folder, for "recently added"
   * ordering. Creation time where the filesystem records one, last-modified
   * otherwise; 0 when the pack could not be stat'd.
   */
  addedAtMs: number;
}

export interface ParsedLibraryEntry extends LibraryEntryBase {
  parsed: true;
  manifest: AuralSongManifest;
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

/** One stat per pack — birthtime where the filesystem keeps one, else mtime. */
async function readAddedAtMs(p: string): Promise<number> {
  const st = await fs.stat(p).catch(() => null);
  if (!st) return 0;
  return st.birthtimeMs > 0 ? st.birthtimeMs : st.mtimeMs;
}

/**
 * Build a library index by scanning a songs folder for AuralSongs.
 *
 * - Directory AuralSongs: attempts to read and validate `manifest.json`.
 * - Zip AuralSongs: included as entries but not parsed yet.
 */
export async function indexSongLibrary(
  songsFolder: string,
  opts: IndexSongLibraryOptions = {}
): Promise<LibraryEntry[]> {
  const discovered = await discoverAuralSongs(songsFolder, { recursive: opts.recursive });

  const out: LibraryEntry[] = [];

  for (const sp of discovered) {
    const base: LibraryEntryBase = {
      kind: sp.kind,
      name: sp.name,
      path: sp.path,
      addedAtMs: await readAddedAtMs(sp.path),
    };

    // Zip AuralSong
    if (sp.kind === "zip") {
      try {
        const json = await readZipManifest(sp.path);
        const v = validateManifest(json);
        if (!v.ok) {
          out.push({ ...base, parsed: false, reason: "invalid_manifest" });
          continue;
        }
        out.push({ ...base, parsed: true, manifest: v.value! });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "read_error";
        if (msg === "missing_manifest") {
          out.push({ ...base, parsed: false, reason: "missing_manifest" });
        } else {
          out.push({ ...base, parsed: false, reason: "read_error" });
        }
      }
      continue;
    }

    // directory AuralSong: expect manifest.json at root
    const manifestPath = path.join(sp.path, "manifest.json");

    try {
      const stat = await fs.stat(manifestPath).catch(() => null);
      if (!stat || !stat.isFile()) {
        out.push({ ...base, parsed: false, reason: "missing_manifest" });
        continue;
      }

      const json = await readJsonFile(manifestPath);
      const v = validateManifest(json);
      if (!v.ok) {
        out.push({ ...base, parsed: false, reason: "invalid_manifest" });
        continue;
      }

      out.push({ ...base, parsed: true, manifest: v.value! });
    } catch {
      out.push({ ...base, parsed: false, reason: "read_error" });
    }
  }

  // deterministic ordering
  out.sort((a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path));

  return out;
}

export function isParsedEntry(e: LibraryEntry): e is ParsedLibraryEntry {
  return e.parsed === true;
}

/**
 * Project an index entry onto the shape `libraryView` sorts, groups, and
 * filters. Unparseable packs keep their slot (`ok: false`) so the library
 * can flag them instead of hiding them; their basename stands in for the
 * title the manifest never yielded.
 */
export function libraryItemFromEntry(e: LibraryEntry): LibraryItem {
  return {
    path: e.path,
    title: isParsedEntry(e) ? e.manifest.title : e.name,
    composer: isParsedEntry(e) ? e.manifest.artist : "",
    durationSec: isParsedEntry(e) ? e.manifest.duration_sec : null,
    addedAtMs: e.addedAtMs,
    ok: isParsedEntry(e),
  };
}
