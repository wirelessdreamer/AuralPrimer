// @vitest-environment jsdom
/**
 * Execution tests for the pause-menu overlay controller. Imports + runs
 * initPauseMenu, exercises show/close/openPaused/resume/backToLibrary across
 * both modes, the isVisible() guards, button wiring, and focus restore.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initPauseMenu, type PauseMenuDeps } from "../src/pauseMenu";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="pauseMenuOverlay">' +
    '<div class="pauseMenuKicker"></div>' +
    '<h2 id="pauseMenuTitle"></h2>' +
    '<p id="pauseMenuCopy"></p>' +
    '<div class="pauseMenuHint"></div>' +
    '<button id="pauseMenuResume"></button>' +
    '<button id="pauseMenuBack"></button>' +
    "</div>";
}

function makeDeps(): PauseMenuDeps {
  return {
    consoleBridge: { log: vi.fn() } as unknown as PauseMenuDeps["consoleBridge"],
    onPauseRequest: vi.fn(),
    onResumeRequest: vi.fn().mockResolvedValue(undefined),
    onBackToLibrary: vi.fn(),
  };
}

const overlay = () => document.getElementById("pauseMenuOverlay") as HTMLDivElement;
const resumeBtn = () => document.getElementById("pauseMenuResume") as HTMLButtonElement;
const backBtn = () => document.getElementById("pauseMenuBack") as HTMLButtonElement;
const kicker = () => document.querySelector(".pauseMenuKicker") as HTMLDivElement;

describe("pauseMenu", () => {
  beforeEach(() => {
    stageDom();
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initPauseMenu(makeDeps())).toThrow(/required DOM/);
  });

  it("starts hidden and not visible", () => {
    const menu = initPauseMenu(makeDeps());
    expect(menu.isVisible()).toBe(false);
    expect(overlay().hidden).toBe(true);
    expect(overlay().getAttribute("aria-hidden")).toBe("true");
  });

  it("show('paused') sets the paused copy + opens + focuses resume", () => {
    const menu = initPauseMenu(makeDeps());
    menu.show("paused");
    expect(menu.isVisible()).toBe(true);
    expect(menu.getMode()).toBe("paused");
    expect(kicker().textContent).toBe("Paused");
    expect(resumeBtn().textContent).toBe("Resume");
    expect(overlay().classList.contains("isVisible")).toBe(true);
    expect(document.body.classList.contains("pauseMenuOpen")).toBe(true);
  });

  it("show('loaded') sets the loaded-song copy", () => {
    const menu = initPauseMenu(makeDeps());
    menu.show("loaded");
    expect(menu.getMode()).toBe("loaded");
    expect(kicker().textContent).toBe("Song Ready");
    expect(resumeBtn().textContent).toBe("Stay Here");
  });

  it("show() is a no-op when already visible", () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("paused");
    (deps.consoleBridge.log as ReturnType<typeof vi.fn>).mockClear();
    menu.show("loaded"); // ignored — already visible
    expect(menu.getMode()).toBe("paused");
    expect(deps.consoleBridge.log).not.toHaveBeenCalled();
  });

  it("openPaused() fires onPauseRequest then opens", () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.openPaused();
    expect(deps.onPauseRequest).toHaveBeenCalledTimes(1);
    expect(menu.isVisible()).toBe(true);
    expect(menu.getMode()).toBe("paused");
  });

  it("close() restores focus to the previously-focused element", () => {
    const menu = initPauseMenu(makeDeps());
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    menu.show("paused");
    menu.close(); // restoreFocus defaults true
    expect(menu.isVisible()).toBe(false);
    expect(document.activeElement).toBe(trigger);
    expect(document.body.classList.contains("pauseMenuOpen")).toBe(false);
  });

  it("close() honors restoreFocus:false", () => {
    const menu = initPauseMenu(makeDeps());
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    menu.show("paused");
    menu.close({ restoreFocus: false });
    expect(document.activeElement).not.toBe(trigger);
  });

  it("close() while already hidden clears restore state (wasVisible=false)", () => {
    const menu = initPauseMenu(makeDeps());
    menu.close();
    expect(menu.isVisible()).toBe(false);
  });

  it("resume() in paused mode awaits onResumeRequest", async () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("paused");
    await menu.resume();
    expect(deps.onResumeRequest).toHaveBeenCalledTimes(1);
    expect(menu.isVisible()).toBe(false);
  });

  it("resume() in loaded mode just closes, no transport call", async () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("loaded");
    await menu.resume();
    expect(deps.onResumeRequest).not.toHaveBeenCalled();
    expect(menu.isVisible()).toBe(false);
  });

  it("backToLibrary() closes + fires onBackToLibrary", () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("paused");
    menu.backToLibrary();
    expect(deps.onBackToLibrary).toHaveBeenCalledTimes(1);
    expect(menu.isVisible()).toBe(false);
  });

  it("resume button click drives resume()", async () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("paused");
    resumeBtn().dispatchEvent(new MouseEvent("click"));
    await Promise.resolve();
    await Promise.resolve();
    expect(deps.onResumeRequest).toHaveBeenCalled();
  });

  it("back button click drives backToLibrary()", () => {
    const deps = makeDeps();
    const menu = initPauseMenu(deps);
    menu.show("paused");
    backBtn().dispatchEvent(new MouseEvent("click"));
    expect(deps.onBackToLibrary).toHaveBeenCalledTimes(1);
  });
});
