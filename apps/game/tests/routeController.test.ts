// @vitest-environment jsdom
/**
 * Execution tests for the top-level route + play-step controller. Stages the
 * nav bar + route panels in jsdom and asserts setRoute sweeps .isActive,
 * stops viz/transport when leaving Play, and that the play-step helpers flip
 * the layout classes + trigger the library refresh.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initRouteController } from "../src/routeController";

function stageDom(): void {
  document.body.innerHTML =
    '<nav>' +
    '  <button id="navHome"></button>' +
    '  <button id="navPlay"></button>' +
    '  <button id="navLearn"></button>' +
    '  <button id="navConfig"></button>' +
    "</nav>" +
    '<button id="homePlay"></button>' +
    '<button id="homeConfig"></button>' +
    '<button id="homeExit"></button>' +
    '<button id="toggleFocus"></button>' +
    '<section class="route" data-route="home"></section>' +
    '<section class="route" data-route="play"></section>' +
    '<section class="route" data-route="learn"></section>' +
    '<section class="route" data-route="config"></section>' +
    '<div id="playLayout"></div>';
}

function makeDeps(overrides: Partial<Parameters<typeof initRouteController>[0]> = {}) {
  return {
    consoleBridge: { log: vi.fn(), warn: vi.fn(), error: vi.fn() } as any,
    pauseMenu: { close: vi.fn() } as any,
    songLibraryPanel: { refresh: vi.fn(async () => {}) } as any,
    transportController: { pause: vi.fn() } as any,
    haveTauri: vi.fn(() => false),
    stopVisualizer: vi.fn(),
    resizeVizCanvas: vi.fn(),
    syncPlaySurfaceMode: vi.fn(),
    isLoadedSongSelected: vi.fn(() => false),
    playLayoutEl: document.getElementById("playLayout") as HTMLElement,
    ...overrides,
  } as Parameters<typeof initRouteController>[0];
}

const active = (id: string) => document.getElementById(id)!.classList.contains("isActive");
const routePanel = (r: string) =>
  document.querySelector(`.route[data-route="${r}"]`) as HTMLElement;

describe("routeController", () => {
  beforeEach(() => stageDom());

  it("throws when #toggleFocus is missing", () => {
    document.getElementById("toggleFocus")!.remove();
    expect(() => initRouteController(makeDeps())).toThrow(/required DOM/);
  });

  it("starts on home", () => {
    const handle = initRouteController(makeDeps());
    expect(handle.getCurrentRoute()).toBe("home");
  });

  it("setRoute(config) sweeps nav + route panels and stops viz/transport (leaving play)", () => {
    const deps = makeDeps();
    const handle = initRouteController(deps);
    handle.setRoute("config");
    expect(handle.getCurrentRoute()).toBe("config");
    expect(active("navConfig")).toBe(true);
    expect(active("navHome")).toBe(false);
    expect(routePanel("config").classList.contains("isActive")).toBe(true);
    expect(routePanel("home").classList.contains("isActive")).toBe(false);
    expect(deps.pauseMenu.close).toHaveBeenCalled();
    expect(deps.stopVisualizer).toHaveBeenCalled();
    expect(deps.transportController.pause).toHaveBeenCalled();
    expect(deps.syncPlaySurfaceMode).toHaveBeenCalled();
  });

  it("setRoute(play) does NOT stop viz/transport", () => {
    const deps = makeDeps();
    const handle = initRouteController(deps);
    (deps.stopVisualizer as any).mockClear();
    handle.setRoute("play");
    expect(active("navPlay")).toBe(true);
    expect(deps.stopVisualizer).not.toHaveBeenCalled();
  });

  it("swallows errors thrown by stopVisualizer when leaving play", () => {
    const deps = makeDeps({
      stopVisualizer: vi.fn(() => { throw new Error("viz-fail"); }),
    });
    const handle = initRouteController(deps);
    expect(() => handle.setRoute("home")).not.toThrow();
  });

  it("openPlaySongFlow enters play + shows the library step and refreshes", () => {
    const deps = makeDeps();
    const handle = initRouteController(deps);
    handle.openPlaySongFlow();
    expect(handle.getCurrentRoute()).toBe("play");
    const layout = document.getElementById("playLayout")!;
    expect(layout.classList.contains("isLibraryOnly")).toBe(true);
    expect(layout.classList.contains("isFocus")).toBe(false);
    expect(deps.songLibraryPanel.refresh).toHaveBeenCalled();
    expect(deps.resizeVizCanvas).toHaveBeenCalled();
  });

  it("showBandSetupStep flips to focus mode and removes isLibraryOnly", () => {
    const deps = makeDeps();
    const handle = initRouteController(deps);
    handle.showBandSetupStep();
    const layout = document.getElementById("playLayout")!;
    expect(layout.classList.contains("isLibraryOnly")).toBe(false);
    expect(layout.classList.contains("isFocus")).toBe(true);
  });

  it("setPlayFocusMode toggles the layout class + resizes the canvas", () => {
    const deps = makeDeps();
    const handle = initRouteController(deps);
    handle.setPlayFocusMode(true);
    expect(document.getElementById("playLayout")!.classList.contains("isFocus")).toBe(true);
    handle.setPlayFocusMode(false);
    expect(document.getElementById("playLayout")!.classList.contains("isFocus")).toBe(false);
  });

  it("canOpenLoadedSongBackOutPrompt requires play route + loaded selection", () => {
    const deps = makeDeps({ isLoadedSongSelected: vi.fn(() => true) });
    const handle = initRouteController(deps);
    expect(handle.canOpenLoadedSongBackOutPrompt()).toBe(false); // on home
    handle.setRoute("play");
    expect(handle.canOpenLoadedSongBackOutPrompt()).toBe(true);
  });

  it("exitApplication closes the window in browser mode (no Tauri)", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => {});
    const handle = initRouteController(makeDeps({ haveTauri: vi.fn(() => false) }));
    await handle.exitApplication();
    expect(close).toHaveBeenCalled();
    close.mockRestore();
  });

  it("nav + home buttons drive the controller; toggleFocus returns to library", () => {
    const deps = makeDeps();
    initRouteController(deps);
    document.getElementById("navPlay")!.dispatchEvent(new MouseEvent("click"));
    expect(active("navPlay")).toBe(true);
    document.getElementById("navConfig")!.dispatchEvent(new MouseEvent("click"));
    expect(active("navConfig")).toBe(true);
    document.getElementById("navHome")!.dispatchEvent(new MouseEvent("click"));
    expect(active("navHome")).toBe(true);
    document.getElementById("homePlay")!.dispatchEvent(new MouseEvent("click"));
    expect(active("navPlay")).toBe(true);
    document.getElementById("homeConfig")!.dispatchEvent(new MouseEvent("click"));
    expect(active("navConfig")).toBe(true);

    (deps.songLibraryPanel.refresh as any).mockClear();
    document.getElementById("toggleFocus")!.dispatchEvent(new MouseEvent("click"));
    expect(deps.songLibraryPanel.refresh).toHaveBeenCalled();
  });

  it("homeExit click invokes exit; failures are routed to consoleBridge.error", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => {
      throw new Error("close-fail");
    });
    const deps = makeDeps({ haveTauri: vi.fn(() => false) });
    initRouteController(deps);
    document.getElementById("homeExit")!.dispatchEvent(new MouseEvent("click"));
    await Promise.resolve();
    await Promise.resolve();
    expect(deps.consoleBridge.error).toHaveBeenCalled();
    close.mockRestore();
  });
});
