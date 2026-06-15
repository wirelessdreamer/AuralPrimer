/**
 * Song library panel — the Play Songs left column.
 *
 * Owns:
 *   - The status line, auralsong list, and details panel DOM
 *   - The songs-folder override input + set/clear buttons
 *   - The "Refresh" button + the auralsong-list render
 *   - The filesystem-watcher auto-refresh wiring (listen for
 *     `songs_folder_changed` events emitted by the Rust `notify` watcher;
 *     calls refresh() on the same scan path the manual button uses)
 *   - A boot-time `start_songs_folder_watch` backstop in case the watcher's
 *     setup() race-lost to a missing folder
 *
 * Extracted from main.ts in Phase 2.C. Replaces the previously TDZ-prone
 * inline arrangement where `refresh()` and the songs_folder_changed listen()
 * sat at the bottom of main.ts and could never register if a module-init
 * error happened earlier in the file. With the wiring now encapsulated, a
 * future refactor can't accidentally pull init out from under the panel.
 */

import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

import type { ManifestSummary } from "./manifestTypes";

export type AuralSongScanEntry = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest?: ManifestSummary;
  error?: string;
};

function isDemoAuralSong(e: AuralSongScanEntry): boolean {
  // Deterministic id for our built-in first-run song.
  return (e.manifest?.song_id ?? "") === "demo_sine_440hz";
}

export type SongLibraryPanelDeps = {
  /** Getter for the currently-selected auralsong (used to mark the list row). */
  selectedAuralSongPath: () => string | null;
  /** Fires when the user clicks a list row. main.ts wires this to selectAuralSong. */
  onSongSelected: (containerPath: string) => Promise<void>;
  /** Whether the Tauri runtime is available (no scan_auralsongs invoke in plain Vite dev). */
  haveTauri: () => boolean;
  /** HTML escape helper, injected to avoid duplication. */
  escapeHtml: (s: string) => string;
};

export type SongLibraryPanelHandle = {
  /** Rescan the songs folder and rerender. */
  refresh: () => Promise<void>;
  /** Disable the songs-folder override controls (used when no Tauri). */
  disableFolderControls: () => void;
  /** Replace the details pane HTML (used by the host to render the selected auralsong). */
  setDetailsHTML: (html: string) => void;
  /** Re-apply the .isSelected class + aria-pressed to the matching list row. */
  setSelectedSongCard: (containerPath: string | null) => void;
};

export function initSongLibraryPanel(deps: SongLibraryPanelDeps): SongLibraryPanelHandle {
  const statusEl = document.getElementById("status") as HTMLPreElement | null;
  const listEl = document.getElementById("list") as HTMLDivElement | null;
  const detailsEl = document.getElementById("details") as HTMLDivElement | null;
  const songsFolderInput = document.getElementById("songsFolder") as HTMLInputElement | null;
  const setOverrideBtn = document.getElementById("setOverride") as HTMLButtonElement | null;
  const clearOverrideBtn = document.getElementById("clearOverride") as HTMLButtonElement | null;
  const refreshBtn = document.getElementById("refresh") as HTMLButtonElement | null;
  if (!statusEl || !listEl || !detailsEl || !songsFolderInput || !setOverrideBtn || !clearOverrideBtn || !refreshBtn) {
    throw new Error("initSongLibraryPanel: required DOM (#status/#list/#details/#songsFolder/#setOverride/#clearOverride/#refresh) missing");
  }

  async function refresh(): Promise<void> {
    statusEl!.textContent = "Loading...";
    listEl!.innerHTML = "";
    detailsEl!.innerHTML = "";

    try {
      const songsFolder = await invoke<string>("get_songs_folder");
      const entries = await invoke<AuralSongScanEntry[]>("scan_auralsongs");

      // Prefer the built-in demo song first, then alphabetical.
      entries.sort((a, b) => {
        const ad = isDemoAuralSong(a);
        const bd = isDemoAuralSong(b);
        if (ad !== bd) return ad ? -1 : 1;
        const at = (a.manifest?.title ?? "").toLowerCase();
        const bt = (b.manifest?.title ?? "").toLowerCase();
        return at.localeCompare(bt);
      });

      songsFolderInput!.value = songsFolder;
      statusEl!.textContent = `songsFolder: ${songsFolder}\ntracks: ${entries.length}`;

      const selected = deps.selectedAuralSongPath();
      listEl!.innerHTML = `
        <ul class="songLibraryList">
          ${entries
            .map((e) => {
              const title = e.manifest?.title ?? "(missing title)";
              const artist = e.manifest?.artist ?? "";
              const ok = e.ok ? "OK" : "INVALID";
              const err = e.error ? `<pre class="error">${deps.escapeHtml(e.error)}</pre>` : "";
              const disabled = e.ok ? "" : "disabled";
              const isSelected = selected === e.container_path ? " isSelected" : "";
              const pressed = isSelected ? "true" : "false";
              const cta = e.ok ? "Choose" : "Invalid";
              return `
                <li>
                  <button class="songSelectBtn${isSelected}" data-path="${deps.escapeHtml(e.container_path)}" aria-pressed="${pressed}" ${disabled}>
                    <span class="songSelectCopy">
                      <span class="songSelectTitleRow">
                        <strong class="songSelectTitle">${deps.escapeHtml(title)}</strong>
                        ${artist ? `<span class="songSelectArtist">${deps.escapeHtml(artist)}</span>` : ""}
                      </span>
                      <span class="meta songSelectMeta">${deps.escapeHtml(ok)} · ${deps.escapeHtml(e.kind)} · ${deps.escapeHtml(e.container_path)}</span>
                    </span>
                    <span class="songSelectCta" aria-hidden="true">${deps.escapeHtml(cta)}</span>
                  </button>
                  ${err}
                </li>
              `;
            })
            .join("\n")}
        </ul>
      `;

      for (const btn of Array.from(listEl!.querySelectorAll("button.songSelectBtn"))) {
        btn.addEventListener("click", async (ev) => {
          const el = ev.currentTarget as HTMLButtonElement;
          const containerPath = el.getAttribute("data-path");
          if (!containerPath) return;
          await deps.onSongSelected(containerPath);
        });
      }
    } catch (e) {
      statusEl!.textContent = String(e);
      listEl!.innerHTML = `
        <p>
          This view must be run via <code>tauri dev</code> (the browser-only Vite dev server cannot invoke Rust commands).
        </p>
      `;
    }
  }

  // Wire button events.
  refreshBtn.addEventListener("click", () => void refresh());

  // Auto-refresh the library panel when files/directories appear, change, or
  // are removed under the songs folder (e.g. `aural_ingest import` from a
  // separate shell drops a new .auralsong/ directory). The Rust side mounts a
  // `notify`-based watcher during setup() and emits this event after a short
  // debounce; we just re-run the same scan the manual refresh button uses.
  if (deps.haveTauri()) {
    void listen("songs_folder_changed", () => {
      void refresh();
    });
    // Idempotent backstop in case the watcher's initial mount in setup() raced
    // ahead of the songs folder being created. If a watcher is already running
    // on the current path, this returns Ok(()) without doing anything.
    void invoke("start_songs_folder_watch").catch((e) => {
      // Best-effort — the user still has the manual refresh button.
      console.warn("start_songs_folder_watch failed", e);
    });
  }

  setOverrideBtn.addEventListener("click", () => {
    const v = songsFolderInput!.value.trim();
    if (!v) return;
    void invoke("set_songs_folder_override", { songsFolder: v }).then(() => refresh());
  });

  clearOverrideBtn.addEventListener("click", () => {
    void invoke("clear_songs_folder_override").then(() => refresh());
  });

  return {
    refresh,
    disableFolderControls: () => {
      setOverrideBtn!.disabled = true;
      clearOverrideBtn!.disabled = true;
    },
    setDetailsHTML: (html) => {
      detailsEl!.innerHTML = html;
    },
    setSelectedSongCard: (containerPath) => {
      for (const btn of Array.from(listEl!.querySelectorAll<HTMLButtonElement>("button.songSelectBtn"))) {
        const isSelected = containerPath !== null && btn.getAttribute("data-path") === containerPath;
        btn.classList.toggle("isSelected", isSelected);
        btn.setAttribute("aria-pressed", isSelected ? "true" : "false");
      }
    },
  };
}
