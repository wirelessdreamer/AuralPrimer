import type { VisualizerModule } from "@auralprimer/viz-sdk";

export type PluginSource = "builtin" | "user";

export type PluginDescriptor = {
  source: PluginSource;
  id: string;
  name: string;
  version?: string;
  description?: string;

  /**
   * Built-in package plugin (dev-friendly): loaded via `import(packageName)`.
   */
  packageName?: string;

  /**
   * Directory on disk containing manifest.json + dist/index.js.
   * Used for user plugins AND bundled plugins.
   */
  pluginPath?: string;
};

export const BUILTIN_PLUGINS: PluginDescriptor[] = [
  {
    source: "builtin",
    id: "viz-beats",
    name: "Beats (built-in)",
    packageName: "@auralprimer/viz-beats"
  },
  {
    source: "builtin",
    id: "viz-lyrics",
    name: "Lyrics (built-in)",
    packageName: "@auralprimer/viz-lyrics"
  },
  {
    source: "builtin",
    id: "viz-nashville",
    name: "Nashville (built-in)",
    packageName: "@auralprimer/viz-nashville"
  },
  {
    source: "builtin",
    id: "viz-fretboard",
    name: "Fretboard (built-in)",
    packageName: "@auralprimer/viz-fretboard"
  },
  {
    source: "builtin",
    id: "viz-drum-highway",
    name: "Drum Highway (built-in)",
    packageName: "@auralprimer/viz-drum-highway"
  }
];

/**
 * IMPORTANT:
 * In production builds, Vite cannot reliably bundle modules loaded via
 * `import(/* @vite-ignore * / someRuntimeString)`.
 *
 * So for built-in visualizers, we provide an explicit import map with string
 * literals. This ensures the visualizer packages are included in the app bundle
 * and "Start visualizer" works in the packaged desktop app.
 */
const BUILTIN_PACKAGE_IMPORTERS: Record<string, () => Promise<any>> = {
  "@auralprimer/viz-beats": () => import("@auralprimer/viz-beats"),
  "@auralprimer/viz-lyrics": () => import("@auralprimer/viz-lyrics"),
  "@auralprimer/viz-nashville": () => import("@auralprimer/viz-nashville"),
  "@auralprimer/viz-fretboard": () => import("@auralprimer/viz-fretboard"),
  "@auralprimer/viz-drum-highway": () => import("@auralprimer/viz-drum-highway")
};

export function isVisualizerModule(mod: any): mod is VisualizerModule {
  return !!mod && typeof mod.createVisualizer === "function";
}

export type LoadedPlugin = {
  descriptor: PluginDescriptor;
  module: VisualizerModule;
  /** Cleanup for Blob URL based imports (user plugins). */
  dispose?: () => void;
};

type JsBlob = { mime: string; bytes: number[] };

type VisualizerManifest = {
  id?: string;
  name?: string;
  version?: string;
  description?: string;
  entry?: string;
};

type VisualizerScanEntry = {
  plugin_path: string;
  ok: boolean;
  manifest?: VisualizerManifest;
  error?: string;
};

export async function scanBundledPlugins(): Promise<PluginDescriptor[]> {
  // Avoid importing Tauri APIs in Node test environment.
  const { invoke } = await import("@tauri-apps/api/core");

  const entries = await invoke<VisualizerScanEntry[]>("scan_bundled_visualizers");
  return entries
    .filter((e) => e.ok && e.manifest?.id)
    .map((e) => ({
      source: "builtin" as const,
      id: e.manifest!.id!,
      name: e.manifest!.name ?? e.manifest!.id!,
      version: e.manifest!.version,
      description: e.manifest!.description,
      pluginPath: e.plugin_path
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

export async function scanUserPlugins(): Promise<PluginDescriptor[]> {
  // Avoid importing Tauri APIs in Node test environment.
  const { invoke } = await import("@tauri-apps/api/core");

  const entries = await invoke<VisualizerScanEntry[]>("scan_visualizers");
  return entries
    .filter((e) => e.ok && e.manifest?.id)
    .map((e) => ({
      source: "user" as const,
      id: e.manifest!.id!,
      name: e.manifest!.name ?? e.manifest!.id!,
      version: e.manifest!.version,
      description: e.manifest!.description,
      pluginPath: e.plugin_path
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

async function importFromBlobUrl(js: JsBlob): Promise<{ mod: any; dispose: () => void }> {
  const bytes = new Uint8Array(js.bytes);

  // In real browser/Tauri env, use Blob URLs.
  // In Node/Vitest env, `URL.createObjectURL` may not exist; use a data URL.
  const canBlobUrl = typeof URL !== "undefined" && typeof (URL as any).createObjectURL === "function";

  if (canBlobUrl) {
    const blob = new Blob([bytes], { type: js.mime || "text/javascript" });
    const url = URL.createObjectURL(blob);
    try {
      // Vite would otherwise try to pre-bundle/resolve this at build time.
      const mod = await import(/* @vite-ignore */ url);
      return {
        mod,
        dispose: () => URL.revokeObjectURL(url)
      };
    } catch (e) {
      URL.revokeObjectURL(url);
      throw e;
    }
  }

  // Data URL fallback.
  const b64 = Buffer.from(bytes).toString("base64");
  const url = `data:${js.mime || "text/javascript"};base64,${b64}`;
  const mod = await import(/* @vite-ignore */ url);
  return { mod, dispose: () => {} };
}

export async function loadPlugin(descriptor: PluginDescriptor): Promise<LoadedPlugin> {
  if (descriptor.source === "builtin") {
    // Prefer package-based built-ins when present.
    if (descriptor.packageName) {
      const importer = BUILTIN_PACKAGE_IMPORTERS[descriptor.packageName];
      if (!importer) {
        throw new Error(
          `Unknown builtin packageName ${descriptor.packageName}. ` +
            `Add it to BUILTIN_PACKAGE_IMPORTERS in apps/desktop/src/plugins.ts so it bundles in production.`
        );
      }

      const mod = await importer();
      if (!isVisualizerModule(mod)) {
        throw new Error(`module ${descriptor.packageName} is not a VisualizerModule`);
      }
      return { descriptor, module: mod };
    }

    // Bundled built-in plugin from disk (resources).
    if (!descriptor.pluginPath) throw new Error("builtin plugin missing pluginPath");

    const { invoke } = await import("@tauri-apps/api/core");
    const js = await invoke<JsBlob>("read_visualizer_entrypoint", { pluginPath: descriptor.pluginPath });

    const imported = await importFromBlobUrl(js);
    if (!isVisualizerModule(imported.mod)) {
      imported.dispose();
      throw new Error(`builtin plugin ${descriptor.id} entrypoint did not export createVisualizer()`);
    }

    return { descriptor, module: imported.mod, dispose: imported.dispose };
  }

  // User plugin
  if (!descriptor.pluginPath) throw new Error("user plugin missing pluginPath");

  const { invoke } = await import("@tauri-apps/api/core");
  const js = await invoke<JsBlob>("read_visualizer_entrypoint", { pluginPath: descriptor.pluginPath });

  const imported = await importFromBlobUrl(js);
  if (!isVisualizerModule(imported.mod)) {
    imported.dispose();
    throw new Error(`user plugin ${descriptor.id} entrypoint did not export createVisualizer()`);
  }

  return { descriptor, module: imported.mod, dispose: imported.dispose };
}
