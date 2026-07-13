import { clamp, clampScrollSpeedMultiplier, scrollWindow } from "@auralprimer/viz-sdk";
import type { Visualizer, VisualizerModule, VizInitContext, FrameContext, TransportState } from "@auralprimer/viz-sdk";
import { inferKeySignature, pitchToNashville, type KeySignatureAnalysis, type MelodicNote } from "@auralprimer/viz-tab";

// Real Nashville Number System visualizer: a scrolling piano roll whose notes
// are labelled by their scale-degree number relative to the song's inferred
// key (1..7 diatonic, b/# of the nearest degree for chromatic notes). Driven by
// the host-provided melodic note data (init.song.notes) — no placeholder loop.

type RollNote = { t_on: number; t_off: number; pitch: number };
export type HarmonyChord = { t: number; t_off: number; label: string; root?: string | null; quality?: string };

const PITCH_CLASS_BY_KEY: Record<string, number> = {
  C: 0,
  "B#": 0,
  "C#": 1,
  Db: 1,
  D: 2,
  "D#": 3,
  Eb: 3,
  E: 4,
  Fb: 4,
  "E#": 5,
  F: 5,
  "F#": 6,
  Gb: 6,
  G: 7,
  "G#": 8,
  Ab: 8,
  A: 9,
  "A#": 10,
  Bb: 10,
  B: 11,
  Cb: 11,
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeMode(value: unknown): "major" | "minor" | null {
  if (typeof value !== "string") return null;
  const mode = value.trim().toLowerCase();
  if (mode === "maj" || mode === "major" || mode === "ionian") return "major";
  if (mode === "min" || mode === "minor" || mode === "aeolian") return "minor";
  return null;
}

function keyAnalysisFromKeyMode(
  key: unknown,
  mode: unknown,
  confidence: unknown,
  score: unknown,
): KeySignatureAnalysis | null {
  if (typeof key !== "string") return null;
  const tonic = key.trim();
  if (!tonic || PITCH_CLASS_BY_KEY[tonic] === undefined) return null;
  const normalizedMode = normalizeMode(mode) ?? "major";
  const accidentalKind = tonic.includes("b") ? "flat" : tonic.includes("#") ? "sharp" : "natural";
  const noteLabelStyle = accidentalKind === "flat" ? "flat" : "sharp";
  return {
    tonic,
    mode: normalizedMode,
    pitchClass: PITCH_CLASS_BY_KEY[tonic],
    label: `${tonic} ${normalizedMode}`,
    accidentalKind,
    accidentalCount: 0,
    accidentals: [],
    noteLabelStyle,
    score: typeof score === "number" && Number.isFinite(score) ? score : 1,
    confidence: typeof confidence === "number" && Number.isFinite(confidence) ? confidence : 1,
  };
}

export function keySignatureFromSongArtifacts(song: VizInitContext["song"]): KeySignatureAnalysis | null {
  const harmony = song?.harmony;
  if (isObject(harmony)) {
    const analysis = keyAnalysisFromKeyMode(
      harmony.key ?? harmony.tonic,
      harmony.mode ?? harmony.scale,
      harmony.confidence,
      harmony.score,
    );
    if (analysis) return analysis;
  }

  const keys = song?.keys;
  if (isObject(keys) && Array.isArray(keys.events)) {
    for (const event of keys.events) {
      if (!isObject(event)) continue;
      const analysis = keyAnalysisFromKeyMode(
        event.key ?? event.tonic,
        event.scale ?? event.mode,
        event.confidence,
        event.score,
      );
      if (analysis) return analysis;
    }
  }
  return null;
}

function chordLabel(event: Record<string, unknown>): string | null {
  const rn = typeof event.rn === "string" ? event.rn.trim() : "";
  const root = typeof event.root === "string" ? event.root.trim() : "";
  const quality = typeof event.quality === "string" ? event.quality.trim() : "";
  if (rn && root) return quality ? `${rn} (${root}${quality})` : `${rn} (${root})`;
  if (rn) return rn;
  if (root) return quality ? `${root}${quality}` : root;
  if (event.root === null) return "N.C.";
  return null;
}

export function harmonyChordsFromSongArtifacts(song: VizInitContext["song"]): HarmonyChord[] {
  const harmony = song?.harmony;
  if (!isObject(harmony) || !Array.isArray(harmony.events)) return [];

  const raw: Array<{
    t: number;
    duration: number | null;
    label: string;
    root?: string | null;
    quality?: string;
  }> = [];
  for (const event of harmony.events) {
    if (!isObject(event)) continue;
    const t = typeof event.t === "number" && Number.isFinite(event.t) ? event.t : null;
    const label = chordLabel(event);
    if (t === null || !label) continue;
    const duration =
      typeof event.duration === "number" && Number.isFinite(event.duration) && event.duration > 0
        ? event.duration
        : null;
    raw.push({
      t,
      duration,
      label,
      root: typeof event.root === "string" || event.root === null ? event.root : undefined,
      quality: typeof event.quality === "string" ? event.quality : undefined,
    });
  }
  raw.sort((a, b) => a.t - b.t || a.label.localeCompare(b.label));

  return raw.map((event, index) => {
    const nextT = raw[index + 1]?.t;
    const fallbackEnd = typeof nextT === "number" && nextT > event.t ? nextT : event.t + 2;
    return {
      t: event.t,
      t_off: event.t + (event.duration ?? Math.max(0.5, fallbackEnd - event.t)),
      label: event.label,
      root: event.root,
      quality: event.quality,
    };
  });
}

const ORIGIN_X = 40; // playhead / "now" line
const BASE_PX_PER_SEC = 140;

class NashvilleVisualizer implements Visualizer {
  private dpr = 1;
  private lastState: TransportState | null = null;
  private notes: RollNote[] = [];
  private chords: HarmonyChord[] = [];
  private key: KeySignatureAnalysis | null = null;
  private minPitch = 48;
  private maxPitch = 72;

  async init(ctx: VizInitContext): Promise<void> {
    const raw = ctx.song?.notes ?? [];
    this.chords = harmonyChordsFromSongArtifacts(ctx.song);
    this.key = keySignatureFromSongArtifacts(ctx.song);
    // Melodic only — drop drums (channel 9). Keep pitched note events.
    const melodic = raw.filter((n) => n.channel !== 9 && Number.isFinite(n.pitch));
    this.notes = melodic.map((n) => ({
      t_on: n.t_on,
      t_off: typeof n.t_off === "number" && n.t_off > n.t_on ? n.t_off : n.t_on + 0.12,
      pitch: n.pitch,
    }));
    this.notes.sort((a, b) => a.t_on - b.t_on);

    if (this.notes.length > 0) {
      this.minPitch = Math.min(...this.notes.map((n) => n.pitch));
      this.maxPitch = Math.max(...this.notes.map((n) => n.pitch));
      const ksNotes: MelodicNote[] = this.notes.map((n) => ({
        t_on: n.t_on,
        t_off: n.t_off,
        pitch: n.pitch,
        velocity: 100,
      }));
      this.key = this.key ?? inferKeySignature(ksNotes);
    }
  }

  onResize(_width: number, _height: number, dpr: number): void {
    this.dpr = dpr;
  }

  update(_dt: number, state: TransportState): void {
    this.lastState = state;
  }

  render(frame: FrameContext): void {
    const g = frame.ctx2d;
    const { width: w, height: h } = frame;

    // Background.
    g.clearRect(0, 0, w, h);
    const bg = g.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, "#0f1520");
    bg.addColorStop(1, "#0a1018");
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);

    const t = frame.state.t;
    const scrollMul = clampScrollSpeedMultiplier(frame.state.scrollSpeedMultiplier);
    const pxPerSecond = BASE_PX_PER_SEC * scrollMul;
    const laneW = w - ORIGIN_X;
    const { windowSec } = scrollWindow({ heightPx: laneW, basePxPerSec: BASE_PX_PER_SEC, scrollMul });

    if (this.notes.length === 0 && this.chords.length === 0) {
      g.fillStyle = "rgba(223,237,255,0.7)";
      g.font = "14px system-ui, sans-serif";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("No melodic notes for Nashville view", w * 0.5, h * 0.5);
      return;
    }

    // Pitch -> y. Pad the observed range by a couple of semitones.
    const lo = this.minPitch - 2;
    const hi = this.maxPitch + 2;
    const span = Math.max(1, hi - lo);
    const chordBandH = this.chords.length > 0 ? 24 : 0;
    const padY = this.chords.length > 0 ? 58 : 28;
    const pitchToY = (pitch: number): number => padY + (1 - (pitch - lo) / span) * (h - padY * 2);
    const rowH = clamp((h - padY * 2) / span, 6, 22);

    // Tonic gridlines (every octave of the tonic pitch class).
    if (this.key) {
      g.strokeStyle = "rgba(255,209,102,0.16)";
      g.lineWidth = 1;
      for (let p = lo; p <= hi; p += 1) {
        if ((((p - this.key.pitchClass) % 12) + 12) % 12 === 0) {
          const y = pitchToY(p);
          g.beginPath();
          g.moveTo(ORIGIN_X, y);
          g.lineTo(w, y);
          g.stroke();
        }
      }
    }

    const tEnd = t + windowSec;
    if (this.chords.length > 0) {
      const bandY = 30;
      for (const chord of this.chords) {
        if (chord.t_off < t - 0.3 || chord.t > tEnd) continue;
        const x = ORIGIN_X + (chord.t - t) * pxPerSecond;
        const chordW = Math.max(22, (chord.t_off - chord.t) * pxPerSecond);
        g.fillStyle = "rgba(255,209,102,0.16)";
        roundRect(g, x, bandY, chordW, chordBandH, 5);
        g.fill();
        g.strokeStyle = "rgba(255,209,102,0.36)";
        g.lineWidth = 1;
        g.stroke();
        if (chordW >= 30) {
          g.fillStyle = "rgba(255,245,210,0.94)";
          g.font = "700 12px ui-monospace, Consolas, monospace";
          g.textAlign = "left";
          g.textBaseline = "middle";
          g.fillText(chord.label, x + 6, bandY + chordBandH / 2, Math.max(8, chordW - 12));
        }
      }
    }

    // Notes scroll right -> left, reaching the playhead at their onset.
    for (const note of this.notes) {
      if (note.t_off < t - 0.3 || note.t_on > tEnd) continue;
      const x = ORIGIN_X + (note.t_on - t) * pxPerSecond;
      const noteW = Math.max(8, (note.t_off - note.t_on) * pxPerSecond);
      const y = pitchToY(note.pitch) - rowH / 2;
      const degree = pitchToNashville(note.pitch, this.key);
      const isTonic = degree === "1";

      g.fillStyle = isTonic ? "rgba(255,209,102,0.9)" : "rgba(108,168,255,0.85)";
      roundRect(g, x, y, noteW, rowH, Math.min(5, rowH * 0.4));
      g.fill();

      if (degree && noteW >= 14 && rowH >= 9) {
        g.fillStyle = "#0a1018";
        g.font = `700 ${Math.min(13, Math.floor(rowH))}px ui-monospace, Consolas, monospace`;
        g.textAlign = "left";
        g.textBaseline = "middle";
        g.fillText(degree, x + 3, y + rowH / 2);
      }
    }

    // Playhead.
    g.strokeStyle = "#3ee6a8";
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(ORIGIN_X, 0);
    g.lineTo(ORIGIN_X, h);
    g.stroke();

    // HUD: key + Nashville label.
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillStyle = "rgba(223,237,255,0.85)";
    g.font = "600 13px system-ui, sans-serif";
    g.fillText(
      this.key ? `Nashville · ${this.key.label} (1 = ${this.key.tonic})` : "Nashville · key unknown",
      12,
      20,
    );

    void this.dpr;
    void this.lastState;
  }

  dispose(): void {
    this.notes = [];
    this.chords = [];
    this.key = null;
  }
}

function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rad = Math.max(0, Math.min(r, w / 2, h / 2));
  g.beginPath();
  g.moveTo(x + rad, y);
  g.arcTo(x + w, y, x + w, y + h, rad);
  g.arcTo(x + w, y + h, x, y + h, rad);
  g.arcTo(x, y + h, x, y, rad);
  g.arcTo(x, y, x + w, y, rad);
  g.closePath();
}

export function createVisualizer(): Visualizer {
  return new NashvilleVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;
