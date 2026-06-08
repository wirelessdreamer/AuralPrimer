/**
 * Secondary-stages controller — owns the per-player Visualizer instances
 * for Players 2..N (Player 1 keeps the static #viz canvas). Each
 * secondary player gets its own dynamically-created canvas appended to
 * #playerStages and its own Visualizer instance keyed off that player's
 * instrument preference.
 *
 * Owns:
 *   - the secondaryStages[] array (private)
 *   - the #playerStages container's data-player-count attribute
 *   - canvas creation / Visualizer init / dispose
 *
 * The host's RAF tick loop calls renderAll() once per frame; resizeVizCanvas
 * calls resizeAll() to mirror the DPR scaling Player 1 does. Extracted from
 * main.ts as Phase 2.U.
 */

import type { Visualizer, TransportState } from "@auralprimer/viz-sdk";
import type { PlayersPanelHandle } from "./playersPanel";
import type { PluginsPanelHandle } from "./pluginsPanel";
import { defaultPluginIdForInstrument, DEFAULT_PLUGIN_ID } from "./playersPanel";
import { loadPlugin } from "./plugins";
import type { ConsoleBridge } from "./consoleBridge";
import type { VizSongContext } from "./vizSongContext";

type SecondaryStage = {
  playerId: string;
  canvas: HTMLCanvasElement;
  ctx2d: CanvasRenderingContext2D;
  viz: Visualizer | null;
  dispose: (() => void) | null;
  pluginId: string;
};

export type SecondaryStagesControllerDeps = {
  playerStagesEl: HTMLElement;
  playersPanel: PlayersPanelHandle;
  pluginsPanel: PluginsPanelHandle;
  consoleBridge: ConsoleBridge;
  /** Live getter for the song context (lyrics + charts + notes). */
  getSongContext: () => VizSongContext;
};

export type SecondaryStagesControllerHandle = {
  /** Build a Visualizer for each player past Player 1. Idempotent (disposes first). */
  build: () => Promise<void>;
  /** Tear down every secondary stage's visualizer + remove its canvas. */
  dispose: () => void;
  /** Per-frame render. Called from the host's RAF tick loop. */
  renderAll: (dt: number, transport: TransportState, dpr: number) => void;
  /** Mirror Player 1's DPR-aware resize across every secondary canvas. */
  resizeAll: (cssWidth: number, cssHeight: number, dpr: number) => void;
  /** Read the current stage count (used to set #playerStages data-player-count). */
  count: () => number;
};

export function initSecondaryStagesController(
  deps: SecondaryStagesControllerDeps,
): SecondaryStagesControllerHandle {
  let secondaryStages: SecondaryStage[] = [];

  function dispose(): void {
    for (const stage of secondaryStages) {
      try {
        stage.viz?.dispose();
      } catch {
        /* swallow */
      }
      if (stage.dispose) {
        try {
          stage.dispose();
        } catch {
          /* swallow */
        }
      }
      stage.canvas.remove();
    }
    secondaryStages = [];
    deps.playerStagesEl.setAttribute("data-player-count", "1");
  }

  async function build(): Promise<void> {
    dispose();
    const extras = deps.playersPanel.getPlayers().slice(1);
    if (extras.length === 0) return;

    for (const player of extras) {
      const canvas = document.createElement("canvas");
      canvas.className = "playerStage__canvas";
      canvas.dataset.playerId = player.id;
      canvas.width = 800;
      canvas.height = 240;
      deps.playerStagesEl.appendChild(canvas);

      const ctx2d = canvas.getContext("2d");
      if (!ctx2d) {
        canvas.remove();
        deps.consoleBridge.log("debugging", `secondary stage ${player.id}: missing 2d context, skipping`);
        continue;
      }

      const pluginId = defaultPluginIdForInstrument(player.instrument);
      const plugins = deps.pluginsPanel.getAvailable();
      const descriptor =
        plugins.find((p) => p.id === pluginId)
        ?? plugins.find((p) => p.id === DEFAULT_PLUGIN_ID)
        ?? plugins[0];
      if (!descriptor) {
        canvas.remove();
        continue;
      }

      let loaded;
      try {
        loaded = await loadPlugin(descriptor);
      } catch (e) {
        deps.consoleBridge.log("debugging", `secondary stage ${player.id}: plugin load failed: ${String(e)}`);
        canvas.remove();
        continue;
      }

      const pviz = loaded.module.createVisualizer();
      try {
        await pviz.init({
          canvas,
          ctx2d,
          song: deps.getSongContext(),
          players: [{ id: player.id, name: player.name, instrument: player.instrument }],
        });
      } catch (e) {
        deps.consoleBridge.log("debugging", `secondary stage ${player.id}: init failed: ${String(e)}`);
        try { pviz.dispose(); } catch { /* swallow */ }
        loaded.dispose?.();
        canvas.remove();
        continue;
      }

      secondaryStages.push({
        playerId: player.id,
        canvas,
        ctx2d,
        viz: pviz,
        dispose: loaded.dispose ?? null,
        pluginId: descriptor.id,
      });
    }

    deps.playerStagesEl.setAttribute("data-player-count", String(1 + secondaryStages.length));
  }

  function renderAll(dt: number, transport: TransportState, dpr: number): void {
    for (const stage of secondaryStages) {
      if (!stage.viz) continue;
      try {
        stage.viz.update(dt, transport);
        stage.viz.render({
          canvas: stage.canvas,
          ctx2d: stage.ctx2d,
          width: stage.canvas.width / dpr,
          height: stage.canvas.height / dpr,
          dpr,
          state: transport,
        });
      } catch (e) {
        // Individual stage failures shouldn't kill the whole RAF loop.
        deps.consoleBridge.log("debugging", `secondary stage ${stage.playerId} render failed: ${String(e)}`);
      }
    }
  }

  function resizeAll(cssWidth: number, cssHeight: number, dpr: number): void {
    for (const stage of secondaryStages) {
      const sw = stage.canvas.clientWidth || cssWidth;
      const sh = stage.canvas.clientHeight || cssHeight;
      stage.canvas.width = Math.floor(sw * dpr);
      stage.canvas.height = Math.floor(sh * dpr);
      stage.ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
      stage.viz?.onResize(sw, sh, dpr);
    }
  }

  return {
    build,
    dispose,
    renderAll,
    resizeAll,
    count: () => secondaryStages.length,
  };
}
