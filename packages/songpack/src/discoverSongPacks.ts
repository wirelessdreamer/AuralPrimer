import { promises as fs } from "node:fs";
import path from "node:path";

export type SongPackKind = "zip" | "directory";

export interface DiscoveredSongPack {
  /** Absolute path to the .songpack file or directory */
  path: string;
  kind: SongPackKind;
  /** Basename (e.g., "MySong.songpack") */
  name: string;
}

export interface DiscoverSongPacksOptions {
  /** If true, walk subdirectories. Default false. */
  recursive?: boolean;
}

function isSongPackFileName(name: string): boolean {
  return name.toLowerCase().endsWith(".songpack");
}

/**
 * Discover SongPacks in a folder.
 *
 * Rules (per docs/songpack-spec.md):
 * - `*.songpack` file => zip SongPack
 * - `*.songpack/` directory => directory SongPack
 */
export async function discoverSongPacks(
  songsFolder: string,
  opts: DiscoverSongPacksOptions = {}
): Promise<DiscoveredSongPack[]> {
  const recursive = opts.recursive ?? false;

  const out: DiscoveredSongPack[] = [];

  async function walk(dir: string): Promise<void> {
    const entries = await fs.readdir(dir, { withFileTypes: true });

    for (const ent of entries) {
      const abs = path.join(dir, ent.name);

      // Directory SongPack
      if (ent.isDirectory() && isSongPackFileName(ent.name)) {
        out.push({ path: abs, kind: "directory", name: ent.name });
        continue;
      }

      // Zip SongPack
      if (ent.isFile() && isSongPackFileName(ent.name)) {
        out.push({ path: abs, kind: "zip", name: ent.name });
        continue;
      }

      if (recursive && ent.isDirectory()) {
        await walk(abs);
      }
    }
  }

  await walk(path.resolve(songsFolder));

  // deterministic ordering
  out.sort((a, b) => a.name.localeCompare(b.name) || a.path.localeCompare(b.path));

  return out;
}
