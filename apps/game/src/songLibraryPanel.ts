/**
 * Song library panel — the Play Songs left column.
 *
 * Owns:
 *   - The status line, auralsong list, and details panel DOM
 *   - The library toolbar (search / sort / group-by-composer) it injects
 *     above the list, plus the render that honours it
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
 *
 * Ordering, grouping, and filtering live in `@auralprimer/auralsong/
 * libraryView` so this panel and the Node-side library index agree on what
 * "sorted by composer" means. The defaults reproduce the flat, demo-first,
 * title-sorted list the picker has always shown, so a handful of songs looks
 * exactly as it did before the toolbar existed.
 */

import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import {
  buildLibraryView,
  type LibraryGroup,
  type LibraryItem,
  type LibrarySortKey,
  type LibraryView,
} from "@auralprimer/auralsong/libraryView";

import type { ManifestSummary } from "./manifestTypes";

export type AuralSongScanEntry = {
  container_path: string;
  kind: string;
  ok: boolean;
  manifest?: ManifestSummary;
  error?: string;
  /**
   * Epoch ms the pack landed in the songs folder. Optional: the Rust
   * `scan_auralsongs` command does not report it yet, so the "Recently
   * added" sort stays out of the toolbar until some row carries one.
   */
  added_at_ms?: number | null;
};

type SortOption = {
  key: LibrarySortKey;
  descending: boolean;
  label: string;
};

const SORT_OPTIONS: SortOption[] = [
  { key: "title", descending: false, label: "Title" },
  { key: "composer", descending: false, label: "Composer" },
  { key: "duration", descending: false, label: "Duration" },
  { key: "added", descending: true, label: "Recently added" },
];

function isDemoAuralSong(e: AuralSongScanEntry): boolean {
  // Deterministic id for our built-in first-run song.
  return (e.manifest?.song_id ?? "") === "demo_sine_440hz";
}

function toLibraryItem(e: AuralSongScanEntry): LibraryItem {
  const duration = e.manifest?.duration_sec;
  return {
    path: e.container_path,
    title: e.manifest?.title ?? "(missing title)",
    composer: e.manifest?.artist ?? "",
    durationSec: typeof duration === "number" ? duration : null,
    addedAtMs: typeof e.added_at_ms === "number" ? e.added_at_ms : null,
    ok: e.ok,
    // The built-in demo song stays at the top of whatever the user picked.
    pinned: isDemoAuralSong(e),
  };
}

function formatDuration(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec) || sec < 0) return "";
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
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

  // The toolbar is built here rather than in the app shell so the panel owns
  // every control it reads, and any host that stages #list gets it for free.
  const toolbar = document.createElement("div");
  toolbar.id = "songLibraryToolbar";
  toolbar.className = "songLibraryToolbar";
  toolbar.innerHTML = `
    <input id="songLibrarySearch" class="songLibrarySearch" type="search" placeholder="Search title or composer" aria-label="Search the song library" />
    <label class="meta songLibraryControl">Sort <select id="songLibrarySort" aria-label="Sort songs by"></select></label>
    <label class="meta songLibraryControl"><input id="songLibraryGroup" type="checkbox" /> Group by composer</label>
  `;
  listEl.before(toolbar);

  const searchEl = toolbar.querySelector("#songLibrarySearch") as HTMLInputElement;
  const sortEl = toolbar.querySelector("#songLibrarySort") as HTMLSelectElement;
  const groupEl = toolbar.querySelector("#songLibraryGroup") as HTMLInputElement;

  // Last scan, kept so a search keystroke or a sort change rerenders without
  // another round-trip to Rust.
  let items: LibraryItem[] = [];
  const entryByPath = new Map<string, AuralSongScanEntry>();
  let songsFolder = "";

  // View state.
  let sortKey: LibrarySortKey = "title";
  let sortDescending = false;
  let groupByComposer = false;
  let query = "";
  const collapsedGroups = new Set<string>();

  /** Offer "Recently added" only while the scan actually dates the packs. */
  function syncSortOptions(): void {
    const options = SORT_OPTIONS.filter(
      (o) => o.key !== "added" || items.some((i) => i.addedAtMs !== null)
    );
    if (!options.some((o) => o.key === sortKey)) {
      sortKey = "title";
      sortDescending = false;
    }
    sortEl.innerHTML = options
      .map((o) => `<option value="${o.key}">${deps.escapeHtml(o.label)}</option>`)
      .join("");
    sortEl.value = sortKey;
  }

  function renderStatus(view: LibraryView): void {
    const lines = [`songsFolder: ${songsFolder}`, `tracks: ${view.total}`];
    if (view.matched !== view.total) lines.push(`showing: ${view.matched}`);
    if (view.broken > 0) lines.push(`needs attention: ${view.broken}`);
    statusEl!.textContent = lines.join("\n");
  }

  function renderRow(item: LibraryItem, selected: string | null): string {
    const entry = entryByPath.get(item.path);
    const ok = item.ok ? "OK" : "INVALID";
    const err = entry?.error ? `<pre class="error">${deps.escapeHtml(entry.error)}</pre>` : "";
    const disabled = item.ok ? "" : "disabled";
    const isSelected = selected === item.path ? " isSelected" : "";
    const pressed = isSelected ? "true" : "false";
    const cta = item.ok ? "Choose" : "Invalid";
    const meta = [ok, entry?.kind ?? "", formatDuration(item.durationSec), item.path]
      .filter(Boolean)
      .join(" · ");
    return `
      <li class="${item.ok ? "" : "isBroken"}">
        <button class="songSelectBtn${isSelected}" data-path="${deps.escapeHtml(item.path)}" aria-pressed="${pressed}" ${disabled}>
          <span class="songSelectCopy">
            <span class="songSelectTitleRow">
              <strong class="songSelectTitle">${deps.escapeHtml(item.title)}</strong>
              ${item.composer ? `<span class="songSelectArtist">${deps.escapeHtml(item.composer)}</span>` : ""}
            </span>
            <span class="meta songSelectMeta">${deps.escapeHtml(meta)}</span>
          </span>
          <span class="songSelectCta" aria-hidden="true">${deps.escapeHtml(cta)}</span>
        </button>
        ${err}
      </li>
    `;
  }

  function renderGroup(group: LibraryGroup, selected: string | null): string {
    const rows = `
      <ul class="songLibraryList">
        ${group.items.map((i) => renderRow(i, selected)).join("\n")}
      </ul>
    `;
    // The ungrouped view is the historical flat list — no wrapper at all.
    if (group.label === "") return rows;

    const broken = group.items.filter((i) => !i.ok).length;
    const counts = broken > 0 ? `${group.items.length} · ${broken} invalid` : `${group.items.length}`;
    const open = collapsedGroups.has(group.label) ? "" : " open";
    return `
      <details class="songLibraryGroup" data-group="${deps.escapeHtml(group.label)}"${open}>
        <summary class="songLibraryGroupHead">
          <span class="songLibraryGroupName">${deps.escapeHtml(group.label)}</span>
          <span class="meta">${deps.escapeHtml(counts)}</span>
        </summary>
        ${rows}
      </details>
    `;
  }

  function renderList(): void {
    const view = buildLibraryView(items, {
      sort: sortKey,
      descending: sortDescending,
      groupByComposer,
      query,
    });
    const selected = deps.selectedAuralSongPath();

    if (view.matched === 0) {
      listEl!.innerHTML =
        view.total === 0
          ? `<ul class="songLibraryList"></ul>`
          : `<p class="meta songLibraryEmpty">No songs match that search.</p>`;
    } else {
      listEl!.innerHTML = view.groups.map((g) => renderGroup(g, selected)).join("\n");
    }

    renderStatus(view);

    for (const btn of Array.from(listEl!.querySelectorAll("button.songSelectBtn"))) {
      btn.addEventListener("click", async (ev) => {
        const el = ev.currentTarget as HTMLButtonElement;
        const containerPath = el.getAttribute("data-path");
        if (!containerPath) return;
        await deps.onSongSelected(containerPath);
      });
    }

    // Remember which composers the user folded away so the next keystroke
    // doesn't spring them all open again.
    for (const group of Array.from(listEl!.querySelectorAll<HTMLDetailsElement>("details.songLibraryGroup"))) {
      group.addEventListener("toggle", () => {
        const label = group.getAttribute("data-group") ?? "";
        if (group.open) collapsedGroups.delete(label);
        else collapsedGroups.add(label);
      });
    }
  }

  async function refresh(): Promise<void> {
    statusEl!.textContent = "Loading...";
    listEl!.innerHTML = "";
    detailsEl!.innerHTML = "";

    try {
      songsFolder = await invoke<string>("get_songs_folder");
      const entries = await invoke<AuralSongScanEntry[]>("scan_auralsongs");

      songsFolderInput!.value = songsFolder;

      entryByPath.clear();
      for (const e of entries) entryByPath.set(e.container_path, e);
      items = entries.map(toLibraryItem);

      syncSortOptions();
      renderList();
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

  // Toolbar: filtering and ordering are pure view state, so they rerender
  // from the last scan instead of invoking Rust again.
  searchEl.addEventListener("input", () => {
    query = searchEl.value;
    renderList();
  });

  sortEl.addEventListener("change", () => {
    const option = SORT_OPTIONS.find((o) => o.key === sortEl.value);
    if (!option) return;
    sortKey = option.key;
    sortDescending = option.descending;
    renderList();
  });

  groupEl.addEventListener("change", () => {
    groupByComposer = groupEl.checked;
    renderList();
  });

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
