/**
 * sheetMusic.ts — Standard music notation renderer for the game app.
 *
 * An alternative to the falling-note piano roll (TabRenderer): renders the
 * selected instrument's notes on a horizontal, scrolling staff with note
 * heads positioned by diatonic staff position (vertical) and onset time
 * (horizontal). A fixed playhead marks "now"; the music scrolls leftward
 * underneath it as playback advances.
 *
 * This is a deliberately pragmatic v1, NOT an engraving engine. See
 * SIMPLIFICATIONS below for what is faked vs. real notation.
 *
 * ── SIMPLIFICATIONS (v1) ────────────────────────────────────────────────
 *  - Rhythm is snapped to the nearest "sensible" duration (whole / half /
 *    quarter / eighth / sixteenth) derived from t_off - t_on against the
 *    song tempo. No dotted notes, no triplets, no tuplets.
 *  - No beaming: every flagged note draws its own flag. No ties across
 *    barlines — a long note is drawn as a single head at its onset.
 *  - No voicing / chord-stem sharing: overlapping notes are each drawn as
 *    independent heads (they share an x if onsets coincide, but no common
 *    stem). Stem direction is purely by staff position (middle-line rule).
 *  - Accidentals are drawn per-note for any pitch class outside the key
 *    signature; there is no measure-scoped accidental memory (so a repeated
 *    out-of-key pitch in the same bar re-prints its accidental). Natural
 *    signs for in-key-but-explicitly-natural cases are not distinguished.
 *  - Key signature accidentals are listed to the right of the clef as text
 *    glyphs in signature order rather than placed on their exact staff lines.
 *  - Ledger lines are drawn for notes above/below the staff.
 *  - Onset-time x mapping is linear in seconds (scaled by the host "note
 *    spacing" multiplier); barlines come from bpm + time signature.
 */

import { clampScrollSpeedMultiplier } from "@auralprimer/viz-sdk";
import { inferKeySignature, pitchToNashville } from "./index";
import type {
  InstrumentRole,
  KeySignatureAnalysis,
  MelodicNote,
  MelodicTrackSelection,
  PianoRenderOptions,
} from "./index";

const BG_TOP = "#0f1520";
const BG_BOTTOM = "#0a1018";
const STAFF_LINE_COLOR = "rgba(222, 232, 255, 0.34)";
const LEDGER_COLOR = "rgba(222, 232, 255, 0.46)";
const BARLINE_COLOR = "rgba(214, 227, 255, 0.30)";
const TEXT_COLOR = "#ecf2ff";
const PLAYHEAD_COLOR = "rgba(244, 250, 255, 0.9)";

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

/**
 * Diatonic staff position for each pitch class, expressed as the number of
 * letter-name steps above C (C=0, D=1, E=2, F=3, G=4, A=5, B=6) plus whether
 * the note needs an accidental relative to a "natural letter". The accidental
 * is decided per key signature at render time; here we only record which white
 * letter a pitch class maps to and the default accidental offset.
 */
type DiatonicSpec = { letter: number; sharp: boolean };
const PITCH_CLASS_DIATONIC: DiatonicSpec[] = [
  { letter: 0, sharp: false }, // C
  { letter: 0, sharp: true }, // C#
  { letter: 1, sharp: false }, // D
  { letter: 2, sharp: true }, // D# (drawn as Eb-ish; see flat keys below)
  { letter: 2, sharp: false }, // E
  { letter: 3, sharp: false }, // F
  { letter: 3, sharp: true }, // F#
  { letter: 4, sharp: false }, // G
  { letter: 5, sharp: true }, // G#
  { letter: 5, sharp: false }, // A
  { letter: 6, sharp: true }, // A#
  { letter: 6, sharp: false }, // B
];

// Letter index for a flat spelling of each accidental pitch class, so flat
// keys position the head on the *upper* letter line (e.g. Eb sits on the E
// space, not the D# space). Only used when the key signature is a flat key.
const FLAT_LETTER_OVERRIDE: Record<number, number> = {
  1: 1, // Db -> D
  3: 2, // Eb -> E
  6: 4, // Gb -> G
  8: 5, // Ab -> A
  10: 6, // Bb -> B
};

type DurationGlyph = {
  /** filled (quarter and shorter) vs open (half/whole) note head. */
  filled: boolean;
  /** whole notes carry no stem. */
  stem: boolean;
  /** number of flags (0 = quarter/half, 1 = eighth, 2 = sixteenth). */
  flags: number;
};

function mod(n: number, m: number): number {
  return ((n % m) + m) % m;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/**
 * Snap a note duration (seconds) to the nearest power-of-two note value and
 * return how to draw its head/stem/flags. `beatSec` is one quarter note.
 */
function durationGlyph(durationSec: number, beatSec: number): DurationGlyph {
  const beats = durationSec / Math.max(1e-4, beatSec);
  // Candidate note values in beats: 16th .. whole.
  if (beats >= 3) return { filled: false, stem: false, flags: 0 }; // whole
  if (beats >= 1.5) return { filled: false, stem: true, flags: 0 }; // half
  if (beats >= 0.75) return { filled: true, stem: true, flags: 0 }; // quarter
  if (beats >= 0.375) return { filled: true, stem: true, flags: 1 }; // eighth
  return { filled: true, stem: true, flags: 2 }; // sixteenth
}

/**
 * A single clef + its placement. We compute a diatonic-step coordinate so a
 * note's vertical position is independent of which clef it lands on. Step is
 * measured as "diatonic steps above C0" = octave*7 + letterIndex; higher step
 * => higher on the page (smaller y).
 */
type Clef = {
  kind: "treble" | "bass";
  /** y of the TOP staff line of this clef's 5-line staff. */
  topLineY: number;
  /** diatonic step (octave*7 + letter) that sits ON the top staff line. */
  topLineStep: number;
};

const STEP_PER_OCTAVE = 7;

function pitchToDiatonicStep(pitch: number, key: KeySignatureAnalysis | null): { step: number; accidental: "" | "#" | "b" | "n" } {
  const pc = mod(pitch, 12);
  const octave = Math.floor(pitch / 12) - 1; // MIDI octave (C4 = 60)
  const spec = PITCH_CLASS_DIATONIC[pc];
  const flatKey = key?.accidentalKind === "flat";

  let letter = spec.letter;
  if (flatKey && spec.sharp && FLAT_LETTER_OVERRIDE[pc] !== undefined) {
    letter = FLAT_LETTER_OVERRIDE[pc];
  }

  // Step counts letters above C of the SAME octave block. For B (letter 6)
  // and its neighbours the octave is already correct because MIDI octaves
  // start at C, matching our letter-0 = C convention.
  const step = octave * STEP_PER_OCTAVE + letter;

  // Decide whether this note needs a printed accidental. A pitch class is
  // "in key" if its name appears among the key's accidentals (e.g. F# in G
  // major) or it is a natural white key not contradicted by the signature.
  let accidental: "" | "#" | "b" | "n" = "";
  if (spec.sharp) {
    // Black key. In-key only if the signature already raises/lowers it.
    const sharpName = `${"CCDDEFFGGAAB"[pc]}#`;
    const inSharpKey = key?.accidentals.includes(sharpName) ?? false;
    if (flatKey) {
      const flatName = `${["", "D", "", "E", "", "", "G", "", "A", "", "B"][pc]}b`;
      const inFlatKey = key?.accidentals.includes(flatName) ?? false;
      accidental = inFlatKey ? "" : "b";
    } else {
      accidental = inSharpKey ? "" : "#";
    }
  } else {
    // White key. Needs a natural only if the signature would otherwise
    // alter this letter (e.g. F-natural in G major). We approximate: if the
    // signature contains an accidental on this same letter, print a natural.
    const letterName = "CDEFGAB"[letter];
    const altered = key?.accidentals.some((a) => a[0] === letterName) ?? false;
    if (altered) accidental = "n";
  }

  return { step, accidental };
}

export class SheetMusicRenderer {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private resizeObserver: ResizeObserver;
  private track: MelodicTrackSelection | null = null;
  private role: InstrumentRole = "melodic";
  private keySignature: KeySignatureAnalysis | null = null;
  /** Whether to draw a grand staff (treble + bass) or treble only. */
  private grandStaff = false;

  /** Seconds of music visible to the right of the playhead. */
  private lookAheadSec = 5.0;
  /** Seconds of already-played music visible to the left of the playhead. */
  private lookBehindSec = 1.6;
  /** Fraction of canvas width where the playhead sits (from left). */
  private playheadFrac = 0.24;

  constructor(container: HTMLElement) {
    this.container = container;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "tabCanvas sheetCanvas";
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

  /**
   * Drive canvas sizing externally. Used when the renderer is hosted offscreen
   * (e.g. wrapped as a Visualizer plugin that blits onto the host canvas),
   * where the ResizeObserver on a detached container won't fire usefully.
   */
  resize(width: number, height: number): void {
    this.canvas.width = Math.max(1, Math.floor(width));
    this.canvas.height = Math.max(1, Math.floor(height));
  }

  /** The renderer's own canvas — for blitting when hosted offscreen. */
  getCanvas(): HTMLCanvasElement {
    return this.canvas;
  }

  setTrack(track: MelodicTrackSelection | null): void {
    this.track = track;
    this.role = track?.role ?? "melodic";
    this.keySignature = track ? inferKeySignature(track.notes) : null;
    // Grand staff for wide-range / keyboard instruments; single treble staff
    // for the rest. Decide by observed pitch span and role.
    this.grandStaff = this.decideGrandStaff(track);
    const pianoLike = track?.role === "keys" || track?.role === "melodic";
    this.container.classList.toggle("isPianoMode", Boolean(track) && pianoLike);
    this.canvas.classList.toggle("isPianoMode", Boolean(track) && pianoLike);
  }

  private decideGrandStaff(track: MelodicTrackSelection | null): boolean {
    if (!track || track.notes.length === 0) return false;
    if (track.role === "bass") return false;
    let min = Infinity;
    let max = -Infinity;
    for (const n of track.notes) {
      if (n.pitch < min) min = n.pitch;
      if (n.pitch > max) max = n.pitch;
    }
    // Crosses middle C with meaningful range below it, or very wide span.
    return (min < 57 && max > 64) || max - min > 26 || track.role === "keys";
  }

  render(timeSec: number, opts: PianoRenderOptions = {}): void {
    const { ctx, canvas } = this;
    const w = canvas.width;
    const h = canvas.height;

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, BG_TOP);
    grad.addColorStop(1, BG_BOTTOM);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);

    if (!this.track || this.track.notes.length === 0) {
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = "14px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("No notes for this instrument", w * 0.5, h * 0.5);
      return;
    }

    this.renderSheet(timeSec, opts);
  }

  private renderSheet(t: number, opts: PianoRenderOptions): void {
    const { ctx, canvas, track } = this;
    if (!track) return;

    const w = canvas.width;
    const h = canvas.height;

    const scrollMul = clampScrollSpeedMultiplier(opts.scrollSpeedMultiplier);
    const lookAheadSec = this.lookAheadSec / scrollMul;
    const lookBehindSec = this.lookBehindSec / scrollMul;
    const playheadX = w * this.playheadFrac;
    // Pixels per second: the visible window (look-behind + look-ahead) spans
    // the canvas from the left edge to the right edge.
    const totalWindow = lookBehindSec + lookAheadSec;
    const pxPerSec = w / totalWindow;
    const timeToX = (noteTime: number): number => playheadX + (noteTime - t) * pxPerSec;
    const xToTime = (x: number): number => t + (x - playheadX) / pxPerSec;

    const headerH = 28;
    const staffSpacing = this.computeStaffSpacing(h, headerH);

    // Build clef layout. Step coordinate is octave*7 + letterIndex.
    // Treble: top line = F5. F5 is octave 5, letter 3 (F) => 5*7 + 3 = 38.
    // Bass: top line = A3. A3 is octave 3, letter 5 (A) => 3*7 + 5 = 26.
    // Centre the staff block in the usable area, reserving ledger-line headroom
    // above the top staff and below the bottom staff so extreme notes stay on
    // screen instead of being truncated.
    const usable = h - headerH - 16;
    const clefs: Clef[] = [];
    if (this.grandStaff) {
      const blockSpaces = 5 + 4 + 3.2 + 4 + 5; // = 21.2
      const startY = headerH + Math.max(0, (usable - blockSpaces * staffSpacing) / 2);
      const trebleTop = startY + staffSpacing * 5;
      const bassTop = trebleTop + staffSpacing * 4 + staffSpacing * 3.2;
      clefs.push({ kind: "treble", topLineY: trebleTop, topLineStep: 38 });
      clefs.push({ kind: "bass", topLineY: bassTop, topLineStep: 26 });
    } else {
      const single = this.role === "bass" ? "bass" : "treble";
      const blockSpaces = 4 + 4 + 4; // = 12
      const startY = headerH + Math.max(0, (usable - blockSpaces * staffSpacing) / 2);
      const top = startY + staffSpacing * 4;
      clefs.push({
        kind: single,
        topLineY: top,
        topLineStep: single === "bass" ? 26 : 38,
      });
    }

    this.drawStaves(clefs, staffSpacing, w);
    this.drawBarlines(t, opts, timeToX, xToTime, clefs, staffSpacing, w);
    this.drawClefsAndKey(clefs, staffSpacing);
    this.drawPlayhead(playheadX, headerH, clefs, staffSpacing);

    // Half a staff-space per diatonic step.
    const halfSpace = staffSpacing * 0.5;
    const color = ROLE_COLORS[this.role];
    const glow = ROLE_GLOW_COLORS[this.role];
    const beatSec = 60 / (Number.isFinite(opts.bpm) && (opts.bpm as number) > 0 ? (opts.bpm as number) : 120);

    const tStart = t - lookBehindSec;
    const tEnd = t + lookAheadSec;

    for (const note of track.notes) {
      if (note.t_on > tEnd || note.t_on < tStart) continue;
      const clef = this.pickClef(note.pitch, clefs);
      const { step, accidental } = pitchToDiatonicStep(note.pitch, this.keySignature);
      // y where the note head sits. topLineStep is on topLineY; each step up
      // moves up by halfSpace.
      const y = clef.topLineY + (clef.topLineStep - step) * halfSpace;
      const x = timeToX(note.t_on);
      const glyph = durationGlyph(note.t_off - note.t_on, beatSec);

      // Stem direction by the middle-line rule: heads on/above the middle
      // staff line get a down-stem (dir +1), heads below get an up-stem
      // (dir -1). The middle line sits at topLineY + 2*spacing.
      const middleY = clef.topLineY + staffSpacing * 2;
      const stemDir: -1 | 1 = y < middleY ? 1 : -1;

      // Fade notes far from the playhead and dim those already passed.
      const dt = note.t_on - t;
      const past = dt < 0;
      const dist = Math.abs(dt) / totalWindow;
      const alpha = past ? clamp(0.35 - dist * 0.3, 0.12, 0.4) : clamp(1 - dist * 0.7, 0.45, 1);

      this.drawLedgerLines(x, y, clef, staffSpacing, halfSpace, step, alpha);
      this.drawNote(x, y, glyph, accidental, staffSpacing, stemDir, color, glow, alpha);

      // Nashville numbers overlay: scale-degree centred on the note head.
      if (opts.nashville && this.keySignature) {
        const degree = pitchToNashville(note.pitch, this.keySignature);
        if (degree) {
          ctx.save();
          ctx.globalAlpha = alpha;
          ctx.fillStyle = "#0a1018";
          ctx.font = `700 ${Math.max(8, Math.round(staffSpacing * 0.85))}px ui-monospace, Consolas, monospace`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(degree, x, y);
          ctx.restore();
        }
      }
    }

    this.drawHeader(w, opts);
  }

  private computeStaffSpacing(h: number, headerH: number): number {
    const usable = h - headerH - 16;
    if (this.grandStaff) {
      // Reserve ~5 spaces of ledger-line headroom above the treble and below
      // the bass so high/low notes aren't truncated:
      // 5 + treble(4) + gap(3.2) + bass(4) + 5 ≈ 21.2 spaces.
      return clamp(usable / 21.5, 6, 15);
    }
    // 4 ledger margin + staff(4) + 4 ledger margin = 12 spaces.
    return clamp(usable / 12, 8, 20);
  }

  private pickClef(pitch: number, clefs: Clef[]): Clef {
    if (clefs.length === 1) return clefs[0];
    // Grand staff: middle C (60) and above go treble, below go bass.
    return pitch >= 60 ? clefs[0] : clefs[1];
  }

  private drawStaves(clefs: Clef[], spacing: number, w: number): void {
    const { ctx } = this;
    ctx.strokeStyle = STAFF_LINE_COLOR;
    ctx.lineWidth = 1;
    for (const clef of clefs) {
      for (let line = 0; line < 5; line += 1) {
        const y = clef.topLineY + line * spacing;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }
    }
    // Grand-staff brace/connector on the left.
    if (clefs.length === 2) {
      ctx.strokeStyle = "rgba(222, 232, 255, 0.5)";
      ctx.lineWidth = 2;
      const top = clefs[0].topLineY;
      const bottom = clefs[1].topLineY + spacing * 4;
      ctx.beginPath();
      ctx.moveTo(2, top);
      ctx.lineTo(2, bottom);
      ctx.stroke();
    }
  }

  private drawBarlines(
    t: number,
    opts: PianoRenderOptions,
    timeToX: (n: number) => number,
    xToTime: (x: number) => number,
    clefs: Clef[],
    spacing: number,
    w: number,
  ): void {
    const { ctx } = this;
    const bpm = Number.isFinite(opts.bpm) && (opts.bpm as number) > 0 ? (opts.bpm as number) : 120;
    const beatsPerBar = Math.max(1, opts.timeSignature?.[0] ?? 4);
    const beatSec = 60 / bpm;
    const barSec = beatSec * beatsPerBar;
    if (barSec <= 0) return;

    const top = clefs[0].topLineY;
    const bottom = clefs[clefs.length - 1].topLineY + spacing * 4;

    // x in [0, w] maps to a time range; cover it (plus a margin) with bars.
    const leftTime = xToTime(0);
    const rightTime = xToTime(w);
    const firstBar = Math.floor(leftTime / barSec) - 1;
    const lastBar = Math.ceil(rightTime / barSec) + 1;
    void t;

    ctx.strokeStyle = BARLINE_COLOR;
    ctx.lineWidth = 1.4;
    for (let bar = firstBar; bar <= lastBar; bar += 1) {
      const x = timeToX(bar * barSec);
      if (x < -2 || x > w + 2) continue;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
    }
  }

  private drawClefsAndKey(clefs: Clef[], spacing: number): void {
    const { ctx } = this;
    const clefBg = "rgba(10, 16, 26, 0.82)";
    const accCount = this.keySignature?.accidentals.length ?? 0;
    const backingW = Math.max(96, 50 + accCount * spacing * 0.95 + 16);

    for (const clef of clefs) {
      const cy = clef.topLineY + spacing * 2;
      // Backing so the clef + key sig stay legible over staff lines.
      ctx.fillStyle = clefBg;
      ctx.fillRect(0, clef.topLineY - spacing * 0.8, backingW, spacing * 5.6);

      ctx.fillStyle = TEXT_COLOR;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      // Unicode musical clef glyphs; fall back gracefully if a font lacks
      // them (they still occupy space, just may render as tofu).
      ctx.font = `${Math.round(spacing * 4.4)}px "Segoe UI Symbol", "Noto Music", serif`;
      const glyph = clef.kind === "treble" ? "𝄞" : "𝄢"; // 𝄞 / 𝄢
      ctx.fillText(glyph, 6, cy);
    }

    // Key signature: place each accidental on its CANONICAL staff line/space.
    // Sharp order F C G D A E B / flat order B E A D G C F, with the standard
    // treble-clef step positions (step = octave*7 + letterIndex). Bass clef is
    // the same pattern two octaves lower (-14 steps).
    const key = this.keySignature;
    if (key && key.accidentals.length) {
      const isFlat = key.accidentalKind === "flat";
      const sym = isFlat ? "♭" : "♯";
      const baseSteps = isFlat
        ? [34, 37, 33, 36, 32, 35, 31] // B4 E5 A4 D5 G4 C5 F4
        : [38, 35, 39, 36, 33, 37, 34]; // F5 C5 G5 D5 A4 E5 B4
      const halfSpace = spacing * 0.5;
      const x0 = 50;
      const dx = spacing * 0.95;
      ctx.font = `${Math.round(spacing * 1.9)}px "Segoe UI Symbol", serif`;
      ctx.fillStyle = "rgba(255, 245, 214, 0.92)";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const clef of clefs) {
        const stepOffset = clef.kind === "bass" ? -14 : 0;
        for (let i = 0; i < key.accidentals.length && i < baseSteps.length; i += 1) {
          const step = baseSteps[i] + stepOffset;
          const y = clef.topLineY + (clef.topLineStep - step) * halfSpace;
          ctx.fillText(sym, x0 + i * dx, y);
        }
      }
    }
  }

  private drawPlayhead(x: number, headerH: number, clefs: Clef[], spacing: number): void {
    const { ctx, canvas } = this;
    const top = clefs[0].topLineY - spacing;
    const bottom = clefs[clefs.length - 1].topLineY + spacing * 5;
    ctx.save();
    ctx.strokeStyle = ROLE_COLORS[this.role];
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(x, Math.max(headerH, top));
    ctx.lineTo(x, Math.min(canvas.height, bottom));
    ctx.stroke();
    ctx.restore();

    ctx.save();
    ctx.fillStyle = ROLE_COLORS[this.role];
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.moveTo(x - 5, Math.max(headerH, top));
    ctx.lineTo(x + 5, Math.max(headerH, top));
    ctx.lineTo(x, Math.max(headerH, top) + 7);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  private drawLedgerLines(
    x: number,
    y: number,
    clef: Clef,
    spacing: number,
    halfSpace: number,
    step: number,
    alpha: number,
  ): void {
    const { ctx } = this;
    const topLineY = clef.topLineY;
    const bottomLineY = clef.topLineY + spacing * 4;
    const len = spacing * 1.1;

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = LEDGER_COLOR;
    ctx.lineWidth = 1.2;

    // Above the staff: ledger every full space above the top line.
    if (y < topLineY - halfSpace) {
      let ly = topLineY - spacing;
      while (ly >= y - 1) {
        ctx.beginPath();
        ctx.moveTo(x - len * 0.5, ly);
        ctx.lineTo(x + len * 0.5, ly);
        ctx.stroke();
        ly -= spacing;
      }
    }
    // Below the staff.
    if (y > bottomLineY + halfSpace) {
      let ly = bottomLineY + spacing;
      while (ly <= y + 1) {
        ctx.beginPath();
        ctx.moveTo(x - len * 0.5, ly);
        ctx.lineTo(x + len * 0.5, ly);
        ctx.stroke();
        ly += spacing;
      }
    }
    ctx.restore();
    void step;
  }

  private drawNote(
    x: number,
    y: number,
    glyph: DurationGlyph,
    accidental: "" | "#" | "b" | "n",
    spacing: number,
    stemDir: -1 | 1,
    color: string,
    glow: string,
    alpha: number,
  ): void {
    const { ctx } = this;
    const headRx = spacing * 0.62;
    const headRy = spacing * 0.46;

    ctx.save();
    ctx.globalAlpha = alpha;

    // Soft glow halo.
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.ellipse(x, y, headRx + 3, headRy + 3, -0.32, 0, Math.PI * 2);
    ctx.fill();

    // Accidental to the left of the head.
    if (accidental) {
      const acc = accidental === "#" ? "♯" : accidental === "b" ? "♭" : "♮";
      ctx.fillStyle = "rgba(255, 245, 214, 0.95)";
      ctx.font = `${Math.round(spacing * 2.0)}px "Segoe UI Symbol", serif`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(acc, x - headRx - 2, y);
    }

    // Note head (slightly rotated ellipse, like real engraving).
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.ellipse(x, y, headRx, headRy, -0.32, 0, Math.PI * 2);
    if (glyph.filled) {
      ctx.fill();
    } else {
      ctx.lineWidth = Math.max(1.4, spacing * 0.16);
      ctx.strokeStyle = color;
      ctx.stroke();
    }

    // Stem + flags. Direction supplied by the caller (middle-line rule).
    if (glyph.stem) {
      const dir = stemDir;
      const stemLen = spacing * 3.2;
      const stemX = dir < 0 ? x + headRx - 1 : x - headRx + 1;
      const stemEndY = y + dir * stemLen;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1.4, spacing * 0.16);
      ctx.beginPath();
      ctx.moveTo(stemX, y);
      ctx.lineTo(stemX, stemEndY);
      ctx.stroke();

      for (let f = 0; f < glyph.flags; f += 1) {
        const fy = stemEndY - dir * f * spacing * 0.8;
        ctx.beginPath();
        ctx.moveTo(stemX, fy);
        ctx.quadraticCurveTo(
          stemX + spacing * 1.1,
          fy + dir * spacing * 0.5,
          stemX + spacing * 0.7,
          fy + dir * spacing * 1.5,
        );
        ctx.lineWidth = Math.max(1.6, spacing * 0.22);
        ctx.stroke();
      }
    }

    ctx.restore();
  }

  private drawHeader(w: number, opts: PianoRenderOptions = {}): void {
    const { ctx } = this;
    const track = this.track;
    if (!track) return;

    ctx.save();
    ctx.fillStyle = "rgba(240,246,255,0.92)";
    ctx.font = "700 12px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(track.trackName.toUpperCase(), 10, 7);

    ctx.fillStyle = "rgba(210,223,246,0.84)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    const keyLabel = this.keySignature?.label ?? "Key unknown";
    const staffLabel = this.grandStaff ? "Grand staff" : this.role === "bass" ? "Bass clef" : "Treble clef";
    const nash = opts.nashville && this.keySignature ? "  •  Nashville #" : "";
    ctx.fillText(`${keyLabel}  •  ${staffLabel}${nash}`, w - 12, 9);
    ctx.restore();
  }

  dispose(): void {
    this.resizeObserver.disconnect();
    this.container.classList.remove("isPianoMode");
    this.canvas.remove();
  }
}
