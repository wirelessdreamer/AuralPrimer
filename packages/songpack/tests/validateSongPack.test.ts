import { validateSongPack } from "../src/validateSongPack";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { zipSync, strToU8 } from "fflate";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("validateSongPack", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("validates a minimal directory SongPack with manifest.json", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Good.songpack");
    mkdirSync(sp);
    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    const res = await validateSongPack(sp);
    expect(res.containerKind).toBe("directory");
    expect(res.ok).toBe(true);
    expect(res.issues).toEqual([]);
  });

  it("reports missing manifest.json in directory SongPack", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Bad.songpack");
    mkdirSync(sp);

    const res = await validateSongPack(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.path === "manifest.json" && i.code === "missing_required_file")).toBe(true);
  });

  it("validates a minimal zip SongPack with manifest.json", async () => {
    dir = tmpDir();
    const zipBytes = zipSync({
      "manifest.json": strToU8(
        JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
      )
    });
    const p = path.join(dir, "GoodZip.songpack");
    writeFileSync(p, Buffer.from(zipBytes));

    const res = await validateSongPack(p);
    expect(res.containerKind).toBe("zip");
    expect(res.ok).toBe(true);
  });

  it("reports schema_invalid when manifest is present but invalid", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Invalid.songpack");
    mkdirSync(sp);
    writeFileSync(path.join(sp, "manifest.json"), JSON.stringify({ schema_version: "1.0.0" }));

    const res = await validateSongPack(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.code === "schema_invalid" && i.path === "manifest.json")).toBe(true);
  });

  it("validates optional features if present", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Feat.songpack");
    mkdirSync(path.join(sp, "features"), { recursive: true });

    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );
    writeFileSync(
      path.join(sp, "features", "beats.json"),
      JSON.stringify({ beats_version: "1.0.0", beats: [{ t: 0, bar: 0, beat: 0 }] })
    );

    const res = await validateSongPack(sp);
    expect(res.ok).toBe(true);
  });

  it("validates charts/*.json if present", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Charts.songpack");
    mkdirSync(path.join(sp, "charts"), { recursive: true });

    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    writeFileSync(
      path.join(sp, "charts", "easy.json"),
      JSON.stringify({ chart_version: "1.0.0", mode: "drums_groove", difficulty: "easy", targets: [] })
    );

    const res = await validateSongPack(sp);
    expect(res.ok).toBe(true);
  });
});
