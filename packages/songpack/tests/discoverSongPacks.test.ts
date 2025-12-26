import { discoverSongPacks } from "../src/discoverSongPacks";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("discoverSongPacks", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("finds zip SongPacks (*.songpack files) in top-level folder", async () => {
    dir = tmpDir();

    writeFileSync(path.join(dir, "A.songpack"), "not really a zip");
    writeFileSync(path.join(dir, "ignore.txt"), "x");

    const found = await discoverSongPacks(dir);

    expect(found).toEqual([
      {
        name: "A.songpack",
        kind: "zip",
        path: path.join(dir, "A.songpack")
      }
    ]);
  });

  it("finds directory SongPacks (*.songpack directories) in top-level folder", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "B.songpack"));
    writeFileSync(path.join(dir, "B.songpack", "manifest.json"), "{}");

    const found = await discoverSongPacks(dir);

    expect(found).toEqual([
      {
        name: "B.songpack",
        kind: "directory",
        path: path.join(dir, "B.songpack")
      }
    ]);
  });

  it("if recursive=true, also finds SongPacks in nested folders", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "nested"));
    writeFileSync(path.join(dir, "nested", "C.songpack"), "x");

    const foundNonRecursive = await discoverSongPacks(dir, { recursive: false });
    expect(foundNonRecursive).toEqual([]);

    const foundRecursive = await discoverSongPacks(dir, { recursive: true });
    expect(foundRecursive).toEqual([
      {
        name: "C.songpack",
        kind: "zip",
        path: path.join(dir, "nested", "C.songpack")
      }
    ]);
  });

  it("sorts deterministically", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "Z.songpack"));
    writeFileSync(path.join(dir, "A.songpack"), "x");

    const found = await discoverSongPacks(dir);
    expect(found.map((x) => x.name)).toEqual(["A.songpack", "Z.songpack"]);
  });
});
