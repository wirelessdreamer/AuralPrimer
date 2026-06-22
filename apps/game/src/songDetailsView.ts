/**
 * Song-details presentation — three small DOM-writer functions that share
 * a "currently-selected song" mental model but write to three different
 * UI surfaces:
 *
 *   - the HUD key/mode chip (#hudKeyMode)
 *   - the band-setup label + path + playStart-button gate
 *   - the right-rail details slot inside the song library panel
 *
 * Pure presentation: zero state, no Tauri. Extracted from main.ts as
 * Phase 2.N.
 */

import type { AuralSongDetails } from "./auralsong";
import { extractKeyModeFromManifest } from "./hud";
import { inferKeySignature, type MelodicNote } from "./tabRenderer";
import type { SongLibraryPanelHandle } from "./songLibraryPanel";
import type { ConsoleBridge } from "./consoleBridge";

export type SongDetailsViewDeps = {
  songLibraryPanel: SongLibraryPanelHandle;
  consoleBridge: ConsoleBridge;
  escapeHtml: (s: string) => string;
};

export type SongDetailsViewHandle = {
  /**
   * Update the HUD key/mode chip. Prefers a data-driven key inferred from the
   * primary melodic track's notes (the same `inferKeySignature` the tab view
   * uses), so the header matches the on-screen key signature. Falls back to the
   * manifest, then to the default, when no notes are available.
   */
  setHudKeyMode: (manifestRaw: unknown, melodicNotes?: MelodicNote[] | null) => void;
  /** Replace the right-rail details slot with the given AuralSong details. */
  renderDetails: (details: AuralSongDetails) => void;
  /** Update the band-setup widget's title/artist/path + playStart gate. */
  setSelectedSongSetupLabel: (
    details: AuralSongDetails | null,
    containerPath: string | null,
  ) => void;
};

function yesNo(v: boolean): string {
  return v ? "yes" : "no";
}

export function initSongDetailsView(deps: SongDetailsViewDeps): SongDetailsViewHandle {
  const hudKeyModeEl = document.getElementById("hudKeyMode") as HTMLDivElement | null;
  const selectedSongLabelEl = document.getElementById("selectedSongLabel") as HTMLDivElement | null;
  const selectedSongPathEl = document.getElementById("selectedSongPath") as HTMLDivElement | null;
  const playStartBtn = document.getElementById("playStart") as HTMLButtonElement | null;
  if (!hudKeyModeEl || !selectedSongLabelEl || !selectedSongPathEl || !playStartBtn) {
    throw new Error(
      "initSongDetailsView: required DOM (#hudKeyMode/#selectedSongLabel/#selectedSongPath/#playStart) missing",
    );
  }

  const esc = deps.escapeHtml;

  function setHudKeyMode(manifestRaw: unknown, melodicNotes?: MelodicNote[] | null): void {
    if (melodicNotes && melodicNotes.length > 0) {
      const ks = inferKeySignature(melodicNotes);
      if (ks) {
        hudKeyModeEl!.textContent = ks.label;
        return;
      }
    }
    const km = extractKeyModeFromManifest(manifestRaw);
    hudKeyModeEl!.textContent = `${km.key} ${km.mode}`;
  }

  function renderDetails(details: AuralSongDetails): void {
    const title = details.manifest_summary?.title ?? "(missing title)";
    const artist = details.manifest_summary?.artist ?? "";

    const raw = details.manifest_raw ? JSON.stringify(details.manifest_raw, null, 2) : "(no manifest)";

    deps.songLibraryPanel.setDetailsHTML(`
      <h3>Details</h3>
      <div class="meta">${esc(details.kind)} · ${esc(details.container_path)}</div>

      <h4>${esc(title)} ${esc(artist)}</h4>

      ${details.error ? `<pre class="error">${esc(details.error)}</pre>` : ""}

      <h4>Features</h4>
      <ul>
        <li>beats: ${esc(yesNo(details.has_beats))}</li>
        <li>tempo_map: ${esc(yesNo(details.has_tempo_map))}</li>
        <li>sections: ${esc(yesNo(details.has_sections))}</li>
        <li>events: ${esc(yesNo(details.has_events))}</li>
        <li>lyrics: ${esc(yesNo(Boolean(details.has_lyrics)))}</li>
      </ul>

      <h4>Audio</h4>
      <ul>
        <li>mix.mp3: ${esc(yesNo(details.has_mix_mp3))}</li>
        <li>mix.ogg: ${esc(yesNo(details.has_mix_ogg))}</li>
        <li>mix.wav: ${esc(yesNo(Boolean(details.has_mix_wav)))}</li>
      </ul>

      <h4>Charts</h4>
      ${details.charts.length ? `<ul>${details.charts.map((c) => `<li>${esc(c)}</li>`).join("\n")}</ul>` : "(none)"}

      <h4>manifest.json</h4>
      <pre>${esc(raw)}</pre>
    `);
  }

  function setSelectedSongSetupLabel(
    details: AuralSongDetails | null,
    containerPath: string | null,
  ): void {
    const title = details?.manifest_summary?.title?.trim() || "(no song selected)";
    const artist = details?.manifest_summary?.artist?.trim() || "";
    selectedSongLabelEl!.textContent = artist ? `${title}  ·  ${artist}` : title;
    selectedSongPathEl!.textContent = containerPath ?? "";
    playStartBtn!.disabled = !containerPath;
    deps.consoleBridge.log("gamestate", "selected song updated", {
      title,
      artist,
      containerPath: containerPath ?? "",
      playEnabled: Boolean(containerPath),
    });
  }

  return { setHudKeyMode, renderDetails, setSelectedSongSetupLabel };
}
