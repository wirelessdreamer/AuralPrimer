/**
 * guitarViews.ts - the guitar and bass play surfaces.
 *
 * Ported from the mock-ups in docs/guitar-prototype/, whose canvas renderers
 * are the spec -- minus what those pages invented to stand in for a player
 * (simulated judgements, a demo synth, a flip switch). The game draws real
 * state only: what is sounding and what is coming.
 *
 *   lanes      six vertical lanes falling onto a hit line, like the keys
 *              highway, with a small neck strip under it for the hand position
 *   highway    a perspective fretboard whose camera follows the hand
 *   tab        tablature scrolling past a fixed playhead: the old tab lane, same
 *              direction and pixel rate, now with the tab's own marks
 *   coach      a still neck at real fret spacing, countdown rings and chord cards
 *   nashville  the neck around the hand's pentatonic position, every number in
 *              the key on it, bleeding out from the box (nashville-fretboard.html)
 *
 * With Numbers on (setNumbering) every view labels notes by Nashville number,
 * coloured by octave and shaped by tier (fretNumbers.ts), and chords by number.
 *
 * All of them read a FretChart built once per track (fretted.ts). Strings are
 * indexed lowest first; the high string is drawn on top in tab and coach.
 */

import {
  anchorAt,
  bendLabel,
  buildFretChart,
  constantBeats,
  fretLabel,
  fretWireFraction,
  isSounding,
  lastAtOrBefore,
  lookAheadSec,
  lowerBound,
  neckFretCount,
  stringLabel,
  tabStaffLayout,
  visibleNotes,
  type ChordChange,
  type FretChart,
  type FretNote,
  type GridBeat,
  type GridSection,
} from "./fretted";
import {
  CHROMATIC_COLOR,
  OCTAVE_COLORS,
  chordNumber,
  chordTone,
  degreeLabel,
  focusWeight,
  keyNotes,
  neighbours,
  numberFrame,
  octaveBand,
  positionForHand,
  tierOf,
  toneColor,
  type CountFrom,
  type NumberFrame,
  type PositionInstance,
  type Triad,
} from "./fretNumbers";
import { ROLE_COLORS, type InstrumentRole, type MelodicTrackSelection, type PianoRenderOptions } from "./index";
import { clampScrollSpeedMultiplier } from "@auralprimer/viz-sdk";

export type FretView = "lanes" | "highway" | "tab" | "coach" | "nashville";
export const FRET_VIEWS: readonly FretView[] = ["lanes", "highway", "tab", "coach", "nashville"];

/**
 * How notes are labelled. Numbers need the song's key; `chords` says what a
 * chord's notes show (their number, their job in the chord, or the number with
 * a chord-tone ring) and `count` whether the 1 is the song's or its relative's.
 */
export type FretNumbering = {
  labels: "frets" | "numbers";
  chords: "numbers" | "tones" | "both";
  count: CountFrom;
};
export const DEFAULT_FRET_NUMBERING: FretNumbering = { labels: "frets", chords: "numbers", count: "song" };

/** How one note is drawn: its label, colour and shape (tier), with optional rings. */
type Look = {
  label: string;
  color: string;
  shape: "fill" | "hollow" | "dashed";
  /** A white ring: the 1. */
  whiteRing: boolean;
  /** A chord-tone ring colour (root, third, fifth). */
  toneRing: string | null;
};

/** Frets the Nashville neck lays positions out on. */
const NASHVILLE_NECK_FRETS = 24;
/** Frets the Nashville neck shows at once. */
const NASHVILLE_WINDOW_FRETS = 11;
const NASHVILLE_LEAD_SEC = 0.9;

/**
 * String colours, lowest string first: the red / yellow / blue / orange /
 * green / purple order tab-highway players already know, then two more for
 * seven- and eight-string guitars.
 */
export const STRING_COLORS = ["#ff5c7a", "#f5d547", "#38bdf8", "#fb923c", "#34d399", "#a78bfa", "#f472b6", "#a3e635"];

//: Pixels of travel per second of song in the tab view, before note spacing.
//: The old tab lane's rate, and the Nashville chord lane's: two lanes over the
//: same music scroll together, so reading one teaches the other.
export const TAB_BASE_PX_PER_SEC = 140;

// Seconds of look-ahead at 1.00x spacing; `[` / `]` divide them.
const LANES_LOOK_SEC = 2.4;
const HIGHWAY_LOOK_SEC = 2.6;
const COACH_LEAD_SEC = 1.6;
const STRIP_LOOK_SEC = 0.9;

const FONT_UI = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
const FONT_NUM = "ui-monospace, SFMono-Regular, Consolas, monospace";
const GEM_INK = "#061018";

const TECH_LABEL: Record<string, string> = {
  pm: "Palm mute", ho: "Hammer-on", po: "Pull-off", sl: "Slide", slu: "Slide out", bn: "Bend",
  vb: "Vibrato", mt: "Mute", fhm: "Fret-hand mute", hm: "Harmonic", hp: "Pinch harmonic", tp: "Tap",
  ac: "Accent", tr: "Tremolo",
};

type Palette = { accent: string; text: string; muted: string; faint: string; line: string };

/** The game's own tokens (style.css), so the views match the rest of the stage. */
function readPalette(): Palette {
  const css = (name: string, fallback: string): string => {
    try {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    } catch {
      return fallback;
    }
  };
  return {
    accent: css("--accent2", "#22d3ee"),
    text: css("--text", "rgba(245, 247, 255, 0.92)"),
    muted: css("--muted", "rgba(245, 247, 255, 0.70)"),
    faint: css("--muted2", "rgba(245, 247, 255, 0.55)"),
    line: css("--border", "rgba(140, 170, 255, 0.16)"),
  };
}

/** A #rrggbb colour at alpha `a`; anything else is returned as it is. */
function rgba(color: string, a: number): string {
  let h = color.trim().replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/i.test(h)) return color;
  const v = parseInt(h, 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v));
const stringColor = (s: number): string => STRING_COLORS[s % STRING_COLORS.length];

export type GuitarRendererOptions = {
  /**
   * The label on a lane gem, highway gem or coach dot when labels are frets.
   * The fret number by default. Numbers mode labels from the key instead.
   */
  noteLabel?: (note: FretNote) => string;
};

type Frame = {
  scrollMul: number;
  bpm: number;
  beatsPerBar: number;
  beats: GridBeat[];
  sections: GridSection[];
  dt: number;
};

export class GuitarRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly resizeObserver: ResizeObserver;
  private readonly noteLabel: (note: FretNote) => string;
  private readonly palette: Palette;
  private W = 0;
  private H = 0;
  private dpr = 1;
  private role: InstrumentRole = "melodic";
  private trackName = "";
  private chart: FretChart | null = null;
  /** Highway camera: centre fret and frets in view, eased toward the hand. */
  private cam: { x: number; w: number } | null = null;
  private lastWallMs: number | null = null;
  private lastT = 0;
  private numbering: FretNumbering = DEFAULT_FRET_NUMBERING;
  /** The song's key as counted, or null when the song has none. */
  private keyFrame: NumberFrame | null = null;
  private keyText = "";
  /** keyFrame while labels are numbers: everything that labels by number reads this. */
  private nf: NumberFrame | null = null;
  /** Each hand position's pentatonic box, worked out once per key. */
  private handBoxes: { majorPc: number; list: { t: number; inst: PositionInstance }[] } | null = null;
  /** The Nashville neck's window, eased toward the focused box. */
  private nashView: { lo: number; hi: number } | null = null;

  constructor(
    private readonly container: HTMLElement,
    readonly view: FretView,
    opts: GuitarRendererOptions = {},
  ) {
    this.noteLabel = opts.noteLabel ?? fretLabel;
    this.palette = readPalette();
    this.canvas = document.createElement("canvas");
    this.canvas.className = "tabCanvas";
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    // The tab keeps the old tab lane's strip; the other three are play
    // surfaces and need the room a keyboard gets.
    container.classList.toggle("isGuitarStage", view !== "tab");
    this.resize(container.clientWidth || 800, container.clientHeight || 180);
    this.resizeObserver = new ResizeObserver(([entry]) => {
      if (entry) this.resize(entry.contentRect.width, entry.contentRect.height);
    });
    this.resizeObserver.observe(container);
  }

  private resize(width: number, height: number): void {
    this.dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
    this.W = Math.max(1, Math.floor(width));
    this.H = Math.max(1, Math.floor(height));
    this.canvas.width = Math.round(this.W * this.dpr);
    this.canvas.height = Math.round(this.H * this.dpr);
  }

  setTrack(track: MelodicTrackSelection | null): void {
    this.role = track?.role ?? "melodic";
    this.trackName = track?.trackName ?? "";
    this.chart = track ? buildFretChart(track) : null;
    this.cam = null;
    this.handBoxes = null;
    this.nashView = null;
  }

  /** Frets or numbers, and how numbers read; applies from the next frame. */
  setNumbering(numbering: FretNumbering): void {
    this.numbering = { ...numbering };
  }

  render(timeSec: number, opts: PianoRenderOptions = {}): void {
    const { ctx } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.W, this.H);

    const wall = typeof performance !== "undefined" ? performance.now() : Date.now();
    const dt = this.lastWallMs === null ? 0 : Math.min(0.1, (wall - this.lastWallMs) / 1000);
    this.lastWallMs = wall;
    // A seek snaps the camera rather than sweeping it across the neck.
    if (Math.abs(timeSec - this.lastT) > 1) {
      this.cam = null;
      this.nashView = null;
    }
    this.lastT = timeSec;

    // No key, no numbers: the views say so and keep to frets rather than
    // count from a guess.
    const key = opts.songKey ?? null;
    this.keyFrame = key ? numberFrame(key, this.numbering.count) : null;
    this.keyText = key
      ? `${key.label ?? ""}${this.numbering.count === "relative" ? ", counted from its relative" : ""}`
      : "";
    this.nf = this.numbering.labels === "numbers" ? this.keyFrame : null;

    const chart = this.chart;
    if (!chart || chart.notes.length === 0) {
      this.paintBg();
      ctx.fillStyle = this.palette.text;
      ctx.font = `14px ${FONT_UI}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("No notes for this instrument", this.W / 2, this.H / 2);
      return;
    }

    const frame: Frame = {
      scrollMul: clampScrollSpeedMultiplier(opts.scrollSpeedMultiplier),
      bpm: Number.isFinite(opts.bpm) && (opts.bpm as number) > 0 ? (opts.bpm as number) : 120,
      beatsPerBar: opts.timeSignature?.[0] ?? 4,
      beats: opts.beats ?? [],
      sections: opts.sections ?? [],
      dt,
    };
    if (this.view === "lanes") this.drawLanes(chart, timeSec, frame);
    else if (this.view === "highway") this.drawHighway(chart, timeSec, frame);
    else if (this.view === "tab") this.drawTab(chart, timeSec, frame);
    else if (this.view === "nashville") this.drawNashville(chart, timeSec, frame);
    else this.drawCoach(chart, timeSec, frame);
  }

  dispose(): void {
    this.resizeObserver.disconnect();
    this.container.classList.remove("isGuitarStage");
    this.canvas.remove();
  }

  // --- Shared pieces --------------------------------------------------------

  /** The song's beats between t0 and t1, or a constant-tempo grid without a timeline. */
  private beatsIn(f: Frame, t0: number, t1: number): GridBeat[] {
    if (f.beats.length === 0) return constantBeats(f.bpm, f.beatsPerBar, t0, t1);
    const out: GridBeat[] = [];
    for (let i = lowerBound(f.beats, t0); i < f.beats.length && f.beats[i].t <= t1; i += 1) out.push(f.beats[i]);
    return out;
  }

  private sectionAt(f: Frame, t: number): string {
    return lastAtOrBefore(f.sections, t + 0.05)?.name ?? "";
  }

  /** Row from the top for string `s`: the high string on top. */
  private row(chart: FretChart, s: number): number {
    return chart.n - 1 - s;
  }

  private gauge(chart: FretChart, s: number): number {
    return 1 + (chart.n - 1 - s) * 0.42;
  }

  private rr(x: number, y: number, w: number, h: number, r: number): void {
    const { ctx } = this;
    ctx.beginPath();
    if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, w / 2, h / 2)));
    else ctx.rect(x, y, w, h);
  }

  private paintBg(top = "#0d1322", bottom = "#05060a"): void {
    const g = this.ctx.createLinearGradient(0, 0, 0, this.H);
    g.addColorStop(0, top);
    g.addColorStop(1, bottom);
    this.ctx.fillStyle = g;
    this.ctx.fillRect(0, 0, this.W, this.H);
  }

  /** Part name in its role colour, then tuning and section. */
  private hud(chart: FretChart, f: Frame, now: number, extra?: string): void {
    const { ctx } = this;
    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    ctx.font = `800 12px ${FONT_UI}`;
    ctx.fillStyle = ROLE_COLORS[this.role];
    ctx.fillText(this.trackName.toUpperCase(), 16, 12);
    ctx.font = `600 11.5px ${FONT_UI}`;
    ctx.fillStyle = this.palette.muted;
    const showKey = this.nf !== null || (this.view === "nashville" && this.keyFrame !== null);
    ctx.fillText([chart.tuningName, showKey ? this.keyText : "", this.sectionAt(f, now)].filter(Boolean).join("  ·  "), 16, 29);
    let y = 46;
    const warning = this.keyWarning();
    if (warning) {
      ctx.fillStyle = CHROMATIC_COLOR;
      ctx.font = `700 11.5px ${FONT_UI}`;
      ctx.fillText(warning, 16, y);
      y += 17;
    }
    if (extra) {
      ctx.fillStyle = this.palette.accent;
      ctx.font = `700 11.5px ${FONT_UI}`;
      ctx.fillText(extra, 16, y);
    }
  }

  /** Said plainly when numbers were asked for and the song has no key to count from. */
  private keyWarning(): string {
    const asked = this.numbering.labels === "numbers" || this.view === "nashville";
    return asked && !this.keyFrame ? "No key for this song, so no numbers: showing frets" : "";
  }

  /** Where a slide lands: its fret, or that note's number when labels are numbers. */
  private slideTarget(chart: FretChart, x: FretNote): string {
    const to = x.tech.sl as number;
    return this.nf ? degreeLabel(this.nf, chart.open[x.s] + to) : String(to);
  }

  /** A chord's name, or its Nashville number when labels are numbers. */
  private chordTitle(c: { name: string; triad: Triad | null }): string {
    return this.nf && c.triad ? chordNumber(this.nf, c.triad) : c.name;
  }

  /**
   * How a pitch looks in numbers: its number (or its chord tone), its
   * octave's colour, its tier's shape, a white ring on the 1.
   */
  private pitchLook(frame: NumberFrame, midi: number, triad: Triad | null): Look {
    const tone = triad ? chordTone(triad, midi) : null;
    if (tone && this.numbering.chords === "tones") {
      return { label: tone, color: toneColor(tone), shape: "fill", whiteRing: false, toneRing: null };
    }
    const tier = tierOf(frame, midi);
    return {
      label: degreeLabel(frame, midi),
      color: OCTAVE_COLORS[octaveBand(frame, this.chart!.open[0], midi)],
      shape: tier === "mode" ? "hollow" : tier === "chrom" ? "dashed" : "fill",
      whiteRing: tier === "root",
      toneRing: tone && this.numbering.chords === "both" ? toneColor(tone) : null,
    };
  }

  /** How a chart note looks: by fret and string, or by number when labels are numbers. */
  private look(x: FretNote): Look {
    if (!this.nf || x.tech.mt) {
      // Open strings hollow; a muted note has no pitch to number.
      const shape = x.f === 0 && !x.tech.mt ? "hollow" : "fill";
      return { label: this.noteLabel(x), color: stringColor(x.s), shape, whiteRing: false, toneRing: null };
    }
    return this.pitchLook(this.nf, x.midi, x.chord && x.chord.size >= 2 ? x.chord.triad : null);
  }

  private handText(chart: FretChart, now: number, lead: number): string {
    const an = anchorAt(chart, now + lead);
    return `HAND · FRETS ${an.fret}–${an.fret + an.width - 1}`;
  }

  /** Small technique tags either side of a gem's label. */
  private techTags(x: FretNote): [string, string] {
    const t = x.tech;
    const left = t.ho ? "H" : t.po ? "P" : t.tp ? "T" : t.hp ? "PH" : t.pm ? "PM" : "";
    const slide = (t.sl as number) || (t.slu as number) || 0;
    const right = t.bn
      ? (t.bn as number) >= 2 ? "↑1" : "↑½"
      : t.sl ? (slide > x.f ? "↗" : "↘") : t.slu ? "⤳" : t.vb ? "~" : t.ac ? ">" : "";
    return [left, right];
  }

  /**
   * A note gem: label plus technique tags. Filled, or hollow (an open string;
   * in numbers, a note that completes the scale) or dashed (outside the key);
   * harmonics are diamonds.
   */
  private drawGem(x: FretNote, cx: number, cy: number, w: number, h: number, look: Look, alpha: number): void {
    const { ctx } = this;
    const color = look.color;
    ctx.globalAlpha = clamp(alpha, 0, 1);
    if (look.toneRing) {
      this.rr(cx - w / 2 - 4, cy - h / 2 - 4, w + 8, h + 8, h / 2.6 + 4);
      ctx.strokeStyle = look.toneRing;
      ctx.lineWidth = 3;
      ctx.stroke();
    }
    if (look.whiteRing) {
      this.rr(cx - w / 2 - 1.5, cy - h / 2 - 1.5, w + 3, h + 3, h / 2.6 + 1.5);
      ctx.strokeStyle = this.palette.text;
      ctx.lineWidth = 2.4;
      ctx.stroke();
    }
    if (x.tech.hm) {
      ctx.beginPath();
      ctx.moveTo(cx, cy - h / 2 - 3);
      ctx.lineTo(cx + w / 2, cy);
      ctx.lineTo(cx, cy + h / 2 + 3);
      ctx.lineTo(cx - w / 2, cy);
      ctx.closePath();
      ctx.fillStyle = rgba(color, 0.22);
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = this.palette.text;
    } else if (look.shape !== "fill") {
      this.rr(cx - w / 2, cy - h / 2, w, h, h / 2.6);
      ctx.fillStyle = look.shape === "dashed" ? "rgba(10,14,22,0.9)" : rgba(color, 0.16);
      ctx.fill();
      if (look.shape === "dashed") ctx.setLineDash([3, 2.5]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
    } else {
      this.rr(cx - w / 2, cy - h / 2, w, h, h / 2.6);
      ctx.fillStyle = x.tech.mt ? rgba(color, 0.45) : color;
      ctx.fill();
      if (x.tech.pm) {
        // Palm mute: hatched, so it reads as damped before the tag is read.
        ctx.save();
        ctx.clip();
        ctx.strokeStyle = "rgba(0,0,0,0.28)";
        ctx.lineWidth = 2;
        for (let k = -h; k < w; k += 6) {
          ctx.beginPath();
          ctx.moveTo(cx - w / 2 + k, cy + h / 2);
          ctx.lineTo(cx - w / 2 + k + h, cy - h / 2);
          ctx.stroke();
        }
        ctx.restore();
      }
      ctx.fillStyle = GEM_INK;
    }
    if (h >= 11) {
      ctx.font = `800 ${Math.round(clamp(h * 0.62, 9, 16))}px ${FONT_NUM}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(look.label, cx, cy + 0.5);
      if (w >= 40) {
        const [l, r] = this.techTags(x);
        ctx.font = `800 ${Math.round(clamp(h * 0.38, 8, 10))}px ${FONT_UI}`;
        if (l) {
          ctx.textAlign = "left";
          ctx.fillText(l, cx - w / 2 + 5, cy + 0.5);
        }
        if (r) {
          ctx.textAlign = "right";
          ctx.fillText(r, cx + w / 2 - 5, cy + 0.5);
        }
      }
    }
    ctx.globalAlpha = 1;
  }

  /** A compact uniform neck: the hand window, sounding notes, and the next ones. */
  private miniNeck(chart: FretChart, now: number, f: Frame, x0: number, y0: number, w: number, h: number): void {
    const { ctx } = this;
    const F = neckFretCount(chart.maxFret);
    const fx = (b: number): number => x0 + 16 + (b / F) * (w - 16);
    const sy = (s: number): number => y0 + 6 + (this.row(chart, s) / Math.max(1, chart.n - 1)) * (h - 12);
    ctx.fillStyle = "rgba(26,33,51,0.9)";
    this.rr(x0, y0 - 4, w, h + 8, 8);
    ctx.fill();
    const an = anchorAt(chart, now + 0.15);
    const ax = fx(an.fret - 1);
    const aw = fx(an.fret - 1 + an.width) - ax;
    ctx.fillStyle = rgba(this.palette.accent, 0.14);
    ctx.fillRect(ax, y0 - 4, aw, h + 8);
    ctx.strokeStyle = rgba(this.palette.accent, 0.6);
    ctx.lineWidth = 1;
    ctx.strokeRect(ax + 0.5, y0 - 3.5, aw - 1, h + 7);
    ctx.strokeStyle = "rgba(220,228,250,0.16)";
    for (let b = 1; b <= F; b += 1) {
      ctx.beginPath();
      ctx.moveTo(fx(b), y0 - 2);
      ctx.lineTo(fx(b), y0 + h + 2);
      ctx.stroke();
    }
    ctx.strokeStyle = "rgba(240,236,220,0.7)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(fx(0), y0 - 3);
    ctx.lineTo(fx(0), y0 + h + 3);
    ctx.stroke();
    ctx.fillStyle = this.palette.faint;
    ctx.font = `700 9.5px ${FONT_NUM}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const b of [3, 5, 7, 9, 12, 15, 17, 19, 21, 24]) {
      if (b <= F) ctx.fillText(String(b), (fx(b - 1) + fx(b)) / 2, y0 + h + 6);
    }
    for (let s = 0; s < chart.n; s += 1) {
      ctx.strokeStyle = rgba(stringColor(s), 0.45);
      ctx.lineWidth = this.gauge(chart, s) * 0.8;
      ctx.beginPath();
      ctx.moveTo(fx(0), sy(s));
      ctx.lineTo(x0 + w, sy(s));
      ctx.stroke();
    }
    for (const x of visibleNotes(chart.notes, now, now + lookAheadSec(STRIP_LOOK_SEC, f.scrollMul))) {
      const cx = x.f === 0 ? fx(0) - 7 : (fx(x.f - 1) + fx(x.f)) / 2;
      const cy = sy(x.s);
      const col = stringColor(x.s);
      if (isSounding(x, now)) {
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(cx, cy, 5.5, 0, Math.PI * 2);
        ctx.fill();
      } else if (x.t > now) {
        ctx.strokeStyle = rgba(col, 0.75);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, 4.5, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  // --- String lanes ----------------------------------------------------------

  private drawLanes(chart: FretChart, now: number, f: Frame): void {
    const { ctx, W, H } = this;
    const P = this.palette;
    this.paintBg();
    const n = chart.n;
    const laneW = Math.min(80, (W * 0.6) / n);
    const left = (W - laneW * n) / 2;
    const hitY = Math.round(H * (W < 520 ? 0.72 : 0.7));
    const look = lookAheadSec(LANES_LOOK_SEC, f.scrollMul);
    const pps = (hitY - 30) / look;
    const lx = (s: number): number => left + s * laneW + laneW / 2;
    const yOf = (t: number): number => hitY - (t - now) * pps;

    // Bed and lanes, low string on the left.
    ctx.fillStyle = "rgba(18,25,40,0.72)";
    this.rr(left - 8, 0, laneW * n + 16, hitY + 22, 12);
    ctx.fill();
    for (let s = 0; s < n; s += 1) {
      if (s % 2) {
        ctx.fillStyle = "rgba(140,170,255,0.035)";
        ctx.fillRect(left + s * laneW, 0, laneW, hitY + 14);
      }
      ctx.strokeStyle = rgba(stringColor(s), 0.32);
      ctx.lineWidth = this.gauge(chart, s);
      ctx.beginPath();
      ctx.moveTo(lx(s), 0);
      ctx.lineTo(lx(s), hitY);
      ctx.stroke();
    }
    // Beat lines and bar numbers.
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.font = `700 10px ${FONT_NUM}`;
    for (const b of this.beatsIn(f, now - 0.3, now + look)) {
      const y = yOf(b.t);
      ctx.strokeStyle = b.down ? "rgba(220,228,250,0.26)" : "rgba(220,228,250,0.08)";
      ctx.lineWidth = b.down ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(left - 8, y);
      ctx.lineTo(left + laneW * n + 8, y);
      ctx.stroke();
      if (b.down) {
        ctx.fillStyle = P.faint;
        ctx.fillText(String(b.m), left - 14, y);
      }
    }
    // Section flags.
    ctx.textAlign = "left";
    for (const sec of f.sections) {
      const y = yOf(sec.t);
      if (y < 16 || y > hitY) continue;
      ctx.font = `800 10.5px ${FONT_UI}`;
      const label = sec.name.toUpperCase();
      const tw = ctx.measureText(label).width;
      const x = left + laneW * n + 18;
      ctx.fillStyle = rgba(P.accent, 0.14);
      this.rr(x, y - 10, tw + 16, 20, 10);
      ctx.fill();
      ctx.fillStyle = P.accent;
      ctx.fillText(label, x + 8, y);
    }

    const vis = visibleNotes(chart.notes, now - 0.3, now + look);
    // Sustain tails: wavy for vibrato, angled for slides.
    for (const x of vis) {
      if (x.sus < 0.12) continue;
      const yt = Math.max(yOf(x.t + x.sus), 0);
      const yb = Math.min(yOf(x.t), hitY);
      if (yb <= yt) continue;
      const cx = lx(x.s);
      const col = stringColor(x.s);
      const slide = (x.tech.sl as number) || (x.tech.slu as number) || 0;
      ctx.strokeStyle = rgba(col, 0.55);
      ctx.lineWidth = 7;
      ctx.lineCap = "round";
      ctx.beginPath();
      if (x.tech.vb) {
        for (let y = yb; y >= yt; y -= 3) ctx.lineTo(cx + Math.sin(y * 0.45) * 4, y);
      } else if (slide) {
        ctx.moveTo(cx, yb);
        ctx.lineTo(cx + (slide > x.f ? 1 : -1) * laneW * 0.32, yt);
      } else {
        ctx.moveTo(cx, yb);
        ctx.lineTo(cx, yt);
      }
      ctx.stroke();
      ctx.lineCap = "butt";
      if (x.tech.sl && yt > 8) {
        ctx.fillStyle = col;
        ctx.font = `800 10px ${FONT_NUM}`;
        ctx.textAlign = "center";
        ctx.fillText(`→${this.slideTarget(chart, x)}`, cx + ((x.tech.sl as number) > x.f ? 1 : -1) * laneW * 0.32, yt - 8);
      }
    }
    // Chord bars, and the chord's name beside them.
    const seen = new Set<object>();
    for (const x of vis) {
      if (!x.chord || x.chord.size < 2 || seen.has(x.chord) || x.t < now - 0.05) continue;
      seen.add(x.chord);
      const xs = vis.filter((y) => y.chord === x.chord).map((y) => lx(y.s));
      const y = yOf(x.t);
      ctx.fillStyle = "rgba(235,240,255,0.10)";
      this.rr(Math.min(...xs) - laneW / 2 + 2, y - 17, Math.max(...xs) - Math.min(...xs) + laneW - 4, 34, 10);
      ctx.fill();
      const title = this.chordTitle(x.chord);
      if (title) {
        ctx.fillStyle = P.text;
        ctx.font = `800 12px ${FONT_UI}`;
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(title, left - 34, y);
      }
    }
    // Gems.
    for (const x of vis) {
      if (x.t < now - 0.12) continue;
      const y = yOf(x.t);
      if (y < -20) continue;
      this.drawGem(x, lx(x.s), Math.min(y, hitY + 6), laneW * 0.78, 24, this.look(x), x.t < now ? 1 - (now - x.t) / 0.12 : 1);
    }
    // Hit line and receivers: a receiver shows its string, or the fret sounding on it.
    const glow = ctx.createLinearGradient(0, hitY - 18, 0, hitY + 18);
    glow.addColorStop(0, rgba(P.accent, 0));
    glow.addColorStop(0.5, rgba(P.accent, 0.22));
    glow.addColorStop(1, rgba(P.accent, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(left - 8, hitY - 18, laneW * n + 16, 36);
    ctx.strokeStyle = P.accent;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(left - 8, hitY);
    ctx.lineTo(left + laneW * n + 8, hitY);
    ctx.stroke();
    for (let s = 0; s < n; s += 1) {
      const cx = lx(s);
      const col = stringColor(s);
      const on = vis.find((x) => x.s === s && isSounding(x, now));
      this.rr(cx - laneW * 0.4, hitY - 15, laneW * 0.8, 30, 10);
      ctx.fillStyle = on ? rgba(col, 0.35) : "rgba(8,12,20,0.7)";
      ctx.fill();
      ctx.strokeStyle = rgba(col, on ? 1 : 0.55);
      ctx.lineWidth = on ? 2.5 : 1.5;
      ctx.stroke();
      ctx.fillStyle = on ? P.text : rgba(col, 0.9);
      ctx.font = `800 12px ${FONT_NUM}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(on ? this.look(on).label : stringLabel(chart.open, s), cx, hitY + 0.5);
    }

    // The hand-position strip.
    const stripTop = hitY + 44;
    const stripH = Math.max(30, H - hitY - 76);
    if (stripTop + stripH + 8 <= H) {
      this.miniNeck(chart, now, f, 24, stripTop, W - 48, stripH);
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.font = `800 10.5px ${FONT_UI}`;
      ctx.fillStyle = P.accent;
      ctx.fillText(this.handText(chart, now, 0.15), 24, hitY + 36);
    }
    this.hud(chart, f, now);
  }

  // --- Neck highway ----------------------------------------------------------

  private drawHighway(chart: FretChart, now: number, f: Frame): void {
    const { ctx, W, H } = this;
    const P = this.palette;
    this.paintBg("#0a0f1c", "#05070c");
    const n = chart.n;
    const look = lookAheadSec(HIGHWAY_LOOK_SEC, f.scrollMul);
    const horizonY = H * 0.08;
    const groundY = H * 0.86;
    const vpX = W / 2;
    const zMax = 4.2;
    const zs = zMax / look;
    const k = (z: number): number => 1 / (1 + Math.max(0, z));
    const proj = (x0: number, y0: number, z: number): [number, number] => [
      vpX + (x0 - vpX) * k(z),
      horizonY + (y0 - horizonY) * k(z),
    ];
    const gap = Math.min((groundY - H * 0.36) / n, 44);
    // Low string highest, as the neck looks from behind it.
    const sy = (s: number): number => groundY - 26 - this.row(chart, s) * gap;

    // The camera follows the hand, slightly ahead of time.
    const an = anchorAt(chart, now + 0.35);
    const camT = an.fret - 1 + an.width / 2;
    const visT = Math.max(an.width + 4, 8);
    if (!this.cam) this.cam = { x: camT, w: visT };
    const ease = Math.min(1, f.dt * 3.2);
    this.cam.x += (camT - this.cam.x) * ease;
    this.cam.w += (visT - this.cam.w) * ease;
    const fretW = (W * 0.94) / this.cam.w;
    const fx = (b: number): number => W / 2 + (b - this.cam!.x) * fretW;

    // Ground: fret columns to the vanishing point, the hand window lit.
    const F = 24;
    const inHand = (fr: number): boolean => fr >= an.fret && fr < an.fret + an.width;
    for (let fr = 1; fr <= F; fr += 1) {
      const [ax, ay] = proj(fx(fr - 1), groundY, 0);
      const [bx] = proj(fx(fr), groundY, 0);
      const [cx, cy] = proj(fx(fr), groundY, zMax);
      const [dx] = proj(fx(fr - 1), groundY, zMax);
      ctx.fillStyle = inHand(fr) ? rgba(P.accent, 0.1) : fr % 2 ? "rgba(140,170,255,0.03)" : "rgba(140,170,255,0.055)";
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, ay);
      ctx.lineTo(cx, cy);
      ctx.lineTo(dx, cy);
      ctx.closePath();
      ctx.fill();
    }
    ctx.strokeStyle = "rgba(220,228,250,0.10)";
    ctx.lineWidth = 1;
    for (let b = 0; b <= F; b += 1) {
      const [x1, y1] = proj(fx(b), groundY, 0);
      const [x2, y2] = proj(fx(b), groundY, zMax);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    for (const b of this.beatsIn(f, now, now + look)) {
      const z = (b.t - now) * zs;
      const [x1, y1] = proj(fx(0), groundY, z);
      const [x2] = proj(fx(F), groundY, z);
      ctx.strokeStyle = b.down ? "rgba(220,228,250,0.22)" : "rgba(220,228,250,0.07)";
      ctx.lineWidth = b.down ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y1);
      ctx.stroke();
    }
    // Fret numbers on the near edge.
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = `800 12px ${FONT_NUM}`;
    for (let fr = 1; fr <= F; fr += 1) {
      const x = fx(fr - 0.5);
      if (x < -20 || x > W + 20) continue;
      ctx.fillStyle = inHand(fr) ? P.accent : P.faint;
      ctx.fillText(String(fr), x, groundY + 10);
    }
    // Fret wires at the hit plane, then the strings.
    ctx.strokeStyle = "rgba(220,228,250,0.12)";
    ctx.lineWidth = 1;
    const topString = Math.min(sy(0), sy(n - 1));
    for (let b = 0; b <= F; b += 1) {
      const x = fx(b);
      if (x < 0 || x > W) continue;
      ctx.beginPath();
      ctx.moveTo(x, topString - 14);
      ctx.lineTo(x, groundY);
      ctx.stroke();
    }
    for (let s = 0; s < n; s += 1) {
      ctx.strokeStyle = rgba(stringColor(s), 0.85);
      ctx.lineWidth = this.gauge(chart, s) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, sy(s));
      ctx.lineTo(W, sy(s));
      ctx.stroke();
      ctx.fillStyle = stringColor(s);
      ctx.font = `800 11px ${FONT_NUM}`;
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(stringLabel(chart.open, s), 8, sy(s) - 3);
    }

    // Notes, far to near.
    const vis = visibleNotes(chart.notes, now - 0.1, now + look).sort((p, q) => q.t - p.t || p.s - q.s);
    const handLeft = fx(an.fret - 1);
    const handRight = fx(an.fret - 1 + an.width);
    const noteX = (x: FretNote): number => (x.f === 0 ? (handLeft + handRight) / 2 : fx(x.f - 0.5));
    for (const x of vis) {
      const col = stringColor(x.s);
      const x0 = noteX(x);
      const y0 = sy(x.s);
      // Sustain ribbon along the string.
      if (x.sus > 0.12 && x.t + x.sus > now) {
        const z1 = Math.max(0, (x.t - now) * zs);
        const z2 = Math.min(zMax, (x.t + x.sus - now) * zs);
        const [p1x, p1y] = proj(x0, y0, z1);
        const [p2x, p2y] = proj(x0, y0, z2);
        ctx.strokeStyle = rgba(col, 0.5);
        ctx.lineWidth = 7 * k(z1);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(p1x, p1y);
        ctx.lineTo(p2x, p2y);
        ctx.stroke();
        ctx.lineCap = "butt";
      }
      if (x.t < now - 0.08) continue;
      const z = Math.max(0, (x.t - now) * zs);
      const kk = k(z);
      const [px, py] = proj(x0, y0, z);
      const [, gy] = proj(x0, groundY, z);
      // Drop line to the ground, so depth and fret read together.
      ctx.strokeStyle = rgba(col, 0.35);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(px, gy);
      ctx.stroke();
      ctx.fillStyle = rgba(col, 0.35);
      ctx.beginPath();
      ctx.ellipse(px, gy, 10 * kk, 3 * kk, 0, 0, Math.PI * 2);
      ctx.fill();
      const w = x.f === 0 ? (handRight - handLeft) * kk * 0.94 : fretW * 0.7 * kk;
      this.drawGem(x, px, py, w, Math.max(5, 26 * kk), this.look(x), x.t < now ? 1 - (now - x.t) / 0.08 : 0.35 + 0.65 * kk ** 0.6);
    }
    // Chord frames, named above.
    const seen = new Set<object>();
    for (const x of vis) {
      if (!x.chord || x.chord.size < 2 || seen.has(x.chord) || x.t < now - 0.05) continue;
      seen.add(x.chord);
      const z = Math.max(0, (x.t - now) * zs);
      const kk = k(z);
      const pts = vis.filter((y) => y.chord === x.chord).map((y) => proj(noteX(y), sy(y.s), z));
      const xs = pts.map((p) => p[0]);
      const ys = pts.map((p) => p[1]);
      const pad = 16 * kk + 4;
      ctx.strokeStyle = `rgba(235,240,255,${0.25 + 0.4 * kk})`;
      ctx.lineWidth = 1.5;
      this.rr(
        Math.min(...xs) - fretW * 0.4 * kk - 2,
        Math.min(...ys) - pad,
        Math.max(...xs) - Math.min(...xs) + fretW * 0.8 * kk + 4,
        Math.max(...ys) - Math.min(...ys) + pad * 2,
        6 * kk + 2,
      );
      ctx.stroke();
      const title = this.chordTitle(x.chord);
      if (title && kk > 0.3) {
        ctx.fillStyle = P.text;
        ctx.font = `800 ${Math.round(10 + 6 * kk)}px ${FONT_UI}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(title, (Math.min(...xs) + Math.max(...xs)) / 2, Math.min(...ys) - pad - 3);
      }
    }
    // Sounding notes glow where they meet the strings.
    for (const x of vis) {
      if (!isSounding(x, now)) continue;
      const cx = noteX(x);
      const cy = sy(x.s);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 34);
      g.addColorStop(0, rgba(stringColor(x.s), 0.55));
      g.addColorStop(1, rgba(stringColor(x.s), 0));
      ctx.fillStyle = g;
      ctx.fillRect(cx - 34, cy - 34, 68, 68);
    }
    this.hud(chart, f, now, `HAND · FRETS ${an.fret}–${an.fret + an.width - 1}`);
  }

  // --- Tab scroll ------------------------------------------------------------

  private drawTab(chart: FretChart, now: number, f: Frame): void {
    const { ctx, W, H } = this;
    const P = this.palette;
    this.paintBg("#0b101c", "#070a12");
    const n = chart.n;
    const { gap, yTop, yBot } = tabStaffLayout(H, n);
    const ry = (s: number): number => yTop + this.row(chart, s) * gap;
    // Fixed playhead, notes moving right to left at a fixed pixel rate: the
    // old tab lane's geometry, so its feel carries over.
    const px = Math.max(70, Math.round(W * 0.15));
    const pxPerSec = TAB_BASE_PX_PER_SEC * f.scrollMul;
    const xOf = (t: number): number => px + (t - now) * pxPerSec;
    const left = 40;
    const right = W - 12;
    const t0 = now - (px - left) / pxPerSec;
    const t1 = now + (W - px) / pxPerSec;

    // Staff, with each string named in its colour.
    for (let s = 0; s < n; s += 1) {
      ctx.strokeStyle = "rgba(220,228,250,0.28)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(left, ry(s));
      ctx.lineTo(right, ry(s));
      ctx.stroke();
      ctx.fillStyle = stringColor(s);
      ctx.font = `800 12px ${FONT_NUM}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(stringLabel(chart.open, s), 20, ry(s));
    }
    // Bar lines from the beat map; a tick under the staff for the other beats.
    ctx.font = `700 10px ${FONT_NUM}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    for (const b of this.beatsIn(f, t0, t1)) {
      const x = xOf(b.t);
      if (x < left) continue;
      if (b.down) {
        ctx.strokeStyle = "rgba(220,228,250,0.42)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x, yTop);
        ctx.lineTo(x, yBot);
        ctx.stroke();
        ctx.fillStyle = P.faint;
        ctx.fillText(String(b.m), x + 3, yTop - 4);
      } else {
        ctx.strokeStyle = "rgba(220,228,250,0.10)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, yBot + 4);
        ctx.lineTo(x, yBot + 9);
        ctx.stroke();
      }
    }
    // Section boxes, along the top.
    for (const sec of f.sections) {
      const x = xOf(sec.t);
      if (x < left - 200 || x > right) continue;
      ctx.font = `800 11px ${FONT_UI}`;
      const label = sec.name.toUpperCase();
      const tw = ctx.measureText(label).width;
      ctx.strokeStyle = rgba(P.accent, 0.7);
      ctx.lineWidth = 1;
      this.rr(x, yTop - 48, tw + 14, 18, 4);
      ctx.stroke();
      ctx.fillStyle = P.accent;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, x + 7, yTop - 39);
    }

    const vis = visibleNotes(chart.notes, t0, t1);
    // Chord names when they change.
    let lastName: string | null = null;
    let lastX = -1e9;
    let lastEnd = -1e9;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.font = `800 13px ${FONT_UI}`;
    for (const x of vis) {
      if (!x.chord || x.chord.size < 2 || x.chord.t !== x.t) continue;
      const title = this.chordTitle(x.chord);
      if (!title) continue;
      const cx = xOf(x.t);
      if (title === lastName && cx - lastX < pxPerSec * 2) continue;
      if (cx - 6 < lastEnd + 6) continue; // no room before the next label
      lastName = title;
      lastX = cx;
      lastEnd = cx - 6 + ctx.measureText(title).width;
      ctx.fillStyle = P.text;
      ctx.fillText(title, cx - 6, yTop - 18);
    }
    // Palm-mute brackets under the staff.
    ctx.font = `800 10px ${FONT_UI}`;
    ctx.textBaseline = "middle";
    let pmStart: number | null = null;
    let pmEnd = 0;
    const flushPm = (): void => {
      if (pmStart === null) return;
      const xa = xOf(pmStart) - 6;
      const xb = Math.max(xa + 30, xOf(pmEnd) + 8);
      const y = yBot + 16;
      ctx.fillStyle = P.muted;
      ctx.textAlign = "left";
      ctx.fillText("P.M.", xa, y);
      ctx.strokeStyle = P.faint;
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(xa + 26, y);
      ctx.lineTo(xb, y);
      ctx.lineTo(xb, y - 5);
      ctx.stroke();
      ctx.setLineDash([]);
      pmStart = null;
    };
    for (const x of vis) {
      if (!x.tech.pm) continue;
      if (pmStart !== null && x.t - pmEnd > 0.45) flushPm();
      if (pmStart === null) pmStart = x.t;
      pmEnd = x.t;
    }
    flushPm();

    // Notes.
    const fontPx = Math.round(clamp(gap * 0.85, 10, 14));
    const prevOnString = new Map<number, FretNote>();
    for (const x of vis) {
      const cx = xOf(x.t);
      const cy = ry(x.s);
      const col = stringColor(x.s);
      // Tab marks stay tab marks: x for a mute, <n> for a harmonic. With
      // numbers on, n is the note's number in its octave's colour.
      const lk = this.nf ? this.look(x) : null;
      const n = lk ? lk.label : String(x.f);
      const label = x.tech.mt ? "x" : x.tech.hm ? `<${n}>` : n;
      const ink = lk && !x.tech.mt ? lk.color : col;
      // Played notes step back, the way the old lane dimmed them past the line.
      const played = x.t + Math.max(x.sus, 0.14) < now;
      ctx.globalAlpha = played ? 0.5 : 1;
      // Sustain line, or vibrato squiggle above it.
      if (x.sus > 0.15) {
        const xe = xOf(x.t + x.sus);
        ctx.strokeStyle = rgba(col, x.tech.vb ? 0.9 : 0.35);
        ctx.lineWidth = x.tech.vb ? 1.6 : 2;
        ctx.beginPath();
        if (x.tech.vb) {
          for (let xx = cx + 10; xx <= Math.max(cx + 24, xe); xx += 2) ctx.lineTo(xx, cy - gap * 0.55 + Math.sin(xx * 0.55) * 2.5);
        } else {
          ctx.moveTo(cx + 10, cy);
          ctx.lineTo(xe, cy);
        }
        ctx.stroke();
      }
      // Hammer-on / pull-off arc from the previous note on this string.
      const prev = prevOnString.get(x.s);
      if ((x.tech.ho || x.tech.po) && prev && x.t - prev.t < 1.2) {
        const xa = xOf(prev.t);
        const xm = (xa + cx) / 2;
        ctx.strokeStyle = P.muted;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(xa + 4, cy - 9);
        ctx.quadraticCurveTo(xm, cy - gap * 0.95, cx - 4, cy - 9);
        ctx.stroke();
        ctx.fillStyle = P.muted;
        ctx.font = `700 10px ${FONT_UI}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(x.tech.ho ? "h" : "p", xm, cy - gap * 0.6);
      }
      prevOnString.set(x.s, x);
      // Fret number, knocked out of the line.
      ctx.font = `800 ${fontPx}px ${FONT_NUM}`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "#0a0f1a";
      ctx.fillRect(cx - tw / 2 - 3, cy - fontPx / 2 - 2, tw + 6, fontPx + 4);
      if (lk?.toneRing) {
        ctx.strokeStyle = lk.toneRing;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(tw / 2 + 4, fontPx * 0.75), 0, Math.PI * 2);
        ctx.stroke();
      }
      if (isSounding(x, now)) {
        ctx.shadowColor = ink;
        ctx.shadowBlur = 14;
      }
      ctx.fillStyle = rgba(ink, 0.95);
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, cx, cy + 0.5);
      ctx.shadowBlur = 0;
      // Slides, bends, taps, accents.
      const slide = (x.tech.sl as number) || (x.tech.slu as number) || 0;
      if (slide) {
        const up = slide > x.f;
        ctx.strokeStyle = P.muted;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(cx + tw / 2 + 4, cy + (up ? 5 : -5));
        ctx.lineTo(cx + tw / 2 + 16, cy + (up ? -5 : 5));
        ctx.stroke();
        if (x.tech.sl) {
          ctx.fillStyle = P.muted;
          ctx.font = `700 10px ${FONT_NUM}`;
          ctx.textAlign = "left";
          ctx.fillText(this.slideTarget(chart, x), cx + tw / 2 + 18, cy);
        }
      }
      if (x.tech.bn) {
        const bx = cx + tw / 2 + 3;
        const top = cy - gap * 1.1;
        ctx.strokeStyle = P.text;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(bx, cy);
        ctx.quadraticCurveTo(bx + 12, cy, bx + 12, top);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(bx + 8, top + 5);
        ctx.lineTo(bx + 12, top);
        ctx.lineTo(bx + 16, top + 5);
        ctx.stroke();
        ctx.fillStyle = P.text;
        ctx.font = `700 10px ${FONT_UI}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(bendLabel(x.tech.bn as number), bx + 12, top - 2);
      }
      if (x.tech.tp || x.tech.ac) {
        ctx.fillStyle = P.muted;
        ctx.font = `800 10px ${FONT_UI}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(x.tech.tp ? "T" : ">", cx, yTop - 6);
      }
      ctx.globalAlpha = 1;
    }

    // Playhead.
    ctx.strokeStyle = P.accent;
    ctx.lineWidth = 2;
    ctx.shadowColor = P.accent;
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.moveTo(px, yTop - 12);
    ctx.lineTo(px, yBot + 12);
    ctx.stroke();
    ctx.shadowBlur = 0;

    // One-line HUD over a backing, so a section box sliding under it stays legible.
    const hudText = [chart.tuningName, this.nf ? this.keyText : "", this.sectionAt(f, now)].filter(Boolean).join("  ·  ");
    const warning = this.keyWarning();
    ctx.font = `800 11px ${FONT_UI}`;
    const nameW = ctx.measureText(this.trackName.toUpperCase()).width;
    ctx.font = `600 11px ${FONT_UI}`;
    const restW = ctx.measureText(hudText).width;
    const warnW = warning ? ctx.measureText(warning).width + 14 : 0;
    ctx.fillStyle = "rgba(7,10,18,0.82)";
    this.rr(4, 2, nameW + restW + warnW + 24, 17, 5);
    ctx.fill();
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.font = `800 11px ${FONT_UI}`;
    ctx.fillStyle = ROLE_COLORS[this.role];
    ctx.fillText(this.trackName.toUpperCase(), 10, 5);
    ctx.font = `600 11px ${FONT_UI}`;
    ctx.fillStyle = P.muted;
    ctx.fillText(hudText, 18 + nameW, 5);
    if (warning) {
      ctx.fillStyle = CHROMATIC_COLOR;
      ctx.fillText(warning, 32 + nameW + restW, 5);
    }
  }

  // --- Neck coach ------------------------------------------------------------

  private drawCoach(chart: FretChart, now: number, f: Frame): void {
    const { ctx, W, H } = this;
    const P = this.palette;
    this.paintBg("#0c1120", "#06080e");
    const n = chart.n;
    const F = neckFretCount(chart.maxFret);
    const nx0 = 52;
    const nx1 = W - 20;
    const neckTop = Math.max(70, H * 0.13);
    const neckH = clamp(H * 0.38, 120, 30 * n + 20);
    const fretX = (b: number): number => nx0 + fretWireFraction(b, F) * (nx1 - nx0);
    const cxOf = (fr: number): number => (fr === 0 ? nx0 - 16 : (fretX(fr - 1) + fretX(fr)) / 2);
    const sy = (s: number): number => neckTop + 14 + (this.row(chart, s) / Math.max(1, n - 1)) * (neckH - 28);

    // The neck, with its inlays.
    const wood = ctx.createLinearGradient(0, neckTop, 0, neckTop + neckH);
    wood.addColorStop(0, "#1d1712");
    wood.addColorStop(1, "#140f0c");
    ctx.fillStyle = wood;
    this.rr(nx0, neckTop, nx1 - nx0, neckH, 6);
    ctx.fill();
    ctx.fillStyle = "rgba(240,232,215,0.18)";
    for (const b of [3, 5, 7, 9, 15, 17, 19, 21]) {
      if (b > F) continue;
      ctx.beginPath();
      ctx.arc(cxOf(b), neckTop + neckH / 2, 6, 0, Math.PI * 2);
      ctx.fill();
    }
    for (const b of [12, 24]) {
      if (b > F) continue;
      for (const d of [-1, 1]) {
        ctx.beginPath();
        ctx.arc(cxOf(b), neckTop + neckH / 2 + d * neckH * 0.22, 6, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    // The hand window.
    const an = anchorAt(chart, now + 0.2);
    const ax = fretX(an.fret - 1);
    const bx = fretX(Math.min(F, an.fret - 1 + an.width));
    ctx.fillStyle = rgba(P.accent, 0.13);
    ctx.fillRect(ax, neckTop - 6, bx - ax, neckH + 12);
    ctx.strokeStyle = rgba(P.accent, 0.7);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(ax, neckTop - 6, bx - ax, neckH + 12);
    ctx.fillStyle = P.accent;
    ctx.font = `800 10.5px ${FONT_UI}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(`HAND · FRETS ${an.fret}–${an.fret + an.width - 1}`, ax, neckTop - 10);
    // Frets, nut, numbers, strings.
    ctx.strokeStyle = "#b9b2a4";
    ctx.lineWidth = 2;
    for (let b = 1; b <= F; b += 1) {
      ctx.beginPath();
      ctx.moveTo(fretX(b), neckTop);
      ctx.lineTo(fretX(b), neckTop + neckH);
      ctx.stroke();
    }
    ctx.strokeStyle = "#efe9da";
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(nx0, neckTop);
    ctx.lineTo(nx0, neckTop + neckH);
    ctx.stroke();
    ctx.fillStyle = P.faint;
    ctx.font = `700 10.5px ${FONT_NUM}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const b of [1, 3, 5, 7, 9, 12, 15, 17, 19, 21, 24]) {
      if (b <= F) ctx.fillText(String(b), cxOf(b), neckTop + neckH + 8);
    }
    for (let s = 0; s < n; s += 1) {
      ctx.strokeStyle = rgba(stringColor(s), 0.7);
      ctx.lineWidth = this.gauge(chart, s);
      ctx.beginPath();
      ctx.moveTo(nx0, sy(s));
      ctx.lineTo(nx1, sy(s));
      ctx.stroke();
      ctx.fillStyle = stringColor(s);
      ctx.font = `800 12px ${FONT_NUM}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(stringLabel(chart.open, s), 16, sy(s));
    }

    // The shape being held, outlined.
    const shape = lastAtOrBefore(chart.changes, now + 0.25);
    if (shape && now <= shape.end + 0.6) {
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = "rgba(240,236,220,0.55)";
      ctx.lineWidth = 1.5;
      shape.frets.forEach((fr, s) => {
        if (fr > 0 && s < n) {
          ctx.beginPath();
          ctx.arc(cxOf(fr), sy(s), 13, 0, Math.PI * 2);
          ctx.stroke();
        }
      });
      ctx.setLineDash([]);
    }

    // Next notes count down in place; sounding notes light with their finger.
    const lead = lookAheadSec(COACH_LEAD_SEC, f.scrollMul);
    const vis = visibleNotes(chart.notes, now - 0.2, now + lead);
    const drawn = new Set<string>();
    for (const x of vis) {
      if (x.t <= now) continue;
      const key = `${x.s}:${x.f}`;
      if (drawn.has(key)) continue;
      drawn.add(key);
      const cx = cxOf(x.f);
      const cy = sy(x.s);
      const col = this.look(x).color;
      const p = 1 - (x.t - now) / lead;
      ctx.fillStyle = rgba(col, 0.12 + 0.2 * p);
      ctx.beginPath();
      ctx.arc(cx, cy, 12, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = col;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, 13, -Math.PI / 2, -Math.PI / 2 + p * Math.PI * 2);
      ctx.stroke();
    }
    const sounding = vis.filter((x) => isSounding(x, now));
    // Any octave counts: with numbers on, every other copy of a sounding
    // note's number on the neck is ringed.
    if (this.nf) {
      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = P.text;
      ctx.lineWidth = 1.6;
      for (const x of sounding) {
        if (x.tech.mt) continue;
        for (let s = 0; s < n; s += 1) {
          for (let fr = 0; fr <= F; fr += 1) {
            if ((chart.open[s] + fr - x.midi) % 12 !== 0 || (s === x.s && fr === x.f)) continue;
            ctx.beginPath();
            ctx.arc(cxOf(fr), sy(s), 15, 0, Math.PI * 2);
            ctx.stroke();
          }
        }
      }
      ctx.restore();
    }
    for (const x of sounding) {
      const cx = cxOf(x.f);
      const cy = sy(x.s);
      const look = this.look(x);
      // Frets mode lights every sounding dot solid; numbers keep the tier's shape.
      if (!this.nf) look.shape = "fill";
      const col = look.color;
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 30);
      g.addColorStop(0, rgba(col, 0.5));
      g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g;
      ctx.fillRect(cx - 30, cy - 30, 60, 60);
      this.drawDot(cx, cy, 13, look);
      const finger = x.finger > 0 ? x.finger : shape && shape.frets[x.s] === x.f ? shape.fingers[x.s] ?? -1 : -1;
      if (finger > 0) {
        ctx.fillStyle = P.text;
        ctx.beginPath();
        ctx.arc(cx + 11, cy - 11, 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = GEM_INK;
        ctx.font = `800 9px ${FONT_UI}`;
        ctx.fillText(String(finger), cx + 11, cy - 10.5);
      }
    }

    // Lower deck: the coming chord shapes, and the next notes in words.
    const deckTop = neckTop + neckH + 40;
    const deckH = H - deckTop - 14;
    if (deckH > 90) {
      const cardW = clamp(W * 0.1, 78, 104);
      const cardH = Math.min(deckH, cardW * 1.3);
      if (chart.changes.length > 0) {
        ctx.fillStyle = P.faint;
        ctx.font = `800 10.5px ${FONT_UI}`;
        ctx.textAlign = "left";
        ctx.textBaseline = "bottom";
        ctx.fillText("CHORD SHAPES", 20, deckTop - 6);
        const maxCards = Math.max(1, Math.floor((W * (W > 640 ? 0.62 : 0.96)) / (cardW + 10)));
        let i = Math.max(0, lowerBound(chart.changes, now + 0.001) - 1);
        for (let c = 0, cx = 20; c < maxCards && i < chart.changes.length; c += 1, i += 1, cx += cardW + 10) {
          this.chordCard(chart, chart.changes[i], cx, deckTop, cardW, cardH, now);
        }
      }
      if (W > 640) {
        const fx0 = W * 0.66;
        const fw = W - fx0 - 20;
        ctx.fillStyle = P.faint;
        ctx.font = `800 10.5px ${FONT_UI}`;
        ctx.textAlign = "left";
        ctx.textBaseline = "bottom";
        ctx.fillText("COMING UP", fx0, deckTop - 6);
        ctx.fillStyle = "rgba(15,21,34,0.8)";
        this.rr(fx0, deckTop, fw, cardH, 10);
        ctx.fill();
        ctx.font = `700 12px ${FONT_NUM}`;
        ctx.textBaseline = "top";
        const rows = Math.floor((cardH - 16) / 19);
        let k = 0;
        for (let i = lowerBound(chart.notes, now + 1e-6); i < chart.notes.length && k < rows; i += 1, k += 1) {
          const x = chart.notes[i];
          const tech = Object.keys(x.tech).map((key) => TECH_LABEL[key] ?? key).join(", ");
          const text = `${(x.t - now).toFixed(2).padStart(5)}s  ${stringLabel(chart.open, x.s)} ${String(x.f).padEnd(3)} ${tech}`;
          ctx.fillStyle = stringColor(x.s);
          ctx.fillText(text, fx0 + 12, deckTop + 10 + k * 19, fw - 24);
        }
      }
    }
    this.hud(chart, f, now);
  }

  // --- Nashville neck --------------------------------------------------------

  /**
   * The pentatonic box the hand is in at `t`: each hand position from the tab
   * (or derived from the notes when it has none) mapped once per key to the
   * box it overlaps most.
   */
  private handBoxAt(chart: FretChart, frame: NumberFrame, t: number): PositionInstance {
    if (!this.handBoxes || this.handBoxes.majorPc !== frame.majorPc) {
      this.handBoxes = {
        majorPc: frame.majorPc,
        list: chart.anchors.map((a) => ({ t: a.t, inst: positionForHand(frame, chart.open, a, NASHVILLE_NECK_FRETS) })),
      };
    }
    const list = this.handBoxes.list;
    return (lastAtOrBefore(list, t) ?? list[0]).inst;
  }

  /** A neck showing frets f0..f1 in a box, at real fret spacing; returns where frets and strings land. */
  private drawNeck(
    box: { x: number; y: number; w: number; h: number },
    f0: number,
    f1: number,
    n: number,
  ): { fx: (f: number) => number; sy: (s: number) => number; r: number } {
    const { ctx } = this;
    const openGap = f0 === 0 ? Math.min(28, box.w * 0.06) : 0;
    const x0 = box.x + openGap;
    const x1 = box.x + box.w;
    const wire = (k: number): number => 1 - 2 ** (-k / 12);
    const w0 = wire(Math.max(0, f0 - 1));
    const w1 = wire(f1);
    const xw = (k: number): number => x0 + ((wire(k) - w0) / (w1 - w0)) * (x1 - x0);
    const fx = (fr: number): number => (fr === 0 ? box.x + openGap / 2 : (xw(fr - 1) + xw(fr)) / 2);
    const gap = box.h / (n - 1 + 0.9);
    const top = box.y + gap * 0.45;
    const sy = (s: number): number => top + (n - 1 - s) * gap;
    const wood = ctx.createLinearGradient(0, box.y, 0, box.y + box.h);
    wood.addColorStop(0, "#1d1712");
    wood.addColorStop(1, "#140f0c");
    ctx.fillStyle = wood;
    ctx.fillRect(x0 - (f0 === 0 ? 0 : 2), box.y, x1 - x0 + 2, box.h);
    ctx.fillStyle = "rgba(240,232,215,0.16)";
    for (const m of [3, 5, 7, 9, 15, 17, 19, 21]) {
      if (m < f0 || m > f1) continue;
      ctx.beginPath();
      ctx.arc(fx(m), box.y + box.h / 2, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    for (const m of [12, 24]) {
      if (m < f0 || m > f1) continue;
      for (const d of [0.3, 0.7]) {
        ctx.beginPath();
        ctx.arc(fx(m), box.y + box.h * d, 5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.strokeStyle = "#b9b2a4";
    ctx.lineWidth = 2;
    for (let k = Math.max(1, f0); k <= f1; k += 1) {
      ctx.beginPath();
      ctx.moveTo(xw(k), box.y);
      ctx.lineTo(xw(k), box.y + box.h);
      ctx.stroke();
    }
    if (f0 === 0) {
      ctx.strokeStyle = "#efe9da";
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(x0, box.y);
      ctx.lineTo(x0, box.y + box.h);
      ctx.stroke();
    }
    for (let s = 0; s < n; s += 1) {
      ctx.strokeStyle = "rgba(230,226,214,0.55)";
      ctx.lineWidth = 0.8 + (n - 1 - s) * 0.35;
      ctx.beginPath();
      ctx.moveTo(box.x, sy(s));
      ctx.lineTo(x1, sy(s));
      ctx.stroke();
    }
    ctx.fillStyle = this.palette.faint;
    ctx.font = `700 10.5px ${FONT_NUM}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let fr = Math.max(1, f0); fr <= f1; fr += 1) ctx.fillText(String(fr), fx(fr), box.y + box.h + 5);
    const cell = xw(Math.min(f1, Math.max(1, f0) + 1)) - xw(Math.max(1, f0));
    return { fx, sy, r: Math.max(6, Math.min(gap * 0.42, Math.abs(cell) * 0.36)) };
  }

  /** A box's outline: around the lowest and highest dot on each string. */
  private boxOutline(
    inst: PositionInstance,
    m: { fx: (f: number) => number; sy: (s: number) => number; r: number },
    alpha: number,
    dashed: boolean,
  ): void {
    const { ctx } = this;
    const n = inst.perString.length;
    const pad = m.r + 5;
    const left: [number, number][] = [];
    const right: [number, number][] = [];
    for (let s = n - 1; s >= 0; s -= 1) left.push([m.fx(inst.perString[s][0]) - pad, m.sy(s)]);
    for (let s = 0; s < n; s += 1) right.push([m.fx(inst.perString[s][1]) + pad, m.sy(s)]);
    const pts: [number, number][] = [
      [left[0][0], left[0][1] - pad],
      ...left,
      [left[left.length - 1][0], left[left.length - 1][1] + pad],
      [right[0][0], right[0][1] + pad],
      ...right,
      [right[right.length - 1][0], right[right.length - 1][1] - pad],
    ];
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.closePath();
    ctx.lineJoin = "round";
    if (!dashed) {
      ctx.fillStyle = rgba(this.palette.accent, 0.08);
      ctx.fill();
    }
    if (dashed) ctx.setLineDash([6, 5]);
    ctx.strokeStyle = rgba(this.palette.accent, 0.55);
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();
  }

  /** How a neck dot looks: numbered, or its fret when labels are frets. */
  private neckLook(frame: NumberFrame, chart: FretChart, s: number, fr: number): Look {
    const look = this.pitchLook(frame, chart.open[s] + fr, null);
    if (!this.nf) look.label = String(fr);
    return look;
  }

  /**
   * The neck around the hand's box, with every number in the key on it,
   * brightest in the box and fading with distance to a floor: a position is a
   * focus, not a fence. The sounding note lights, every other octave of it is
   * ringed, and the next note counts down in place.
   */
  private drawNashville(chart: FretChart, now: number, f: Frame): void {
    const frame = this.keyFrame;
    if (!frame) {
      // No key, no numbers or boxes to draw: the coach, which needs neither, says so.
      this.drawCoach(chart, now, f);
      return;
    }
    const { ctx, W, H } = this;
    const P = this.palette;
    this.paintBg("#0c1120", "#06080e");
    const inst = this.handBoxAt(chart, frame, now + 0.2);
    // The window eases toward the box rather than jumping, so the bright
    // stretch of neck slides into the next box as the hand moves.
    if (!this.nashView) this.nashView = { lo: inst.lo, hi: inst.hi };
    const ease = Math.min(1, f.dt * 5);
    this.nashView.lo += (inst.lo - this.nashView.lo) * ease;
    this.nashView.hi += (inst.hi - this.nashView.hi) * ease;
    const focus = this.nashView;
    const f0 = Math.max(0, Math.floor(focus.lo) - 3);
    const f1 = Math.min(NASHVILLE_NECK_FRETS, f0 + NASHVILLE_WINDOW_FRETS);
    const m = this.drawNeck({ x: 14, y: 76, w: W - 28, h: Math.max(80, H - 76 - 40) }, f0, f1, chart.n);
    const inView = (fr: number): boolean => fr >= f0 && fr <= f1;

    for (const nb of neighbours(frame, chart.open, inst, NASHVILLE_NECK_FRETS)) {
      if (nb.hi >= f0 && nb.lo <= f1) this.boxOutline(nb, m, 0.7, true);
    }
    this.boxOutline(inst, m, 1, false);
    for (const kn of keyNotes(frame, chart.open, f0, f1)) {
      const w = focusWeight(kn.f, [focus]);
      const look = this.neckLook(frame, chart, kn.s, kn.f);
      ctx.save();
      ctx.globalAlpha = 0.75 * (0.25 + 0.75 * w);
      this.drawDot(m.fx(kn.f), m.sy(kn.s), m.r * (look.shape === "hollow" ? 0.78 : 1) * (0.72 + 0.28 * w), look);
      ctx.restore();
    }

    // The next notes: a ring that closes as they arrive.
    const lead = lookAheadSec(NASHVILLE_LEAD_SEC, f.scrollMul);
    const nextIdx = lowerBound(chart.notes, now + 1e-6);
    const next = chart.notes[nextIdx];
    if (next && next.t - now < lead) {
      const pr = 1 - (next.t - now) / lead;
      ctx.strokeStyle = P.text;
      ctx.lineWidth = 3;
      for (let i = nextIdx; i < chart.notes.length && chart.notes[i].t - next.t < 0.03; i += 1) {
        const x = chart.notes[i];
        if (!inView(x.f)) continue;
        ctx.beginPath();
        ctx.arc(m.fx(x.f), m.sy(x.s), m.r + 5, -Math.PI / 2, -Math.PI / 2 + pr * Math.PI * 2);
        ctx.stroke();
      }
    }

    const sounding = visibleNotes(chart.notes, now - 0.2, now).filter((x) => isSounding(x, now) && !x.tech.mt);
    // Any octave counts: ring every other copy of a sounding number in view.
    ctx.save();
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = P.text;
    ctx.lineWidth = 1.6;
    for (const x of sounding) {
      for (let s = 0; s < chart.n; s += 1) {
        for (let fr = f0; fr <= f1; fr += 1) {
          if ((chart.open[s] + fr - x.midi) % 12 !== 0 || (s === x.s && fr === x.f)) continue;
          ctx.beginPath();
          ctx.arc(m.fx(fr), m.sy(s), m.r + 4, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
    }
    ctx.restore();
    for (const x of sounding) {
      if (!inView(x.f)) continue;
      const look = this.neckLook(frame, chart, x.s, x.f);
      if (this.nf && x.chord && x.chord.size >= 2) Object.assign(look, this.pitchLook(frame, x.midi, x.chord.triad));
      this.drawDot(m.fx(x.f), m.sy(x.s), m.r * 1.18, look, look.color);
    }

    // What the last note was, in words.
    const lastIdx = lowerBound(chart.notes, now + 1e-6) - 1;
    const last = lastIdx >= 0 ? chart.notes[lastIdx] : null;
    if (last && now - last.t < 2 && !last.tech.mt) {
      const tier = tierOf(frame, last.midi);
      const what = tier === "chrom" ? "outside the key" : tier === "mode" ? "completes the scale" : "any octave counts";
      ctx.font = `800 12px ${FONT_UI}`;
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillStyle = tier === "chrom" ? CHROMATIC_COLOR : P.muted;
      ctx.fillText(`${degreeLabel(frame, last.midi)} · ${what}`, W - 16, 12);
    }
    this.hud(chart, f, now, `POSITION ${inst.k}`);
  }

  /**
   * A numbered dot (the mock-up's): filled, hollow or dashed by tier, a white
   * ring on the 1, a chord-tone ring outside that, the label on top.
   */
  private drawDot(cx: number, cy: number, r: number, look: Look, glow?: string): void {
    const { ctx } = this;
    ctx.save();
    if (glow) {
      ctx.shadowColor = glow;
      ctx.shadowBlur = r * 1.6;
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    if (look.shape === "fill") {
      ctx.fillStyle = look.color;
      ctx.fill();
    } else {
      ctx.fillStyle = "rgba(10,14,22,0.88)";
      ctx.fill();
      if (look.shape === "dashed") ctx.setLineDash([3, 2.5]);
      ctx.strokeStyle = look.color;
      ctx.lineWidth = 2.2;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.shadowBlur = 0;
    if (look.whiteRing) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 1.5, 0, Math.PI * 2);
      ctx.strokeStyle = this.palette.text;
      ctx.lineWidth = 2.4;
      ctx.stroke();
    }
    if (look.toneRing) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 3.5, 0, Math.PI * 2);
      ctx.strokeStyle = look.toneRing;
      ctx.lineWidth = 3.2;
      ctx.stroke();
    }
    if (look.label && r >= 5) {
      ctx.fillStyle = look.shape === "fill" ? GEM_INK : look.color;
      ctx.font = `800 ${Math.round(r * (look.label.length > 1 ? 0.92 : 1.1))}px ${FONT_NUM}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(look.label, cx, cy + 0.5);
    }
    ctx.restore();
  }

  /** A chord diagram card: name (or number), countdown, and the shape with its fingers or numbers. */
  private chordCard(chart: FretChart, ch: ChordChange, x: number, y: number, w: number, h: number, now: number): void {
    const { ctx } = this;
    const P = this.palette;
    const active = now >= ch.t - 0.05 && now <= ch.end + 0.6;
    ctx.fillStyle = active ? rgba(P.accent, 0.1) : "rgba(15,21,34,0.8)";
    this.rr(x, y, w, h, 10);
    ctx.fill();
    ctx.strokeStyle = active ? P.accent : P.line;
    ctx.lineWidth = active ? 1.5 : 1;
    ctx.stroke();
    ctx.fillStyle = P.text;
    ctx.font = `800 14px ${FONT_UI}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(this.chordTitle(ch), x + 10, y + 8, w - 20);
    ctx.fillStyle = P.faint;
    ctx.font = `700 10px ${FONT_NUM}`;
    ctx.textAlign = "right";
    const dtS = ch.t - now;
    ctx.fillText(dtS > 0.05 ? `in ${dtS.toFixed(1)}s` : "now", x + w - 8, y + 10);
    const n = chart.n;
    const frets = ch.frets.slice(0, n);
    const fretted = frets.filter((fr) => fr > 0);
    const lo = fretted.length ? Math.min(...fretted) : 1;
    const base = lo > 3 ? lo : 1;
    const gx = x + 16;
    const gw = w - 32;
    const gy = y + 44;
    const gh = h - 58;
    const rows = 4;
    if (gh <= 0) return;
    for (let s = 0; s < n; s += 1) {
      const sx = gx + (s / Math.max(1, n - 1)) * gw;
      ctx.strokeStyle = "rgba(220,228,250,0.4)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(sx, gy);
      ctx.lineTo(sx, gy + gh);
      ctx.stroke();
    }
    for (let r = 0; r <= rows; r += 1) {
      const ry = gy + (r / rows) * gh;
      const nut = r === 0 && base === 1;
      ctx.strokeStyle = nut ? "rgba(240,236,220,0.9)" : "rgba(220,228,250,0.3)";
      ctx.lineWidth = nut ? 3 : 1;
      ctx.beginPath();
      ctx.moveTo(gx, ry);
      ctx.lineTo(gx + gw, ry);
      ctx.stroke();
    }
    if (base > 1) {
      ctx.fillStyle = P.faint;
      ctx.font = `700 9.5px ${FONT_NUM}`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(`${base}fr`, gx - 3, gy + gh / rows / 2);
    }
    frets.forEach((fr, s) => {
      const sx = gx + (s / Math.max(1, n - 1)) * gw;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.font = `700 9.5px ${FONT_UI}`;
      ctx.fillStyle = P.muted;
      if (fr < 0) ctx.fillText("×", sx, gy - 2);
      else if (fr === 0) ctx.fillText("○", sx, gy - 2);
      else {
        const r = fr - base;
        if (r < 0 || r >= rows) return;
        const cy = gy + ((r + 0.5) / rows) * gh;
        if (this.nf) {
          // The shape's notes by number (or chord tone) instead of finger.
          this.drawDot(sx, cy, Math.min(8, gw / n / 1.4), this.pitchLook(this.nf, chart.open[s] + fr, ch.triad));
          return;
        }
        ctx.fillStyle = stringColor(s);
        ctx.beginPath();
        ctx.arc(sx, cy, Math.min(7, gw / n / 1.6), 0, Math.PI * 2);
        ctx.fill();
        const finger = ch.fingers[s];
        if (finger > 0) {
          ctx.fillStyle = GEM_INK;
          ctx.font = `800 8.5px ${FONT_UI}`;
          ctx.textBaseline = "middle";
          ctx.fillText(String(finger), sx, cy + 0.5);
        }
      }
    });
  }
}
