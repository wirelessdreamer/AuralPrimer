import path from "node:path";
import { promises as fs } from "node:fs";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { zipSync, type Zippable } from "fflate";

import { parseFeedpakManifest } from "../src/feedpak/parseManifest";
import { validateFeedpakManifest } from "../src/feedpak/validateManifest";
import { isFeedpakManifest } from "../src/feedpak/manifest";
import { loadFeedpak, loadFeedpakFromDirectory, loadFeedpakFromZipBytes } from "../src/feedpak/loadFeedpak";

// Resolve the committed fixture relative to this test file so the suite is
// independent of the process working directory.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(HERE, "../../feedpak/fixtures/minimal.feedpak");
const MANIFEST_PATH = path.join(FIXTURE_DIR, "manifest.yaml");

async function fixtureZipBytes(): Promise<Uint8Array> {
  const files: Zippable = {};
  async function addDir(absDir: string, prefix = ""): Promise<void> {
    for (const entry of await fs.readdir(absDir, { withFileTypes: true })) {
      const abs = path.join(absDir, entry.name);
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        await addDir(abs, rel);
      } else {
        const raw = await fs.readFile(abs);
        files[rel] = new Uint8Array(raw);
      }
    }
  }
  await addDir(FIXTURE_DIR);
  return zipSync(files, { level: 0 });
}

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
    expect(manifest.drum_tab).toBe("drum_tab.json");
    expect(manifest.vocal_pitch).toBe("vocal_pitch.json");
    expect(manifest.vocal_pitch_contour).toBe("vocal_pitch_contour.json");
    expect(manifest.pitch_extraction).toMatchObject({
      engine: "aural_ingest",
      model: "melodic_rmvpe",
      version: "1.0.0",
    });
    expect(manifest.keys).toBe("keys.json");
    expect(manifest.harmony).toBe("harmony.json");
    expect(manifest.aural_fingering).toEqual({
      lead_guitar: "aural/fingering.lead_guitar.json",
    });

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

    // drum_tab pointer resolves and exists, including authored velocity.
    expect(pack.drumTab?.path).toBe("drum_tab.json");
    expect(pack.drumTab?.exists).toBe(true);

    // Vocal pitch pointers resolve and exist.
    expect(pack.vocalPitch?.path).toBe("vocal_pitch.json");
    expect(pack.vocalPitch?.exists).toBe(true);
    expect(pack.vocalPitchContour?.path).toBe("vocal_pitch_contour.json");
    expect(pack.vocalPitchContour?.exists).toBe(true);

    // Key/harmony pointers resolve and exist.
    expect(pack.keys?.path).toBe("keys.json");
    expect(pack.keys?.exists).toBe(true);
    expect(pack.harmony?.path).toBe("harmony.json");
    expect(pack.harmony?.exists).toBe(true);

    // AuralPrimer extension pointer resolves and exists.
    expect(pack.auralNotesMid?.path).toBe("aural/notes.mid");
    expect(pack.auralNotesMid?.exists).toBe(true);
    expect(pack.auralFingering?.lead_guitar?.path).toBe("aural/fingering.lead_guitar.json");
    expect(pack.auralFingering?.lead_guitar?.exists).toBe(true);

    // Bytes are not read eagerly; content is pulled lazily.
    const files = pack.listFiles();
    expect(files).toContain("manifest.yaml");
    expect(files).toContain("audio/stems/keys.wav");

    const timeline = await pack.readJson("song_timeline.json");
    expect(timeline).toBeTruthy();
    const drumTab = await pack.readJson(pack.drumTab!.path);
    expect((drumTab as { hits: Array<{ p: string; v?: number }> }).hits[0]).toMatchObject({
      p: "kick",
      v: 96,
    });
    const vocalPitch = await pack.readJson(pack.vocalPitch!.path);
    expect((vocalPitch as { notes: Array<{ midi: number }> }).notes[0]?.midi).toBe(69);
    const vocalContour = await pack.readJson(pack.vocalPitchContour!.path);
    expect((vocalContour as { samples: Array<{ hz: number }> }).samples[0]?.hz).toBe(440);
    const keys = await pack.readJson(pack.keys!.path);
    expect((keys as { events: Array<{ key: string }> }).events[0]?.key).toBe("C");
    const harmony = await pack.readJson(pack.harmony!.path);
    const harmonyEvents = (harmony as { events: Array<{ root: string; quality: string; rn: string }> })
      .events;
    expect(harmonyEvents[0]).toMatchObject({ root: "C", quality: "maj", rn: "I" });
    const fingering = await pack.readJson(pack.auralFingering!.lead_guitar.path);
    expect((fingering as { notes: Array<{ string: number; fret: number }> }).notes[0]).toMatchObject({
      string: 1,
      fret: 5,
    });
  });

  it("auto-detects the directory container kind", async () => {
    const pack = await loadFeedpak(FIXTURE_DIR);
    expect(pack.containerKind).toBe("directory");
  });

  it("loads zipped feedpak model-artifact pointers with directory parity", async () => {
    const pack = await loadFeedpakFromZipBytes(await fixtureZipBytes(), "minimal.feedpak");

    expect(pack.containerKind).toBe("zip");
    expect(pack.songTimeline).toMatchObject({ path: "song_timeline.json", exists: true });
    expect(pack.drumTab).toMatchObject({ path: "drum_tab.json", exists: true });
    expect(pack.keys).toMatchObject({ path: "keys.json", exists: true });
    expect(pack.harmony).toMatchObject({ path: "harmony.json", exists: true });
    expect(pack.vocalPitch).toMatchObject({ path: "vocal_pitch.json", exists: true });
    expect(pack.vocalPitchContour).toMatchObject({ path: "vocal_pitch_contour.json", exists: true });
    expect(pack.auralNotesMid).toMatchObject({ path: "aural/notes.mid", exists: true });
    expect(pack.auralFingering?.lead_guitar).toMatchObject({
      path: "aural/fingering.lead_guitar.json",
      exists: true,
    });
    const vocalPitch = await pack.readJson(pack.vocalPitch!.path);
    expect((vocalPitch as { notes: Array<{ midi: number }> }).notes[0]?.midi).toBe(69);
    const fingering = await pack.readJson(pack.auralFingering!.lead_guitar.path);
    expect((fingering as { notes: Array<{ string: number; fret: number }> }).notes[0]).toMatchObject({
      string: 1,
      fret: 5,
    });
  });

  it("loads safe non-default model artifact pointers", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "auralsong-feedpak-custom-"));
    const feedpakDir = path.join(tmp, "custom.feedpak");
    await fs.mkdir(path.join(feedpakDir, "custom", "aural"), { recursive: true });
    await fs.mkdir(path.join(feedpakDir, "charts"), { recursive: true });
    await fs.mkdir(path.join(feedpakDir, "audio"), { recursive: true });

    await fs.writeFile(path.join(feedpakDir, "charts", "lead.json"), "{}");
    await fs.writeFile(path.join(feedpakDir, "audio", "lead.wav"), "wav");
    await fs.writeFile(path.join(feedpakDir, "custom", "timeline.json"), '{"beats":[]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "drums.json"), '{"hits":[]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "keys-doc.json"), '{"events":[{"key":"D"}]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "harmony-doc.json"), '{"events":[{"root":"D"}]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "vocal-notes.json"), '{"notes":[{"midi":62}]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "vocal-contour.json"), '{"samples":[{"hz":293.66}]}');
    await fs.writeFile(path.join(feedpakDir, "custom", "aural", "notes.mid"), "midi");
    await fs.writeFile(path.join(feedpakDir, "custom", "aural", "fingering.json"), '{"notes":[]}');

    await fs.writeFile(
      path.join(feedpakDir, "manifest.yaml"),
      [
        "feedpak_version: 1.11.0",
        "title: Custom Pointer Fixture",
        "artist: AuralPrimer",
        "duration: 1.0",
        "arrangements:",
        "- id: lead_guitar",
        "  name: Lead",
        "  type: tab",
        "  file: charts/lead.json",
        "stems:",
        "- id: lead_guitar",
        "  file: audio/lead.wav",
        "  default: true",
        "song_timeline: custom/timeline.json",
        "drum_tab: custom/drums.json",
        "keys: custom/keys-doc.json",
        "harmony: custom/harmony-doc.json",
        "vocal_pitch: custom/vocal-notes.json",
        "vocal_pitch_contour: custom/vocal-contour.json",
        "aural_notes_mid: custom/aural/notes.mid",
        "aural_fingering:",
        "  lead_guitar: custom/aural/fingering.json",
        "",
      ].join("\n"),
    );

    const pack = await loadFeedpakFromDirectory(feedpakDir);

    expect(pack.songTimeline).toMatchObject({ path: "custom/timeline.json", exists: true });
    expect(pack.drumTab).toMatchObject({ path: "custom/drums.json", exists: true });
    expect(pack.keys).toMatchObject({ path: "custom/keys-doc.json", exists: true });
    expect(pack.harmony).toMatchObject({ path: "custom/harmony-doc.json", exists: true });
    expect(pack.vocalPitch).toMatchObject({ path: "custom/vocal-notes.json", exists: true });
    expect(pack.vocalPitchContour).toMatchObject({ path: "custom/vocal-contour.json", exists: true });
    expect(pack.auralNotesMid).toMatchObject({ path: "custom/aural/notes.mid", exists: true });
    expect(pack.auralFingering?.lead_guitar).toMatchObject({
      path: "custom/aural/fingering.json",
      exists: true,
    });
    expect((await pack.readJson(pack.keys!.path)) as { events: Array<{ key: string }> }).toMatchObject({
      events: [{ key: "D" }],
    });
  });

  it("treats unsafe manifest pointers as missing and unreadable", async () => {
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "auralsong-feedpak-"));
    const feedpakDir = path.join(tmp, "unsafe.feedpak");
    await fs.mkdir(feedpakDir);
    await fs.mkdir(path.join(feedpakDir, "custom"));
    await fs.writeFile(path.join(feedpakDir, "custom", "keys.json"), "{}");
    await fs.writeFile(path.join(tmp, "outside.json"), "{}");
    await fs.writeFile(path.join(tmp, "outside.mid"), "midi");
    await fs.writeFile(path.join(tmp, "outside.wav"), "wav");
    await fs.writeFile(
      path.join(feedpakDir, "manifest.yaml"),
      [
        "feedpak_version: 1.11.0",
        "title: Unsafe Pointer Fixture",
        "artist: AuralPrimer",
        "duration: 1.0",
        "arrangements:",
        "- id: bad",
        "  name: Bad",
        "  type: chart",
        "  file: ../outside.json",
        "  notation: ../outside.json",
        "stems:",
        "- id: vocals",
        "  file: ../outside.wav",
        "  default: true",
        "song_timeline: ../outside.json",
        "drum_tab: ../outside.json",
        "keys: custom/./keys.json",
        "harmony: ../outside.json",
        "vocal_pitch: ../outside.json",
        "vocal_pitch_contour: ../outside.json",
        "aural_notes_mid: ../outside.mid",
        "aural_fingering:",
        "  lead_guitar: ../outside.json",
        "",
      ].join("\n"),
    );

    const pack = await loadFeedpakFromDirectory(feedpakDir);
    expect(pack.arrangements[0]?.file?.exists).toBe(false);
    expect(pack.arrangements[0]?.notation?.exists).toBe(false);
    expect(pack.stems[0]?.file.exists).toBe(false);
    expect(pack.songTimeline?.exists).toBe(false);
    expect(pack.drumTab?.exists).toBe(false);
    expect(pack.keys?.exists).toBe(false);
    expect(pack.harmony?.exists).toBe(false);
    expect(pack.vocalPitch?.exists).toBe(false);
    expect(pack.vocalPitchContour?.exists).toBe(false);
    expect(pack.auralNotesMid?.exists).toBe(false);
    expect(pack.auralFingering?.lead_guitar.exists).toBe(false);
    await expect(pack.readText("../outside.json")).resolves.toBeNull();
    await expect(pack.readBytes("../outside.wav")).resolves.toBeNull();
    await expect(pack.readJson("../outside.json")).resolves.toBeNull();
    await expect(pack.readJson("custom/./keys.json")).resolves.toBeNull();
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

  it("rejects unsafe AuralPrimer extension relpaths", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);
    manifest.aural_notes_mid = "../escape.mid";
    manifest.aural_spectrogram = "aural\\spectrogram";
    manifest.aural_refine_candidates = { keys: "/escape.json" };
    manifest.aural_fingering = { lead_guitar: "C:/escape.json" };
    manifest.aural_benchmark = "aural/../benchmark";

    const res = validateFeedpakManifest(manifest);
    expect(res.ok).toBe(false);
    expect(res.errors?.map((err) => err.instancePath)).toEqual(
      expect.arrayContaining([
        "/aural_notes_mid",
        "/aural_spectrogram",
        "/aural_refine_candidates/keys",
        "/aural_fingering/lead_guitar",
        "/aural_benchmark",
      ]),
    );
  });

  it("rejects unsupported aural_fingering manifest roles", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);
    manifest.aural_fingering = { drums: "aural/fingering.drums.json" };

    const res = validateFeedpakManifest(manifest);
    expect(res.ok).toBe(false);
    expect(res.errors?.map((err) => err.instancePath)).toContain("/aural_fingering");
  });

  it("rejects relpaths with dot path segments", async () => {
    const yamlText = await fs.readFile(MANIFEST_PATH, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);
    manifest.keys = "custom/./keys.json";
    manifest.aural_fingering = { lead_guitar: "aural/./fingering.json" };

    const res = validateFeedpakManifest(manifest);
    expect(res.ok).toBe(false);
    expect(res.errors?.map((err) => err.instancePath)).toEqual(
      expect.arrayContaining(["/keys", "/aural_fingering/lead_guitar"]),
    );
  });
});
