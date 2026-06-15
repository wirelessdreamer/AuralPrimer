import path from "node:path";
import { promises as fs } from "node:fs";

import { loadAuralSongFromDirectory, loadAuralSongFromZip, loadAuralSong } from "../src/loadAuralSong";
import { writeAuralSongZipFromDirectory } from "../src/buildAuralSongZip";

const FIXTURE_DIR = path.join(process.cwd(), "assets/test_fixtures/auralsongs/minimal_valid.auralsong");

describe("loadAuralSong", () => {
  it("loads a directory auralsong", async () => {
    const pack = await loadAuralSongFromDirectory(FIXTURE_DIR);

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

  it("loads a zip auralsong (built from fixture)", async () => {
    const tmp = path.join(process.cwd(), ".tmp-tests");
    await fs.mkdir(tmp, { recursive: true });

    const zipPath = path.join(tmp, "minimal_valid.auralsong");
    await writeAuralSongZipFromDirectory(FIXTURE_DIR, zipPath);

    const pack = await loadAuralSongFromZip(zipPath);
    expect(pack.containerKind).toBe("zip");

    const files = pack.listFiles();
    expect(files).toContain("manifest.json");
    expect(files).toContain("charts/easy.json");

    const m = pack.manifest as any;
    expect(m.title).toBeTruthy();
  });

  it("auto-detects container kind", async () => {
    const pack = await loadAuralSong(FIXTURE_DIR);
    expect(pack.containerKind).toBe("directory");
  });
});
