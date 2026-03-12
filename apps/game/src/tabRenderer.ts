/**
 * tabRenderer.ts — Scrolling tab renderer for melodic instruments.
 *
 * Supports two display modes:
 *   1) **Tab view** (fretted instruments: bass, guitar) — horizontal strings with
 *      fret numbers scrolling right-to-left past a hit line.
 *   2) **Piano-roll view** (keys/synth) — vertical pitch axis with note blocks
 *      scrolling right-to-left.
 *
 * Usage:
 *   const renderer = new TabRenderer(containerEl);
 *   renderer.setTrack(track);    // MelodicTrackSelection from chartLoader
 *   // In your render loop:
 *   renderer.render(currentTimeSec);
 */

import type { MelodicNote, InstrumentRole, MelodicTrackSelection } from "./chartLoader";

// ─── Tuning definitions ───────────────────────────────────────────────────────

export type Tuning = {
  name: string;
  /** MIDI note numbers for each string, lowest (thickest) first. */
  strings: number[];
};

export const TUNING_BASS_STANDARD: Tuning = {
  name: "Bass Standard (EADG)",
  strings: [28, 33, 38, 43], // E1, A1, D2, G2
};

export const TUNING_GUITAR_STANDARD: Tuning = {
  name: "Guitar Standard (EADGBE)",
  strings: [40, 45, 50, 55, 59, 64], // E2, A2, D3, G3, B3, E4
};

// ─── Fret mapping ─────────────────────────────────────────────────────────────

function pitchToFret(pitch: number, tuning: Tuning): { string: number; fret: number } | null {
  // Find the best string/fret combination (prefer lower frets).
  let best: { string: number; fret: number } | null = null;

  for (let s = 0; s < tuning.strings.length; s++) {
    const fret = pitch - tuning.strings[s];
    if (fret < 0 || fret > 24) continue;
    if (!best || fret < best.fret) {
      best = { string: s, fret };
    }
  }
  return best;
}

// ─── Note name util ───────────────────────────────────────────────────────────

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function midiToNoteName(pitch: number): string {
  const name = NOTE_NAMES[pitch % 12];
  const octave = Math.floor(pitch / 12) - 1;
  return `${name}${octave}`;
}

// ─── Colors ───────────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<InstrumentRole, string> = {
  bass: "#ff6b35",       // warm orange
  rhythm_guitar: "#00d4aa",  // teal
  lead_guitar: "#ff4d6d",   // hot pink
  keys: "#7b68ee",       // medium slate blue
  melodic: "#adb5bd",    // neutral gray
};

const ROLE_GLOW_COLORS: Record<InstrumentRole, string> = {
  bass: "rgba(255, 107, 53, 0.4)",
  rhythm_guitar: "rgba(0, 212, 170, 0.4)",
  lead_guitar: "rgba(255, 77, 109, 0.4)",
  keys: "rgba(123, 104, 238, 0.4)",
  melodic: "rgba(173, 181, 189, 0.3)",
};

const BG_COLOR = "#1a1a2e";
const STRING_COLOR = "rgba(255,255,255,0.18)";
const HIT_LINE_COLOR = "rgba(255,255,255,0.65)";
const TEXT_COLOR = "#e0e0e0";

// ─── Renderer ─────────────────────────────────────────────────────────────────

export class TabRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private track: MelodicTrackSelection | null = null;
  private tuning: Tuning | null = null;
  private role: InstrumentRole = "melodic";

  /** Seconds of lookahead visible to the right of the hit line. */
  private windowSec = 4.0;
  /** Fraction of canvas width where the hit line sits (from left). */
  private hitLineFrac = 0.15;

  constructor(container: HTMLElement) {
    this.canvas = document.createElement("canvas");
    this.canvas.className = "tabCanvas";
    this.canvas.width = container.clientWidth || 800;
    this.canvas.height = 160;
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;

    // Observe resize.
    const ro = new ResizeObserver(([entry]) => {
      if (entry) {
        this.canvas.width = entry.contentRect.width;
        this.canvas.height = entry.contentRect.height;
      }
    });
    ro.observe(container);
  }

  setTrack(track: MelodicTrackSelection | null): void {
    this.track = track;
    this.role = track?.role ?? "melodic";

    if (track?.role === "bass") {
      this.tuning = TUNING_BASS_STANDARD;
    } else if (track?.role === "rhythm_guitar" || track?.role === "lead_guitar") {
      this.tuning = TUNING_GUITAR_STANDARD;
    } else {
      this.tuning = null; // piano-roll mode for keys
    }
  }

  /** Render one frame at the given transport time. */
  render(timeSec: number): void {
    const { ctx, canvas } = this;
    const w = canvas.width;
    const h = canvas.height;

    // Clear.
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, w, h);

    if (!this.track || this.track.notes.length === 0) {
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = "14px 'Inter', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No notes for this instrument", w / 2, h / 2);
      return;
    }

    if (this.tuning) {
      this.renderTab(timeSec);
    } else {
      this.renderPianoRoll(timeSec);
    }
  }

  // ─── Tab view (fretted instruments) ───────────────────────────────────────

  private renderTab(t: number): void {
    const { ctx, canvas, tuning, track } = this;
    if (!tuning || !track) return;

    const w = canvas.width;
    const h = canvas.height;
    const numStrings = tuning.strings.length;
    const yPad = 20;
    const stringSpacing = (h - yPad * 2) / Math.max(1, numStrings - 1);

    // Draw strings.
    ctx.strokeStyle = STRING_COLOR;
    ctx.lineWidth = 1;
    for (let s = 0; s < numStrings; s++) {
      const y = yPad + s * stringSpacing;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    // Hit line.
    const hitX = w * this.hitLineFrac;
    ctx.strokeStyle = HIT_LINE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(hitX, 0);
    ctx.lineTo(hitX, h);
    ctx.stroke();

    // Time window.
    const tStart = t - 0.5;  // small look-behind
    const tEnd = t + this.windowSec;
    const color = ROLE_COLORS[this.role];
    const glow = ROLE_GLOW_COLORS[this.role];

    ctx.font = "bold 13px 'Inter', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const note of track.notes) {
      if (note.t_on > tEnd || note.t_off < tStart) continue;

      const fretInfo = pitchToFret(note.pitch, tuning);
      if (!fretInfo) continue;

      const x = hitX + ((note.t_on - t) / this.windowSec) * (w - hitX);
      const y = yPad + fretInfo.string * stringSpacing;

      // Distance from hit line for glow intensity.
      const dist = Math.abs(note.t_on - t);
      const alpha = dist < 0.1 ? 1.0 : Math.max(0.3, 1.0 - dist / this.windowSec);

      // Glow circle.
      ctx.save();
      ctx.globalAlpha = alpha * 0.6;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, 16, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Fret number pill.
      const pillW = 22;
      const pillH = 18;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x - pillW / 2, y - pillH / 2, pillW, pillH, 4);
      ctx.fill();

      ctx.fillStyle = "#000";
      ctx.fillText(String(fretInfo.fret), x, y);
      ctx.restore();
    }

    // Track label.
    ctx.save();
    ctx.fillStyle = color;
    ctx.font = "bold 11px 'Inter', sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(track.trackName.toUpperCase(), 8, 4);
    ctx.restore();
  }

  // ─── Piano-roll view (keys/synth) ─────────────────────────────────────────

  private renderPianoRoll(t: number): void {
    const { ctx, canvas, track } = this;
    if (!track) return;

    const w = canvas.width;
    const h = canvas.height;

    // Determine pitch range.
    let minPitch = 127;
    let maxPitch = 0;
    for (const n of track.notes) {
      if (n.pitch < minPitch) minPitch = n.pitch;
      if (n.pitch > maxPitch) maxPitch = n.pitch;
    }
    // Add padding.
    minPitch = Math.max(0, minPitch - 2);
    maxPitch = Math.min(127, maxPitch + 2);
    const pitchRange = Math.max(1, maxPitch - minPitch);

    // Hit line.
    const hitX = w * this.hitLineFrac;
    ctx.strokeStyle = HIT_LINE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(hitX, 0);
    ctx.lineTo(hitX, h);
    ctx.stroke();

    // Horizontal pitch guides (every octave C).
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.font = "9px 'Inter', sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let p = minPitch; p <= maxPitch; p++) {
      if (p % 12 === 0) {
        const y = h - ((p - minPitch) / pitchRange) * (h - 10) - 5;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        ctx.fillText(midiToNoteName(p), hitX - 6, y);
      }
    }

    // Time window.
    const tStart = t - 0.5;
    const tEnd = t + this.windowSec;
    const color = ROLE_COLORS[this.role];
    const glow = ROLE_GLOW_COLORS[this.role];

    for (const note of track.notes) {
      if (note.t_off < tStart || note.t_on > tEnd) continue;

      const x1 = hitX + ((note.t_on - t) / this.windowSec) * (w - hitX);
      const x2 = hitX + ((note.t_off - t) / this.windowSec) * (w - hitX);
      const noteW = Math.max(4, x2 - x1);
      const y = h - ((note.pitch - minPitch) / pitchRange) * (h - 10) - 5;
      const noteH = Math.max(3, (h - 10) / pitchRange);

      const dist = Math.abs(note.t_on - t);
      const alpha = dist < 0.1 ? 1.0 : Math.max(0.3, 1.0 - dist / this.windowSec);

      // Glow.
      ctx.save();
      ctx.globalAlpha = alpha * 0.5;
      ctx.fillStyle = glow;
      ctx.fillRect(x1 - 2, y - noteH / 2 - 2, noteW + 4, noteH + 4);
      ctx.restore();

      // Note block.
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x1, y - noteH / 2, noteW, noteH, 2);
      ctx.fill();
      ctx.restore();
    }

    // Track label.
    ctx.save();
    ctx.fillStyle = ROLE_COLORS[this.role];
    ctx.font = "bold 11px 'Inter', sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(track.trackName.toUpperCase(), 8, 4);
    ctx.restore();
  }

  dispose(): void {
    this.canvas.remove();
  }
}
