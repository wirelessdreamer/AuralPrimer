/**
 * Plugins panel — owns the visualizer plugin dropdown (#pluginSelect),
 * the refresh button (#pluginRefresh), the in-memory `availablePlugins`
 * list, the auto/user selection-mode state, and the per-plugin
 * "requirements satisfied?" gating that disables options for AuralSongs
 * missing required data (lyrics, notes.mid, etc.).
 *
 * Extracted from main.ts as Phase 2.M. The host wires three callbacks:
 *   - getSelectedAuralSongDetails  — for requirement gating
 *   - getPreferredPluginIdForPlayers  — read playersPanel preferred id
 *   - onPluginSelectionChange  — host-side viz restart on user change
 *
 * The mutual dep between this panel and playersPanel is broken via
 * lambda capture of a `let` reference on the host side (see main.ts).
 */

import {
  BUILTIN_PLUGINS,
  scanBundledPlugins,
  scanUserPlugins,
  type PluginDescriptor,
} from "./plugins";
import type { AuralSongDetails } from "./auralsong";

export type PluginsPanelDeps = {
  /** <select id="pluginSelect"> the panel owns. */
  pluginSelect: HTMLSelectElement;
  /** <button id="pluginRefresh"> the panel owns. */
  refreshBtn: HTMLButtonElement;
  /** Host-side viz status setter (used when plugin scan fails in browser-only). */
  setVizStatus: (msg: string) => void;
  /** Same escapeHtml the rest of main.ts uses. */
  escapeHtml: (s: string) => string;
  /** Live getter — returns the currently-selected AuralSongDetails (may be null). */
  getSelectedAuralSongDetails: () => AuralSongDetails | null;
  /**
   * Live getter — returns the plugin id playersPanel would prefer given its
   * current player roster. Returns null if no preference (auto mode no-op).
   * Lambda is called only after user interaction or song selection, so a
   * `let` ref captured by the host is fine.
   */
  getPreferredPluginIdForPlayers: () => string | null;
  /** Called after the user picks a different plugin (#pluginSelect change). */
  onPluginSelectionChange: () => void;
};

export type PluginsPanelHandle = {
  /** Async scan (builtins + bundled + user) + re-render. */
  refresh: () => Promise<void>;
  /** Re-render with current availability gating (no scan). */
  render: () => void;
  /** Currently-selected plugin's id, or null if none. */
  getSelectedPluginId: () => string | null;
  /**
   * Currently-selected plugin descriptor. Falls back to the first available
   * plugin if the index is out of range. Returns null only if no plugins.
   */
  getCurrent: () => PluginDescriptor | null;
  /** Read-only view of the loaded plugins list. */
  getAvailable: () => PluginDescriptor[];
  /** Force a refresh from auto preference (no-op if mode === "user"). */
  syncPreferred: () => boolean;
  /** Switch selection mode back to "auto" — typically when player roster changes. */
  setSelectionModeAuto: () => void;
};

/**
 * Per-plugin requirement gating. Add cases here as new visualizers need
 * specific AuralSong data to function.
 */
function pluginRequirements(id: string): {
  ok: (d: AuralSongDetails | null) => boolean;
  reason: string;
} {
  switch (id) {
    case "viz-lyrics":
      return {
        ok: (d) => Boolean(d?.has_lyrics || d?.has_vocal_pitch || d?.has_vocal_pitch_contour),
        reason: "Requires lyrics or vocal pitch artifacts"
      };
    case "viz-drum-highway":
      return {
        ok: (d) => Boolean(d?.has_notes_mid || d?.has_drum_tab),
        reason: "Requires aural/notes.mid or drum_tab.json"
      };
    default:
      return { ok: () => true, reason: "" };
  }
}

export function initPluginsPanel(deps: PluginsPanelDeps): PluginsPanelHandle {
  let availablePlugins: PluginDescriptor[] = [...BUILTIN_PLUGINS];
  let selectionMode: "auto" | "user" = "auto";

  function getSelectedPluginId(): string | null {
    const idx = deps.pluginSelect.selectedIndex;
    if (idx < 0 || idx >= availablePlugins.length) return null;
    return availablePlugins[idx]?.id ?? null;
  }

  function setPluginSelectionById(pluginId: string | null): boolean {
    if (!pluginId) return false;
    const idx = availablePlugins.findIndex((p) => p.id === pluginId);
    if (idx < 0) return false;
    const option = Array.from(deps.pluginSelect.options).find((o) => o.value === String(idx));
    if (!option || option.disabled) return false;
    if (deps.pluginSelect.value === String(idx)) return false;
    deps.pluginSelect.value = String(idx);
    return true;
  }

  function syncPreferred(): boolean {
    if (selectionMode !== "auto") return false;
    return setPluginSelectionById(deps.getPreferredPluginIdForPlayers());
  }

  function render(): void {
    const details = deps.getSelectedAuralSongDetails();
    const previousSelectedId = getSelectedPluginId();
    const esc = deps.escapeHtml;

    deps.pluginSelect.innerHTML = availablePlugins
      .map((p, idx) => {
        const req = pluginRequirements(p.id);
        const ok = req.ok(details);
        const label = `${p.name} (${p.source})${ok ? "" : " — missing data"}`;
        const disabled = ok ? "" : "disabled";
        const title = ok || !req.reason ? "" : ` title="${esc(req.reason)}"`;
        return `<option value="${idx}" ${disabled}${title}>${esc(label)}</option>`;
      })
      .join("\n");

    setPluginSelectionById(previousSelectedId);
    syncPreferred();

    // If selected plugin became disabled, choose first enabled.
    if (deps.pluginSelect.selectedOptions.length && deps.pluginSelect.selectedOptions[0].disabled) {
      const firstEnabled = Array.from(deps.pluginSelect.options).find((o) => !o.disabled);
      if (firstEnabled) deps.pluginSelect.value = firstEnabled.value;
    }
  }

  async function refresh(): Promise<void> {
    availablePlugins = [...BUILTIN_PLUGINS];
    try {
      const bundled = await scanBundledPlugins();
      const user = await scanUserPlugins();

      // Dedup by id: prefer bundled over package over user.
      const byId = new Map<string, PluginDescriptor>();
      for (const p of availablePlugins) byId.set(p.id, p);
      for (const p of bundled) byId.set(p.id, p);
      for (const p of user) if (!byId.has(p.id)) byId.set(p.id, p);
      availablePlugins = Array.from(byId.values());
    } catch (e) {
      // Expected in browser-only mode (no Tauri).
      deps.setVizStatus(`plugin scan failed (expected in browser-only mode): ${String(e)}`);
    }

    // Sort: built-ins first, then user.
    availablePlugins.sort((a, b) => {
      if (a.source !== b.source) return a.source === "builtin" ? -1 : 1;
      return a.id.localeCompare(b.id);
    });

    render();
  }

  // User picked a different plugin -> flip mode + tell host to restart viz.
  deps.pluginSelect.addEventListener("change", () => {
    selectionMode = "user";
    deps.onPluginSelectionChange();
  });

  // Refresh button just re-scans.
  deps.refreshBtn.addEventListener("click", () => {
    void refresh();
  });

  return {
    refresh,
    render,
    getSelectedPluginId,
    getCurrent: () => {
      if (!availablePlugins.length) return null;
      const idx = deps.pluginSelect.selectedIndex;
      if (idx < 0 || idx >= availablePlugins.length) return availablePlugins[0];
      return availablePlugins[idx];
    },
    getAvailable: () => availablePlugins,
    syncPreferred,
    setSelectionModeAuto: () => {
      selectionMode = "auto";
    },
  };
}
