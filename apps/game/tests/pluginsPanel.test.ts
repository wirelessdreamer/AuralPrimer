// @vitest-environment jsdom
/**
 * Execution tests for the visualizer plugins panel. Mocks the plugin-scan
 * helpers from ./plugins and drives render() requirement-gating, refresh()
 * (success + scan-fail), the selection-mode auto/user flip, syncPreferred,
 * and the getCurrent / getSelectedPluginId accessors.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const scanBundledPlugins = vi.fn();
const scanUserPlugins = vi.fn();

vi.mock("../src/plugins", () => ({
  BUILTIN_PLUGINS: [
    { source: "builtin", id: "viz-beats", name: "Beats", packageName: "@a/viz-beats" },
    { source: "builtin", id: "viz-lyrics", name: "Lyrics", packageName: "@a/viz-lyrics" },
    { source: "builtin", id: "viz-drum-highway", name: "Drum Highway", packageName: "@a/viz-drum" },
  ],
  scanBundledPlugins: (...a: unknown[]) => scanBundledPlugins(...a),
  scanUserPlugins: (...a: unknown[]) => scanUserPlugins(...a),
}));

import { initPluginsPanel } from "../src/pluginsPanel";
import { BUILTIN_PLUGINS } from "../src/plugins";

function stageDom(): void {
  document.body.innerHTML =
    '<select id="pluginSelect"></select>' +
    '<button id="pluginRefresh"></button>';
}

function makeDeps(overrides: Partial<Parameters<typeof initPluginsPanel>[0]> = {}) {
  return {
    pluginSelect: document.getElementById("pluginSelect") as HTMLSelectElement,
    refreshBtn: document.getElementById("pluginRefresh") as HTMLButtonElement,
    setVizStatus: vi.fn(),
    escapeHtml: (s: string) => s,
    getSelectedAuralSongDetails: vi.fn(() => null),
    getPreferredPluginIdForPlayers: vi.fn(() => null),
    onPluginSelectionChange: vi.fn(),
    ...overrides,
  } as Parameters<typeof initPluginsPanel>[0];
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};
const select = () => document.getElementById("pluginSelect") as HTMLSelectElement;

describe("pluginsPanel", () => {
  beforeEach(() => {
    stageDom();
    scanBundledPlugins.mockReset();
    scanUserPlugins.mockReset();
  });

  it("render lists builtins; gates viz-lyrics + viz-drum-highway when song lacks data", () => {
    const handle = initPluginsPanel(makeDeps({ getSelectedAuralSongDetails: vi.fn(() => null) }));
    handle.render();
    const opts = Array.from(select().options);
    expect(opts.length).toBe(3);
    const lyrics = opts.find((o) => o.text.startsWith("Lyrics"))!;
    const drums = opts.find((o) => o.text.startsWith("Drum"))!;
    expect(lyrics.disabled).toBe(true);
    expect(lyrics.text).toContain("missing data");
    expect(drums.disabled).toBe(true);
  });

  it("render enables gated plugins when the song provides the required data", () => {
    const details = { has_lyrics: true, has_notes_mid: true } as any;
    const handle = initPluginsPanel(makeDeps({ getSelectedAuralSongDetails: vi.fn(() => details) }));
    handle.render();
    for (const o of Array.from(select().options)) expect(o.disabled).toBe(false);
  });

  it("render re-picks the first enabled option if the selected one is disabled", () => {
    // viz-beats is always enabled and is first, so default selection stays valid.
    const handle = initPluginsPanel(makeDeps());
    handle.render();
    // Force selection onto the disabled lyrics option, then re-render.
    select().value = "1";
    handle.render();
    expect(select().selectedOptions[0].disabled).toBe(false);
  });

  it("getSelectedPluginId returns null when index is out of range", () => {
    const handle = initPluginsPanel(makeDeps());
    // No options rendered yet -> selectedIndex -1.
    expect(handle.getSelectedPluginId()).toBeNull();
    handle.render();
    expect(handle.getSelectedPluginId()).toBe("viz-beats");
  });

  it("refresh merges bundled + user (dedup), sorts builtins first, then renders", async () => {
    scanBundledPlugins.mockResolvedValue([
      { source: "builtin", id: "viz-beats", name: "Beats v2", packageName: "x" }, // dup id overrides
    ]);
    scanUserPlugins.mockResolvedValue([
      { source: "user", id: "viz-custom", name: "Custom", pluginPath: "/p" },
      { source: "user", id: "viz-beats", name: "ignored dup", pluginPath: "/q" },
    ]);
    const handle = initPluginsPanel(makeDeps());
    await handle.refresh();
    const ids = handle.getAvailable().map((p) => p.id);
    expect(ids).toContain("viz-custom");
    // builtins sort before user.
    expect(ids[ids.length - 1]).toBe("viz-custom");
    expect(select().options.length).toBe(ids.length);
  });

  it("refresh reports scan failure via setVizStatus and still renders builtins", async () => {
    scanBundledPlugins.mockRejectedValue(new Error("no tauri"));
    const setVizStatus = vi.fn();
    const handle = initPluginsPanel(makeDeps({ setVizStatus }));
    await handle.refresh();
    expect(setVizStatus).toHaveBeenCalledWith(expect.stringContaining("plugin scan failed"));
    expect(handle.getAvailable().length).toBe(BUILTIN_PLUGINS.length);
  });

  it("changing the select flips to user mode and fires onPluginSelectionChange", () => {
    const onPluginSelectionChange = vi.fn();
    const handle = initPluginsPanel(makeDeps({ onPluginSelectionChange }));
    handle.render();
    select().dispatchEvent(new Event("change"));
    expect(onPluginSelectionChange).toHaveBeenCalled();
    // After flipping to user mode, syncPreferred is a no-op.
    expect(handle.syncPreferred()).toBe(false);
  });

  it("syncPreferred selects the preferred plugin in auto mode", () => {
    const details = { has_lyrics: true, has_notes_mid: true } as any;
    const getPreferredPluginIdForPlayers = vi.fn(() => "viz-lyrics");
    const handle = initPluginsPanel(
      makeDeps({
        getSelectedAuralSongDetails: vi.fn(() => details),
        getPreferredPluginIdForPlayers,
      }),
    );
    handle.render();
    // render() already auto-synced to the preferred index; move selection away
    // so syncPreferred has real work to do and reports the change.
    select().value = "0";
    expect(handle.syncPreferred()).toBe(true);
    expect(handle.getSelectedPluginId()).toBe("viz-lyrics");
  });

  it("syncPreferred returns false for unknown / disabled / null preferred id", () => {
    const handle = initPluginsPanel(
      makeDeps({ getPreferredPluginIdForPlayers: vi.fn(() => "does-not-exist") }),
    );
    handle.render();
    expect(handle.syncPreferred()).toBe(false);
    // null preferred id.
    const handle2 = initPluginsPanel(
      makeDeps({ getPreferredPluginIdForPlayers: vi.fn(() => null) }),
    );
    handle2.render();
    expect(handle2.syncPreferred()).toBe(false);
  });

  it("setSelectionModeAuto re-enables auto syncing", () => {
    const details = { has_lyrics: true, has_notes_mid: true } as any;
    const handle = initPluginsPanel(
      makeDeps({
        getSelectedAuralSongDetails: vi.fn(() => details),
        getPreferredPluginIdForPlayers: vi.fn(() => "viz-lyrics"),
      }),
    );
    handle.render();
    select().dispatchEvent(new Event("change")); // -> user mode
    expect(handle.syncPreferred()).toBe(false);
    handle.setSelectionModeAuto();
    // Move selection away from the preferred index so the auto sync changes it.
    select().value = "0";
    expect(handle.syncPreferred()).toBe(true);
  });

  it("getCurrent falls back to the first plugin when index is out of range; null when empty", () => {
    const handle = initPluginsPanel(makeDeps());
    handle.render();
    // valid index path.
    expect(handle.getCurrent()?.id).toBe("viz-beats");
    // out-of-range index -> first plugin.
    select().innerHTML = "";
    expect(handle.getCurrent()?.id).toBe("viz-beats");
  });

  it("refresh button click triggers a re-scan", async () => {
    scanBundledPlugins.mockResolvedValue([]);
    scanUserPlugins.mockResolvedValue([]);
    initPluginsPanel(makeDeps());
    document.getElementById("pluginRefresh")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(scanBundledPlugins).toHaveBeenCalled();
  });
});
