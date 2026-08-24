import { indexSongLibrary, libraryItemFromEntry, type LibraryEntry } from "../src/indexSongLibrary";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { zipSync, strToU8 } from "fflate";
import path from "node:path";
import os from "node:os";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

function makeDirAuralSong(root: string, name: string, manifest: object): string {
  const sp = path.join(root, name);
  mkdirSync(sp);
  writeFileSync(path.join(sp, "manifest.json"), JSON.stringify(manifest));
  return sp;
}

describe("indexSongLibrary", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("parses directory AuralSongs with valid manifest.json", async () => {
    dir = tmpDir();

    makeDirAuralSong(dir, "Good.auralsong", {
      schema_version: "1.0.0",
      song_id: "abc",
      title: "Song",
      artist: "Artist",
      duration_sec: 12.34
    });

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      name: "Good.auralsong",
      kind: "directory",
      parsed: true
    });

    const e = entries[0] as LibraryEntry;
    if (e.parsed) {
      expect(e.manifest.title).toBe("Song");
      expect(e.manifest.schema_version).toBe("1.0.0");
    }
  });

  it("marks directory AuralSongs missing manifest.json as unparsed", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "Missing.auralsong"));

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      kind: "directory",
      name: "Missing.auralsong",
      path: path.join(dir, "Missing.auralsong"),
      parsed: false,
      reason: "missing_manifest"
    });
  });

  it("marks invalid manifest.json as unparsed", async () => {
    dir = tmpDir();

    makeDirAuralSong(dir, "Invalid.auralsong", {
      schema_version: "1.0.0",
      title: "Song"
    });

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      kind: "directory",
      name: "Invalid.auralsong",
      path: path.join(dir, "Invalid.auralsong"),
      parsed: false,
      reason: "invalid_manifest"
    });
  });

  it("parses zip AuralSongs by reading manifest.json inside the zip", async () => {
    dir = tmpDir();

    const zipBytes = zipSync({
      "manifest.json": strToU8(
        JSON.stringify({
          schema_version: "1.0.0",
          song_id: "zip-1",
          title: "ZipSong",
          artist: "ZipArtist",
          duration_sec: 1.23
        })
      )
    });
    writeFileSync(path.join(dir, "Zip.auralsong"), Buffer.from(zipBytes));

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: "zip", name: "Zip.auralsong", parsed: true });

    const e = entries[0] as LibraryEntry;
    if (e.parsed) {
      expect(e.manifest.title).toBe("ZipSong");
    }
  });

  it("marks zip AuralSongs missing manifest.json as unparsed", async () => {
    dir = tmpDir();

    const zipBytes = zipSync({
      "not-manifest.json": strToU8("{}").slice()
    });
    writeFileSync(path.join(dir, "NoManifest.auralsong"), Buffer.from(zipBytes));

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      kind: "zip",
      name: "NoManifest.auralsong",
      path: path.join(dir, "NoManifest.auralsong"),
      parsed: false,
      reason: "missing_manifest"
    });
  });

  it("dates every entry so the library can sort by recently added", async () => {
    dir = tmpDir();

    makeDirAuralSong(dir, "Dated.auralsong", {
      schema_version: "1.0.0",
      song_id: "dated",
      title: "Dated",
      artist: "Artist",
      duration_sec: 1
    });
    mkdirSync(path.join(dir, "Undatable.auralsong"));

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(2);
    for (const e of entries) {
      expect(e.addedAtMs).toBeGreaterThan(0);
      expect(e.addedAtMs).toBeLessThanOrEqual(Date.now() + 1000);
    }
  });
});

describe("libraryItemFromEntry", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("projects a parsed entry onto the sortable library shape", async () => {
    dir = tmpDir();

    makeDirAuralSong(dir, "Prelude.auralsong", {
      schema_version: "1.0.0",
      song_id: "prelude",
      title: "Prelude in C",
      artist: "J.S. Bach",
      duration_sec: 118.5
    });

    const [entry] = await indexSongLibrary(dir);
    const item = libraryItemFromEntry(entry);

    expect(item).toMatchObject({
      path: path.join(dir, "Prelude.auralsong"),
      title: "Prelude in C",
      composer: "J.S. Bach",
      durationSec: 118.5,
      ok: true
    });
    expect(item.addedAtMs).toBe(entry.addedAtMs);
  });

  it("keeps an unparseable pack visible, falling back to its basename", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "Broken.auralsong"));

    const [entry] = await indexSongLibrary(dir);

    expect(libraryItemFromEntry(entry)).toMatchObject({
      title: "Broken.auralsong",
      composer: "",
      durationSec: null,
      ok: false
    });
  });
});
