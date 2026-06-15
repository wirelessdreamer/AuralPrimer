import { promises as fs } from "node:fs";
import path from "node:path";

export type AuralSongKind = "zip" | "directory";

export interface DiscoveredAuralSong {
  /** Absolute path to the .auralsong file or directory */
  path: string;
  kind: AuralSongKind;
  /** Basename (e.g., "MySong.auralsong") */
  name: string;
}

export interface DiscoverAuralSongsOptions {
  /** If true, walk subdirectories. Default false. */
  recursive?: boolean;
}

function isAuralSongFileName(name: string): boolean {
  return name.toLowerCase().endsWith(".auralsong");
}

/**
 * Discover AuralSongs in a folder.
 *
 * Rules (per docs/auralsong-spec.md):
 * - `*.auralsong` file => zip AuralSong
 * - `*.auralsong/` directory => directory AuralSong
 */
export async function discoverAuralSongs(
  songsFolder: string,
  opts: DiscoverAuralSongsOptions = {}
): Promise<DiscoveredAuralSong[]> {
  const recursive = opts.recursive ?? false;

  const out: DiscoveredAuralSong[] = [];

  async function walk(dir: string): Promise<void> {
    const entries = await fs.readdir(dir, { withFileTypes: true });

    for (const ent of entries) {
      const abs = path.join(dir, ent.name);

      // Directory AuralSong
      if (ent.isDirectory() && isAuralSongFileName(ent.name)) {
        out.push({ path: abs, kind: "directory", name: ent.name });
        continue;
      }

      // Zip AuralSong
      if (ent.isFile() && isAuralSongFileName(ent.name)) {
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
