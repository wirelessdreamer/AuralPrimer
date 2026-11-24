import {
  buildLibraryView,
  filterLibraryItems,
  groupLibraryItemsByComposer,
  groupLibraryItemsByGenre,
  sortLibraryItems,
  UNKNOWN_COMPOSER_LABEL,
  UNKNOWN_GENRE_LABEL,
  type LibraryItem
} from "../src/libraryView";

function item(partial: Partial<LibraryItem> & { title: string }): LibraryItem {
  return {
    path: `/songs/${partial.title}.feedpak`,
    composer: "",
    genre: "",
    durationSec: null,
    addedAtMs: null,
    ok: true,
    ...partial
  };
}

const paths = (items: readonly LibraryItem[]): string[] => items.map((i) => i.title);

describe("sortLibraryItems", () => {
  it("sorts by title case-insensitively and defaults to ascending", () => {
    const items = [item({ title: "goldberg" }), item({ title: "Aria" }), item({ title: "Bourree" })];

    expect(paths(sortLibraryItems(items))).toEqual(["Aria", "Bourree", "goldberg"]);
    expect(paths(sortLibraryItems(items, "title", true))).toEqual(["goldberg", "Bourree", "Aria"]);
  });

  it("keeps pinned items first in every ordering", () => {
    const items = [
      item({ title: "Aria" }),
      item({ title: "Zeta", pinned: true }),
      item({ title: "Bourree" })
    ];

    expect(paths(sortLibraryItems(items, "title"))[0]).toBe("Zeta");
    expect(paths(sortLibraryItems(items, "title", true))[0]).toBe("Zeta");
  });

  it("sorts by composer and sinks the composer-less items in both directions", () => {
    const items = [
      item({ title: "Nocturne", composer: "Chopin" }),
      item({ title: "Untitled" }),
      item({ title: "Invention", composer: "Bach" })
    ];

    expect(paths(sortLibraryItems(items, "composer"))).toEqual([
      "Invention",
      "Nocturne",
      "Untitled"
    ]);
    expect(paths(sortLibraryItems(items, "composer", true))).toEqual([
      "Nocturne",
      "Invention",
      "Untitled"
    ]);
  });

  it("sorts by genre and sinks the genre-less items in both directions", () => {
    const items = [
      item({ title: "Nocturne", genre: "Romantic" }),
      item({ title: "Untitled" }),
      item({ title: "Invention", genre: "Baroque" })
    ];

    expect(paths(sortLibraryItems(items, "genre"))).toEqual([
      "Invention",
      "Nocturne",
      "Untitled"
    ]);
    // "Unknown" is not "the earliest" -- it sinks whichever way you sort.
    expect(paths(sortLibraryItems(items, "genre", true))).toEqual([
      "Nocturne",
      "Invention",
      "Untitled"
    ]);
  });

  it("sorts by duration with unknown durations last", () => {
    const items = [
      item({ title: "Long", durationSec: 600 }),
      item({ title: "Unknown" }),
      item({ title: "Short", durationSec: 42 })
    ];

    expect(paths(sortLibraryItems(items, "duration"))).toEqual(["Short", "Long", "Unknown"]);
    expect(paths(sortLibraryItems(items, "duration", true))).toEqual(["Long", "Short", "Unknown"]);
  });

  it("sorts by date added, newest first when descending, undated last", () => {
    const items = [
      item({ title: "Older", addedAtMs: 1_000 }),
      item({ title: "Undated" }),
      item({ title: "Newer", addedAtMs: 9_000 })
    ];

    expect(paths(sortLibraryItems(items, "added", true))).toEqual(["Newer", "Older", "Undated"]);
    expect(paths(sortLibraryItems(items, "added"))).toEqual(["Older", "Newer", "Undated"]);
  });

  it("breaks ties on title then path so the order never depends on scan order", () => {
    const a = item({ title: "Prelude", composer: "Bach", path: "/songs/b.feedpak" });
    const b = item({ title: "Prelude", composer: "Bach", path: "/songs/a.feedpak" });
    const c = item({ title: "Fugue", composer: "Bach" });

    expect(sortLibraryItems([a, b, c], "composer").map((i) => i.path)).toEqual([
      c.path,
      "/songs/a.feedpak",
      "/songs/b.feedpak"
    ]);
  });

  it("does not mutate the input array", () => {
    const items = [item({ title: "Zeta" }), item({ title: "Aria" })];

    sortLibraryItems(items);

    expect(paths(items)).toEqual(["Zeta", "Aria"]);
  });
});

describe("filterLibraryItems", () => {
  const items = [
    item({ title: "Prelude in C", composer: "J.S. Bach" }),
    item({ title: "Nocturne Op. 9", composer: "Chopin" }),
    item({ title: "Fur Elise", composer: "Beethoven" })
  ];

  it("returns everything for an empty query", () => {
    expect(filterLibraryItems(items, "   ")).toHaveLength(3);
  });

  it("matches title and composer case-insensitively", () => {
    expect(paths(filterLibraryItems(items, "bach"))).toEqual(["Prelude in C"]);
    expect(paths(filterLibraryItems(items, "NOCTURNE"))).toEqual(["Nocturne Op. 9"]);
  });

  it("requires every token to match, across both fields", () => {
    expect(paths(filterLibraryItems(items, "bach prelude"))).toEqual(["Prelude in C"]);
    expect(filterLibraryItems(items, "bach nocturne")).toEqual([]);
  });
});

describe("groupLibraryItemsByComposer", () => {
  it("buckets alphabetically with the composer-less bucket last", () => {
    const groups = groupLibraryItemsByComposer([
      item({ title: "Nocturne", composer: "Chopin" }),
      item({ title: "Mystery" }),
      item({ title: "Invention", composer: "Bach" }),
      item({ title: "Fugue", composer: "Bach" })
    ]);

    expect(groups.map((g) => g.label)).toEqual(["Bach", "Chopin", UNKNOWN_COMPOSER_LABEL]);
    expect(paths(groups[0].items)).toEqual(["Invention", "Fugue"]);
    expect(paths(groups[2].items)).toEqual(["Mystery"]);
  });

  it("matches composers case-insensitively and keeps the first spelling", () => {
    const groups = groupLibraryItemsByComposer([
      item({ title: "Invention", composer: "Bach" }),
      item({ title: "Fugue", composer: "  bach " })
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Bach");
    expect(groups[0].items).toHaveLength(2);
  });

  it("does not let a song titled like the unknown bucket jump the queue", () => {
    const groups = groupLibraryItemsByComposer([
      item({ title: "Odd", composer: UNKNOWN_COMPOSER_LABEL }),
      item({ title: "Real", composer: "Zelenka" }),
      item({ title: "Nameless" })
    ]);

    // The literal "Unknown composer" composer sorts alphabetically; only the
    // genuinely composer-less bucket is forced last.
    expect(groups.map((g) => g.label)).toEqual([
      UNKNOWN_COMPOSER_LABEL,
      "Zelenka",
      UNKNOWN_COMPOSER_LABEL
    ]);
    expect(paths(groups[0].items)).toEqual(["Odd"]);
    expect(paths(groups[2].items)).toEqual(["Nameless"]);
  });
});

describe("groupLibraryItemsByGenre", () => {
  it("buckets alphabetically with the genre-less bucket last", () => {
    const groups = groupLibraryItemsByGenre([
      item({ title: "Nocturne", genre: "Romantic" }),
      item({ title: "Mystery" }),
      item({ title: "Invention", genre: "Baroque" }),
      item({ title: "Fugue", genre: "Baroque" })
    ]);

    expect(groups.map((g) => g.label)).toEqual(["Baroque", "Romantic", UNKNOWN_GENRE_LABEL]);
    expect(paths(groups[0].items)).toEqual(["Invention", "Fugue"]);
    expect(paths(groups[2].items)).toEqual(["Mystery"]);
  });

  it("matches genres case-insensitively and keeps the first spelling", () => {
    // Genre is free text typed at import, so "Classical" and "classical"
    // are one genre with two spellings, not two genres.
    const groups = groupLibraryItemsByGenre([
      item({ title: "Invention", genre: "Classical" }),
      item({ title: "Fugue", genre: "  classical " })
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Classical");
    expect(groups[0].items).toHaveLength(2);
  });

  it("does not let a song tagged like the unknown bucket jump the queue", () => {
    const groups = groupLibraryItemsByGenre([
      item({ title: "Odd", genre: UNKNOWN_GENRE_LABEL }),
      item({ title: "Real", genre: "Worship" }),
      item({ title: "Nameless" })
    ]);

    expect(groups.map((g) => g.label)).toEqual([
      UNKNOWN_GENRE_LABEL,
      "Worship",
      UNKNOWN_GENRE_LABEL
    ]);
    expect(paths(groups[0].items)).toEqual(["Odd"]);
    expect(paths(groups[2].items)).toEqual(["Nameless"]);
  });
});

describe("buildLibraryView", () => {
  const items = [
    item({ title: "Nocturne", composer: "Chopin", genre: "Romantic", durationSec: 300 }),
    item({ title: "Invention", composer: "Bach", genre: "Baroque", durationSec: 90 }),
    item({ title: "Broken", ok: false })
  ];

  it("defaults to one flat, title-sorted group — the historical list", () => {
    const view = buildLibraryView(items);

    expect(view.groups).toHaveLength(1);
    expect(view.groups[0].label).toBe("");
    expect(paths(view.groups[0].items)).toEqual(["Broken", "Invention", "Nocturne"]);
    expect(view).toMatchObject({ total: 3, matched: 3, broken: 1 });
  });

  it("groups by composer when asked, honouring the sort inside each group", () => {
    const view = buildLibraryView(items, { sort: "duration", descending: true, groupBy: "composer" });

    expect(view.groups.map((g) => g.label)).toEqual(["Bach", "Chopin", UNKNOWN_COMPOSER_LABEL]);
    expect(paths(view.groups[2].items)).toEqual(["Broken"]);
  });

  it("groups by genre when asked", () => {
    const view = buildLibraryView(items, { groupBy: "genre" });

    expect(view.groups.map((g) => g.label)).toEqual(["Baroque", "Romantic", UNKNOWN_GENRE_LABEL]);
    expect(paths(view.groups[2].items)).toEqual(["Broken"]);
  });

  it("groups by nothing when asked for none — the historical flat list", () => {
    const view = buildLibraryView(items, { groupBy: "none" });

    expect(view.groups).toHaveLength(1);
    expect(view.groups[0].label).toBe("");
  });

  it("reports counts for the filtered subset", () => {
    const view = buildLibraryView(items, { query: "bach" });

    expect(view).toMatchObject({ total: 3, matched: 1, broken: 0 });
    expect(paths(view.groups[0].items)).toEqual(["Invention"]);
  });

  it("combines exact artist and genre filters with text search", () => {
    const rows = [
      item({ title: "Spring", composer: "Vivaldi", genre: "Classical" }),
      item({ title: "Winter", composer: " VIVALDI ", genre: "classical" }),
      item({ title: "Spring Waltz", composer: "Chopin", genre: "Classical" }),
      item({ title: "Winter Remix", composer: "Vivaldi", genre: "Electronic" }),
      item({ title: "Demo", pinned: true }),
    ];
    const view = buildLibraryView(rows, { artist: " vivaldi ", genre: " CLASSICAL ", query: "winter" });
    expect(paths(view.groups[0].items)).toEqual(["Winter"]);
    expect(view).toMatchObject({ total: 5, matched: 1, broken: 0 });
    expect(buildLibraryView(rows, { artist: "Vivaldi", genre: "Electronic", query: "spring" }).matched).toBe(0);
  });

  it("distinguishes All from unknown artists and untagged genres", () => {
    const rows = [item({ title: "Unknown" }), item({ title: "Named", composer: "Bach", genre: "Classical" })];
    expect(buildLibraryView(rows, { artist: null, genre: null }).matched).toBe(2);
    expect(paths(buildLibraryView(rows, { artist: "", genre: "" }).groups[0].items)).toEqual(["Unknown"]);
    expect(buildLibraryView(rows, { artist: "Bach", genre: "" }).matched).toBe(0);
  });

  it("returns no groups when nothing matches or the library is empty", () => {
    expect(buildLibraryView(items, { query: "liszt" }).groups).toEqual([]);
    expect(buildLibraryView([]).groups).toEqual([]);
    expect(buildLibraryView([], { groupBy: "composer" })).toMatchObject({
      groups: [],
      total: 0,
      matched: 0,
      broken: 0
    });
  });
});
