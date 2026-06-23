// @vitest-environment jsdom
/**
 * Execution tests for the song-library panel. Mocks the Tauri core invoke +
 * event listen modules and drives refresh() (populate / sort / select / empty
 * / error), the songs-folder override controls, the watcher auto-refresh
 * wiring, and the handle accessors (setDetailsHTML / setSelectedSongCard /
 * disableFolderControls).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const invoke = vi.fn();
const listen = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: (...a: unknown[]) => listen(...a) }));

import { initSongLibraryPanel } from "../src/songLibraryPanel";
import type { AuralSongScanEntry } from "../src/songLibraryPanel";

function stageDom(): void {
  document.body.innerHTML =
    '<pre id="status"></pre>' +
    '<div id="list"></div>' +
    '<div id="details"></div>' +
    '<input id="songsFolder" />' +
    '<button id="setOverride"></button>' +
    '<button id="clearOverride"></button>' +
    '<button id="refresh"></button>';
}

function makeDeps(overrides: Partial<Parameters<typeof initSongLibraryPanel>[0]> = {}) {
  return {
    selectedAuralSongPath: vi.fn(() => null),
    onSongSelected: vi.fn(async () => {}),
    haveTauri: vi.fn(() => false),
    escapeHtml: (s: string) => s,
    ...overrides,
  } as Parameters<typeof initSongLibraryPanel>[0];
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};
const status = () => document.getElementById("status")!.textContent;
const list = () => document.getElementById("list") as HTMLDivElement;

const entries: AuralSongScanEntry[] = [
  {
    container_path: "/songs/zeta.auralsong",
    kind: "dir",
    ok: true,
    manifest: { song_id: "zeta", title: "Zeta", artist: "Z" } as any,
  },
  {
    container_path: "/songs/demo.auralsong",
    kind: "dir",
    ok: true,
    manifest: { song_id: "demo_sine_440hz", title: "Demo" } as any,
  },
  {
    container_path: "/songs/broken.auralsong",
    kind: "dir",
    ok: false,
    error: "manifest parse error",
  },
];

describe("songLibraryPanel", () => {
  beforeEach(() => {
    stageDom();
    invoke.mockReset();
    listen.mockReset();
    listen.mockResolvedValue(() => {});
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initSongLibraryPanel(makeDeps())).toThrow(/required DOM/);
  });

  it("refresh populates the list, sorting the demo song first", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "get_songs_folder") return "/songs";
      if (cmd === "scan_auralsongs") return entries;
      return undefined;
    });
    const handle = initSongLibraryPanel(makeDeps());
    await handle.refresh();
    await flush();

    expect((document.getElementById("songsFolder") as HTMLInputElement).value).toBe("/songs");
    expect(status()).toContain("tracks: 3");
    const btns = Array.from(list().querySelectorAll("button.songSelectBtn"));
    expect(btns.length).toBe(3);
    // Demo sorts first.
    expect(btns[0].getAttribute("data-path")).toBe("/songs/demo.auralsong");
    // Invalid entry's button is disabled + shows its error.
    const broken = btns.find((b) => b.getAttribute("data-path") === "/songs/broken.auralsong")!;
    expect((broken as HTMLButtonElement).disabled).toBe(true);
    expect(list().innerHTML).toContain("manifest parse error");
  });

  it("clicking a valid song row fires onSongSelected with its path", async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === "get_songs_folder" ? "/songs" : entries,
    );
    const onSongSelected = vi.fn(async () => {});
    const handle = initSongLibraryPanel(makeDeps({ onSongSelected }));
    await handle.refresh();
    const demo = list().querySelector(
      'button.songSelectBtn[data-path="/songs/demo.auralsong"]',
    ) as HTMLButtonElement;
    demo.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flush();
    expect(onSongSelected).toHaveBeenCalledWith("/songs/demo.auralsong");
  });

  it("marks the currently-selected row from selectedAuralSongPath", async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === "get_songs_folder" ? "/songs" : entries,
    );
    const handle = initSongLibraryPanel(
      makeDeps({ selectedAuralSongPath: vi.fn(() => "/songs/zeta.auralsong") }),
    );
    await handle.refresh();
    const zeta = list().querySelector(
      'button.songSelectBtn[data-path="/songs/zeta.auralsong"]',
    ) as HTMLButtonElement;
    expect(zeta.classList.contains("isSelected")).toBe(true);
    expect(zeta.getAttribute("aria-pressed")).toBe("true");
  });

  it("refresh shows the browser-only fallback on invoke error", async () => {
    invoke.mockRejectedValue(new Error("no tauri here"));
    const handle = initSongLibraryPanel(makeDeps());
    await handle.refresh();
    expect(status()).toContain("no tauri here");
    expect(list().innerHTML).toContain("tauri dev");
  });

  it("the Refresh button triggers a rescan", async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === "get_songs_folder" ? "/songs" : [],
    );
    initSongLibraryPanel(makeDeps());
    document.getElementById("refresh")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(invoke).toHaveBeenCalledWith("scan_auralsongs");
  });

  it("wires the watcher + backstop when Tauri is present and re-refreshes on event", async () => {
    let watcherCb: (() => void) | undefined;
    listen.mockImplementation(async (_name: string, cb: () => void) => {
      watcherCb = cb;
      return () => {};
    });
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "get_songs_folder") return "/songs";
      if (cmd === "scan_auralsongs") return [];
      return undefined;
    });
    initSongLibraryPanel(makeDeps({ haveTauri: vi.fn(() => true) }));
    await flush();
    expect(listen).toHaveBeenCalledWith("songs_folder_changed", expect.any(Function));
    expect(invoke).toHaveBeenCalledWith("start_songs_folder_watch");
    invoke.mockClear();
    watcherCb?.();
    await flush();
    expect(invoke).toHaveBeenCalledWith("scan_auralsongs");
  });

  it("warns (best-effort) when the watcher backstop invoke rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "start_songs_folder_watch") throw new Error("watch-fail");
      if (cmd === "get_songs_folder") return "/songs";
      return [];
    });
    initSongLibraryPanel(makeDeps({ haveTauri: vi.fn(() => true) }));
    await flush();
    expect(warn).toHaveBeenCalledWith("start_songs_folder_watch failed", expect.any(Error));
    warn.mockRestore();
  });

  it("set-override button invokes the override command with the trimmed value", async () => {
    invoke.mockResolvedValue(undefined);
    initSongLibraryPanel(makeDeps());
    (document.getElementById("songsFolder") as HTMLInputElement).value = "  /new/folder  ";
    document.getElementById("setOverride")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(invoke).toHaveBeenCalledWith("set_songs_folder_override", { songsFolder: "/new/folder" });
  });

  it("set-override is a no-op for an empty value", async () => {
    initSongLibraryPanel(makeDeps());
    (document.getElementById("songsFolder") as HTMLInputElement).value = "   ";
    document.getElementById("setOverride")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(invoke).not.toHaveBeenCalledWith("set_songs_folder_override", expect.anything());
  });

  it("clear-override button invokes the clear command", async () => {
    invoke.mockResolvedValue(undefined);
    initSongLibraryPanel(makeDeps());
    document.getElementById("clearOverride")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(invoke).toHaveBeenCalledWith("clear_songs_folder_override");
  });

  it("disableFolderControls disables both override buttons", () => {
    const handle = initSongLibraryPanel(makeDeps());
    handle.disableFolderControls();
    expect((document.getElementById("setOverride") as HTMLButtonElement).disabled).toBe(true);
    expect((document.getElementById("clearOverride") as HTMLButtonElement).disabled).toBe(true);
  });

  it("setDetailsHTML replaces the details pane", () => {
    const handle = initSongLibraryPanel(makeDeps());
    handle.setDetailsHTML("<p>hi</p>");
    expect(document.getElementById("details")!.innerHTML).toBe("<p>hi</p>");
  });

  it("setSelectedSongCard toggles isSelected + aria-pressed across rows", async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === "get_songs_folder" ? "/songs" : entries,
    );
    const handle = initSongLibraryPanel(makeDeps());
    await handle.refresh();
    handle.setSelectedSongCard("/songs/zeta.auralsong");
    const zeta = list().querySelector(
      'button[data-path="/songs/zeta.auralsong"]',
    ) as HTMLButtonElement;
    const demo = list().querySelector(
      'button[data-path="/songs/demo.auralsong"]',
    ) as HTMLButtonElement;
    expect(zeta.classList.contains("isSelected")).toBe(true);
    expect(zeta.getAttribute("aria-pressed")).toBe("true");
    expect(demo.classList.contains("isSelected")).toBe(false);
    // null clears all.
    handle.setSelectedSongCard(null);
    expect(zeta.classList.contains("isSelected")).toBe(false);
  });
});
