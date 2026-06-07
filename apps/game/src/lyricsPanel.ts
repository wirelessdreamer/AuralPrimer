/**
 * Lyrics playback display panel.
 *
 * Owns the karaoke-style lyric overlay above the visualizer:
 *   - #playLyrics container (show/hide)
 *   - #playLyricsCurrent (active line with karaoke split)
 *   - #playLyricsNext (next-line preview)
 *
 * Pure: takes the lyrics + current playhead time + current plugin id as
 * args on every render call. The host owns the `currentLyrics` state and
 * passes it in (the lyrics are also consumed by buildVizSongContext, so
 * they outlive any single panel render).
 *
 * Extracted from main.ts as Phase 2.F.
 */

import type { LyricsFile } from "./lyricsGenerator";

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function escapeHtmlBasic(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function findActiveLyricLineIndex(lines: LyricsFile["lines"], t: number): number {
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (t >= line.start && t <= line.end) return i;
  }
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (t >= lines[i].start) idx = i;
  }
  return idx;
}

function computeLyricHighlightCharIndex(line: LyricsFile["lines"][number], t: number): number {
  const text = line.text ?? "";
  const chunks = line.chunks ?? [];
  if (!chunks.length) {
    const dur = Math.max(0.001, line.end - line.start);
    const p = clamp((t - line.start) / dur, 0, 1);
    return Math.round(p * text.length);
  }

  let idx = -1;
  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (t >= chunk.start && t <= chunk.end) {
      const dur = Math.max(0.001, chunk.end - chunk.start);
      const p = clamp((t - chunk.start) / dur, 0, 1);
      const span = Math.max(0, chunk.char_end - chunk.char_start);
      return chunk.char_start + Math.round(p * span);
    }
    if (t >= chunk.end) idx = i;
  }
  if (idx >= 0) return chunks[idx].char_end;
  return 0;
}

export type LyricsPanelHandle = {
  /**
   * Render or hide the lyric overlay for playback time `t`. Pass the
   * currently-selected visualizer plugin id so we can skip overlaying
   * lyrics when the viz-lyrics plugin is itself the active visualizer
   * (it draws lyrics on the canvas instead).
   */
  render: (t: number, lyrics: LyricsFile | null, currentPluginId: string | null) => void;
  /** Force-hide the overlay (used by stopVisualizer). */
  clear: () => void;
};

export function initLyricsPanel(): LyricsPanelHandle {
  const containerEl = document.getElementById("playLyrics") as HTMLDivElement | null;
  const currentEl = document.getElementById("playLyricsCurrent") as HTMLDivElement | null;
  const nextEl = document.getElementById("playLyricsNext") as HTMLDivElement | null;
  if (!containerEl || !currentEl || !nextEl) {
    throw new Error("initLyricsPanel: required DOM (#playLyrics/#playLyricsCurrent/#playLyricsNext) missing");
  }

  // Cached last render state so we only touch DOM when the visible content
  // changes. Avoids hundreds of innerHTML assignments per second at 60fps.
  let lastState = "__hidden";

  function clear(): void {
    if (lastState === "__hidden") return;
    containerEl!.hidden = true;
    currentEl!.innerHTML = "";
    nextEl!.textContent = "";
    lastState = "__hidden";
  }

  function render(t: number, lyrics: LyricsFile | null, currentPluginId: string | null): void {
    if (!lyrics?.lines?.length) {
      clear();
      return;
    }
    if (currentPluginId === "viz-lyrics") {
      // The visualizer plugin is itself drawing lyrics on the canvas; don't
      // double up.
      clear();
      return;
    }

    const lines = lyrics.lines;
    const previewLeadSec = 3;
    const postLineHoldSec = 1.5;
    const idx = findActiveLyricLineIndex(lines, t);

    if (idx < 0) {
      const firstLine = lines[0];
      if (firstLine.start - t > previewLeadSec) {
        clear();
        return;
      }
      const previewState = `preview|${firstLine.text}`;
      if (lastState === previewState) return;
      containerEl!.hidden = false;
      currentEl!.innerHTML = "";
      nextEl!.textContent = firstLine.text ?? "";
      lastState = previewState;
      return;
    }

    const line = lines[idx];
    if (idx === lines.length - 1 && t > line.end + postLineHoldSec) {
      clear();
      return;
    }

    const text = line.text ?? "";
    const splitAt = clamp(computeLyricHighlightCharIndex(line, t), 0, text.length);
    const currentHtml = [
      `<span class="playLyricsDone">${escapeHtmlBasic(text.slice(0, splitAt))}</span>`,
      `<span class="playLyricsRest">${escapeHtmlBasic(text.slice(splitAt))}</span>`,
    ].join("");
    const nextText = lines[idx + 1]?.text ?? "";
    const renderState = `${idx}|${splitAt}|${currentHtml}|${nextText}`;
    if (lastState === renderState) return;

    containerEl!.hidden = false;
    currentEl!.innerHTML = currentHtml;
    nextEl!.textContent = nextText;
    lastState = renderState;
  }

  return { render, clear };
}
