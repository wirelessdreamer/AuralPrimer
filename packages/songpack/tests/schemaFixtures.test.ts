import path from "node:path";

import { validateSongPack } from "../src/validateSongPack";

const FIXTURES_DIR = path.join(process.cwd(), "assets", "test_fixtures", "songpacks");

/**
 * Contract tests: fixtures must remain schema-valid.
 *
 * These are intended to be fast and always-on in CI.
 */
describe("SongPack fixture schema validation", () => {
  it("validates minimal_valid.songpack fixture", async () => {
    const p = path.join(FIXTURES_DIR, "minimal_valid.songpack");
    const res = await validateSongPack(p);
    expect(res.ok).toBe(true);
    expect(res.issues).toEqual([]);
  });
});
