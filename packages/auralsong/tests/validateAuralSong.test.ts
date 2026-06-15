import { validateAuralSong } from "../src/validateAuralSong";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { zipSync, strToU8 } from "fflate";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("validateAuralSong", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("validates a minimal directory AuralSong with manifest.json", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Good.auralsong");
    mkdirSync(sp);
    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    const res = await validateAuralSong(sp);
    expect(res.containerKind).toBe("directory");
    expect(res.ok).toBe(true);
    expect(res.issues).toEqual([]);
  });

  it("reports missing manifest.json in directory AuralSong", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Bad.auralsong");
    mkdirSync(sp);

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.path === "manifest.json" && i.code === "missing_required_file")).toBe(true);
  });

  it("reports missing manifest-declared asset files", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "MissingAsset.auralsong");
    mkdirSync(sp);
    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({
        schema_version: "1.0.0",
        song_id: "a",
        title: "t",
        artist: "r",
        duration_sec: 1,
        assets: {
          audio: {
            mix_path: "audio/mix.wav"
          }
        }
      })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.path === "audio/mix.wav" && i.code === "missing_required_file")).toBe(true);
  });

  it("validates a minimal zip AuralSong with manifest.json", async () => {
    dir = tmpDir();
    const zipBytes = zipSync({
      "manifest.json": strToU8(
        JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
      )
    });
    const p = path.join(dir, "GoodZip.auralsong");
    writeFileSync(p, Buffer.from(zipBytes));

    const res = await validateAuralSong(p);
    expect(res.containerKind).toBe("zip");
    expect(res.ok).toBe(true);
  });

  it("reports schema_invalid when manifest is present but invalid", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Invalid.auralsong");
    mkdirSync(sp);
    writeFileSync(path.join(sp, "manifest.json"), JSON.stringify({ schema_version: "1.0.0" }));

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.code === "schema_invalid" && i.path === "manifest.json")).toBe(true);
  });

  it("reports schema_invalid when schema_version is not semver", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "InvalidVersion.auralsong");
    mkdirSync(sp);
    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "v1", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.code === "schema_invalid" && i.path === "manifest.json#/schema_version")).toBe(true);
  });

  it("reports unsupported when schema_version is not supported", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "UnsupportedVersion.auralsong");
    mkdirSync(sp);
    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "2.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.code === "unsupported" && i.path === "manifest.json#/schema_version")).toBe(true);
  });

  it("validates optional features if present", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Feat.auralsong");
    mkdirSync(path.join(sp, "features"), { recursive: true });

    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );
    writeFileSync(
      path.join(sp, "features", "beats.json"),
      JSON.stringify({ beats_version: "1.0.0", beats: [{ t: 0, bar: 0, beat: 0 }] })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(true);
  });

  it("reports out-of-range event times", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "BadEvents.auralsong");
    mkdirSync(path.join(sp, "features"), { recursive: true });

    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 10 })
    );
    writeFileSync(
      path.join(sp, "features", "events.json"),
      JSON.stringify({
        events_version: "1.0.0",
        notes: [{ track_id: "guitar", t_on: 1, t_off: 12 }]
      })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(false);
    expect(res.issues.some((i) => i.path === "features/events.json#/notes/0/t_off" && i.code === "schema_invalid")).toBe(true);
  });

  it("validates charts/*.json if present", async () => {
    dir = tmpDir();
    const sp = path.join(dir, "Charts.auralsong");
    mkdirSync(path.join(sp, "charts"), { recursive: true });

    writeFileSync(
      path.join(sp, "manifest.json"),
      JSON.stringify({ schema_version: "1.0.0", song_id: "a", title: "t", artist: "r", duration_sec: 1 })
    );

    writeFileSync(
      path.join(sp, "charts", "easy.json"),
      JSON.stringify({ chart_version: "1.0.0", mode: "drums_groove", difficulty: "easy", targets: [] })
    );

    const res = await validateAuralSong(sp);
    expect(res.ok).toBe(true);
  });
});
