import path from "node:path";
import { promises as fs } from "node:fs";

import { canonicalizeAuralSongDirectory } from "../src/canonicalizeAuralSong";
import { buildAuralSongZipFromDirectory } from "../src/buildAuralSongZip";
import { loadAuralSongFromDirectory, loadAuralSongFromZipBytes } from "../src/loadAuralSong";
import { validateLoadedAuralSong } from "../src/validateLoadedAuralSong";

const FIXTURE_DIR = path.join(process.cwd(), "assets/test_fixtures/auralsongs/minimal_valid.auralsong");

function sortObjectKeysDeep(value: any): any {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(sortObjectKeysDeep);
  if (typeof value !== "object") return value;
  const out: any = {};
  for (const k of Object.keys(value).sort()) out[k] = sortObjectKeysDeep(value[k]);
  return out;
}

describe("AuralSong round-trip", () => {
  it("directory -> canonicalize -> zip -> load preserves manifest/features/charts", async () => {
    const tmp = path.join(process.cwd(), ".tmp-tests", "roundtrip");
    await fs.rm(tmp, { recursive: true, force: true });
    await fs.mkdir(tmp, { recursive: true });

    // Copy fixture into tmp (so canonicalize is safe).
    await fs.cp(FIXTURE_DIR, tmp, { recursive: true });

    // Normalize JSON in the directory.
    await canonicalizeAuralSongDirectory(tmp);

    // Load directory.
    const dirPack = await loadAuralSongFromDirectory(tmp);
    const dirValid = await validateLoadedAuralSong(dirPack);
    expect(dirValid.ok).toBe(true);

    // Build deterministic zip from the canonicalized directory.
    const zipBytes = await buildAuralSongZipFromDirectory(tmp, {
      validate: true,
      canonicalizeJson: true
    });

    // Load zip bytes.
    const zipPack = await loadAuralSongFromZipBytes(zipBytes, "<roundtrip>");
    const zipValid = await validateLoadedAuralSong(zipPack);
    expect(zipValid.ok).toBe(true);

    // Compare structures (order-insensitive).
    expect(sortObjectKeysDeep(zipPack.manifest)).toEqual(sortObjectKeysDeep(dirPack.manifest));
    expect(sortObjectKeysDeep(zipPack.features)).toEqual(sortObjectKeysDeep(dirPack.features));
    expect(sortObjectKeysDeep(zipPack.charts)).toEqual(sortObjectKeysDeep(dirPack.charts));
  }, 30_000);
});
