/**
 * tabRenderer.ts — thin re-export shim.
 *
 * The implementation moved into `visualizers/viz-tab/` as Phase 2.G of the
 * decomposition. This shim preserves the existing import path
 * (`./tabRenderer`) so the game frontend and its tests don't need to be
 * touched in this pass; a follow-up can migrate them to import directly
 * from `@auralprimer/viz-tab`.
 *
 * The full Visualizer-plugin adapter (so TabRenderer can be loaded via
 * the existing plugin registry alongside viz-drum-highway / viz-beats /
 * viz-nashville and the per-player secondary stages can host it) is
 * deferred — it requires extending the Visualizer interface to communicate
 * which instrument lane to render.
 */
export * from "@auralprimer/viz-tab";
