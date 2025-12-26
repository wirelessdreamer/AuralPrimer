import { indexSongLibrary, type LibraryEntry } from "../src/indexSongLibrary";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { zipSync, strToU8 } from "fflate";
import path from "node:path";
import os from "node:os";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

function makeDirSongPack(root: string, name: string, manifest: object): string {
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

  it("parses directory SongPacks with valid manifest.json", async () => {
    dir = tmpDir();

    makeDirSongPack(dir, "Good.songpack", {
      schema_version: "1.0.0",
      song_id: "abc",
      title: "Song",
      artist: "Artist",
      duration_sec: 12.34
    });

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      name: "Good.songpack",
      kind: "directory",
      parsed: true
    });

    const e = entries[0] as LibraryEntry;
    if (e.parsed) {
      expect(e.manifest.title).toBe("Song");
      expect(e.manifest.schema_version).toBe("1.0.0");
    }
  });

  it("marks directory SongPacks missing manifest.json as unparsed", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "Missing.songpack"));

    const entries = await indexSongLibrary(dir);

    expect(entries).toEqual([
      {
        kind: "directory",
        name: "Missing.songpack",
        path: path.join(dir, "Missing.songpack"),
        parsed: false,
        reason: "missing_manifest"
      }
    ]);
  });

  it("marks invalid manifest.json as unparsed", async () => {
    dir = tmpDir();

    makeDirSongPack(dir, "Invalid.songpack", {
      schema_version: "1.0.0",
      title: "Song"
    });

    const entries = await indexSongLibrary(dir);

    expect(entries).toEqual([
      {
        kind: "directory",
        name: "Invalid.songpack",
        path: path.join(dir, "Invalid.songpack"),
        parsed: false,
        reason: "invalid_manifest"
      }
    ]);
  });

  it("parses zip SongPacks by reading manifest.json inside the zip", async () => {
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
    writeFileSync(path.join(dir, "Zip.songpack"), Buffer.from(zipBytes));

    const entries = await indexSongLibrary(dir);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: "zip", name: "Zip.songpack", parsed: true });

    const e = entries[0] as LibraryEntry;
    if (e.parsed) {
      expect(e.manifest.title).toBe("ZipSong");
    }
  });

  it("marks zip SongPacks missing manifest.json as unparsed", async () => {
    dir = tmpDir();

    const zipBytes = zipSync({
      "not-manifest.json": strToU8("{}").slice()
    });
    writeFileSync(path.join(dir, "NoManifest.songpack"), Buffer.from(zipBytes));

    const entries = await indexSongLibrary(dir);

    expect(entries).toEqual([
      {
        kind: "zip",
        name: "NoManifest.songpack",
        path: path.join(dir, "NoManifest.songpack"),
        parsed: false,
        reason: "missing_manifest"
      }
    ]);
  });
});
