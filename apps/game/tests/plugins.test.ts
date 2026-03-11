// @vitest-environment jsdom

// We only test built-in loading here (Node test env doesn't have Tauri invoke).

describe("plugins", () => {
  it("loads the built-in viz-beats module and exposes createVisualizer", async () => {
    const { BUILTIN_PLUGINS, loadPlugin } = await import("../src/plugins");

    const p = BUILTIN_PLUGINS[0];
    const loaded = await loadPlugin(p);

    expect(typeof loaded.module.createVisualizer).toBe("function");
    expect(loaded.descriptor.id).toBe("viz-beats");

    // Make sure dispose exists only for user plugins.
    expect(loaded.dispose).toBeUndefined();
  });

  it("loads the built-in viz-nashville module and exposes createVisualizer", async () => {
    const { BUILTIN_PLUGINS, loadPlugin } = await import("../src/plugins");

    const p = BUILTIN_PLUGINS.find((x) => x.id === "viz-nashville");
    expect(p).toBeTruthy();

    const loaded = await loadPlugin(p!);

    expect(typeof loaded.module.createVisualizer).toBe("function");
    expect(loaded.descriptor.id).toBe("viz-nashville");
    expect(loaded.dispose).toBeUndefined();
  });

  it("loads the built-in viz-fretboard module and exposes createVisualizer", async () => {
    const { BUILTIN_PLUGINS, loadPlugin } = await import("../src/plugins");

    const p = BUILTIN_PLUGINS.find((x) => x.id === "viz-fretboard");
    expect(p).toBeTruthy();

    const loaded = await loadPlugin(p!);

    expect(typeof loaded.module.createVisualizer).toBe("function");
    expect(loaded.descriptor.id).toBe("viz-fretboard");
    expect(loaded.dispose).toBeUndefined();
  });

  it("loads the built-in viz-lyrics module and exposes createVisualizer", async () => {
    const { BUILTIN_PLUGINS, loadPlugin } = await import("../src/plugins");

    const p = BUILTIN_PLUGINS.find((x) => x.id === "viz-lyrics");
    expect(p).toBeTruthy();

    const loaded = await loadPlugin(p!);

    expect(typeof loaded.module.createVisualizer).toBe("function");
    expect(loaded.descriptor.id).toBe("viz-lyrics");
    expect(loaded.dispose).toBeUndefined();
  });

  it("loads the built-in viz-drum-highway module and exposes createVisualizer", async () => {
    const { BUILTIN_PLUGINS, loadPlugin } = await import("../src/plugins");

    const p = BUILTIN_PLUGINS.find((x) => x.id === "viz-drum-highway");
    expect(p).toBeTruthy();

    const loaded = await loadPlugin(p!);

    expect(typeof loaded.module.createVisualizer).toBe("function");
    expect(loaded.descriptor.id).toBe("viz-drum-highway");
    expect(loaded.dispose).toBeUndefined();
  });

  it("scanUserPlugins invokes tauri command (smoke)", async () => {
    // Mock the Tauri API module.
    vi.doMock("@tauri-apps/api/core", () => {
      return {
        invoke: async (cmd: string) => {
          if (cmd !== "scan_visualizers") throw new Error(`unexpected cmd ${cmd}`);
          return [
            {
              plugin_path: "/tmp/viz-one",
              ok: true,
              manifest: { id: "viz-one", name: "One", entry: "dist/index.js" }
            }
          ];
        }
      };
    });

    const { scanUserPlugins } = await import("../src/plugins");
    const res = await scanUserPlugins();
    expect(res).toHaveLength(1);
    expect(res[0]).toMatchObject({ id: "viz-one", source: "user" });

    vi.doUnmock("@tauri-apps/api/core");
  });

  it("scanBundledPlugins invokes tauri command (smoke)", async () => {
    vi.doMock("@tauri-apps/api/core", () => {
      return {
        invoke: async (cmd: string) => {
          if (cmd !== "scan_bundled_visualizers") throw new Error(`unexpected cmd ${cmd}`);
          return [
            {
              plugin_path: "/tmp/viz-bundled",
              ok: true,
              manifest: { id: "viz-bundled", name: "Bundled", entry: "dist/index.js" }
            }
          ];
        }
      };
    });

    const { scanBundledPlugins } = await import("../src/plugins");
    const res = await scanBundledPlugins();
    expect(res).toHaveLength(1);
    expect(res[0]).toMatchObject({ id: "viz-bundled", source: "builtin" });

    vi.doUnmock("@tauri-apps/api/core");
  });

  it("scan commands filter invalid entries and sort by id", async () => {
    vi.doMock("@tauri-apps/api/core", () => {
      return {
        invoke: async (cmd: string) => {
          if (cmd === "scan_visualizers") {
            return [
              { plugin_path: "/b", ok: true, manifest: { id: "z" } },
              { plugin_path: "/x", ok: false, manifest: { id: "x" } },
              { plugin_path: "/a", ok: true, manifest: { id: "a" } },
              { plugin_path: "/m", ok: true, manifest: {} },
            ];
          }
          if (cmd === "scan_bundled_visualizers") {
            return [
              { plugin_path: "/b2", ok: true, manifest: { id: "b" } },
              { plugin_path: "/a2", ok: true, manifest: { id: "a" } },
            ];
          }
          throw new Error(`unexpected cmd ${cmd}`);
        },
      };
    });

    const { scanUserPlugins, scanBundledPlugins } = await import("../src/plugins");
    const users = await scanUserPlugins();
    const builtins = await scanBundledPlugins();

    expect(users.map((x) => x.id)).toEqual(["a", "z"]);
    expect(builtins.map((x) => x.id)).toEqual(["a", "b"]);
    vi.doUnmock("@tauri-apps/api/core");
  });

  it("isVisualizerModule guard behaves correctly", async () => {
    const { isVisualizerModule } = await import("../src/plugins");
    expect(isVisualizerModule({ createVisualizer: () => ({}) })).toBe(true);
    expect(isVisualizerModule({})).toBe(false);
    expect(isVisualizerModule(null)).toBe(false);
  });

  it("throws for unknown builtin package import mapping", async () => {
    const { loadPlugin } = await import("../src/plugins");
    await expect(
      loadPlugin({
        source: "builtin",
        id: "x",
        name: "x",
        packageName: "@auralprimer/not-mapped",
      })
    ).rejects.toThrow("Unknown builtin packageName");
  });

  it("throws when plugin paths are missing", async () => {
    const { loadPlugin } = await import("../src/plugins");
    await expect(
      loadPlugin({
        source: "builtin",
        id: "x",
        name: "x",
      })
    ).rejects.toThrow("builtin plugin missing pluginPath");

    await expect(
      loadPlugin({
        source: "user",
        id: "y",
        name: "y",
      })
    ).rejects.toThrow("user plugin missing pluginPath");
  });

  it("loads disk-backed builtin and user plugins via read_visualizer_entrypoint", async () => {
    const src =
      "export function createVisualizer(){return {init:async()=>{},onResize(){},update(){},render(){},dispose(){}}}";
    const bytes = Array.from(Buffer.from(src, "utf8"));

    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string) => {
        if (cmd !== "read_visualizer_entrypoint") throw new Error(`unexpected cmd ${cmd}`);
        return { mime: "text/javascript", bytes };
      }),
    }));

    const oldCreate = (URL as any).createObjectURL;
    try {
      // Force data URL import path in test env.
      (URL as any).createObjectURL = undefined;
      const { loadPlugin } = await import("../src/plugins");

      const b = await loadPlugin({ source: "builtin", id: "b", name: "b", pluginPath: "/plugins/b" });
      expect(typeof b.module.createVisualizer).toBe("function");
      expect(typeof b.dispose).toBe("function");
      b.dispose?.();

      const u = await loadPlugin({ source: "user", id: "u", name: "u", pluginPath: "/plugins/u" });
      expect(typeof u.module.createVisualizer).toBe("function");
      expect(typeof u.dispose).toBe("function");
      u.dispose?.();
    } finally {
      (URL as any).createObjectURL = oldCreate;
      vi.doUnmock("@tauri-apps/api/core");
    }
  });

  it("rejects disk-backed plugin that does not export createVisualizer", async () => {
    const bytes = Array.from(Buffer.from("export const x = 1;", "utf8"));
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async () => ({ mime: "text/javascript", bytes })),
    }));

    const oldCreate = (URL as any).createObjectURL;
    try {
      (URL as any).createObjectURL = undefined;
      const { loadPlugin } = await import("../src/plugins");

      await expect(
        loadPlugin({ source: "builtin", id: "b", name: "b", pluginPath: "/plugins/b" })
      ).rejects.toThrow("did not export createVisualizer");
    } finally {
      (URL as any).createObjectURL = oldCreate;
      vi.doUnmock("@tauri-apps/api/core");
    }
  });
});
