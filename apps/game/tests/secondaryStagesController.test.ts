// @vitest-environment jsdom
/**
 * Execution tests for the secondary-stages controller (per-player viz
 * canvases for Players 2..N).
 *
 * Imports + runs initSecondaryStagesController with a mocked ./plugins
 * loadPlugin, a fake playersPanel/pluginsPanel, and a stubbed canvas 2d
 * context. Drives build() (sibling canvas creation), renderAll, resizeAll,
 * dispose, and the load/init error paths.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const loadPlugin = vi.fn();
vi.mock("../src/plugins", () => ({
  loadPlugin: (...args: unknown[]) => loadPlugin(...args),
}));

import {
  initSecondaryStagesController,
  type SecondaryStagesControllerDeps,
} from "../src/secondaryStagesController";

// --- canvas 2d stub (jsdom returns null from getContext) ----------------
const ctx2dStub = {
  setTransform: vi.fn(),
} as unknown as CanvasRenderingContext2D;
let returnNullContext = false;

beforeEach(() => {
  returnNullContext = false;
  vi.stubGlobal("HTMLCanvasElement", HTMLCanvasElement);
  HTMLCanvasElement.prototype.getContext = vi.fn(function () {
    return returnNullContext ? null : ctx2dStub;
  }) as unknown as HTMLCanvasElement["getContext"];
});

// --- fakes ---------------------------------------------------------------
function makeViz() {
  return {
    init: vi.fn().mockResolvedValue(undefined),
    update: vi.fn(),
    render: vi.fn(),
    onResize: vi.fn(),
    dispose: vi.fn(),
  };
}

function makePlayers(insts: string[]) {
  // Player 1 plus the given extras.
  const players = [
    { id: "p1", name: "Player 1", instrument: "drums" },
    ...insts.map((inst, i) => ({ id: `p${i + 2}`, name: `Player ${i + 2}`, instrument: inst })),
  ];
  return players;
}

function makeDeps(
  insts: string[],
  overrides: Partial<SecondaryStagesControllerDeps> = {},
): SecondaryStagesControllerDeps {
  const stagesEl = document.getElementById("playerStages") as HTMLElement;
  return {
    playerStagesEl: stagesEl,
    playersPanel: {
      getPlayers: vi.fn().mockReturnValue(makePlayers(insts)),
    } as unknown as SecondaryStagesControllerDeps["playersPanel"],
    pluginsPanel: {
      getAvailable: vi
        .fn()
        .mockReturnValue([
          { id: "viz-beats" },
          { id: "viz-drum-highway" },
          { id: "viz-nashville" },
          { id: "viz-lyrics" },
        ]),
    } as unknown as SecondaryStagesControllerDeps["pluginsPanel"],
    consoleBridge: { log: vi.fn() } as unknown as SecondaryStagesControllerDeps["consoleBridge"],
    getSongContext: vi.fn().mockReturnValue({}),
    ...overrides,
  };
}

const stagesEl = () => document.getElementById("playerStages") as HTMLElement;
const canvases = () => Array.from(stagesEl().querySelectorAll("canvas"));

describe("secondaryStagesController", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="playerStages"></div>';
    loadPlugin.mockReset();
  });

  it("build() with only Player 1 creates no stages and sets count=1", async () => {
    const deps = makeDeps([]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(0);
    expect(canvases().length).toBe(0);
    expect(stagesEl().getAttribute("data-player-count")).toBe("1");
  });

  it("build() creates one canvas + visualizer per extra player", async () => {
    const viz1 = makeViz();
    const viz2 = makeViz();
    const disposeFns = [vi.fn(), vi.fn()];
    loadPlugin
      .mockResolvedValueOnce({ module: { createVisualizer: () => viz1 }, dispose: disposeFns[0] })
      .mockResolvedValueOnce({ module: { createVisualizer: () => viz2 }, dispose: disposeFns[1] });
    const deps = makeDeps(["lead_guitar", "bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(2);
    expect(canvases().length).toBe(2);
    expect(stagesEl().getAttribute("data-player-count")).toBe("3");
    expect(viz1.init).toHaveBeenCalled();
    expect(viz2.init).toHaveBeenCalled();
    // Canvases carry the player id.
    expect(canvases()[0].dataset.playerId).toBe("p2");
  });

  it("build() picks the instrument's default plugin, falling back when absent", async () => {
    const viz = makeViz();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: vi.fn() });
    const deps = makeDeps(["drums"]); // drums → viz-drum-highway
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    const descriptor = loadPlugin.mock.calls[0][0];
    expect(descriptor.id).toBe("viz-drum-highway");
  });

  it("build() falls back to DEFAULT_PLUGIN_ID when the preferred id is unavailable", async () => {
    const viz = makeViz();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: vi.fn() });
    const deps = makeDeps(["drums"], {
      pluginsPanel: {
        getAvailable: vi.fn().mockReturnValue([{ id: "viz-beats" }]),
      } as unknown as SecondaryStagesControllerDeps["pluginsPanel"],
    });
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(loadPlugin.mock.calls[0][0].id).toBe("viz-beats");
  });

  it("build() skips a player when no descriptors are available at all", async () => {
    const deps = makeDeps(["bass"], {
      pluginsPanel: {
        getAvailable: vi.fn().mockReturnValue([]),
      } as unknown as SecondaryStagesControllerDeps["pluginsPanel"],
    });
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(0);
    expect(canvases().length).toBe(0);
  });

  it("build() skips a player whose canvas has no 2d context", async () => {
    returnNullContext = true;
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(0);
    expect(deps.consoleBridge.log).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("missing 2d context"),
    );
  });

  it("build() skips a player when plugin load rejects", async () => {
    loadPlugin.mockRejectedValue(new Error("boom"));
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(0);
    expect(deps.consoleBridge.log).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("plugin load failed"),
    );
  });

  it("build() skips + disposes when viz.init rejects", async () => {
    const viz = makeViz();
    viz.init.mockRejectedValue(new Error("init-fail"));
    const dispose = vi.fn();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(0);
    expect(viz.dispose).toHaveBeenCalled();
    expect(dispose).toHaveBeenCalled();
    expect(deps.consoleBridge.log).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("init failed"),
    );
  });

  it("build() is idempotent — disposes existing stages first", async () => {
    const viz = makeViz();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: vi.fn() });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(ctl.count()).toBe(1);
    await ctl.build();
    expect(ctl.count()).toBe(1);
    expect(canvases().length).toBe(1);
    expect(viz.dispose).toHaveBeenCalled();
  });

  it("renderAll updates + renders each stage and swallows render errors", async () => {
    const viz = makeViz();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: vi.fn() });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    const transport = { time: 0 } as never;
    ctl.renderAll(0.016, transport, 2);
    expect(viz.update).toHaveBeenCalledWith(0.016, transport);
    expect(viz.render).toHaveBeenCalled();

    viz.render.mockImplementationOnce(() => {
      throw new Error("render-boom");
    });
    expect(() => ctl.renderAll(0.016, transport, 2)).not.toThrow();
    expect(deps.consoleBridge.log).toHaveBeenCalledWith(
      "debugging",
      expect.stringContaining("render failed"),
    );
  });

  it("resizeAll rescales each canvas for DPR and notifies the viz", async () => {
    const viz = makeViz();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: vi.fn() });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    ctl.resizeAll(400, 200, 2);
    const canvas = canvases()[0];
    // clientWidth/Height are 0 in jsdom → falls back to css args * dpr.
    expect(canvas.width).toBe(800);
    expect(canvas.height).toBe(400);
    expect(ctx2dStub.setTransform).toHaveBeenCalledWith(2, 0, 0, 2, 0, 0);
    expect(viz.onResize).toHaveBeenCalledWith(400, 200, 2);
  });

  it("dispose() tears down stages, removes canvases, resets count", async () => {
    const viz = makeViz();
    const pluginDispose = vi.fn();
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: pluginDispose });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    ctl.dispose();
    expect(ctl.count()).toBe(0);
    expect(canvases().length).toBe(0);
    expect(viz.dispose).toHaveBeenCalled();
    expect(pluginDispose).toHaveBeenCalled();
    expect(stagesEl().getAttribute("data-player-count")).toBe("1");
  });

  it("dispose() swallows errors thrown by viz.dispose / plugin dispose", async () => {
    const viz = makeViz();
    viz.dispose.mockImplementation(() => {
      throw new Error("dispose-boom");
    });
    const pluginDispose = vi.fn(() => {
      throw new Error("plugin-dispose-boom");
    });
    loadPlugin.mockResolvedValue({ module: { createVisualizer: () => viz }, dispose: pluginDispose });
    const deps = makeDeps(["bass"]);
    const ctl = initSecondaryStagesController(deps);
    await ctl.build();
    expect(() => ctl.dispose()).not.toThrow();
    expect(ctl.count()).toBe(0);
  });

  it("renderAll is a no-op when there are no stages", () => {
    const deps = makeDeps([]);
    const ctl = initSecondaryStagesController(deps);
    expect(() => ctl.renderAll(0.016, {} as never, 1)).not.toThrow();
  });
});
