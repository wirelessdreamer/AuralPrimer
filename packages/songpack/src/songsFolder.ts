import { promises as fs } from "node:fs";
import path from "node:path";

export interface SongsFolderPaths {
  /** Where we store persistent app settings (json) */
  configDir: string;
  /** Where we store app data (songs, caches, etc.) */
  dataDir: string;
  /** Default songs folder if user has not overridden it */
  defaultSongsFolder: string;
  /** Settings JSON file path */
  settingsPath: string;
}

export interface ResolveSongsFolderOptions {
  /** Override platform for testing. Defaults to process.platform. */
  platform?: NodeJS.Platform;
  /** Override env for testing. Defaults to process.env. */
  env?: Record<string, string | undefined>;
  /** Override home dir for testing. Defaults to os.homedir(). */
  homeDir?: string;
  /** Override config dir (takes precedence over computed). */
  configDir?: string;
  /** Override data dir (takes precedence over computed). */
  dataDir?: string;
  /** Settings filename inside configDir. Default: settings.json */
  settingsFileName?: string;
}

export interface AppSettings {
  songsFolder?: string;
}

function defaultHomeDir(): string {
  // Avoid importing os in tests if you want pure injection; this is only used when not provided.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const os = require("node:os") as typeof import("node:os");
  return os.homedir();
}

function computeDirs(
  platform: NodeJS.Platform,
  env: Record<string, string | undefined>,
  homeDir: string
): { configDir: string; dataDir: string } {
  if (platform === "win32") {
    const localAppData = env.LOCALAPPDATA || path.join(homeDir, "AppData", "Local");
    const roamingAppData = env.APPDATA || path.join(homeDir, "AppData", "Roaming");
    return {
      configDir: path.join(roamingAppData, "AuralPrimer"),
      dataDir: path.join(localAppData, "AuralPrimer")
    };
  }

  // Linux (and other unix-like). We only target Linux, but keep a reasonable fallback.
  const xdgConfig = env.XDG_CONFIG_HOME || path.join(homeDir, ".config");
  const xdgData = env.XDG_DATA_HOME || path.join(homeDir, ".local", "share");
  return {
    configDir: path.join(xdgConfig, "AuralPrimer"),
    dataDir: path.join(xdgData, "AuralPrimer")
  };
}

export function resolveSongsFolderPaths(opts: ResolveSongsFolderOptions = {}): SongsFolderPaths {
  const platform = opts.platform ?? (process.platform as NodeJS.Platform);
  const env = opts.env ?? process.env;
  const homeDir = opts.homeDir ?? defaultHomeDir();

  const computed = computeDirs(platform, env, homeDir);

  const configDir = opts.configDir ?? computed.configDir;
  const dataDir = opts.dataDir ?? computed.dataDir;

  const settingsFileName = opts.settingsFileName ?? "settings.json";
  const settingsPath = path.join(configDir, settingsFileName);

  const defaultSongsFolder = path.join(dataDir, "songs");

  return { configDir, dataDir, defaultSongsFolder, settingsPath };
}

export async function loadAppSettings(paths: SongsFolderPaths): Promise<AppSettings> {
  try {
    const raw = await fs.readFile(paths.settingsPath, "utf-8");
    const json = JSON.parse(raw);
    if (!json || typeof json !== "object") return {};
    const o = json as Record<string, unknown>;
    const songsFolder = typeof o.songsFolder === "string" ? o.songsFolder : undefined;
    return { songsFolder };
  } catch {
    return {};
  }
}

export async function saveAppSettings(paths: SongsFolderPaths, settings: AppSettings): Promise<void> {
  await fs.mkdir(paths.configDir, { recursive: true });
  const tmpPath = `${paths.settingsPath}.tmp`;
  await fs.writeFile(tmpPath, JSON.stringify(settings, null, 2), "utf-8");
  await fs.rename(tmpPath, paths.settingsPath);
}

/**
 * Resolve the effective songs folder.
 *
 * - If settings.json defines songsFolder, use it.
 * - Otherwise use defaultSongsFolder.
 */
export async function resolveSongsFolder(opts: ResolveSongsFolderOptions = {}): Promise<string> {
  const paths = resolveSongsFolderPaths(opts);
  const settings = await loadAppSettings(paths);
  return settings.songsFolder ?? paths.defaultSongsFolder;
}

export async function setSongsFolderOverride(
  songsFolder: string,
  opts: ResolveSongsFolderOptions = {}
): Promise<void> {
  const paths = resolveSongsFolderPaths(opts);
  const settings = await loadAppSettings(paths);
  await saveAppSettings(paths, { ...settings, songsFolder });
}

export async function clearSongsFolderOverride(opts: ResolveSongsFolderOptions = {}): Promise<void> {
  const paths = resolveSongsFolderPaths(opts);
  const settings = await loadAppSettings(paths);
  const { songsFolder: _ignored, ...rest } = settings;
  await saveAppSettings(paths, rest);
}
