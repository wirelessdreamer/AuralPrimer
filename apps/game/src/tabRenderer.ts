/**
 * tabRenderer.ts - Scrolling melodic note renderer for the game app.
 *
 * Supports two display modes:
 * 1) Tab view for fretted instruments (bass, guitar)
 * 2) Piano-roll view for keys and generic melodic tracks
 */

import type { InstrumentRole, MelodicNote, MelodicTrackSelection } from "./chartLoader";

export type Tuning = {
  name: string;
  /** MIDI note numbers for each string, lowest (thickest) first. */
  strings: number[];
};

export const TUNING_BASS_STANDARD: Tuning = {
  name: "Bass Standard (EADG)",
  strings: [28, 33, 38, 43],
};

export const TUNING_GUITAR_STANDARD: Tuning = {
  name: "Guitar Standard (EADGBE)",
  strings: [40, 45, 50, 55, 59, 64],
};

export type PianoRenderOptions = {
  bpm?: number;
  timeSignature?: [number, number];
  liveInputNotes?: PianoLiveInputNote[];
};

export type PianoLiveInputNote = {
  pitch: number;
  velocity?: number;
  velocityUnit?: number;
  isPressed?: boolean;
  heldBySustain?: boolean;
};

type KeySignatureKind = "sharp" | "flat" | "natural";
type NoteLabelStyle = "sharp" | "flat" | "dual";

export type KeySignatureAnalysis = {
  tonic: string;
  mode: "major" | "minor";
  pitchClass: number;
  label: string;
  accidentalKind: KeySignatureKind;
  accidentalCount: number;
  accidentals: string[];
  noteLabelStyle: NoteLabelStyle;
  score: number;
  confidence: number;
};

type KeyboardKey = {
  midi: number;
  x: number;
  w: number;
  centerX: number;
  isBlack: boolean;
};

type KeyboardLayout = {
  byMidi: Map<number, KeyboardKey>;
  white: KeyboardKey[];
  black: KeyboardKey[];
};

const SHARP_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);
const BG_COLOR = "#111721";
const PANEL_COLOR = "#141d2b";
const STRING_COLOR = "rgba(255,255,255,0.18)";
const HIT_LINE_COLOR = "rgba(255,255,255,0.76)";
const TEXT_COLOR = "#ecf2ff";

const ROLE_COLORS: Record<InstrumentRole, string> = {
  bass: "#ff8a3d",
  rhythm_guitar: "#20c997",
  lead_guitar: "#ff5f7a",
  keys: "#ffd166",
  melodic: "#9dd7ff",
};

const ROLE_GLOW_COLORS: Record<InstrumentRole, string> = {
  bass: "rgba(255, 138, 61, 0.40)",
  rhythm_guitar: "rgba(32, 201, 151, 0.40)",
  lead_guitar: "rgba(255, 95, 122, 0.40)",
  keys: "rgba(255, 209, 102, 0.36)",
  melodic: "rgba(157, 215, 255, 0.34)",
};

const MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];

const MAJOR_SIGNATURES = {
  C: { pitchClass: 0, accidentalKind: "natural" as const, accidentals: [] },
  G: { pitchClass: 7, accidentalKind: "sharp" as const, accidentals: ["F#"] },
  D: { pitchClass: 2, accidentalKind: "sharp" as const, accidentals: ["F#", "C#"] },
  A: { pitchClass: 9, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#"] },
  E: { pitchClass: 4, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#", "D#"] },
  B: { pitchClass: 11, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#", "D#", "A#"] },
  "F#": { pitchClass: 6, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#", "D#", "A#", "E#"] },
  F: { pitchClass: 5, accidentalKind: "flat" as const, accidentals: ["Bb"] },
  Bb: { pitchClass: 10, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb"] },
  Eb: { pitchClass: 3, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab"] },
  Ab: { pitchClass: 8, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db"] },
  Db: { pitchClass: 1, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db", "Gb"] },
  Gb: { pitchClass: 6, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"] },
} satisfies Record<string, { pitchClass: number; accidentalKind: KeySignatureKind; accidentals: string[] }>;

const MINOR_SIGNATURES = {
  A: { pitchClass: 9, accidentalKind: "natural" as const, accidentals: [] },
  E: { pitchClass: 4, accidentalKind: "sharp" as const, accidentals: ["F#"] },
  B: { pitchClass: 11, accidentalKind: "sharp" as const, accidentals: ["F#", "C#"] },
  "F#": { pitchClass: 6, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#"] },
  "C#": { pitchClass: 1, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#", "D#"] },
  "G#": { pitchClass: 8, accidentalKind: "sharp" as const, accidentals: ["F#", "C#", "G#", "D#", "A#"] },
  D: { pitchClass: 2, accidentalKind: "flat" as const, accidentals: ["Bb"] },
  G: { pitchClass: 7, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb"] },
  C: { pitchClass: 0, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab"] },
  F: { pitchClass: 5, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db"] },
  Bb: { pitchClass: 10, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db", "Gb"] },
  Eb: { pitchClass: 3, accidentalKind: "flat" as const, accidentals: ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"] },
} satisfies Record<string, { pitchClass: number; accidentalKind: KeySignatureKind; accidentals: string[] }>;

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function mod(n: number, m: number): number {
  return ((n % m) + m) % m;
}

function pitchToFret(pitch: number, tuning: Tuning): { string: number; fret: number } | null {
  let best: { string: number; fret: number } | null = null;

  for (let s = 0; s < tuning.strings.length; s += 1) {
    const fret = pitch - tuning.strings[s];
    if (fret < 0 || fret > 24) continue;
    if (!best || fret < best.fret) {
      best = { string: s, fret };
    }
  }

  return best;
}

function isBlackKey(pitch: number): boolean {
  return BLACK_PITCH_CLASSES.has(mod(pitch, 12));
}

function noteNameForPitchClass(pitchClass: number, style: NoteLabelStyle = "sharp"): string {
  if (style === "flat") return FLAT_NOTE_NAMES[pitchClass];
  if (style === "dual" && BLACK_PITCH_CLASSES.has(pitchClass)) {
    return `${SHARP_NOTE_NAMES[pitchClass]}/${FLAT_NOTE_NAMES[pitchClass]}`;
  }
  return SHARP_NOTE_NAMES[pitchClass];
}

export function midiToNoteName(pitch: number, style: NoteLabelStyle = "sharp"): string {
  const pitchClass = mod(pitch, 12);
  const name = noteNameForPitchClass(pitchClass, style);
  const octave = Math.floor(pitch / 12) - 1;
  return `${name}${octave}`;
}

function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  radius: number
): void {
  const r = clamp(radius, 0, Math.min(w, h) * 0.5);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function rgbToCss(rgb: readonly [number, number, number], alpha = 1): string {
  const [r, g, b] = rgb.map((value) => Math.round(clamp(value, 0, 255))) as [number, number, number];
  return `rgba(${r}, ${g}, ${b}, ${clamp(alpha, 0, 1)})`;
}

function mixRgb(a: readonly [number, number, number], b: readonly [number, number, number], t: number): [number, number, number] {
  const mix = clamp(t, 0, 1);
  return [
    a[0] + (b[0] - a[0]) * mix,
    a[1] + (b[1] - a[1]) * mix,
    a[2] + (b[2] - a[2]) * mix,
  ];
}

function velocityToUnit(value: number | undefined): number {
  if (!Number.isFinite(value)) return 0.7;
  if ((value as number) <= 1) return clamp(value as number, 0, 1);
  return clamp((value as number) / 127, 0, 1);
}

function cosineSimilarity(weights: number[], template: number[], tonicPitchClass: number): number {
  let dot = 0;
  let weightNorm = 0;
  let templateNorm = 0;

  for (let i = 0; i < 12; i += 1) {
    const w = weights[mod(i + tonicPitchClass, 12)];
    const t = template[i];
    dot += w * t;
    weightNorm += w * w;
    templateNorm += t * t;
  }

  if (weightNorm <= 1e-9 || templateNorm <= 1e-9) return 0;
  return dot / Math.sqrt(weightNorm * templateNorm);
}

function buildPitchClassWeights(notes: MelodicNote[]): number[] {
  const weights = Array<number>(12).fill(0);

  for (const note of notes) {
    const pitchClass = mod(note.pitch, 12);
    const duration = Math.max(0.06, note.t_off - note.t_on);
    const velocity = 0.5 + velocityToUnit(note.velocity) * 0.5;
    weights[pitchClass] += duration * velocity;
  }

  return weights;
}

export function inferKeySignature(notes: MelodicNote[]): KeySignatureAnalysis | null {
  if (!notes.length) return null;

  const weights = buildPitchClassWeights(notes);
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  if (totalWeight <= 1e-9) return null;

  let best: KeySignatureAnalysis | null = null;
  let runnerUpScore = Number.NEGATIVE_INFINITY;

  const consumeCandidate = (
    tonic: string,
    mode: "major" | "minor",
    pitchClass: number,
    accidentalKind: KeySignatureKind,
    accidentals: string[]
  ) => {
    const template = mode === "major" ? MAJOR_PROFILE : MINOR_PROFILE;
    const score = cosineSimilarity(weights, template, pitchClass);
    const noteLabelStyle: NoteLabelStyle = accidentalKind === "flat" ? "flat" : accidentalKind === "sharp" ? "sharp" : "dual";
    const candidate: KeySignatureAnalysis = {
      tonic,
      mode,
      pitchClass,
      label: `${tonic} ${mode}`,
      accidentalKind,
      accidentalCount: accidentals.length,
      accidentals,
      noteLabelStyle,
      score,
      confidence: 0,
    };

    if (!best || score > best.score) {
      if (best) runnerUpScore = Math.max(runnerUpScore, best.score);
      best = candidate;
    } else {
      runnerUpScore = Math.max(runnerUpScore, score);
    }
  };

  for (const [tonic, sig] of Object.entries(MAJOR_SIGNATURES)) {
    consumeCandidate(tonic, "major", sig.pitchClass, sig.accidentalKind, sig.accidentals);
  }

  for (const [tonic, sig] of Object.entries(MINOR_SIGNATURES)) {
    consumeCandidate(tonic, "minor", sig.pitchClass, sig.accidentalKind, sig.accidentals);
  }

  const resolvedBest = best as KeySignatureAnalysis | null;
  if (!resolvedBest) return null;

  const gap = Math.max(0, resolvedBest.score - Math.max(0, runnerUpScore));
  const confidence = clamp(0.52 + gap * 1.9, 0.52, 0.99);
  return { ...resolvedBest, confidence };
}

function buildKeyboardLayout(x0: number, width: number): KeyboardLayout {
  const whiteKeyCount = 52;
  const whiteKeyWidth = width / whiteKeyCount;
  const blackKeyWidth = whiteKeyWidth * 0.62;

  const byMidi = new Map<number, KeyboardKey>();
  const white: KeyboardKey[] = [];
  const black: KeyboardKey[] = [];
  let whiteIndex = 0;

  for (let midi = 21; midi <= 108; midi += 1) {
    const blackKey = isBlackKey(midi);
    if (!blackKey) {
      const x = x0 + whiteIndex * whiteKeyWidth;
      const key: KeyboardKey = {
        midi,
        x,
        w: whiteKeyWidth,
        centerX: x + whiteKeyWidth * 0.5,
        isBlack: false,
      };
      byMidi.set(midi, key);
      white.push(key);
      whiteIndex += 1;
      continue;
    }

    const x = x0 + whiteIndex * whiteKeyWidth - blackKeyWidth * 0.5;
    const key: KeyboardKey = {
      midi,
      x,
      w: blackKeyWidth,
      centerX: x + blackKeyWidth * 0.5,
      isBlack: true,
    };
    byMidi.set(midi, key);
    black.push(key);
  }

  return { byMidi, white, black };
}

function noteBodyColor(blackKey: boolean, approach: number): string {
  const cool = blackKey ? ([155, 126, 255] as const) : ([126, 238, 195] as const);
  const hot = blackKey ? ([255, 157, 214] as const) : ([255, 184, 91] as const);
  return rgbToCss(mixRgb(cool, hot, approach), 0.95);
}

function noteGlowColor(blackKey: boolean, approach: number, velocity: number): string {
  const glowBase = blackKey ? ([197, 170, 255] as const) : ([194, 255, 229] as const);
  const glowHot = blackKey ? ([255, 220, 244] as const) : ([255, 229, 179] as const);
  return rgbToCss(mixRgb(glowBase, glowHot, approach), 0.15 + velocity * 0.28);
}

export class TabRenderer {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private resizeObserver: ResizeObserver;
  private track: MelodicTrackSelection | null = null;
  private tuning: Tuning | null = null;
  private role: InstrumentRole = "melodic";
  private keySignature: KeySignatureAnalysis | null = null;

  /** Seconds of lookahead visible to the right of the hit line. */
  private windowSec = 4.0;
  /** Fraction of canvas width where the hit line sits (from left). */
  private hitLineFrac = 0.15;
  private pianoLookAheadSec = 7.0;
  private pianoLookBehindSec = 0.35;

  constructor(container: HTMLElement) {
    this.container = container;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "tabCanvas";
    this.canvas.width = container.clientWidth || 800;
    this.canvas.height = container.clientHeight || 180;
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;

    this.resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return;
      this.canvas.width = Math.max(1, Math.floor(entry.contentRect.width));
      this.canvas.height = Math.max(1, Math.floor(entry.contentRect.height));
    });
    this.resizeObserver.observe(container);
  }

  setTrack(track: MelodicTrackSelection | null): void {
    this.track = track;
    this.role = track?.role ?? "melodic";

    if (track?.role === "bass") {
      this.tuning = TUNING_BASS_STANDARD;
    } else if (track?.role === "rhythm_guitar" || track?.role === "lead_guitar") {
      this.tuning = TUNING_GUITAR_STANDARD;
    } else {
      this.tuning = null;
    }

    const pianoMode = !this.tuning && Boolean(track);
    this.container.classList.toggle("isPianoMode", pianoMode);
    this.canvas.classList.toggle("isPianoMode", pianoMode);
    this.keySignature = pianoMode && track ? inferKeySignature(track.notes) : null;
  }

  render(timeSec: number, opts: PianoRenderOptions = {}): void {
    const { ctx, canvas } = this;
    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, w, h);

    if (!this.track || this.track.notes.length === 0) {
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = "14px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("No notes for this instrument", w * 0.5, h * 0.5);
      return;
    }

    if (this.tuning) {
      this.renderTab(timeSec);
      return;
    }

    this.renderPianoRoll(timeSec, opts);
  }

  private renderTab(t: number): void {
    const { ctx, canvas, tuning, track } = this;
    if (!tuning || !track) return;

    const w = canvas.width;
    const h = canvas.height;
    const numStrings = tuning.strings.length;
    const yPad = 20;
    const stringSpacing = (h - yPad * 2) / Math.max(1, numStrings - 1);

    ctx.strokeStyle = STRING_COLOR;
    ctx.lineWidth = 1;
    for (let s = 0; s < numStrings; s += 1) {
      const y = yPad + s * stringSpacing;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    const hitX = w * this.hitLineFrac;
    ctx.strokeStyle = HIT_LINE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(hitX, 0);
    ctx.lineTo(hitX, h);
    ctx.stroke();

    const tStart = t - 0.5;
    const tEnd = t + this.windowSec;
    const color = ROLE_COLORS[this.role];
    const glow = ROLE_GLOW_COLORS[this.role];

    ctx.font = "700 13px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const note of track.notes) {
      if (note.t_on > tEnd || note.t_off < tStart) continue;
      const fretInfo = pitchToFret(note.pitch, tuning);
      if (!fretInfo) continue;

      const x = hitX + ((note.t_on - t) / this.windowSec) * (w - hitX);
      const y = yPad + fretInfo.string * stringSpacing;
      const dist = Math.abs(note.t_on - t);
      const alpha = dist < 0.1 ? 1.0 : Math.max(0.3, 1.0 - dist / this.windowSec);

      ctx.save();
      ctx.globalAlpha = alpha * 0.58;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, 16, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      const pillW = 22;
      const pillH = 18;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      roundRectPath(ctx, x - pillW * 0.5, y - pillH * 0.5, pillW, pillH, 5);
      ctx.fill();
      ctx.fillStyle = "#051018";
      ctx.fillText(String(fretInfo.fret), x, y);
      ctx.restore();
    }

    ctx.save();
    ctx.fillStyle = color;
    ctx.font = "700 11px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(track.trackName.toUpperCase(), 8, 4);
    ctx.restore();
  }

  private renderPianoRoll(t: number, opts: PianoRenderOptions): void {
    const { ctx, canvas, track } = this;
    if (!track) return;

    const w = canvas.width;
    const h = canvas.height;
    const layoutPadX = 18;
    const rollTop = 16;
    const keyboardHeight = clamp(h * 0.23, 78, 108);
    const keyboardTop = h - keyboardHeight - 12;
    const rollBottom = keyboardTop - 12;
    const rollHeight = Math.max(80, rollBottom - rollTop);
    const hitY = rollBottom;
    const keyboardWidth = Math.max(60, w - layoutPadX * 2);
    const keyboard = buildKeyboardLayout(layoutPadX, keyboardWidth);
    const pxPerSec = rollHeight / this.pianoLookAheadSec;
    const activeKeys = new Map<number, number>();
    const liveKeys = new Map<number, { intensity: number; heldBySustain: boolean }>();
    const noteStyle = this.keySignature?.noteLabelStyle ?? "dual";

    const bgGrad = ctx.createLinearGradient(0, 0, 0, h);
    bgGrad.addColorStop(0, "#0f1520");
    bgGrad.addColorStop(1, "#0a1018");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, w, h);

    ctx.fillStyle = PANEL_COLOR;
    roundRectPath(ctx, 8, 8, w - 16, rollBottom - 2, 14);
    ctx.fill();

    for (const key of keyboard.white) {
      ctx.fillStyle = mod(Math.floor((key.midi - 21) / 2), 2) === 0 ? "rgba(255,255,255,0.028)" : "rgba(255,255,255,0.018)";
      ctx.fillRect(key.x, rollTop, key.w, rollHeight);
      ctx.strokeStyle = "rgba(255,255,255,0.045)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(key.x + key.w, rollTop);
      ctx.lineTo(key.x + key.w, rollBottom);
      ctx.stroke();
    }

    for (const key of keyboard.black) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.18)";
      ctx.fillRect(key.x, rollTop, key.w, rollHeight);
      ctx.strokeStyle = "rgba(255,255,255,0.04)";
      ctx.strokeRect(key.x, rollTop, key.w, rollHeight);
    }

    const bpm = Number.isFinite(opts.bpm) && (opts.bpm as number) > 0 ? (opts.bpm as number) : 120;
    const beatsPerBar = Math.max(1, opts.timeSignature?.[0] ?? 4);
    const subdivisionSec = (60 / bpm) * 0.5;
    const firstSubdivision = Math.floor((t - this.pianoLookBehindSec) / subdivisionSec) - 1;
    const lastSubdivision = Math.ceil((t + this.pianoLookAheadSec) / subdivisionSec) + 1;

    for (let subdivision = firstSubdivision; subdivision <= lastSubdivision; subdivision += 1) {
      const lineTime = subdivision * subdivisionSec;
      const y = hitY - (lineTime - t) * pxPerSec;
      if (y < rollTop || y > hitY + 1) continue;

      const isBeat = mod(subdivision, 2) === 0;
      const beatIndex = Math.trunc(subdivision / 2);
      const isMeasure = isBeat && mod(beatIndex, beatsPerBar) === 0;

      ctx.strokeStyle = isMeasure
        ? "rgba(245, 248, 255, 0.22)"
        : isBeat
          ? "rgba(214, 227, 255, 0.14)"
          : "rgba(214, 227, 255, 0.07)";
      ctx.lineWidth = isMeasure ? 1.8 : isBeat ? 1.1 : 1;
      ctx.beginPath();
      ctx.moveTo(layoutPadX, y);
      ctx.lineTo(w - layoutPadX, y);
      ctx.stroke();
    }

    const hitBandGrad = ctx.createLinearGradient(0, hitY - 16, 0, hitY + 10);
    hitBandGrad.addColorStop(0, "rgba(255,255,255,0.00)");
    hitBandGrad.addColorStop(0.42, "rgba(255,255,255,0.12)");
    hitBandGrad.addColorStop(1, "rgba(255,255,255,0.02)");
    ctx.fillStyle = hitBandGrad;
    ctx.fillRect(layoutPadX, hitY - 16, keyboardWidth, 28);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.88)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(layoutPadX, hitY);
    ctx.lineTo(layoutPadX + keyboardWidth, hitY);
    ctx.stroke();

    for (const note of track.notes) {
      if (note.t_off < t - this.pianoLookBehindSec || note.t_on > t + this.pianoLookAheadSec) continue;

      const key = keyboard.byMidi.get(note.pitch);
      if (!key) continue;

      const velocity = velocityToUnit(note.velocity);
      const dt = note.t_on - t;
      const approach = clamp(1 - dt / this.pianoLookAheadSec, 0, 1);
      const noteTop = hitY - (note.t_off - t) * pxPerSec;
      const noteBottom = hitY - (note.t_on - t) * pxPerSec;
      const visibleTop = clamp(noteTop, rollTop, hitY);
      const visibleBottom = clamp(noteBottom, rollTop, hitY);
      const height = Math.max(6, visibleBottom - visibleTop);
      const noteX = key.x + (key.isBlack ? 1.5 : 1.2);
      const noteW = Math.max(4, key.w - (key.isBlack ? 3 : 2.4));
      const glowColor = noteGlowColor(key.isBlack, approach, velocity);
      const bodyColor = noteBodyColor(key.isBlack, approach);

      ctx.fillStyle = glowColor;
      roundRectPath(ctx, noteX - 2, visibleTop - 2, noteW + 4, height + 4, Math.min(8, noteW * 0.4));
      ctx.fill();

      ctx.fillStyle = bodyColor;
      roundRectPath(ctx, noteX, visibleTop, noteW, height, Math.min(7, noteW * 0.4));
      ctx.fill();

      ctx.fillStyle = "rgba(255,255,255,0.18)";
      roundRectPath(ctx, noteX + 1, visibleTop + 1, Math.max(2, noteW - 2), Math.max(2, height * 0.15), 2);
      ctx.fill();

      if (dt <= 0.08 && note.t_off >= t - 0.02) {
        activeKeys.set(note.pitch, Math.max(activeKeys.get(note.pitch) ?? 0, 0.35 + velocity * 0.65));
      }
    }

    for (const note of opts.liveInputNotes ?? []) {
      const pitch = Math.trunc(note.pitch);
      const key = keyboard.byMidi.get(pitch);
      if (!key) continue;
      const velocity =
        typeof note.velocityUnit === "number"
          ? clamp(note.velocityUnit, 0, 1)
          : typeof note.velocity === "number"
            ? velocityToUnit(note.velocity)
            : 0.8;
      const intensity = Math.max(note.heldBySustain && !note.isPressed ? 0.42 : 0.78, velocity);
      liveKeys.set(pitch, {
        intensity: Math.max(liveKeys.get(pitch)?.intensity ?? 0, intensity),
        heldBySustain: Boolean(note.heldBySustain && !note.isPressed),
      });
      activeKeys.set(pitch, Math.max(activeKeys.get(pitch) ?? 0, intensity));
    }

    const accentLabel = this.keySignature
      ? this.keySignature.accidentalCount === 0
        ? "0 accidentals"
        : `${this.keySignature.accidentalCount} ${this.keySignature.accidentalKind}${this.keySignature.accidentalCount === 1 ? "" : "s"}`
      : "signature unknown";
    const accentList = this.keySignature?.accidentals.length ? this.keySignature.accidentals.join(", ") : "none";

    ctx.fillStyle = "rgba(8, 15, 25, 0.74)";
    roundRectPath(ctx, 16, 16, 178, 54, 11);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.lineWidth = 1;
    roundRectPath(ctx, 16, 16, 178, 54, 11);
    ctx.stroke();

    ctx.fillStyle = "rgba(240,246,255,0.92)";
    ctx.font = "700 12px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(track.trackName.toUpperCase(), 28, 26);

    ctx.fillStyle = "rgba(210,223,246,0.84)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(this.keySignature?.label ?? "Key signature unavailable", 28, 43);
    ctx.fillStyle = "rgba(192,208,234,0.64)";
    ctx.fillText(`${accentLabel}  •  ${accentList}`, 28, 58);

    ctx.fillStyle = "rgba(240,246,255,0.82)";
    ctx.font = "11px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "right";
    ctx.fillText(`${bpm.toFixed(1)} BPM`, w - 18, 26);
    if (liveKeys.size > 0) {
      const liveLabel = `${liveKeys.size} MIDI key${liveKeys.size === 1 ? "" : "s"} down`;
      ctx.fillStyle = "rgba(103,247,255,0.92)";
      ctx.fillText(liveLabel, w - 18, 42);
    }

    for (const key of keyboard.white) {
      const intensity = activeKeys.get(key.midi) ?? 0;
      const live = liveKeys.get(key.midi);
      ctx.fillStyle = "#f3efe7";
      ctx.fillRect(key.x, keyboardTop, key.w, keyboardHeight);

      if (intensity > 0) {
        const fill = noteBodyColor(false, 0.72);
        ctx.fillStyle = fill.replace(", 0.95)", `, ${0.28 + intensity * 0.42})`);
        ctx.fillRect(key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), keyboardHeight - 2);

        ctx.fillStyle = noteGlowColor(false, 0.9, intensity);
        ctx.fillRect(key.x - 2, keyboardTop - 6, key.w + 4, 10);
      }

      if (live) {
        ctx.fillStyle = live.heldBySustain ? "rgba(105, 231, 255, 0.26)" : "rgba(34, 211, 238, 0.42)";
        ctx.fillRect(key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), keyboardHeight - 2);
        ctx.strokeStyle = "rgba(103, 247, 255, 0.92)";
        ctx.lineWidth = Math.max(2, key.w * 0.08);
        ctx.strokeRect(key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), keyboardHeight - 2);
      }

      ctx.strokeStyle = "rgba(8, 12, 18, 0.85)";
      ctx.lineWidth = 1;
      ctx.strokeRect(key.x, keyboardTop, key.w, keyboardHeight);

      if (mod(key.midi, 12) === 0) {
        ctx.fillStyle = intensity > 0 ? "rgba(12,20,28,0.92)" : "rgba(22,30,38,0.52)";
        ctx.font = "10px ui-monospace, SFMono-Regular, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(midiToNoteName(key.midi, noteStyle), key.centerX, h - 6);
      }
    }

    const blackKeyHeight = keyboardHeight * 0.62;
    for (const key of keyboard.black) {
      const intensity = activeKeys.get(key.midi) ?? 0;
      const live = liveKeys.get(key.midi);
      const blackGrad = ctx.createLinearGradient(0, keyboardTop, 0, keyboardTop + blackKeyHeight);
      blackGrad.addColorStop(0, "#171c24");
      blackGrad.addColorStop(1, "#04070d");
      ctx.fillStyle = blackGrad;
      roundRectPath(ctx, key.x, keyboardTop, key.w, blackKeyHeight, 4);
      ctx.fill();

      if (intensity > 0) {
        const glow = noteBodyColor(true, 0.85).replace(", 0.95)", `, ${0.34 + intensity * 0.34})`);
        ctx.fillStyle = glow;
        roundRectPath(ctx, key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), blackKeyHeight - 2, 4);
        ctx.fill();
      }

      if (live) {
        ctx.fillStyle = live.heldBySustain ? "rgba(105, 231, 255, 0.22)" : "rgba(34, 211, 238, 0.46)";
        roundRectPath(ctx, key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), blackKeyHeight - 2, 4);
        ctx.fill();
        ctx.strokeStyle = "rgba(116, 248, 255, 0.95)";
        ctx.lineWidth = 2;
        roundRectPath(ctx, key.x - 1, keyboardTop - 1, key.w + 2, blackKeyHeight + 2, 5);
        ctx.stroke();
      }

      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.lineWidth = 1;
      roundRectPath(ctx, key.x, keyboardTop, key.w, blackKeyHeight, 4);
      ctx.stroke();

      if (intensity > 0) {
        ctx.fillStyle = "rgba(250,252,255,0.92)";
        ctx.font = "8px ui-monospace, SFMono-Regular, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(noteNameForPitchClass(mod(key.midi, 12), noteStyle), key.centerX, keyboardTop + blackKeyHeight * 0.55);
      }
    }

    ctx.fillStyle = this.keySignature ? "rgba(255, 245, 214, 0.86)" : "rgba(210,223,246,0.70)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(this.keySignature?.label ?? "Piano roll", 18, keyboardTop - 8);
  }

  dispose(): void {
    this.resizeObserver.disconnect();
    this.container.classList.remove("isPianoMode");
    this.canvas.remove();
  }
}
