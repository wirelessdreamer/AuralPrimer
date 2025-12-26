import path from "node:path";
import os from "node:os";
import { mkdtempSync, rmSync } from "node:fs";

import {
  clearSongsFolderOverride,
  resolveSongsFolder,
  resolveSongsFolderPaths,
  setSongsFolderOverride
} from "../src/songsFolder";

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "auralprimer-"));
}

describe("songs folder policy", () => {
  let base: string | undefined;

  afterEach(() => {
    if (base) rmSync(base, { recursive: true, force: true });
    base = undefined;
  });

  it("computes a deterministic default songs folder under dataDir/songs", async () => {
    base = tmpDir();

    const paths = resolveSongsFolderPaths({
      platform: "linux",
      homeDir: "/home/test",
      env: { XDG_CONFIG_HOME: path.join(base, "cfg"), XDG_DATA_HOME: path.join(base, "data") }
    });

    expect(paths.defaultSongsFolder).toBe(path.join(base, "data", "AuralPrimer", "songs"));
    expect(paths.settingsPath).toBe(path.join(base, "cfg", "AuralPrimer", "settings.json"));

    const eff = await resolveSongsFolder({
      platform: "linux",
      homeDir: "/home/test",
      env: { XDG_CONFIG_HOME: path.join(base, "cfg"), XDG_DATA_HOME: path.join(base, "data") }
    });

    expect(eff).toBe(paths.defaultSongsFolder);
  });

  it("persists an override in settings.json", async () => {
    base = tmpDir();

    const opts = {
      platform: "linux" as const,
      homeDir: "/home/test",
      env: { XDG_CONFIG_HOME: path.join(base, "cfg"), XDG_DATA_HOME: path.join(base, "data") }
    };

    const custom = path.join(base, "MySongs");

    await setSongsFolderOverride(custom, opts);
    expect(await resolveSongsFolder(opts)).toBe(custom);

    await clearSongsFolderOverride(opts);
    const paths = resolveSongsFolderPaths(opts);
    expect(await resolveSongsFolder(opts)).toBe(paths.defaultSongsFolder);
  });

  it("computes Windows defaults from LOCALAPPDATA/APPDATA", () => {
    base = tmpDir();

    const paths = resolveSongsFolderPaths({
      platform: "win32",
      homeDir: "C:\\Users\\Test",
      env: {
        LOCALAPPDATA: path.join(base, "LocalAppData"),
        APPDATA: path.join(base, "Roaming")
      }
    });

    expect(paths.dataDir).toBe(path.join(base, "LocalAppData", "AuralPrimer"));
    expect(paths.configDir).toBe(path.join(base, "Roaming", "AuralPrimer"));
    expect(paths.defaultSongsFolder).toBe(path.join(base, "LocalAppData", "AuralPrimer", "songs"));
  });
});
