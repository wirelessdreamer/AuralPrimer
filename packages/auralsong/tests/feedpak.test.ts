import path from "node:path";
import { promises as fs } from "node:fs";
import { fileURLToPath } from "node:url";

import { parseFeedpakManifest } from "../src/feedpak/parseManifest";
import { validateFeedpakManifest } from "../src/feedpak/validateManifest";
import { isFeedpakManifest } from "../src/feedpak/manifest";
import { loadFeedpak, loadFeedpakFromDirectory } from "../src/feedpak/loadFeedpak";

// Resolve the committed fixture relative to this test file so the suite is
// independent of the process working directory.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(HERE, "../../feedpak/fixtures/minimal.feedpak");
const MANIFEST_PATH = path.join(FIXTURE_DIR, "manifest.yaml");

describe("feedpak parseManifest", () => {
  it("parses the minimal fixture manifest with typed fields preserved", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);

    expect(manifest.feedpak_version).toBe("1.11.0");
    expect(manifest.title).toBe("Minimal Fixture");
    expect(manifest.artist).toBe("AuralPrimer");
    expect(manifest.duration).toBe(2.0);

    expect(manifest.arrangements).toHaveLength(1);
    expect(manifest.arrangements[0]?.id).toBe("keys");
    expect(manifest.arrangements[0]?.type).toBe("piano");
    expect(manifest.arrangements[0]?.notation).toBe("arrangements/notation_keys.json");

    expect(manifest.stems).toHaveLength(1);
    expect(manifest.stems[0]?.id).toBe("keys");
    expect(manifest.stems[0]?.file).toBe("audio/stems/keys.wav");
    expect(manifest.stems[0]?.default).toBe(true);

    expect(manifest.song_timeline).toBe("song_timeline.json");

    expect(isFeedpakManifest(manifest)).toBe(true);
  });

  it("retains the AuralPrimer extension key and arbitrary extra keys", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    // Append an arbitrary, non-modelled key to prove the index signature
    // lets unknown keys survive a parse round-trip untouched.
    const augmented = `${yamlText}\nx_unmodelled_key: keep-me\n`;
    const manifest = parseFeedpakManifest(augmented);

    // AuralPrimer extension key from the fixture.
    expect(manifest.aural_notes_mid).toBe("aural/notes.mid");
    // Arbitrary extension key passed through the index signature.
    expect(manifest["x_unmodelled_key"]).toBe("keep-me");
  });
});

describe("feedpak loadFeedpak", () => {
  it("loads the directory fixture and resolves manifest pointers", async () => {
    const pack = await loadFeedpakFromDirectory(FIXTURE_DIR);

    expect(pack.containerKind).toBe("directory");
    expect(pack.manifest.title).toBe("Minimal Fixture");

    // Arrangement pointer (notation) resolves and exists on disk.
    expect(pack.arrangements).toHaveLength(1);
    const arr = pack.arrangements[0]!;
    expect(arr.notation?.path).toBe("arrangements/notation_keys.json");
    expect(arr.notation?.exists).toBe(true);

    // Stem pointer resolves and exists on disk.
    expect(pack.stems).toHaveLength(1);
    expect(pack.stems[0]!.file.path).toBe("audio/stems/keys.wav");
    expect(pack.stems[0]!.file.exists).toBe(true);

    // song_timeline pointer resolves and exists.
    expect(pack.songTimeline?.path).toBe("song_timeline.json");
    expect(pack.songTimeline?.exists).toBe(true);

    // AuralPrimer extension pointer resolves and exists.
    expect(pack.auralNotesMid?.path).toBe("aural/notes.mid");
    expect(pack.auralNotesMid?.exists).toBe(true);

    // Bytes are not read eagerly; content is pulled lazily.
    const files = pack.listFiles();
    expect(files).toContain("manifest.yaml");
    expect(files).toContain("audio/stems/keys.wav");

    const timeline = await pack.readJson("song_timeline.json");
    expect(timeline).toBeTruthy();
  });

  it("auto-detects the directory container kind", async () => {
    const pack = await loadFeedpak(FIXTURE_DIR);
    expect(pack.containerKind).toBe("directory");
  });
});

describe("feedpak validateManifest", () => {
  it("validates the fixture manifest against the schema", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);

    const res = validateFeedpakManifest(manifest);
    expect(res.ok).toBe(true);
    expect(res.value?.title).toBe("Minimal Fixture");
  });

  it("rejects a deliberately broken manifest (missing required fields)", () => {
    const broken = {
      feedpak_version: "1.11.0",
      title: "Broken"
      // missing artist, duration, arrangements, stems
    };

    const res = validateFeedpakManifest(broken);
    expect(res.ok).toBe(false);
    expect(res.errors?.length).toBeGreaterThan(0);
  });
});
