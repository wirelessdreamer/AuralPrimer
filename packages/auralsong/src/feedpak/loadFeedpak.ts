import { promises as fs } from "node:fs";
import path from "node:path";
import { unzipSync, strFromU8 } from "fflate";

import type { FeedpakManifest, FeedpakArrangement, FeedpakStem } from "./manifest";
import { parseFeedpakManifest } from "./parseManifest";

export type FeedpakContainerKind = "directory" | "zip";

/**
 * A pointer to a file referenced by the manifest. Bytes are NOT read
 * eagerly — `exists` records whether the target is present in the
 * container; callers pull the actual content via the loaded pack's
 * `readBytes` / `readText` / `readJson`.
 */
export interface FeedpakPointer {
  /** POSIX relative path inside the package (verbatim from the manifest). */
  path: string;
  /** Whether the referenced file is present in the container. */
  exists: boolean;
}

/** An arrangement plus its resolved file/notation pointers. */
export interface FeedpakLoadedArrangement {
  entry: FeedpakArrangement;
  file: FeedpakPointer | null;
  notation: FeedpakPointer | null;
}

/** A stem plus its resolved audio pointer. */
export interface FeedpakLoadedStem {
  entry: FeedpakStem;
  file: FeedpakPointer;
}

/**
 * A loaded feedpak. The manifest is parsed eagerly; every referenced
 * asset is exposed as a lazy {@link FeedpakPointer}. Read content via the
 * `read*` helpers.
 */
export interface LoadedFeedpak {
  feedpakPath: string;
  containerKind: FeedpakContainerKind;

  manifest: FeedpakManifest;

  arrangements: FeedpakLoadedArrangement[];
  stems: FeedpakLoadedStem[];
  songTimeline: FeedpakPointer | null;

  // AuralPrimer extension pointers (null when the key is absent).
  auralNotesMid: FeedpakPointer | null;
  auralSpectrogram: FeedpakPointer | null;
  /** Map of refine role -> pointer, preserving the manifest's role keys. */
  auralRefineCandidates: Record<string, FeedpakPointer> | null;
  auralBenchmark: FeedpakPointer | null;

  /** All file paths present in the container (POSIX separators, sorted). */
  listFiles(): string[];

  /** Best-effort reads (return null when missing). */
  readText(relPath: string): Promise<string | null>;
  readBytes(relPath: string): Promise<Uint8Array | null>;
  readJson(relPath: string): Promise<unknown | null>;
}

function normalizeRelPath(relPath: string): string {
  // Ensure POSIX separators to match zip path conventions.
  return relPath.split(path.sep).join(path.posix.sep);
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

/** Build the lazy pointer/manifest-derived view shared by dir + zip loaders. */
function buildLoadedFeedpak(opts: {
  feedpakPath: string;
  containerKind: FeedpakContainerKind;
  manifest: FeedpakManifest;
  fileSet: Set<string>;
  fileList: string[];
  readBytes: (relPath: string) => Promise<Uint8Array | null>;
  readText: (relPath: string) => Promise<string | null>;
  readJson: (relPath: string) => Promise<unknown | null>;
}): LoadedFeedpak {
  const { manifest, fileSet } = opts;

  const pointer = (relPath: string): FeedpakPointer => {
    const norm = normalizeRelPath(relPath);
    return { path: relPath, exists: fileSet.has(norm) };
  };
  const pointerOrNull = (relPath: string | undefined | null): FeedpakPointer | null =>
    typeof relPath === "string" && relPath.length > 0 ? pointer(relPath) : null;

  const arrangements: FeedpakLoadedArrangement[] = (manifest.arrangements ?? []).map((entry) => ({
    entry,
    file: pointerOrNull(entry.file),
    notation: pointerOrNull(entry.notation)
  }));

  const stems: FeedpakLoadedStem[] = (manifest.stems ?? []).map((entry) => ({
    entry,
    file: pointer(entry.file)
  }));

  let auralRefineCandidates: Record<string, FeedpakPointer> | null = null;
  const rawCandidates = manifest.aural_refine_candidates;
  if (rawCandidates && typeof rawCandidates === "object" && !Array.isArray(rawCandidates)) {
    auralRefineCandidates = {};
    for (const [role, p] of Object.entries(rawCandidates)) {
      if (typeof p === "string") auralRefineCandidates[role] = pointer(p);
    }
  }

  return {
    feedpakPath: opts.feedpakPath,
    containerKind: opts.containerKind,
    manifest,
    arrangements,
    stems,
    songTimeline: pointerOrNull(manifest.song_timeline),
    auralNotesMid: pointerOrNull(manifest.aural_notes_mid),
    auralSpectrogram: pointerOrNull(manifest.aural_spectrogram),
    auralRefineCandidates,
    auralBenchmark: pointerOrNull(manifest.aural_benchmark),
    listFiles() {
      return [...opts.fileList];
    },
    readText: opts.readText,
    readBytes: opts.readBytes,
    readJson: opts.readJson
  };
}

/** Load a feedpak from a path, auto-detecting directory vs. zip. */
export async function loadFeedpak(feedpakPath: string): Promise<LoadedFeedpak> {
  const st = await fs.stat(feedpakPath);
  if (st.isDirectory()) {
    return loadFeedpakFromDirectory(feedpakPath);
  }
  return loadFeedpakFromZip(feedpakPath);
}

export async function loadFeedpakFromDirectory(feedpakDir: string): Promise<LoadedFeedpak> {
  const manifestAbs = path.join(feedpakDir, "manifest.yaml");
  let yamlText: string;
  try {
    yamlText = await fs.readFile(manifestAbs, "utf-8");
  } catch {
    throw new Error("missing_manifest");
  }
  const manifest = parseFeedpakManifest(yamlText);

  const fileList = await listDirFiles(feedpakDir);
  const fileSet = new Set(fileList);

  const readBytes = async (relPath: string): Promise<Uint8Array | null> => {
    const rel = normalizeRelPath(relPath);
    const abs = path.join(feedpakDir, rel.split(path.posix.sep).join(path.sep));
    try {
      const b = await fs.readFile(abs);
      return new Uint8Array(b);
    } catch {
      return null;
    }
  };

  const readText = async (relPath: string): Promise<string | null> => {
    const rel = normalizeRelPath(relPath);
    const abs = path.join(feedpakDir, rel.split(path.posix.sep).join(path.sep));
    try {
      return await fs.readFile(abs, "utf-8");
    } catch {
      return null;
    }
  };

  const readJson = async (relPath: string): Promise<unknown | null> => {
    const txt = await readText(relPath);
    if (txt == null) return null;
    return JSON.parse(txt);
  };

  return buildLoadedFeedpak({
    feedpakPath: feedpakDir,
    containerKind: "directory",
    manifest,
    fileSet,
    fileList,
    readBytes,
    readText,
    readJson
  });
}

export async function loadFeedpakFromZip(zipFeedpakPath: string): Promise<LoadedFeedpak> {
  const bytes = await fs.readFile(zipFeedpakPath);
  return loadFeedpakFromZipBytes(new Uint8Array(bytes), zipFeedpakPath);
}

export async function loadFeedpakFromZipBytes(
  zipBytes: Uint8Array,
  zipFeedpakPath = "<memory>"
): Promise<LoadedFeedpak> {
  const files = unzipSync(zipBytes) as Record<string, Uint8Array>;

  const manifestBytes = files["manifest.yaml"];
  if (!manifestBytes) throw new Error("missing_manifest");
  const manifest = parseFeedpakManifest(strFromU8(manifestBytes));

  const fileList = listZipFiles(files);
  const fileSet = new Set(fileList);

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

  return buildLoadedFeedpak({
    feedpakPath: zipFeedpakPath,
    containerKind: "zip",
    manifest,
    fileSet,
    fileList,
    readBytes,
    readText,
    readJson
  });
}
