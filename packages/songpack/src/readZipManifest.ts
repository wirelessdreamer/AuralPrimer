import { promises as fs } from "node:fs";
import { unzipSync, strFromU8 } from "fflate";

/**
 * Read `manifest.json` from a zip SongPack.
 *
 * Note: zip SongPacks are `*.songpack` files.
 */
export async function readZipManifest(zipSongPackPath: string): Promise<unknown> {
  const bytes = await fs.readFile(zipSongPackPath);
  const files = unzipSync(new Uint8Array(bytes));

  const manifestBytes = files["manifest.json"];
  if (!manifestBytes) {
    throw new Error("missing_manifest");
  }

  const manifestText = strFromU8(manifestBytes);
  return JSON.parse(manifestText);
}
