/**
 * tabRenderer.ts - Scrolling melodic note renderer for the game app.
 *
 * Supports two display modes:
 * 1) Tab view for fretted instruments (bass, guitar)
 * 2) Piano-roll view for keys and generic melodic tracks
 */

import { clampScrollSpeedMultiplier } from "@auralprimer/viz-sdk";
// Type aliases local to this package — structurally compatible with the
// host's chartLoader types. The host's setTrack() callsite supplies a
// MelodicTrackSelection from chartLoader; TypeScript's structural typing
// makes that interchangeable.
export type InstrumentRole = "bass" | "rhythm_guitar" | "lead_guitar" | "keys" | "vocals" | "melodic";
export type MelodicNote = {
  t_on: number;
  t_off: number;
  pitch: number;
  velocity: number;
  /** Zero-based string index, lowest/thickest string first. */
  string?: number;
  /** Fret number on `string`. */
  fret?: number;
  /** Compact alias for `string`, matching arrangement wire JSON. */
  s?: number;
  /** Compact alias for `fret`, matching arrangement wire JSON. */
  f?: number;
};
export type MelodicTrackSelection = { role: InstrumentRole; trackName: string; channel: number; notes: MelodicNote[] };
export type FretPosition = { string: number; fret: number };

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
  /**
   * Colour falling notes by pitch class instead of by the approach spectrum.
   *
   * Safe to spend hue here because on the lane time is already position: a
   * note's distance from the line says when it arrives, continuously, and the
   * violet-to-green ramp said the same thing a second time in a weaker
   * channel. The keys are the opposite case and keep their state colours.
   */
  noteColors?: boolean;
  timeSignature?: [number, number];
  liveInputNotes?: PianoLiveInputNote[];
  /**
   * Host "Note spacing" multiplier (TransportState.scrollSpeedMultiplier).
   * Higher = notes more spread out. Applied by shrinking the visible
   * look-ahead window so each second of song time occupies more pixels,
   * matching how the viz-* highway plugins scale. Tempo-lock preserved.
   */
  scrollSpeedMultiplier?: number;
  /**
   * Chord names for the chart, one per change (see core-music `chordLabels`).
   * Precomputed by the host rather than derived per frame: naming a chord scans
   * a template table, and doing that for every visible group every frame would
   * be wasted work on a value that never changes.
   */
  chordLabels?: { tSec: number; label: string }[];
  /**
   * Nashville Number System mode. When true, the piano roll labels notes
   * by their scale-degree number relative to the inferred song key
   * (1..7 for diatonic notes, b/# of the nearest degree for chromatic
   * notes) instead of note names. Falls back to note-name labels when the
   * key signature can't be inferred. Default: false (note names).
   */
  nashville?: boolean;
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
  vocals: "#c084fc",
  melodic: "#9dd7ff",
};

const ROLE_GLOW_COLORS: Record<InstrumentRole, string> = {
  bass: "rgba(255, 138, 61, 0.40)",
  rhythm_guitar: "rgba(32, 201, 151, 0.40)",
  lead_guitar: "rgba(255, 95, 122, 0.40)",
  keys: "rgba(255, 209, 102, 0.36)",
  vocals: "rgba(192, 132, 252, 0.34)",
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

function pitchToFret(pitch: number, tuning: Tuning): FretPosition | null {
  let best: FretPosition | null = null;

  for (let s = 0; s < tuning.strings.length; s += 1) {
    const fret = pitch - tuning.strings[s];
    if (fret < 0 || fret > 24) continue;
    if (!best || fret < best.fret) {
      best = { string: s, fret };
    }
  }

  return best;
}

function integerOrNull(value: number | undefined): number | null {
  return Number.isInteger(value) ? (value as number) : null;
}

function explicitFretPosition(note: MelodicNote, tuning: Tuning): FretPosition | null {
  const stringIndex = integerOrNull(note.string) ?? integerOrNull(note.s);
  const fret = integerOrNull(note.fret) ?? integerOrNull(note.f);
  if (stringIndex === null || fret === null) return null;
  if (stringIndex < 0 || stringIndex >= tuning.strings.length) return null;
  if (fret < 0 || fret > 36) return null;
  return { string: stringIndex, fret };
}

export function noteToFretPosition(note: MelodicNote, tuning: Tuning): FretPosition | null {
  return explicitFretPosition(note, tuning) ?? pitchToFret(note.pitch, tuning);
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

// Nashville Number System scale degrees, indexed by the semitone offset of
// the note above the tonic (0..11). Diatonic (major-scale) degrees map to
// plain numbers; chromatic degrees are spelled as a flat or sharp of the
// nearest degree, chosen by the key's note-label convention.
//
// Minor-key decision: relative-major numbering is NOT used. We number against
// the actual minor tonic using the natural-minor scale (1 2 b3 4 5 b6 b7) —
// the tonic is "1" and the minor third is "b3", which is what a minor-key
// chart shows. `pitchClass` from inferKeySignature is the minor tonic's pitch
// class, so no relative-major remapping is needed.
//
// The semitone -> label mapping is therefore the SAME in both modes: what
// changes between major and minor is which degrees are diatonic, not what each
// degree is called. One table per accidental style, used by both modes.
//
// The minor tables used to differ, and were wrong three ways: they labelled the
// minor third "3" (contradicting the comment above), the natural third "b4",
// and index 11 "b8" — which is not a Nashville degree at all. In A minor that
// spelled Bb as "#1"; a raised tonic is vanishingly rare, while b2 (the
// Neapolitan) is ordinary.
const NASHVILLE_SHARP = ["1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7"];
const NASHVILLE_FLAT = ["1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7"];

/**
 * Map a MIDI pitch to its Nashville scale-degree label relative to the
 * analysed key. Returns null when no key analysis is available so callers
 * can fall back to note names. Octave-independent (degree is the same in
 * every octave). Chromatic notes are spelled sharp/flat following the key's
 * `noteLabelStyle` (flat keys -> flats, otherwise sharps).
 */
export function pitchToNashville(pitch: number, analysis: KeySignatureAnalysis | null): string | null {
  if (!analysis) return null;
  const degree = mod(pitch - analysis.pitchClass, 12);
  // Minor keys read as flat-side even when the tonic has no accidental: b3, b6
  // and b7 are diatonic there, so spelling their neighbours with sharps fights
  // the key. A natural-tonic minor (A minor) would otherwise take the sharp
  // table and render Bb as "#1".
  const useFlat = analysis.noteLabelStyle === "flat" || analysis.mode === "minor";
  return (useFlat ? NASHVILLE_FLAT : NASHVILLE_SHARP)[degree];
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
    // For sharp / flat keys, follow the convention. For natural keys
    // (C major, A minor) the prior "dual" label style produced
    // contradictory output -- the HUD said "C major, 0 accidentals"
    // while every black-key label showed "C#/Db". Pick a single
    // convention (sharps) so the key signature read-out and the note
    // labels agree.
    const noteLabelStyle: NoteLabelStyle =
      accidentalKind === "flat" ? "flat" : accidentalKind === "sharp" ? "sharp" : "sharp";
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

/**
 * The key you play next.
 *
 * Its own colour rather than a sample of the falling-note gradient. The keys
 * used to borrow `noteBodyColor` at a fixed approach, which put a muted orange
 * over an off-white key (`#f3efe7`) and a dim violet over a near-black one --
 * both close enough to the key underneath that the board read as undifferen-
 * tiated. Green belongs to nothing else on the keyboard, so it cannot be
 * confused with the key itself, with a note falling toward it, or with the cyan
 * that means a key is being held right now.
 */
const KEY_READY_RGB = [86, 232, 133] as const;

function keyReadyFill(intensity: number, blackKey: boolean): string {
  // Black keys need more of it: the same wash over near-black reads as grey,
  // where over an off-white key it already reads as green.
  const alpha = blackKey ? 0.5 + intensity * 0.42 : 0.42 + intensity * 0.44;
  return rgbToCss(KEY_READY_RGB, alpha);
}

/**
 * Chromatic note colours, indexed by pitch class from C.
 *
 * The Boomwhacker convention: hue names the pitch and the octave repeats it,
 * so every C is red wherever it sits. Its value is that it is not ours -- the
 * same coding is on classroom tubes, keyboard stickers and beginner method
 * books, so what is on screen matches what is already on the instrument.
 *
 * The twelve run the hue circle once per octave, which makes a semitone step a
 * small hue step: the colour wheel and the keyboard move in the same
 * direction. It also means C and C-sharp are both red. That is the palette's
 * one genuinely hard discrimination, and it is covered by a channel we already
 * spend elsewhere -- a natural is a wide note and its sharp is a narrow one,
 * because note width follows key width. Hue names it, width tells it from its
 * own sharp.
 */
const NOTE_COLORS: readonly (readonly [number, number, number])[] = [
  [232, 36, 30],   // C
  [240, 86, 60],   // C#
  [245, 130, 32],  // D
  [253, 181, 21],  // D#
  [245, 224, 29],  // E
  [141, 198, 63],  // F
  [57, 181, 74],   // F#
  [0, 167, 157],   // G
  [46, 127, 193],  // G#
  [92, 78, 158],   // A
  [146, 39, 143],  // A#
  [236, 28, 142],  // B
];

/** The note colour for a MIDI pitch, brightened as it approaches the line. */
function pitchClassColor(pitch: number, approach: number): string {
  const base = NOTE_COLORS[mod(pitch, 12)];
  // Hue is spent on identity, so approach rides brightness instead: a distant
  // note is its own colour, dimmed. Mixing toward white rather than raising
  // alpha keeps the hue recognisable at every distance, which is the whole
  // reason for colouring them.
  return rgbToCss(mixRgb(base, [255, 255, 255] as const, approach * 0.34),
                  0.55 + approach * 0.4);
}

function pitchClassGlow(pitch: number, approach: number, velocity: number): string {
  const base = NOTE_COLORS[mod(pitch, 12)];
  return rgbToCss(mixRgb(base, [255, 255, 255] as const, 0.35),
                  0.12 + approach * 0.14 + velocity * 0.2);
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
      this.renderTab(timeSec, opts);
      return;
    }

    this.renderPianoRoll(timeSec, opts);
  }

  private renderTab(t: number, opts: PianoRenderOptions = {}): void {
    const { ctx, canvas, tuning, track } = this;
    if (!tuning || !track) return;

    const w = canvas.width;
    const h = canvas.height;
    const scrollMul = clampScrollSpeedMultiplier(opts.scrollSpeedMultiplier);
    const windowSec = this.windowSec / scrollMul;
    const numStrings = tuning.strings.length;

    // A receding highway rather than a sideways belt.
    //
    // Scrolling moved every note the same number of pixels per frame however
    // far off it was, so the whole field smeared at once and the fret numbers
    // -- which are the entire point of this view -- went with it. Under
    // perspective a note an octave of time away crawls, and only the notes
    // about to be played move quickly. The eye reads the far ones because they
    // are nearly still, and the near ones because they are large.
    //
    // Depth is 1/(1 + k*u): u is time-to-hit normalised over the window, so
    // u=0 is the hit line and u=1 the horizon. Everything -- lane spread, note
    // size, glyph size -- is that one number, which is what makes it read as
    // distance rather than as things being drawn smaller.
    const DEPTH = 5.2;
    const farScale = 1 / (1 + DEPTH);
    const horizonY = h * 0.06;
    const hitY = h * (1 - this.hitLineFrac);
    const cx = w * 0.5;
    // Near-plane half-width. The lanes reach most of the canvas at the hit
    // line, which is where the player is actually looking.
    const halfSpread = w * 0.44;

    const depthScale = (u: number): number => 1 / (1 + DEPTH * clamp(u, 0, 1));
    // Normalised so u=1 lands exactly on the horizon whatever DEPTH is.
    const depthY = (u: number): number =>
      hitY - ((1 - depthScale(u)) / (1 - farScale)) * (hitY - horizonY);
    const laneOffset = (s: number): number =>
      numStrings <= 1 ? 0 : ((s / (numStrings - 1)) * 2 - 1) * halfSpread;
    const laneX = (s: number, u: number): number => cx + laneOffset(s) * depthScale(u);

    // Lanes, drawn as the rails they are: wide and bright at the hit line,
    // converging and faint at the horizon, so depth is legible with no notes
    // on screen at all.
    for (let s = 0; s < numStrings; s += 1) {
      const steps = 24;
      for (let i = 0; i < steps; i += 1) {
        const u0 = i / steps;
        const u1 = (i + 1) / steps;
        const near = 1 - u0;
        ctx.strokeStyle = STRING_COLOR;
        ctx.globalAlpha = 0.25 + near * 0.75;
        ctx.lineWidth = 0.5 + depthScale(u0) * 1.6;
        ctx.beginPath();
        ctx.moveTo(laneX(s, u0), depthY(u0));
        ctx.lineTo(laneX(s, u1), depthY(u1));
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // The hit line spans the near plane, across the lanes rather than through
    // them, because it is a moment in time and they are pitches.
    ctx.strokeStyle = HIT_LINE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx - halfSpread, hitY);
    ctx.lineTo(cx + halfSpread, hitY);
    ctx.stroke();

    const tStart = t - 0.5;
    const tEnd = t + windowSec;
    const color = ROLE_COLORS[this.role];
    const glow = ROLE_GLOW_COLORS[this.role];

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    // Far to near, so a nearer note occludes the one behind it the way the
    // distance already implies.
    const visible: { note: MelodicNote; fret: FretPosition; u: number }[] = [];
    for (const note of track.notes) {
      if (note.t_on > tEnd || note.t_off < tStart) continue;
      const fretInfo = noteToFretPosition(note, tuning);
      if (!fretInfo) continue;
      visible.push({ note, fret: fretInfo, u: (note.t_on - t) / windowSec });
    }
    visible.sort((a, b) => b.u - a.u);

    for (const { fret: fretInfo, u } of visible) {
      const uc = clamp(u, 0, 1);
      const scale = depthScale(uc);
      // Whole pixels, still: a glyph landing on a different sub-pixel offset
      // each frame is anti-aliased differently each frame, which reads as
      // shimmer. Perspective slows the far ones down but does not put them on
      // the grid.
      const x = Math.round(laneX(fretInfo.string, uc));
      const y = Math.round(depthY(uc));

      // Held past the line rather than clipped: u goes negative for a note
      // being played, and the pill staying put is the confirmation.
      // Floor lands at 0.62 on the horizon, which is where the old fixed
      // window had to be pinned to stay readable -- perspective earns it back
      // instead of asserting it.
      const alpha = u < 0 ? 1 : 0.55 + 0.45 * scale;

      ctx.save();
      ctx.globalAlpha = alpha * 0.5;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, Math.max(4, 17 * scale), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      const pillW = Math.round(Math.max(9, 28 * scale));
      const pillH = Math.round(Math.max(8, 23 * scale));
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      roundRectPath(ctx, x - pillW * 0.5, y - pillH * 0.5, pillW, pillH, Math.max(2, 5 * scale));
      ctx.fill();

      // Below about 9px the digits stop being digits, so the pill carries the
      // note on its own out there. It still says WHEN and WHICH STRING, which
      // is what a note that far away is for; the fret arrives with the size.
      const fontPx = Math.round(16 * scale);
      if (fontPx >= 9) {
        ctx.fillStyle = "#051018";
        ctx.font = `800 ${fontPx}px ui-monospace, SFMono-Regular, Consolas, monospace`;
        ctx.fillText(String(fretInfo.fret), x, y);
      }
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
    // Shrink the look-ahead/behind windows as spacing increases so each
    // second occupies more pixels (matches the viz-* highway scaling).
    const scrollMul = clampScrollSpeedMultiplier(opts.scrollSpeedMultiplier);
    const lookAheadSec = this.pianoLookAheadSec / scrollMul;
    const lookBehindSec = this.pianoLookBehindSec / scrollMul;
    const pxPerSec = rollHeight / lookAheadSec;
    const activeKeys = new Map<number, number>();
    const liveKeys = new Map<number, { intensity: number; heldBySustain: boolean }>();
    const noteStyle = this.keySignature?.noteLabelStyle ?? "dual";
    // Nashville mode is only meaningful when we have a key to number
    // against; otherwise transparently fall back to note-name labels.
    const nashville = Boolean(opts.nashville) && Boolean(this.keySignature);

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
    const firstSubdivision = Math.floor((t - lookBehindSec) / subdivisionSec) - 1;
    const lastSubdivision = Math.ceil((t + lookAheadSec) / subdivisionSec) + 1;

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

    // Hit zone -- the "play the note now" line. Tuned to match the
    // viz-drum-highway treatment so the user reads the same "this is
    // where notes land" cue across instruments. The piano roll's hit
    // zone sits right above the keyboard, so the cue has to compete
    // visually with both the falling notes and the keyboard itself.
    //
    // Layered (back to front):
    //   1. A taller, role-tinted glow band above the line.
    //   2. A wider white outer stroke (the "play here" lane edge).
    //   3. A narrower role-colored accent stroke on top (the actual
    //      "play now" marker -- saturated gold for keys, matches the
    //      note color so the eye reads it as "notes hit HERE").
    //   4. A "PLAY HERE" label on the side, low-priority but explicit.
    const hitBandH = 36;
    const hitBandY = hitY - hitBandH * 0.78;
    const roleAccent = ROLE_COLORS[this.role];
    const roleGlowRgba = ROLE_GLOW_COLORS[this.role];
    const hitBandGrad = ctx.createLinearGradient(0, hitBandY, 0, hitBandY + hitBandH);
    hitBandGrad.addColorStop(0, "rgba(255,255,255,0.00)");
    hitBandGrad.addColorStop(0.55, roleGlowRgba);
    hitBandGrad.addColorStop(1, "rgba(255,255,255,0.02)");
    ctx.fillStyle = hitBandGrad;
    roundRectPath(
      ctx,
      layoutPadX,
      hitBandY,
      keyboardWidth,
      hitBandH,
      10,
    );
    ctx.fill();

    ctx.save();
    ctx.shadowColor = roleAccent;
    ctx.shadowBlur = 18;
    ctx.strokeStyle = "rgba(244, 250, 255, 0.92)";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(layoutPadX, hitY);
    ctx.lineTo(layoutPadX + keyboardWidth, hitY);
    ctx.stroke();
    ctx.restore();

    ctx.strokeStyle = roleAccent;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(layoutPadX + 4, hitY);
    ctx.lineTo(layoutPadX + keyboardWidth - 4, hitY);
    ctx.stroke();

    ctx.save();
    ctx.fillStyle = "rgba(223,237,255,0.72)";
    ctx.font = "700 10px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    ctx.fillText("PLAY HERE", layoutPadX + keyboardWidth, hitBandY - 4);
    ctx.restore();

    // Nashville labels are collected here and drawn after the note pass: drawn
    // inline, a later note's body would paint over an earlier note's number.
    const nashvilleLabels: { left: number; right: number; y: number; text: string; color: string }[] = [];

    for (const note of track.notes) {
      if (note.t_off < t - lookBehindSec || note.t_on > t + lookAheadSec) continue;

      const key = keyboard.byMidi.get(note.pitch);
      if (!key) continue;

      const velocity = velocityToUnit(note.velocity);
      const dt = note.t_on - t;
      const approach = clamp(1 - dt / lookAheadSec, 0, 1);
      const noteTop = hitY - (note.t_off - t) * pxPerSec;
      const noteBottom = hitY - (note.t_on - t) * pxPerSec;
      // A note whose whole span sits past the hit line has nothing left to
      // draw. It still reaches here because the cull above deliberately keeps
      // notes for lookBehindSec after they end, and the clamping below then
      // collapses the note onto the hit line itself: a few pixels of glow, plus
      // — in Nashville mode — a scale degree parked just above the line with no
      // note under it to explain it. Same at the top of the roll for a note that
      // has not entered yet.
      if (noteTop >= hitY || noteBottom <= rollTop) continue;

      const visibleTop = clamp(noteTop, rollTop, hitY);
      const visibleBottom = clamp(noteBottom, rollTop, hitY);
      const height = Math.max(6, visibleBottom - visibleTop);
      const noteX = key.x + (key.isBlack ? 1.5 : 1.2);
      const noteW = Math.max(4, key.w - (key.isBlack ? 3 : 2.4));
      const useNoteColors = opts.noteColors === true;
      const glowColor = useNoteColors
        ? pitchClassGlow(note.pitch, approach, velocity)
        : noteGlowColor(key.isBlack, approach, velocity);
      const bodyColor = useNoteColors
        ? pitchClassColor(note.pitch, approach)
        : noteBodyColor(key.isBlack, approach);

      // Notes fall downward. The BOTTOM of the pill is the onset moment
      // (the note's t_on, which arrives at hitY first). The body of the
      // pill extending upward represents the sustain.
      //
      // Per user request: render the onset as a solid bright cap and
      // the hold body as a more transparent fill so the two read as
      // visually distinct events instead of a single solid column.
      // Nashville mode needs a taller full-width cap to fit the degree glyph
      // legibly (the thin-hold stem can't hold text).
      const onsetCapHeight = nashville
        ? Math.min(18, Math.max(14, height * 0.45))
        : Math.min(10, Math.max(4, height * 0.35));
      const holdTop = visibleTop;
      const holdBottom = Math.max(holdTop, visibleBottom - onsetCapHeight);
      const onsetTop = holdBottom;
      const onsetBottomVisible = visibleBottom;

      // The sustain renders as a THIN centered stem; the onset cap keeps
      // the full key width. The width contrast (not just brightness) is
      // what makes the attack read as a distinct event from the ring-out.
      const holdW = Math.max(2, noteW * 0.42);
      const holdX = noteX + (noteW - holdW) * 0.5;
      const hasHold = holdBottom > holdTop + 1;

      // Soft outer glow halo. The onset gets a full-width halo so the
      // attack pops; the hold only gets a thin halo matching its stem,
      // so the glow doesn't fatten the sustain back up.
      ctx.fillStyle = glowColor;
      if (hasHold) {
        roundRectPath(
          ctx,
          holdX - 1.5,
          visibleTop - 2,
          holdW + 3,
          holdBottom - holdTop + 4,
          Math.min(6, holdW * 0.5),
        );
        ctx.fill();
      }
      roundRectPath(
        ctx,
        noteX - 2,
        onsetTop - 2,
        noteW + 4,
        onsetBottomVisible - onsetTop + 4,
        Math.min(8, noteW * 0.4),
      );
      ctx.fill();

      // Hold body: thin transparent stem over the sustain portion.
      // Reduced opacity so the eye reads it as "the note is still
      // ringing, but the attack has already passed."
      if (hasHold) {
        ctx.save();
        ctx.globalAlpha = 0.40;
        ctx.fillStyle = bodyColor;
        roundRectPath(
          ctx,
          holdX,
          holdTop,
          holdW,
          holdBottom - holdTop,
          Math.min(5, holdW * 0.5),
        );
        ctx.fill();
        ctx.restore();

        // Inner gradient stripe to suggest motion along the sustain.
        ctx.save();
        ctx.globalAlpha = 0.28;
        const holdGrad = ctx.createLinearGradient(0, holdTop, 0, holdBottom);
        holdGrad.addColorStop(0, "rgba(255,255,255,0.00)");
        holdGrad.addColorStop(1, "rgba(255,255,255,0.20)");
        ctx.fillStyle = holdGrad;
        ctx.fillRect(holdX + 0.5, holdTop, Math.max(1, holdW - 1), holdBottom - holdTop);
        ctx.restore();
      }

      // Onset cap: bright solid full-width bar at the bottom. This is the
      // "play me now" moment -- visually loud and the full key width so the
      // user's eye locks on as it descends toward the hit line.
      ctx.fillStyle = bodyColor;
      roundRectPath(
        ctx,
        noteX,
        onsetTop,
        noteW,
        Math.max(2, onsetBottomVisible - onsetTop),
        Math.min(6, noteW * 0.4),
      );
      ctx.fill();

      // Top-edge highlight on the onset cap so the leading edge reads
      // as a distinct bar even at small sizes.
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.fillRect(noteX + 1, onsetTop, Math.max(1, noteW - 2), 1.6);

      // Nashville mode: queue the scale degree to sit BESIDE the note. Stamped
      // inside the onset cap it was limited to the cap's 12-21px width, which
      // capped the type at 8-10px -- too small to read while the notes move.
      // Alongside, it can be half again as large on a backing pill.
      if (nashville) {
        const degree = pitchToNashville(note.pitch, this.keySignature);
        if (degree) {
          nashvilleLabels.push({
            left: noteX,
            right: noteX + noteW,
            y: clamp((onsetTop + onsetBottomVisible) * 0.5, rollTop + 12, hitY - 12),
            text: degree,
            color: bodyColor,
          });
        }
      }

      if (dt <= 0.08 && note.t_off >= t - 0.02) {
        activeKeys.set(note.pitch, Math.max(activeKeys.get(note.pitch) ?? 0, 0.35 + velocity * 0.65));
      }
    }

    // Chord names, riding down the left margin alongside the notes they belong
    // to, so the name arrives at the hit line at the same moment the chord does.
    if (opts.chordLabels && opts.chordLabels.length) {
      ctx.save();
      ctx.font = "800 15px ui-monospace, SFMono-Regular, Consolas, monospace";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (const chord of opts.chordLabels) {
        const dt = chord.tSec - t;
        if (dt < -lookBehindSec || dt > lookAheadSec) continue;

        const y = hitY - dt * pxPerSec;
        if (y < rollTop - 10 || y > hitY + 10) continue;

        // Fade in as it approaches, so the eye is drawn to what is imminent
        // rather than to a wall of equally-loud text.
        const approach = clamp(1 - dt / lookAheadSec, 0, 1);
        const alpha = 0.35 + approach * 0.65;

        const width = ctx.measureText(chord.label).width + 12;
        ctx.globalAlpha = alpha * 0.72;
        ctx.fillStyle = "rgba(3,7,13,0.9)";
        roundRectPath(ctx, layoutPadX + 2, y - 11, width, 22, 5);
        ctx.fill();

        ctx.globalAlpha = alpha;
        ctx.fillStyle = "#a3ff12";
        ctx.fillText(chord.label, layoutPadX + 8, y);
      }
      ctx.restore();
    }

    // Draw the queued Nashville degrees on top of every note body.
    if (nashvilleLabels.length) {
      const fontPx = 14;
      const padX = 5;
      const boxH = fontPx + 6;
      const rollRight = layoutPadX + keyboardWidth;
      ctx.save();
      ctx.font = `800 ${fontPx}px ui-monospace, SFMono-Regular, Consolas, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const label of nashvilleLabels) {
        const boxW = ctx.measureText(label.text).width + padX * 2;
        // Prefer the right of the note; flip to its left at the roll's edge.
        let bx = label.right + 3;
        if (bx + boxW > rollRight) bx = label.left - boxW - 3;
        if (bx < layoutPadX) bx = layoutPadX;
        ctx.fillStyle = "rgba(3,7,13,0.86)";
        roundRectPath(ctx, bx, label.y - boxH * 0.5, boxW, boxH, 5);
        ctx.fill();
        // Border in the note's own colour, so a number is tied to its note.
        ctx.strokeStyle = label.color;
        ctx.lineWidth = 1.25;
        ctx.stroke();
        ctx.fillStyle = "#eaf3ff";
        ctx.fillText(label.text, bx + boxW * 0.5, label.y);
      }
      ctx.restore();
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
        ctx.fillStyle = keyReadyFill(intensity, false);
        ctx.fillRect(key.x + 1, keyboardTop + 1, Math.max(1, key.w - 2), keyboardHeight - 2);

        // A brighter lip along the top edge, where the eye already is because
        // that is where the notes land.
        ctx.fillStyle = rgbToCss(KEY_READY_RGB, 0.5 + intensity * 0.4);
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

      // In note-name mode, label only the C keys as octave anchors. In
      // Nashville mode, label the tonic of every octave with "1" so the
      // user can orient against the key's home note across the keyboard.
      const isTonic = nashville && this.keySignature
        ? mod(key.midi, 12) === mod(this.keySignature.pitchClass, 12)
        : mod(key.midi, 12) === 0;
      if (isTonic) {
        ctx.fillStyle = intensity > 0 ? "rgba(12,20,28,0.92)" : "rgba(22,30,38,0.52)";
        ctx.font = "10px ui-monospace, SFMono-Regular, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        const label = nashville
          ? pitchToNashville(key.midi, this.keySignature) ?? midiToNoteName(key.midi, noteStyle)
          : midiToNoteName(key.midi, noteStyle);
        ctx.fillText(label, key.centerX, h - 6);
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
        ctx.fillStyle = keyReadyFill(intensity, true);
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
        const blackLabel = nashville
          ? pitchToNashville(key.midi, this.keySignature) ?? noteNameForPitchClass(mod(key.midi, 12), noteStyle)
          : noteNameForPitchClass(mod(key.midi, 12), noteStyle);
        ctx.fillText(blackLabel, key.centerX, keyboardTop + blackKeyHeight * 0.55);
      }
    }

    ctx.fillStyle = this.keySignature ? "rgba(255, 245, 214, 0.86)" : "rgba(210,223,246,0.70)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    const rollLabel = this.keySignature?.label ?? "Piano roll";
    ctx.fillText(nashville ? `${rollLabel}  •  Nashville numbers` : rollLabel, 18, keyboardTop - 8);
  }

  dispose(): void {
    this.resizeObserver.disconnect();
    this.container.classList.remove("isPianoMode");
    this.canvas.remove();
  }
}

export { SheetMusicRenderer } from "./sheetMusic";
