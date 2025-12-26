import path from "node:path";
import { promises as fs } from "node:fs";

import { loadSongPackFromDirectory, loadSongPackFromZip, loadSongPack } from "../src/loadSongPack";
import { writeSongPackZipFromDirectory } from "../src/buildSongPackZip";

const FIXTURE_DIR = path.join(process.cwd(), "assets/test_fixtures/songpacks/minimal_valid.songpack");

describe("loadSongPack", () => {
  it("loads a directory songpack", async () => {
    const pack = await loadSongPackFromDirectory(FIXTURE_DIR);

    expect(pack.containerKind).toBe("directory");
    expect(pack.manifest).toBeTruthy();

    expect(pack.features.beats).toBeTruthy();
    expect(pack.features.tempo_map).toBeTruthy();
    expect(pack.features.sections).toBeTruthy();
    expect(pack.features.events).toBeTruthy();

    expect(Object.keys(pack.charts)).toContain("charts/easy.json");

    const files = pack.listFiles();
    expect(files).toContain("manifest.json");
    expect(files).toContain("features/beats.json");
    expect(files).toContain("charts/easy.json");

    const chart = await pack.readJson("charts/easy.json");
    expect(chart).toBeTruthy();
  });

  it("loads a zip songpack (built from fixture)", async () => {
    const tmp = path.join(process.cwd(), ".tmp-tests");
    await fs.mkdir(tmp, { recursive: true });

    const zipPath = path.join(tmp, "minimal_valid.songpack");
    await writeSongPackZipFromDirectory(FIXTURE_DIR, zipPath);

    const pack = await loadSongPackFromZip(zipPath);
    expect(pack.containerKind).toBe("zip");

    const files = pack.listFiles();
    expect(files).toContain("manifest.json");
    expect(files).toContain("charts/easy.json");

    const m = pack.manifest as any;
    expect(m.title).toBeTruthy();
  });

  it("auto-detects container kind", async () => {
    const pack = await loadSongPack(FIXTURE_DIR);
    expect(pack.containerKind).toBe("directory");
  });
});
