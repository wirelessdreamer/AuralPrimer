import path from "node:path";
import os from "node:os";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { unzipSync, strFromU8 } from "fflate";

import { buildSongPackZipFromDirectory } from "../src/buildSongPackZip";
import { validateSongPack } from "../src/validateSongPack";

const FIXTURES_DIR = path.join(process.cwd(), "assets", "test_fixtures", "songpacks");

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("SongPack deliverable: deterministic zip", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("builds a .songpack zip from a directory fixture and it validates", async () => {
    const spDir = path.join(FIXTURES_DIR, "minimal_valid.songpack");
    const bytes = await buildSongPackZipFromDirectory(spDir);

    dir = tmpDir();
    const outPath = path.join(dir, "fixture.songpack");
    writeFileSync(outPath, Buffer.from(bytes));

    const res = await validateSongPack(outPath);
    expect(res.ok).toBe(true);
  });

  it("is byte-for-byte deterministic across runs", async () => {
    const spDir = path.join(FIXTURES_DIR, "minimal_valid.songpack");

    const a = await buildSongPackZipFromDirectory(spDir);
    const b = await buildSongPackZipFromDirectory(spDir);

    expect(Buffer.from(a).equals(Buffer.from(b))).toBe(true);
  });

  it("canonicalizes JSON key ordering inside the zip", async () => {
    dir = tmpDir();
    const spDir = path.join(dir, "Weird.songpack");
    mkdirSync(path.join(spDir, "features"), { recursive: true });

    // Intentionally out-of-order keys.
    writeFileSync(
      path.join(spDir, "manifest.json"),
      JSON.stringify({ title: "t", schema_version: "1.0.0", duration_sec: 1, artist: "a", song_id: "id" })
    );

    writeFileSync(
      path.join(spDir, "features", "beats.json"),
      JSON.stringify({ beats: [{ beat: 0, bar: 0, t: 0 }], beats_version: "1.0.0" })
    );

    const bytes = await buildSongPackZipFromDirectory(spDir, { json: { floatEpsilon: undefined } });
    const files = unzipSync(bytes);

    const manifestText = strFromU8(files["manifest.json"]);
    // Sorted keys: artist, duration_sec, schema_version, song_id, title.
    expect(manifestText).toContain('"artist": "a"');
    expect(manifestText.indexOf('"artist"')).toBeLessThan(manifestText.indexOf('"duration_sec"'));
    expect(manifestText.indexOf('"duration_sec"')).toBeLessThan(manifestText.indexOf('"schema_version"'));
    expect(manifestText.indexOf('"schema_version"')).toBeLessThan(manifestText.indexOf('"song_id"'));
    expect(manifestText.indexOf('"song_id"')).toBeLessThan(manifestText.indexOf('"title"'));

    const beatsText = strFromU8(files["features/beats.json"]);
    expect(beatsText.indexOf('"beats"')).toBeLessThan(beatsText.indexOf('"beats_version"'));
  });
});
