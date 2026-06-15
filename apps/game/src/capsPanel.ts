/**
 * Caps (capabilities) panel — the row of pill chips below the song-info
 * block showing which AuralSong features / charts / mixdowns are available,
 * plus the cross-talk that disables Player chip dropdowns for instruments
 * the AuralSong has no chart for.
 *
 * Owns:
 *   - the dynamically-created #songCaps container (caller hands us the slot)
 *   - the per-render HTML for feature / chart / audio pill rows
 *   - the .playerChip <select> option enable/disable sweep
 *
 * Extracted from main.ts as Phase 2.L (continuation of the instrument-hints
 * extraction). The pure capability computation (computeSongCapabilities)
 * also moved here since renderCaps + applyInstrumentAvailability were its
 * only callers.
 */

import {
  applyInstrumentHintsFromChartJson,
  applyInstrumentHintsFromManifestRaw,
  applyInstrumentHintsFromMappedRole,
  applyInstrumentHintsFromToken,
  type InstrumentFlags,
} from "./instrumentHints";
import { INSTRUMENT_LABELS, type Instrument } from "./instrumentTypes";
import type { DrumChartSelection, MelodicTrackSelection } from "./chartLoader";
import type { PlayersPanelHandle } from "./playersPanel";

/** Loose alias for the chart-spec JSON-by-path map — same as in main.ts. */
export type AuralSongChartsByPath = Record<string, unknown>;

/** Minimum subset of AuralSongDetails the caps panel reads. */
export type CapsAuralSongDetails = {
  charts?: string[];
  manifest_raw?: unknown;
  has_beats?: boolean;
  has_tempo_map?: boolean;
  has_sections?: boolean;
  has_events?: boolean;
  has_lyrics?: boolean;
  has_notes_mid?: boolean;
  has_mix_wav?: boolean;
  has_mix_mp3?: boolean;
  has_mix_ogg?: boolean;
};

export type SongCapabilities = {
  features: {
    beats: boolean;
    tempo_map: boolean;
    sections: boolean;
    events: boolean;
    lyrics: boolean;
    notes_mid: boolean;
  };
  audio: { wav: boolean; mp3: boolean; ogg: boolean };
  charts: { any: boolean; byInstrument: InstrumentFlags };
};

export type CapsPanelDeps = {
  /** Container <div> for the pill rows. Host creates + slots this. */
  capsEl: HTMLElement;
  /** Container that holds .playerChip elements (#players). */
  playersEl: HTMLElement;
  /** Player handle, used to write back instrument changes after auto-fallback. */
  playersPanel: PlayersPanelHandle;
  /** Getter for the currently-selected melodic tracks (live, may change between renders). */
  getSelectedMelodicTracks: () => MelodicTrackSelection[];
  /** Same escapeHtml the rest of main.ts uses (passed in so this stays free of cycles). */
  escapeHtml: (s: string) => string;
};

export type CapsPanelHandle = {
  /** Re-render the pill rows for the given song + drum + chart-JSON context. */
  render: (
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ) => void;
  /** Sweep the .playerChip selects and disable instruments with no chart. */
  applyAvailability: (
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ) => void;
  /** Pure compute, exposed for callers that need the raw caps. */
  compute: (
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ) => SongCapabilities;
};

export function initCapsPanel(deps: CapsPanelDeps): CapsPanelHandle {
  function compute(
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ): SongCapabilities {
    const charts = details?.charts ?? [];
    const byInstrument: InstrumentFlags = {};
    const midiDrumsAvailable = Boolean(drumSelection?.events.length);

    // First pass: filename hints.
    for (const chartPath of charts) applyInstrumentHintsFromToken(chartPath, byInstrument);

    // Second pass: chart JSON content hints (mode/targets/instrument fields).
    for (const [chartPath, chartJson] of Object.entries(chartsByPath ?? {})) {
      applyInstrumentHintsFromToken(chartPath, byInstrument);
      applyInstrumentHintsFromChartJson(chartJson, byInstrument);
    }

    applyInstrumentHintsFromManifestRaw(details?.manifest_raw, byInstrument);

    for (const track of deps.getSelectedMelodicTracks()) {
      applyInstrumentHintsFromMappedRole(track.role, byInstrument);
    }

    if (midiDrumsAvailable) byInstrument.drums = true;

    // Safety fallback: if charts exist but cannot be classified, treat as drums.
    const inferredAny = (Object.keys(INSTRUMENT_LABELS) as Instrument[]).some((inst) => Boolean(byInstrument[inst]));
    if (charts.length > 0 && !inferredAny) byInstrument.drums = true;

    return {
      features: {
        beats: Boolean(details?.has_beats),
        tempo_map: Boolean(details?.has_tempo_map),
        sections: Boolean(details?.has_sections),
        events: Boolean(details?.has_events),
        lyrics: Boolean(details?.has_lyrics),
        notes_mid: Boolean(details?.has_notes_mid),
      },
      audio: {
        wav: Boolean(details?.has_mix_wav),
        mp3: Boolean(details?.has_mix_mp3),
        ogg: Boolean(details?.has_mix_ogg),
      },
      charts: { any: charts.length > 0 || midiDrumsAvailable, byInstrument },
    };
  }

  function render(
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ): void {
    const caps = compute(details, drumSelection, chartsByPath);
    const esc = deps.escapeHtml;
    const pill = (label: string, ok: boolean, hint?: string) => {
      const cls = ok ? "capPill capPill--ok" : "capPill capPill--missing";
      const title = hint ? ` title="${esc(hint)}"` : "";
      return `<span class="${cls}"${title}>${esc(label)}</span>`;
    };

    const featurePills = [
      pill("beats", caps.features.beats, "features/notes.mid (structure track beat pulses)"),
      pill("tempo", caps.features.tempo_map, "features/notes.mid (SetTempo + TimeSignature meta)"),
      pill("sections", caps.features.sections, "features/notes.mid (section markers)"),
      pill("events", caps.features.events, "features/notes.mid (drums ch10 + melodic ch1 notes)"),
      pill("lyrics", caps.features.lyrics, "features/lyrics.json"),
      pill("midi", caps.features.notes_mid, "features/notes.mid"),
    ].join("\n");

    const drumHint = drumSelection
      ? `features/notes.mid (${drumSelection.mode}, ${drumSelection.reason}, events=${drumSelection.events.length})`
      : "chart availability (heuristic)";
    const chartPills = (Object.keys(INSTRUMENT_LABELS) as Instrument[])
      .map((inst) => {
        const hint = inst === "drums" ? drumHint : "chart availability (heuristic)";
        return pill(INSTRUMENT_LABELS[inst], Boolean(caps.charts.byInstrument[inst]), hint);
      })
      .join("\n");

    const audioPills = [
      pill("mix.wav", caps.audio.wav),
      pill("mix.mp3", caps.audio.mp3),
      pill("mix.ogg", caps.audio.ogg),
    ].join("\n");

    deps.capsEl.innerHTML = `
      <div class="capsRow">
        <span class="capsLabel">Data</span>
        <div class="capsPills">${featurePills}</div>
      </div>
      <div class="capsRow">
        <span class="capsLabel">Charts</span>
        <div class="capsPills">${chartPills}</div>
      </div>
      <div class="capsRow">
        <span class="capsLabel">Audio</span>
        <div class="capsPills">${audioPills}</div>
      </div>
    `;
  }

  function applyAvailability(
    details: CapsAuralSongDetails | null,
    drumSelection: DrumChartSelection | null,
    chartsByPath: AuralSongChartsByPath | null,
  ): void {
    const caps = compute(details, drumSelection, chartsByPath);
    for (const chip of Array.from(deps.playersEl.querySelectorAll<HTMLElement>(".playerChip"))) {
      const chipId = chip.getAttribute("data-player-id");
      const sel = chip.querySelector<HTMLSelectElement>("select.playerInstrument");
      if (!sel) continue;
      for (const opt of Array.from(sel.options)) {
        const inst = opt.value as Instrument;
        const has = Boolean(caps.charts.byInstrument[inst]);
        // Only disable if we have *some* chart data but not for this instrument.
        // If there are no charts at all, leave enabled (future non-chart gameplay).
        const disable = caps.charts.any ? !has : false;
        opt.disabled = disable;
        opt.textContent = disable ? `${INSTRUMENT_LABELS[inst]} (no chart)` : INSTRUMENT_LABELS[inst];
      }
      // If current selection is now disabled, pick first enabled.
      if (sel.selectedOptions.length && sel.selectedOptions[0].disabled) {
        const firstEnabled = Array.from(sel.options).find((o) => !o.disabled);
        if (firstEnabled) {
          sel.value = firstEnabled.value;
          if (chipId) deps.playersPanel.setPlayerInstrument(chipId, firstEnabled.value as Instrument);
        }
      }
    }
  }

  return { render, applyAvailability, compute };
}
