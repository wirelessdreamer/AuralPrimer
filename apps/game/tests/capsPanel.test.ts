// @vitest-environment jsdom
/**
 * Execution tests for the caps (capabilities) panel. Drives compute(),
 * render(), and applyAvailability() against jsdom with a stubbed players
 * container + playersPanel handle.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initCapsPanel } from "../src/capsPanel";
import type { CapsAuralSongDetails } from "../src/capsPanel";

function stageDom(): void {
  document.body.innerHTML =
    '<div id="songCaps"></div>' +
    '<div id="players">' +
    '  <div class="playerChip" data-player-id="p1">' +
    '    <select class="playerInstrument">' +
    '      <option value="drums">Drums</option>' +
    '      <option value="bass">Bass</option>' +
    '      <option value="keys">Keys</option>' +
    '    </select>' +
    "  </div>" +
    "</div>";
}

function makeDeps(overrides: Partial<Parameters<typeof initCapsPanel>[0]> = {}) {
  return {
    capsEl: document.getElementById("songCaps") as HTMLElement,
    playersEl: document.getElementById("players") as HTMLElement,
    playersPanel: { setPlayerInstrument: vi.fn() } as any,
    getSelectedMelodicTracks: vi.fn(() => []),
    escapeHtml: (s: string) => s,
    ...overrides,
  } as Parameters<typeof initCapsPanel>[0];
}

const richDetails: CapsAuralSongDetails = {
  charts: ["bass_chart.json"],
  manifest_raw: { source: { parts: { mapped_game_roles: ["keys"] } } },
  has_beats: true,
  has_tempo_map: true,
  has_sections: true,
  has_events: true,
  has_lyrics: true,
  has_notes_mid: true,
  has_drum_tab: true,
  has_song_timeline: true,
  has_keys: true,
  has_harmony: true,
  has_vocal_pitch: true,
  has_vocal_pitch_contour: true,
  has_aural_fingering: true,
  has_mix_wav: true,
  has_mix_mp3: true,
  has_mix_ogg: true,
};

describe("capsPanel", () => {
  beforeEach(() => stageDom());

  it("compute maps every feature/audio flag and infers charts from filename + manifest + tracks", () => {
    const getSelectedMelodicTracks = vi.fn(() => [{ role: "vocals" } as any]);
    const handle = initCapsPanel(makeDeps({ getSelectedMelodicTracks }));
    const caps = handle.compute(richDetails, null, { "guitar.json": { mode: "lead" } });
    expect(caps.features).toEqual({
      beats: true,
      tempo_map: true,
      sections: true,
      events: true,
      lyrics: true,
      notes_mid: true,
      drum_tab: true,
      song_timeline: true,
      keys: true,
      harmony: true,
      vocal_pitch: true,
      vocal_pitch_contour: true,
      aural_fingering: true,
    });
    expect(caps.audio).toEqual({ wav: true, mp3: true, ogg: true });
    expect(caps.charts.any).toBe(true);
    expect(caps.charts.byInstrument.bass).toBe(true); // filename hint
    expect(caps.charts.byInstrument.keys).toBe(true); // manifest mapped role
    expect(caps.charts.byInstrument.lead_guitar).toBe(true); // chart json + chart path
    expect(caps.charts.byInstrument.vocals).toBe(true); // melodic track role
  });

  it("compute treats midi drum selection as drums available", () => {
    const handle = initCapsPanel(makeDeps());
    const caps = handle.compute(null, { mode: "gm", reason: "x", events: [1] } as any, null);
    expect(caps.charts.byInstrument.drums).toBe(true);
    expect(caps.charts.any).toBe(true);
  });

  it("compute falls back to drums when charts exist but cannot be classified", () => {
    const handle = initCapsPanel(makeDeps());
    const caps = handle.compute({ charts: ["xyzzy.json"] }, null, null);
    expect(caps.charts.byInstrument.drums).toBe(true);
  });

  it("compute returns all-false for null details", () => {
    const handle = initCapsPanel(makeDeps());
    const caps = handle.compute(null, null, null);
    expect(caps.charts.any).toBe(false);
    expect(caps.features.beats).toBe(false);
    expect(caps.features.aural_fingering).toBe(false);
    expect(caps.audio.wav).toBe(false);
  });

  it("render writes the three pill rows including the drum-selection hint", () => {
    const handle = initCapsPanel(makeDeps());
    handle.render(richDetails, { mode: "gm", reason: "best", events: [1, 2] } as any, null);
    const html = (document.getElementById("songCaps") as HTMLElement).innerHTML;
    expect(html).toContain("capsRow");
    expect(html).toContain("Data");
    expect(html).toContain("Charts");
    expect(html).toContain("Audio");
    expect(html).toContain("events=2");
    expect(html).toContain("song_timeline.json");
    expect(html).toContain("drum_tab.json");
    expect(html).toContain("vocal_pitch.json");
    expect(html).toContain("aural/fingering");
    expect(html).toContain("capPill--ok");
  });

  it("render uses the generic drum hint when no drum selection", () => {
    const handle = initCapsPanel(makeDeps());
    handle.render(null, null, null);
    const html = (document.getElementById("songCaps") as HTMLElement).innerHTML;
    expect(html).toContain("capPill--missing");
  });

  it("render names drum_tab.json in the drum chart hint when selected", () => {
    const handle = initCapsPanel(makeDeps());
    handle.render(richDetails, { mode: "relaxed", reason: "drum_tab", events: [1, 2, 3] } as any, null);
    const html = (document.getElementById("songCaps") as HTMLElement).innerHTML;
    expect(html).toContain("drum_tab.json");
    expect(html).toContain("events=3");
  });

  it("applyAvailability disables instruments with no chart and re-picks first enabled", () => {
    const setPlayerInstrument = vi.fn();
    const handle = initCapsPanel(
      makeDeps({ playersPanel: { setPlayerInstrument } as any }),
    );
    // Charts only exist for bass -> drums + keys become disabled. The select
    // currently shows Drums (now disabled), so it should fall back to bass... but
    // bass isn't an option here; first enabled option is whatever remains.
    handle.applyAvailability({ charts: ["bass_chart.json"] }, null, null);
    const sel = document.querySelector("select.playerInstrument") as HTMLSelectElement;
    const drums = sel.options[0];
    expect(drums.disabled).toBe(true);
    expect(drums.textContent).toContain("(no chart)");
    // Selection moved to the first enabled option.
    expect(sel.selectedOptions[0].disabled).toBe(false);
    expect(setPlayerInstrument).toHaveBeenCalledWith("p1", expect.any(String));
  });

  it("applyAvailability leaves all options enabled when there are no charts at all", () => {
    const handle = initCapsPanel(makeDeps());
    handle.applyAvailability(null, null, null);
    const sel = document.querySelector("select.playerInstrument") as HTMLSelectElement;
    for (const o of Array.from(sel.options)) {
      expect(o.disabled).toBe(false);
      expect(o.textContent).not.toContain("(no chart)");
    }
  });

  it("applyAvailability skips chips with no instrument select", () => {
    document.getElementById("players")!.innerHTML =
      '<div class="playerChip" data-player-id="p2"></div>';
    const handle = initCapsPanel(makeDeps());
    expect(() => handle.applyAvailability({ charts: ["bass.json"] }, null, null)).not.toThrow();
  });
});
