/**
 * Cross-tool regression test: a SongPack written by aural_ingest must be both
 * discoverable AND pass the TS-side manifest type guard.
 *
 * Context: a Psalm 19 SongPack imported via `aural_ingest import` did not
 * surface in the game's Play Songs panel. The fix is to lock the contract
 * between the Python emitter and the game-side validators. The matching
 * Python-side test is at python/ingest/tests/test_songpack_manifest_schema.py;
 * the Rust-side test is the `manifest_scan_tests` module in
 * apps/game/src-tauri/src/lib.rs.
 *
 * This file binds the *TypeScript* side: discovery + type guard against the
 * exact JSON shape aural_ingest writes.
 */
import { discoverSongPacks } from "../src/discoverSongPacks";
import { isSongPackManifest } from "../src/manifest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

/** Snapshot of the JSON `aural_ingest` writes to <pack>/manifest.json after
 *  the init_songpack stage (post-decode/duration update). Kept in sync with
 *  the Python source via the `_emit_init_manifest` helper in
 *  test_songpack_manifest_schema.py. */
const AURAL_INGEST_MANIFEST = {
  schema_version: "1.0.0",
  song_id: "3333ca45d864ceb68a9e7e5bc4932546",
  title: "Heaven Whispered",
  artist: "Book of Psalms (Psalm 19)",
  duration_sec: 288.44,
  source: {
    original_filename: "Psalm 19 - Heaven Whispered.wav",
    original_sha256:
      "0d388fabf6169dca693adde70a816356841b0e2ed2b219a30d30fdda4965e8e1",
    ingest_timestamp: "2026-05-21T20:15:21Z",
  },
  timing: {
    audio_sample_rate_hz: 44100,
    audio_start_offset_sec: 0.0,
    timebase: "audio",
  },
  pipeline: {
    pipeline_id: "aural_ingest",
    pipeline_version: "0.1.0",
    profile: "gameplay_default",
    stage_fingerprints: { init_songpack: "0.1.0" },
    transcription: { drum_engine: "combined_filter" },
  },
  recognition: {},
  assets: {
    audio: {
      mix_path: "audio/mix.wav",
      stems: { drums: "audio/stems/drums.wav" },
    },
    features: {
      beats_path: "features/beats.json",
      sections_path: "features/sections.json",
      tempo_map_path: "features/tempo_map.json",
    },
    midi: { notes_path: "features/notes.mid" },
  },
};

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-aural-ingest-"));
}

describe("aural_ingest songpack discovery + validation", () => {
  let dir: string | undefined;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("manifest emitted by aural_ingest passes isSongPackManifest type guard", () => {
    expect(isSongPackManifest(AURAL_INGEST_MANIFEST)).toBe(true);
  });

  it("discoverSongPacks finds an aural_ingest-produced directory songpack", async () => {
    dir = tmpDir();

    const packDir = path.join(dir, "psalm_19_heaven_whispered.songpack");
    mkdirSync(packDir);
    writeFileSync(
      path.join(packDir, "manifest.json"),
      JSON.stringify(AURAL_INGEST_MANIFEST, null, 2)
    );

    const found = await discoverSongPacks(dir);
    expect(found).toEqual([
      {
        name: "psalm_19_heaven_whispered.songpack",
        kind: "directory",
        path: packDir,
      },
    ]);
  });

  it("type guard rejects partial manifests missing a required field", () => {
    // Catch regressions in the emitter that drop a required key.
    const requiredFields = [
      "schema_version",
      "song_id",
      "title",
      "artist",
      "duration_sec",
    ] as const;

    for (const field of requiredFields) {
      const broken = { ...AURAL_INGEST_MANIFEST } as Record<string, unknown>;
      delete broken[field];
      expect(isSongPackManifest(broken)).toBe(false);
    }
  });

  it("type guard tolerates aural_ingest's empty-string artist for unknown sources", () => {
    // Suno/Demucs imports often have no artist metadata; aural_ingest's
    // `args.artist or ""` fallback must not make the songpack invalid.
    const minimalArtist = { ...AURAL_INGEST_MANIFEST, artist: "" };
    expect(isSongPackManifest(minimalArtist)).toBe(true);
  });

  it("type guard tolerates aural_ingest's zero-duration init-stage manifest", () => {
    // After init_songpack runs but before decode_audio updates duration,
    // the songpack briefly has duration_sec=0.0 on disk. A user who
    // refreshes the library during this window must still see the entry.
    const partial = { ...AURAL_INGEST_MANIFEST, duration_sec: 0.0 };
    expect(isSongPackManifest(partial)).toBe(true);
  });

  it("type guard rejects schema_version as int (silent-failure guard)", () => {
    // If a future emitter regression types schema_version as a number, the
    // Rust parser silently maps it to None and the UI shows '(missing
    // schema)'. The TS guard must catch this loudly at the validateSongPack
    // step before it reaches the picker.
    const broken = { ...AURAL_INGEST_MANIFEST, schema_version: 1 };
    expect(isSongPackManifest(broken)).toBe(false);
  });

  it("type guard rejects duration_sec as string", () => {
    const broken = { ...AURAL_INGEST_MANIFEST, duration_sec: "288.44" };
    expect(isSongPackManifest(broken)).toBe(false);
  });
});
