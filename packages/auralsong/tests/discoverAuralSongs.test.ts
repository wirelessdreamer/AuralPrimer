import { discoverAuralSongs } from "../src/discoverAuralSongs";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("discoverAuralSongs", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("finds zip AuralSongs (*.auralsong files) in top-level folder", async () => {
    dir = tmpDir();

    writeFileSync(path.join(dir, "A.auralsong"), "not really a zip");
    writeFileSync(path.join(dir, "ignore.txt"), "x");

    const found = await discoverAuralSongs(dir);

    expect(found).toEqual([
      {
        name: "A.auralsong",
        kind: "zip",
        path: path.join(dir, "A.auralsong")
      }
    ]);
  });

  it("finds directory AuralSongs (*.auralsong directories) in top-level folder", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "B.auralsong"));
    writeFileSync(path.join(dir, "B.auralsong", "manifest.json"), "{}");

    const found = await discoverAuralSongs(dir);

    expect(found).toEqual([
      {
        name: "B.auralsong",
        kind: "directory",
        path: path.join(dir, "B.auralsong")
      }
    ]);
  });

  it("if recursive=true, also finds AuralSongs in nested folders", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "nested"));
    writeFileSync(path.join(dir, "nested", "C.auralsong"), "x");

    const foundNonRecursive = await discoverAuralSongs(dir, { recursive: false });
    expect(foundNonRecursive).toEqual([]);

    const foundRecursive = await discoverAuralSongs(dir, { recursive: true });
    expect(foundRecursive).toEqual([
      {
        name: "C.auralsong",
        kind: "zip",
        path: path.join(dir, "nested", "C.auralsong")
      }
    ]);
  });

  it("sorts deterministically", async () => {
    dir = tmpDir();

    mkdirSync(path.join(dir, "Z.auralsong"));
    writeFileSync(path.join(dir, "A.auralsong"), "x");

    const found = await discoverAuralSongs(dir);
    expect(found.map((x) => x.name)).toEqual(["A.auralsong", "Z.auralsong"]);
  });
});
