import path from "node:path";

import { validateAuralSong } from "../src/validateAuralSong";

const FIXTURES_DIR = path.join(process.cwd(), "assets", "test_fixtures", "auralsongs");

/**
 * Contract tests: fixtures must remain schema-valid.
 *
 * These are intended to be fast and always-on in CI.
 */
describe("AuralSong fixture schema validation", () => {
  it("validates minimal_valid.auralsong fixture", async () => {
    const p = path.join(FIXTURES_DIR, "minimal_valid.auralsong");
    const res = await validateAuralSong(p);
    expect(res.ok).toBe(true);
    expect(res.issues).toEqual([]);
  });
});
