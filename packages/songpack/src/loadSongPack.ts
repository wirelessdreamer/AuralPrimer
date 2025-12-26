import { promises as fs } from "node:fs";
import path from "node:path";
import { unzipSync, strFromU8 } from "fflate";

export type SongPackLoadContainerKind = "directory" | "zip";

export type LoadedSongPack = {
  songPackPath: string;
  containerKind: SongPackLoadContainerKind;

  /** Raw JSON objects parsed from disk/zip. */
  manifest: unknown;
  features: Partial<Record<"beats" | "tempo_map" | "sections" | "events" | "lyrics", unknown>>;
  charts: Record<string, unknown>; // key: chart rel path (e.g. charts/easy.json)

  /** Lists all file paths present (POSIX separators). */
  listFiles(): string[];

  /** Best-effort reads (returns null when missing). */
  readText(relPath: string): Promise<string | null>;
  readBytes(relPath: string): Promise<Uint8Array | null>;
  readJson(relPath: string): Promise<unknown | null>;
};

const FEATURE_FILES: Array<{ id: "beats" | "tempo_map" | "sections" | "events" | "lyrics"; relPath: string }> = [
  { id: "beats", relPath: "features/beats.json" },
  { id: "tempo_map", relPath: "features/tempo_map.json" },
  { id: "sections", relPath: "features/sections.json" },
  { id: "events", relPath: "features/events.json" },
  { id: "lyrics", relPath: "features/lyrics.json" }
];

function normalizeRelPath(relPath: string): string {
  // Ensure we use POSIX separators to match zip path conventions.
  return relPath.split(path.sep).join(path.posix.sep);
}

async function fileExists(p: string): Promise<boolean> {
  try {
    const st = await fs.stat(p);
    return st.isFile();
  } catch {
    return false;
  }
}

async function readJsonFile(absPath: string): Promise<unknown> {
  const raw = await fs.readFile(absPath, "utf-8");
  return JSON.parse(raw);
}

function readZipJson(files: Record<string, Uint8Array>, relPath: string): unknown {
  const bytes = files[relPath];
  if (!bytes) throw new Error("missing");
  const text = strFromU8(bytes);
  return JSON.parse(text);
}

function listZipFiles(files: Record<string, Uint8Array>): string[] {
  return Object.keys(files).sort();
}

async function listDirFiles(rootDir: string): Promise<string[]> {
  const out: string[] = [];

  async function walk(absDir: string, relDir: string) {
    const ents = await fs.readdir(absDir, { withFileTypes: true });
    ents.sort((a, b) => a.name.localeCompare(b.name));

    for (const ent of ents) {
      const rel = relDir ? path.posix.join(relDir, ent.name) : ent.name;
      const abs = path.join(absDir, ent.name);

      if (ent.isDirectory()) {
        await walk(abs, rel);
      } else if (ent.isFile()) {
        out.push(rel);
      }
    }
  }

  await walk(rootDir, "");
  out.sort();
  return out;
}

export async function loadSongPack(songPackPath: string): Promise<LoadedSongPack> {
  const st = await fs.stat(songPackPath);
  const containerKind: SongPackLoadContainerKind = st.isDirectory() ? "directory" : "zip";

  if (containerKind === "directory") {
    return loadSongPackFromDirectory(songPackPath);
  }
  return loadSongPackFromZip(songPackPath);
}

export async function loadSongPackFromDirectory(songPackDir: string): Promise<LoadedSongPack> {
  const manifestAbs = path.join(songPackDir, "manifest.json");
  if (!(await fileExists(manifestAbs))) {
    throw new Error("missing_manifest");
  }

  const manifest = await readJsonFile(manifestAbs);

  const features: LoadedSongPack["features"] = {};
  for (const f of FEATURE_FILES) {
    const abs = path.join(songPackDir, f.relPath);
    if (!(await fileExists(abs))) continue;
    features[f.id] = await readJsonFile(abs);
  }

  const charts: Record<string, unknown> = {};
  const chartsDir = path.join(songPackDir, "charts");
  try {
    const st = await fs.stat(chartsDir);
    if (st.isDirectory()) {
      const ents = await fs.readdir(chartsDir);
      ents.sort();
      for (const name of ents) {
        if (!name.endsWith(".json")) continue;
        const rel = `charts/${name}`;
        charts[rel] = await readJsonFile(path.join(songPackDir, rel));
      }
    }
  } catch {
    // ok
  }

  const fileList = await listDirFiles(songPackDir);

  const readBytes = async (relPath: string): Promise<Uint8Array | null> => {
    const rel = normalizeRelPath(relPath);
    const abs = path.join(songPackDir, rel.split(path.posix.sep).join(path.sep));
    if (!(await fileExists(abs))) return null;
    const b = await fs.readFile(abs);
    return new Uint8Array(b);
  };

  const readText = async (relPath: string): Promise<string | null> => {
    const rel = normalizeRelPath(relPath);
    const abs = path.join(songPackDir, rel.split(path.posix.sep).join(path.sep));
    if (!(await fileExists(abs))) return null;
    return fs.readFile(abs, "utf-8");
  };

  const readJson = async (relPath: string): Promise<unknown | null> => {
    const txt = await readText(relPath);
    if (txt == null) return null;
    return JSON.parse(txt);
  };

  return {
    songPackPath: songPackDir,
    containerKind: "directory",
    manifest,
    features,
    charts,
    listFiles() {
      return [...fileList];
    },
    readText,
    readBytes,
    readJson
  } satisfies LoadedSongPack;
}

export async function loadSongPackFromZip(zipSongPackPath: string): Promise<LoadedSongPack> {
  const bytes = await fs.readFile(zipSongPackPath);
  return loadSongPackFromZipBytes(new Uint8Array(bytes), zipSongPackPath);
}

export async function loadSongPackFromZipBytes(zipBytes: Uint8Array, zipSongPackPath = "<memory>"): Promise<LoadedSongPack> {
  const files = unzipSync(zipBytes) as Record<string, Uint8Array>;

  const manifestBytes = files["manifest.json"];
  if (!manifestBytes) throw new Error("missing_manifest");
  const manifest = readZipJson(files, "manifest.json");

  const features: LoadedSongPack["features"] = {};
  for (const f of FEATURE_FILES) {
    if (!files[f.relPath]) continue;
    features[f.id] = readZipJson(files, f.relPath);
  }

  const charts: Record<string, unknown> = {};
  for (const rel of Object.keys(files)) {
    if (!rel.startsWith("charts/") || !rel.endsWith(".json")) continue;
    charts[rel] = readZipJson(files, rel);
  }

  const fileList = listZipFiles(files);

  const readBytes = async (relPath: string): Promise<Uint8Array | null> => {
    const rel = normalizeRelPath(relPath);
    return files[rel] ?? null;
  };

  const readText = async (relPath: string): Promise<string | null> => {
    const b = await readBytes(relPath);
    if (!b) return null;
    return strFromU8(b);
  };

  const readJson = async (relPath: string): Promise<unknown | null> => {
    const txt = await readText(relPath);
    if (txt == null) return null;
    return JSON.parse(txt);
  };

  return {
    songPackPath: zipSongPackPath,
    containerKind: "zip",
    manifest,
    features,
    charts,
    listFiles() {
      return [...fileList];
    },
    readText,
    readBytes,
    readJson
  };
}
