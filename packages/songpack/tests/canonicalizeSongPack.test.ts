import path from "node:path";
import { promises as fs } from "node:fs";

import { buildSongPackZipFromDirectory } from "../src/buildSongPackZip";
import { canonicalizeSongPackZipBytes, canonicalizeSongPackDirectory } from "../src/canonicalizeSongPack";

const FIXTURE_DIR = path.join(process.cwd(), "assets/test_fixtures/songpacks/minimal_valid.songpack");

describe("canonicalizeSongPack", () => {
  it("canonicalizes a directory songpack in-place (idempotent)", async () => {
    const tmp = path.join(process.cwd(), ".tmp-tests", "canon-dir");
    await fs.rm(tmp, { recursive: true, force: true });
    await fs.mkdir(tmp, { recursive: true });

    // Copy fixture into tmp.
    await fs.cp(FIXTURE_DIR, tmp, { recursive: true });

    // Run twice; should be stable.
    await canonicalizeSongPackDirectory(tmp);
    const after1 = await fs.readFile(path.join(tmp, "manifest.json"), "utf-8");

    await canonicalizeSongPackDirectory(tmp);
    const after2 = await fs.readFile(path.join(tmp, "manifest.json"), "utf-8");

    expect(after2).toBe(after1);
  });

  it("canonicalizes zip bytes deterministically", async () => {
    const zip1 = await buildSongPackZipFromDirectory(FIXTURE_DIR);
    const zip2 = await buildSongPackZipFromDirectory(FIXTURE_DIR);

    const c1 = await canonicalizeSongPackZipBytes(zip1);
    const c2 = await canonicalizeSongPackZipBytes(zip2);

    expect(Buffer.from(c1).equals(Buffer.from(c2))).toBe(true);
  });
});
