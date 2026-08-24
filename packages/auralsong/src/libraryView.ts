/**
 * Library view model — sorting, grouping, and filtering for a song library.
 *
 * Pure and dependency-free (no `node:fs`, no DOM) so the two places that
 * present a library can share one ordering contract: the Node-side index
 * (`indexSongLibrary`) and the game's Play Songs panel, which gets its rows
 * from the Rust `scan_auralsongs` command. Each caller maps its own row
 * shape onto `LibraryItem`; everything below works on that shape alone.
 *
 * The defaults reproduce the flat, title-sorted list the picker has always
 * shown, so a small library looks exactly as it did before.
 */

export type LibrarySortKey = "title" | "composer" | "duration" | "added";

/** Group label for items whose pack records no composer/artist. */
export const UNKNOWN_COMPOSER_LABEL = "Unknown composer";

export interface LibraryItem {
  /** Stable identity — the container path. */
  path: string;
  /** Display title. Callers substitute their own placeholder when missing. */
  title: string;
  /** Composer/artist. Empty when the pack records none. */
  composer: string;
  /** Track length in seconds, or null when unknown. */
  durationSec: number | null;
  /** Epoch ms the pack landed in the library, or null when unknown. */
  addedAtMs: number | null;
  /** False for packs whose manifest failed to parse. */
  ok: boolean;
  /** Sorts ahead of everything else (used for the built-in demo song). */
  pinned?: boolean;
}

export interface LibraryGroup {
  /** Composer name, `UNKNOWN_COMPOSER_LABEL`, or "" for the ungrouped list. */
  label: string;
  items: LibraryItem[];
}

export interface LibraryViewOptions {
  /** Sort dimension. Default "title". */
  sort?: LibrarySortKey;
  /** Reverse the sort. Default false. */
  descending?: boolean;
  /** Split into per-composer groups. Default false (one flat list). */
  groupByComposer?: boolean;
  /** Incremental text filter over title + composer. Default "" (no filter). */
  query?: string;
}

export interface LibraryView {
  groups: LibraryGroup[];
  /** Items before filtering. */
  total: number;
  /** Items after filtering. */
  matched: number;
  /** Unparseable packs among the matched items. */
  broken: number;
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base" });
}

/** True when this item records nothing for the sort dimension. */
function isMissing(item: LibraryItem, key: LibrarySortKey): boolean {
  switch (key) {
    case "composer":
      return item.composer.trim() === "";
    case "duration":
      return item.durationSec === null;
    case "added":
      return item.addedAtMs === null;
    default:
      return false;
  }
}

function compareOn(a: LibraryItem, b: LibraryItem, key: LibrarySortKey): number {
  switch (key) {
    case "composer":
      return compareText(a.composer.trim(), b.composer.trim());
    case "duration":
      return (a.durationSec ?? 0) - (b.durationSec ?? 0);
    case "added":
      return (a.addedAtMs ?? 0) - (b.addedAtMs ?? 0);
    default:
      return compareText(a.title, b.title);
  }
}

/**
 * Sort a copy of `items`.
 *
 * Order: pinned items first, then items that record the sort dimension
 * (missing values always sink to the bottom, in BOTH directions — "unknown"
 * is not "the earliest"), then title and path as a deterministic tiebreak so
 * the result never depends on scan order.
 */
export function sortLibraryItems(
  items: readonly LibraryItem[],
  key: LibrarySortKey = "title",
  descending = false
): LibraryItem[] {
  return [...items].sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;

    const aMissing = isMissing(a, key);
    const bMissing = isMissing(b, key);
    if (aMissing !== bMissing) return aMissing ? 1 : -1;

    if (!aMissing) {
      const cmp = compareOn(a, b, key);
      if (cmp !== 0) return descending ? -cmp : cmp;
    }

    return compareText(a.title, b.title) || a.path.localeCompare(b.path);
  });
}

/**
 * Keep the items whose title + composer contain every whitespace-separated
 * token of `query`, case-insensitively. An empty query keeps everything.
 */
export function filterLibraryItems(items: readonly LibraryItem[], query: string): LibraryItem[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [...items];

  return items.filter((item) => {
    const haystack = `${item.title} ${item.composer}`.toLowerCase();
    return tokens.every((t) => haystack.includes(t));
  });
}

/**
 * Bucket items by composer, preserving the incoming item order inside each
 * group. Groups come out alphabetically with the no-composer bucket last.
 * Composer matching is case-insensitive; the first spelling seen wins.
 */
export function groupLibraryItemsByComposer(items: readonly LibraryItem[]): LibraryGroup[] {
  const groups = new Map<string, LibraryGroup>();

  for (const item of items) {
    const composer = item.composer.trim();
    const key = composer.toLowerCase();
    const existing = groups.get(key);
    if (existing) {
      existing.items.push(item);
      continue;
    }
    groups.set(key, { label: composer || UNKNOWN_COMPOSER_LABEL, items: [item] });
  }

  // Keyed on "" rather than the label so a pack literally titled "Unknown
  // composer" can't jump the queue.
  return [...groups.entries()]
    .sort(([ak, a], [bk, b]) => {
      if ((ak === "") !== (bk === "")) return ak === "" ? 1 : -1;
      return compareText(a.label, b.label);
    })
    .map(([, group]) => group);
}

/** Filter, then sort, then (optionally) group — the whole picker order. */
export function buildLibraryView(
  items: readonly LibraryItem[],
  opts: LibraryViewOptions = {}
): LibraryView {
  const matched = sortLibraryItems(
    filterLibraryItems(items, opts.query ?? ""),
    opts.sort ?? "title",
    opts.descending ?? false
  );

  const groups = opts.groupByComposer
    ? groupLibraryItemsByComposer(matched)
    : matched.length > 0
      ? [{ label: "", items: matched }]
      : [];

  return {
    groups,
    total: items.length,
    matched: matched.length,
    broken: matched.filter((i) => !i.ok).length,
  };
}
