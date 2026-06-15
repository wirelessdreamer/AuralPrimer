import { promises as fs } from "node:fs";
import { unzipSync, strFromU8 } from "fflate";

/**
 * Read `manifest.json` from a zip AuralSong.
 *
 * Note: zip AuralSongs are `*.auralsong` files.
 */
export async function readZipManifest(zipAuralSongPath: string): Promise<unknown> {
  const bytes = await fs.readFile(zipAuralSongPath);
  const files = unzipSync(new Uint8Array(bytes));

  const manifestBytes = files["manifest.json"];
  if (!manifestBytes) {
    throw new Error("missing_manifest");
  }

  const manifestText = strFromU8(manifestBytes);
  return JSON.parse(manifestText);
}
