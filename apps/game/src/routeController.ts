/**
 * Top-level route + step controller — owns:
 *   - the currentRoute state (home / play / learn / config)
 *   - the play-step state (library-only vs band-setup vs focus mode)
 *   - the .route / .isActive class sweeps across both the nav bar and
 *     each route panel
 *   - the nav button click handlers (#navHome / #navPlay / #navConfig /
 *     #homePlay / #homeConfig / #homeExit) and the in-play toggleFocus
 *     button
 *   - exitApplication (Tauri window close or browser window.close())
 *
 * The host wires four callbacks for the cross-cutting effects this
 * controller has to trigger (stopVisualizer, resizeVizCanvas,
 * syncPlaySurfaceMode, the loaded-song back-out gate check). Extracted
 * from main.ts as Phase 2.P.
 */

import type { ConsoleBridge } from "./consoleBridge";
import type { PauseMenuHandle } from "./pauseMenu";
import type { SongLibraryPanelHandle } from "./songLibraryPanel";
import type { TransportController } from "./transportController";

export type Route = "home" | "play" | "learn" | "config";

export type RouteControllerDeps = {
  consoleBridge: ConsoleBridge;
  pauseMenu: PauseMenuHandle;
  songLibraryPanel: SongLibraryPanelHandle;
  transportController: TransportController;
  haveTauri: () => boolean;
  /** Cross-cutting effects the host needs to perform on route/step changes. */
  stopVisualizer: () => void;
  resizeVizCanvas: () => void;
  syncPlaySurfaceMode: () => void;
  /**
   * Returns true if a song is loaded + selected. Used by the Esc-on-loaded
   * codepath to decide whether to offer the back-out prompt.
   */
  isLoadedSongSelected: () => boolean;
  /** Main play-area container. Owns isLibraryOnly / isFocus class flags. */
  playLayoutEl: HTMLElement;
};

export type RouteControllerHandle = {
  /** Active route (home / play / learn / config). */
  getCurrentRoute: () => Route;
  /** Switch top-level route + sweep nav highlights. Stops viz/transport when leaving Play. */
  setRoute: (route: Route) => void;
  /** Convenience: enter Play and reveal the library step. */
  openPlaySongFlow: () => void;
  /** Close the Tauri window (or the browser tab in dev). */
  exitApplication: () => Promise<void>;
  /** Show the song-library step (library list visible, gameplay hidden). */
  showSongLibraryStep: () => void;
  /** Show the band-setup step (gameplay visible, library hidden, focus on). */
  showBandSetupStep: () => void;
  /** Toggle the focus-mode layout class. */
  setPlayFocusMode: (enabled: boolean) => void;
  /** True when on Play with a loaded song + selection matching last-loaded path. */
  canOpenLoadedSongBackOutPrompt: () => boolean;
};

export function initRouteController(deps: RouteControllerDeps): RouteControllerHandle {
  const toggleFocusBtn = document.getElementById("toggleFocus") as HTMLButtonElement | null;
  if (!toggleFocusBtn) {
    throw new Error("initRouteController: required DOM (#toggleFocus) missing");
  }

  let currentRoute: Route = "home";

  function setRoute(route: Route): void {
    currentRoute = route;
    const routes = Array.from(document.querySelectorAll<HTMLElement>(".route"));
    for (const el of routes) {
      const r = el.dataset.route as Route | undefined;
      el.classList.toggle("isActive", r === route);
    }

    const navMap: Record<Route, string> = {
      home: "navHome",
      play: "navPlay",
      learn: "navLearn",
      config: "navConfig",
    };

    for (const [r, id] of Object.entries(navMap) as Array<[Route, string]>) {
      document.getElementById(id)?.classList.toggle("isActive", r === route);
    }

    // Keep the experience tidy: stop visuals/audio when leaving Play.
    if (route !== "play") {
      deps.pauseMenu.close({ restoreFocus: false });
      try {
        deps.stopVisualizer();
        deps.transportController.pause();
      } catch {
        // ignore
      }
    }

    deps.syncPlaySurfaceMode();

    // Always scroll to top of content on navigation.
    document.documentElement.scrollTop = 0;
    deps.consoleBridge.log("gamestate", `route -> ${route}`);
  }

  function openPlaySongFlow(): void {
    deps.consoleBridge.log("gamestate", "open play flow");
    setRoute("play");
    showSongLibraryStep();
  }

  async function exitApplication(): Promise<void> {
    if (!deps.haveTauri()) {
      window.close();
      return;
    }
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
  }

  function setPlayFocusMode(enabled: boolean): void {
    deps.playLayoutEl.classList.toggle("isFocus", enabled);
    // Canvas size may change; ensure we resize so the visualizer fills the space.
    deps.resizeVizCanvas();
    deps.consoleBridge.log("gamestate", `play focus mode -> ${enabled ? "focus" : "normal"}`);
  }

  function showSongLibraryStep(): void {
    deps.pauseMenu.close({ restoreFocus: false });
    deps.consoleBridge.log("gamestate", "show song library step");
    deps.playLayoutEl.classList.add("isLibraryOnly");
    setPlayFocusMode(false);
    deps.syncPlaySurfaceMode();
    try {
      deps.stopVisualizer();
      deps.transportController.pause();
    } catch {
      // ignore
    }
    // Always rescan when the library panel is shown. Covers app-start, returning
    // from focus mode / pause menu, and navigating in from Home or the nav bar.
    // Previously the initial DOM left statusEl reading "(not loaded)" until the
    // user clicked Refresh or navigated away and back; new SongPacks dropped
    // into the songs folder by aural_ingest while the app was open also stayed
    // invisible until that manual refresh.
    void deps.songLibraryPanel.refresh();
  }

  function showBandSetupStep(): void {
    deps.pauseMenu.close({ restoreFocus: false });
    deps.consoleBridge.log("gamestate", "show band setup step");
    deps.playLayoutEl.classList.remove("isLibraryOnly");
    setPlayFocusMode(true);
    deps.syncPlaySurfaceMode();
  }

  function canOpenLoadedSongBackOutPrompt(): boolean {
    return currentRoute === "play" && deps.isLoadedSongSelected();
  }

  // Nav bar + home buttons.
  document.getElementById("navHome")?.addEventListener("click", () => setRoute("home"));
  document.getElementById("navPlay")?.addEventListener("click", () => openPlaySongFlow());
  document.getElementById("navConfig")?.addEventListener("click", () => setRoute("config"));
  document.getElementById("homePlay")?.addEventListener("click", () => openPlaySongFlow());
  document.getElementById("homeConfig")?.addEventListener("click", () => setRoute("config"));
  document.getElementById("homeExit")?.addEventListener("click", () => {
    void exitApplication().catch((e) => {
      deps.consoleBridge.error("debugging", "failed to exit app", e);
    });
  });

  // In-play toggle: back out from focus mode to the library step.
  toggleFocusBtn.addEventListener("click", () => {
    showSongLibraryStep();
  });

  return {
    getCurrentRoute: () => currentRoute,
    setRoute,
    openPlaySongFlow,
    exitApplication,
    showSongLibraryStep,
    showBandSetupStep,
    setPlayFocusMode,
    canOpenLoadedSongBackOutPrompt,
  };
}
