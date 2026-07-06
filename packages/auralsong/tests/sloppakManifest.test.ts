/**
 * Pins that the feedpak manifest schema (the Rust `FeedpakManifest` parses a
 * sloppak manifest as-is, and TS validates via the vendored feedpak schema)
 * keeps ACCEPTING a real sloppak manifest.
 *
 * The load-bearing schema property is `arrangementEntry`'s anyOf `file` |
 * `notation`: a sloppak arrangement carries `file` (arrangements/lead.json)
 * with NO `notation`. If a future tightening required `notation`, sloppaks
 * would silently stop validating — this test is the tripwire.
 */
import path from "node:path";
import { promises as fs } from "node:fs";
import { fileURLToPath } from "node:url";

import { parseFeedpakManifest } from "../src/feedpak/parseManifest";
import { validateFeedpakManifest } from "../src/feedpak/validateManifest";
import { isFeedpakManifest } from "../src/feedpak/manifest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SLOPPAK_MANIFEST = path.resolve(
  HERE,
  "../../sloppak/fixtures/minimal.sloppak/manifest.yaml",
);

describe("sloppak manifest against the feedpak schema", () => {
  it("validates the minimal.sloppak fixture manifest", async () => {
    const yamlText = await fs.readFile(SLOPPAK_MANIFEST, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);

    const res = validateFeedpakManifest(manifest);
    expect(res.ok).toBe(true);
    expect(res.value?.title).toBe("Minimal Sloppak");
    expect(res.value?.artist).toBe("Slopsmith");
    expect(res.value?.duration).toBe(4.0);
  });

  it("preserves the sloppak-only unknown key (slopsmith_version) through parse", async () => {
    const yamlText = await fs.readFile(SLOPPAK_MANIFEST, "utf-8");
    const manifest = parseFeedpakManifest(yamlText) as Record<string, unknown>;
    expect(manifest.slopsmith_version).toBe("0.9.0");
    expect(isFeedpakManifest(manifest)).toBe(true);
  });

  it("accepts arrangements that carry `file` and NO `notation` (arrangementEntry anyOf)", async () => {
    const yamlText = await fs.readFile(SLOPPAK_MANIFEST, "utf-8");
    const manifest = parseFeedpakManifest(yamlText);
    const arr = (manifest as { arrangements: Array<Record<string, unknown>> }).arrangements;
    expect(arr[0]?.file).toBe("arrangements/lead.json");
    expect(arr[0]?.notation).toBeUndefined();
    // The whole manifest still validates — proves the schema didn't require notation.
    expect(validateFeedpakManifest(manifest).ok).toBe(true);
  });
});
