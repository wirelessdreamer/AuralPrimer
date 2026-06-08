/**
 * Pause-menu overlay controller.
 *
 * Owns the pause-menu overlay DOM, the open/closed state, restore-focus
 * tracking, and the two modes ("paused" while a song plays / "loaded"
 * when a song is loaded but not yet started). The host wires callbacks
 * for the actual transport operations (pause / resume / back-to-library)
 * via deps so this module stays focused on UI behavior.
 *
 * Extracted from main.ts as Phase 2.J.
 */

import type { ConsoleBridge } from "./consoleBridge";

export type PauseMenuMode = "paused" | "loaded";

export type PauseMenuDeps = {
  consoleBridge: ConsoleBridge;
  /** Called when the user actively chooses to pause via the menu. */
  onPauseRequest: () => void;
  /** Called when the user resumes from a "paused" prompt. */
  onResumeRequest: () => Promise<void>;
  /** Called when the user backs out to song selection (from either mode). */
  onBackToLibrary: () => void;
};

export type PauseMenuHandle = {
  /** True when the overlay is currently open. */
  isVisible: () => boolean;
  /** Programmatically open the menu with the given mode. */
  show: (mode?: PauseMenuMode) => void;
  /** Programmatically close the menu (optionally restoring focus). */
  close: (opts?: { restoreFocus?: boolean }) => void;
  /**
   * Convenience: open as the "paused" mode (the host fires onPauseRequest
   * which pauses the transport, then this opens). Used by Esc / pause button.
   */
  openPaused: () => void;
  /** Resume from the menu — caller for the menu's resume button. */
  resume: () => Promise<void>;
  /** Back to library — caller for the menu's back button. */
  backToLibrary: () => void;
  /** Read current mode (for callers that need to branch on it). */
  getMode: () => PauseMenuMode;
};

export function initPauseMenu(deps: PauseMenuDeps): PauseMenuHandle {
  const overlayEl = document.getElementById("pauseMenuOverlay") as HTMLDivElement | null;
  const kickerEl = overlayEl?.querySelector(".pauseMenuKicker") as HTMLDivElement | null;
  const titleEl = document.getElementById("pauseMenuTitle") as HTMLHeadingElement | null;
  const copyEl = document.getElementById("pauseMenuCopy") as HTMLParagraphElement | null;
  const hintEl = overlayEl?.querySelector(".pauseMenuHint") as HTMLDivElement | null;
  const resumeBtn = document.getElementById("pauseMenuResume") as HTMLButtonElement | null;
  const backBtn = document.getElementById("pauseMenuBack") as HTMLButtonElement | null;
  if (!overlayEl || !kickerEl || !titleEl || !copyEl || !hintEl || !resumeBtn || !backBtn) {
    throw new Error("initPauseMenu: required DOM (#pauseMenuOverlay/.pauseMenuKicker/#pauseMenuTitle/#pauseMenuCopy/.pauseMenuHint/#pauseMenuResume/#pauseMenuBack) missing");
  }

  let isOpen = false;
  let mode: PauseMenuMode = "paused";
  let restoreFocusEl: HTMLElement | null = null;

  // Ensure the overlay starts hidden (idempotent — original main.ts did this too).
  overlayEl.hidden = true;
  overlayEl.classList.remove("isVisible");
  overlayEl.setAttribute("aria-hidden", "true");

  function isVisible(): boolean {
    return isOpen || overlayEl!.classList.contains("isVisible") || !overlayEl!.hidden;
  }

  function setMode(next: PauseMenuMode): void {
    mode = next;
    if (next === "paused") {
      kickerEl!.textContent = "Paused";
      titleEl!.textContent = "Pause Menu";
      copyEl!.textContent = "Keep your place and resume, or head back to song selection.";
      resumeBtn!.textContent = "Resume";
      hintEl!.textContent = "Press Esc again to resume instantly.";
      return;
    }
    kickerEl!.textContent = "Song Ready";
    titleEl!.textContent = "Back Out?";
    copyEl!.textContent = "This song is loaded. Stay here, or head back to song selection.";
    resumeBtn!.textContent = "Stay Here";
    hintEl!.textContent = "Press Esc again to close this prompt.";
  }

  function close(opts?: { restoreFocus?: boolean }): void {
    const wasVisible = isVisible();
    isOpen = false;
    mode = "paused";
    overlayEl!.classList.remove("isVisible");
    overlayEl!.hidden = true;
    overlayEl!.setAttribute("aria-hidden", "true");
    document.body.classList.remove("pauseMenuOpen");

    if (!wasVisible) {
      restoreFocusEl = null;
      return;
    }
    const restoreFocus = opts?.restoreFocus ?? true;
    const focusTarget = restoreFocusEl;
    restoreFocusEl = null;
    if (restoreFocus && focusTarget) focusTarget.focus();
  }

  function show(nextMode: PauseMenuMode = "paused"): void {
    if (isVisible()) return;
    setMode(nextMode);
    isOpen = true;
    restoreFocusEl = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    overlayEl!.hidden = false;
    overlayEl!.classList.add("isVisible");
    overlayEl!.setAttribute("aria-hidden", "false");
    document.body.classList.add("pauseMenuOpen");
    resumeBtn!.focus();
    deps.consoleBridge.log("gamestate", "pause menu -> open");
  }

  function openPaused(): void {
    deps.onPauseRequest();
    // Host's onPauseRequest is responsible for transitioning the transport;
    // we then visually open the menu. If host already opened it, show() is
    // a no-op via isVisible() guard.
    show("paused");
  }

  async function resume(): Promise<void> {
    const at = mode;
    close({ restoreFocus: false });
    if (at === "loaded") {
      deps.consoleBridge.log("gamestate", "pause menu -> close loaded-song prompt");
      return;
    }
    await deps.onResumeRequest();
  }

  function backToLibrary(): void {
    close({ restoreFocus: false });
    deps.onBackToLibrary();
  }

  // Wire the two buttons inside the overlay to the public methods so the
  // host doesn't have to re-wire them after init.
  resumeBtn.addEventListener("click", () => {
    void resume();
  });
  backBtn.addEventListener("click", () => {
    backToLibrary();
  });

  return {
    isVisible,
    show,
    close,
    openPaused,
    resume,
    backToLibrary,
    getMode: () => mode,
  };
}
