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
});
