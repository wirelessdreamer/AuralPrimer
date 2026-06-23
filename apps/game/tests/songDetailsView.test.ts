// @vitest-environment jsdom
/**
 * Execution tests for the song-details presentation panel. Stages the DOM
 * ids the panel reads, mocks the key-inference helpers (`./hud`,
 * `./tabRenderer`) and the injected deps, and exercises each writer:
 * setHudKeyMode (notes vs manifest fallback), renderDetails (full + empty),
 * and setSelectedSongSetupLabel (play-gate + console log).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

const extractKeyModeFromManifest = vi.fn();
const inferKeySignature = vi.fn();

vi.mock("../src/hud", () => ({
  extractKeyModeFromManifest: (...a: unknown[]) => extractKeyModeFromManifest(...a),
}));
vi.mock("../src/tabRenderer", () => ({
  inferKeySignature: (...a: unknown[]) => inferKeySignature(...a),
}));

import { initSongDetailsView } from "../src/songDetailsView";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="hudKeyMode"></div>' +
    '<div id="selectedSongLabel"></div>' +
    '<div id="selectedSongPath"></div>' +
    '<button id="playStart"></button>';
}

function makeDeps() {
  const setDetailsHTML = vi.fn();
  const log = vi.fn();
  return {
    deps: {
      songLibraryPanel: { setDetailsHTML } as any,
      consoleBridge: { log } as any,
      escapeHtml: (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    },
    setDetailsHTML,
    log,
  };
}

function fullDetails(over: Record<string, unknown> = {}) {
  return {
    kind: "auralsong",
    container_path: "/songs/test.auralsong",
    manifest_summary: { title: "My Song", artist: "The Band" },
    manifest_raw: { title: "My Song" },
    has_beats: true,
    has_tempo_map: false,
    has_sections: true,
    has_events: false,
    has_lyrics: true,
    has_mix_mp3: true,
    has_mix_ogg: false,
    has_mix_wav: true,
    charts: ["drums", "bass"],
    error: undefined,
    ...over,
  } as any;
}

const hud = () => document.getElementById("hudKeyMode")!.textContent;

describe("initSongDetailsView", () => {
  beforeEach(() => {
    stageDom();
    extractKeyModeFromManifest.mockReset();
    inferKeySignature.mockReset();
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    const { deps } = makeDeps();
    expect(() => initSongDetailsView(deps)).toThrow(/required DOM/);
  });

  it("setHudKeyMode uses inferred key signature when notes are present", () => {
    inferKeySignature.mockReturnValue({ label: "G major" });
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setHudKeyMode({}, [{ t_on: 0, t_off: 1, pitch: 60, velocity: 1 }] as any);
    expect(hud()).toBe("G major");
    expect(extractKeyModeFromManifest).not.toHaveBeenCalled();
  });

  it("setHudKeyMode falls back to manifest when inference returns null", () => {
    inferKeySignature.mockReturnValue(null);
    extractKeyModeFromManifest.mockReturnValue({ key: "C", mode: "major" });
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setHudKeyMode({ key: "C" }, [{ t_on: 0, t_off: 1, pitch: 60, velocity: 1 }] as any);
    expect(hud()).toBe("C major");
  });

  it("setHudKeyMode uses the manifest when no notes are provided", () => {
    extractKeyModeFromManifest.mockReturnValue({ key: "D", mode: "minor" });
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setHudKeyMode({ key: "D" });
    expect(hud()).toBe("D minor");
    expect(inferKeySignature).not.toHaveBeenCalled();
  });

  it("setHudKeyMode falls back to manifest for an empty notes array", () => {
    extractKeyModeFromManifest.mockReturnValue({ key: "E", mode: "major" });
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setHudKeyMode({}, []);
    expect(hud()).toBe("E major");
  });

  it("renderDetails writes full HTML through the panel", () => {
    const { deps, setDetailsHTML } = makeDeps();
    const view = initSongDetailsView(deps);
    view.renderDetails(fullDetails());
    expect(setDetailsHTML).toHaveBeenCalledTimes(1);
    const html = setDetailsHTML.mock.calls[0][0] as string;
    expect(html).toContain("My Song");
    expect(html).toContain("The Band");
    expect(html).toContain("drums");
    expect(html).toContain("beats: yes");
    expect(html).toContain("tempo_map: no");
  });

  it("renderDetails handles missing title/artist/manifest/charts + error", () => {
    const { deps, setDetailsHTML } = makeDeps();
    const view = initSongDetailsView(deps);
    view.renderDetails(
      fullDetails({
        manifest_summary: undefined,
        manifest_raw: undefined,
        charts: [],
        error: "boom",
        has_lyrics: undefined,
        has_mix_wav: undefined,
      }),
    );
    const html = setDetailsHTML.mock.calls[0][0] as string;
    expect(html).toContain("(missing title)");
    expect(html).toContain("(no manifest)");
    expect(html).toContain("(none)");
    expect(html).toContain("boom");
  });

  it("setSelectedSongSetupLabel enables play + logs when a container path exists", () => {
    const { deps, log } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setSelectedSongSetupLabel(fullDetails(), "/songs/test.auralsong");
    expect(document.getElementById("selectedSongLabel")!.textContent).toBe("My Song  ·  The Band");
    expect(document.getElementById("selectedSongPath")!.textContent).toBe("/songs/test.auralsong");
    expect((document.getElementById("playStart") as HTMLButtonElement).disabled).toBe(false);
    expect(log).toHaveBeenCalledWith("gamestate", "selected song updated", expect.objectContaining({ playEnabled: true }));
  });

  it("setSelectedSongSetupLabel disables play for null details/path", () => {
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setSelectedSongSetupLabel(null, null);
    expect(document.getElementById("selectedSongLabel")!.textContent).toBe("(no song selected)");
    expect(document.getElementById("selectedSongPath")!.textContent).toBe("");
    expect((document.getElementById("playStart") as HTMLButtonElement).disabled).toBe(true);
  });

  it("setSelectedSongSetupLabel shows title only when artist is blank", () => {
    const { deps } = makeDeps();
    const view = initSongDetailsView(deps);
    view.setSelectedSongSetupLabel(
      fullDetails({ manifest_summary: { title: "Solo", artist: "   " } }),
      "/p",
    );
    expect(document.getElementById("selectedSongLabel")!.textContent).toBe("Solo");
  });
});
