import path from "node:path";
import { promises as fs } from "node:fs";

import { canonicalizeSongPackDirectory } from "../src/canonicalizeSongPack";
import { buildSongPackZipFromDirectory } from "../src/buildSongPackZip";
import { loadSongPackFromDirectory, loadSongPackFromZipBytes } from "../src/loadSongPack";
import { validateLoadedSongPack } from "../src/validateLoadedSongPack";

const FIXTURE_DIR = path.join(process.cwd(), "assets/test_fixtures/songpacks/minimal_valid.songpack");

function sortObjectKeysDeep(value: any): any {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(sortObjectKeysDeep);
  if (typeof value !== "object") return value;
  const out: any = {};
  for (const k of Object.keys(value).sort()) out[k] = sortObjectKeysDeep(value[k]);
  return out;
}

describe("SongPack round-trip", () => {
  it("directory -> canonicalize -> zip -> load preserves manifest/features/charts", async () => {
    const tmp = path.join(process.cwd(), ".tmp-tests", "roundtrip");
    await fs.rm(tmp, { recursive: true, force: true });
    await fs.mkdir(tmp, { recursive: true });

    // Copy fixture into tmp (so canonicalize is safe).
    await fs.cp(FIXTURE_DIR, tmp, { recursive: true });

    // Normalize JSON in the directory.
    await canonicalizeSongPackDirectory(tmp);

    // Load directory.
    const dirPack = await loadSongPackFromDirectory(tmp);
    const dirValid = await validateLoadedSongPack(dirPack);
    expect(dirValid.ok).toBe(true);

    // Build deterministic zip from the canonicalized directory.
    const zipBytes = await buildSongPackZipFromDirectory(tmp, {
      validate: true,
      canonicalizeJson: true
    });

    // Load zip bytes.
    const zipPack = await loadSongPackFromZipBytes(zipBytes, "<roundtrip>");
    const zipValid = await validateLoadedSongPack(zipPack);
    expect(zipValid.ok).toBe(true);

    // Compare structures (order-insensitive).
    expect(sortObjectKeysDeep(zipPack.manifest)).toEqual(sortObjectKeysDeep(dirPack.manifest));
    expect(sortObjectKeysDeep(zipPack.features)).toEqual(sortObjectKeysDeep(dirPack.features));
    expect(sortObjectKeysDeep(zipPack.charts)).toEqual(sortObjectKeysDeep(dirPack.charts));
  });
});
