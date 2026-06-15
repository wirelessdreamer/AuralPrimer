import path from "node:path";
import os from "node:os";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { unzipSync, strFromU8 } from "fflate";

import { buildAuralSongZipFromDirectory } from "../src/buildAuralSongZip";
import { validateAuralSong } from "../src/validateAuralSong";

const FIXTURES_DIR = path.join(process.cwd(), "assets", "test_fixtures", "auralsongs");

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("AuralSong deliverable: deterministic zip", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("builds a .auralsong zip from a directory fixture and it validates", async () => {
    const spDir = path.join(FIXTURES_DIR, "minimal_valid.auralsong");
    const bytes = await buildAuralSongZipFromDirectory(spDir);

    dir = tmpDir();
    const outPath = path.join(dir, "fixture.auralsong");
    writeFileSync(outPath, Buffer.from(bytes));

    const res = await validateAuralSong(outPath);
    expect(res.ok).toBe(true);
  });

  it("is byte-for-byte deterministic across runs", async () => {
    const spDir = path.join(FIXTURES_DIR, "minimal_valid.auralsong");

    const a = await buildAuralSongZipFromDirectory(spDir);
    const b = await buildAuralSongZipFromDirectory(spDir);

    expect(Buffer.from(a).equals(Buffer.from(b))).toBe(true);
  });

  it("canonicalizes JSON key ordering inside the zip", async () => {
    dir = tmpDir();
    const spDir = path.join(dir, "Weird.auralsong");
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

    const bytes = await buildAuralSongZipFromDirectory(spDir, { json: { floatEpsilon: undefined } });
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
