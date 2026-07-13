import type { FrameContext, TransportState, Visualizer, VisualizerModule, VizInitContext } from "@auralprimer/viz-sdk";

type SongNote = {
  t_on: number;
  t_off?: number;
  pitch: number;
  velocity?: number;
  string?: number;
  fret?: number;
  s?: number;
  f?: number;
  channel?: number;
  trackName?: string;
};

export type FrettedNote = {
  t_on: number;
  t_off: number;
  pitch: number;
  velocity: number;
  stringIdx: number;
  fret: number;
  trackName?: string;
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function toFinite(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function integerOrNull(v: unknown): number | null {
  return Number.isInteger(v) ? (v as number) : null;
}

function fretBoundaryX(originX: number, w: number, fretCount: number, fret: number): number {
  const t = clamp(fret / Math.max(1, fretCount), 0, 1);
  return originX + w * t;
}

function fretCenterX(originX: number, w: number, fretCount: number, fret: number): number {
  const cellW = w / Math.max(1, fretCount);
  if (fret <= 0) return originX + Math.min(16, cellW * 0.35);
  const left = fretBoundaryX(originX, w, fretCount, fret - 1);
  const right = fretBoundaryX(originX, w, fretCount, fret);
  return (left + right) * 0.5;
}

function stringY(originY: number, h: number, stringCount: number, stringIdx: number): number {
  const t = clamp(stringIdx / Math.max(1, stringCount - 1), 0, 1);
  return originY + h * (1 - t);
}

export function parseFrettedNotes(raw: unknown, stringCount = 6, maxFret = 36): FrettedNote[] {
  if (!Array.isArray(raw)) return [];
  const out: FrettedNote[] = [];

  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const n = item as Partial<SongNote>;
    if (n.channel === 9) continue;

    const tOn = toFinite(n.t_on, Number.NaN);
    const pitch = toFinite(n.pitch, Number.NaN);
    const stringIdx = integerOrNull(n.string) ?? integerOrNull(n.s);
    const fret = integerOrNull(n.fret) ?? integerOrNull(n.f);
    if (!Number.isFinite(tOn) || !Number.isFinite(pitch)) continue;
    if (stringIdx === null || fret === null) continue;
    if (stringIdx < 0 || stringIdx >= stringCount) continue;
    if (fret < 0 || fret > maxFret) continue;

    const rawTOff = toFinite(n.t_off, Number.NaN);
    const tOff = Number.isFinite(rawTOff) && rawTOff > tOn ? rawTOff : tOn + 0.12;
    out.push({
      t_on: tOn,
      t_off: tOff,
      pitch,
      velocity: clamp(toFinite(n.velocity, 96), 0, 127),
      stringIdx,
      fret,
      trackName: typeof n.trackName === "string" ? n.trackName : undefined,
    });
  }

  out.sort((a, b) => a.t_on - b.t_on || a.stringIdx - b.stringIdx || a.fret - b.fret);
  return out;
}

export function pickFretboardNotes(
  notes: readonly FrettedNote[],
  timeSec: number,
  lookBehindSec = 0.08,
  lookAheadSec = 1.5,
): FrettedNote[] {
  const visible = notes.filter((note) => note.t_off >= timeSec - lookBehindSec && note.t_on <= timeSec + lookAheadSec);
  visible.sort((a, b) => {
    const aActive = a.t_on <= timeSec + 0.03 && a.t_off >= timeSec - 0.03;
    const bActive = b.t_on <= timeSec + 0.03 && b.t_off >= timeSec - 0.03;
    if (aActive !== bActive) return aActive ? -1 : 1;
    return Math.abs(a.t_on - timeSec) - Math.abs(b.t_on - timeSec) || a.t_on - b.t_on;
  });
  return visible.slice(0, 12);
}

class FretboardVisualizer implements Visualizer {
  private notes: FrettedNote[] = [];
  private dpr = 1;
  private stringCount = 6;
  private fretCount = 12;

  async init(ctx: VizInitContext): Promise<void> {
    this.notes = parseFrettedNotes(ctx.song?.notes, 9, 36);
    if (this.notes.length > 0) {
      this.stringCount = clamp(Math.max(...this.notes.map((n) => n.stringIdx)) + 1, 4, 9);
      this.fretCount = clamp(Math.max(12, Math.max(...this.notes.map((n) => n.fret))), 12, 36);
    }
  }

  onResize(_width: number, _height: number, dpr: number): void {
    this.dpr = dpr;
  }

  update(_dt: number, _state: TransportState): void {
    // Pure render from transport time.
  }

  render(frame: FrameContext): void {
    const g = frame.ctx2d;
    const bg = "#0f1218";
    const fg = "rgba(255,255,255,0.8)";

    g.clearRect(0, 0, frame.width, frame.height);
    g.fillStyle = bg;
    g.fillRect(0, 0, frame.width, frame.height);

    const pad = 18;
    const originX = pad + 10;
    const originY = 42;
    const fbW = Math.max(80, frame.width - originX - pad);
    const fbH = Math.max(60, frame.height - originY - pad - 18);
    const stringCount = this.stringCount;
    const fretCount = this.fretCount;

    g.fillStyle = "rgba(255,255,255,0.04)";
    g.fillRect(originX, originY, fbW, fbH);
    g.strokeStyle = "rgba(255,255,255,0.12)";
    g.lineWidth = 1;
    g.strokeRect(originX, originY, fbW, fbH);

    for (let f = 0; f <= fretCount; f += 1) {
      const x = fretBoundaryX(originX, fbW, fretCount, f);
      g.strokeStyle = f === 0 ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.16)";
      g.lineWidth = f === 0 ? 3 : 1;
      g.beginPath();
      g.moveTo(x, originY);
      g.lineTo(x, originY + fbH);
      g.stroke();

      if (f > 0 && (f <= 12 || f % 2 === 0)) {
        g.fillStyle = "rgba(255,255,255,0.35)";
        g.font = "11px system-ui";
        g.textAlign = "center";
        g.textBaseline = "top";
        g.fillText(String(f), fretCenterX(originX, fbW, fretCount, f), originY + fbH + 2);
      }
    }

    for (let s = 0; s < stringCount; s += 1) {
      const y = stringY(originY, fbH, stringCount, s);
      const thickness = lerp(2.8, 1.1, s / Math.max(1, stringCount - 1));
      g.strokeStyle = "rgba(255,255,255,0.24)";
      g.lineWidth = thickness;
      g.beginPath();
      g.moveTo(originX, y);
      g.lineTo(originX + fbW, y);
      g.stroke();
    }

    const visibleNotes = pickFretboardNotes(this.notes, frame.state.t);
    for (const note of visibleNotes) {
      const active = note.t_on <= frame.state.t + 0.03 && note.t_off >= frame.state.t - 0.03;
      const timeDist = Math.max(0, note.t_on - frame.state.t);
      const alpha = active ? 1 : clamp(0.28 + (1 - timeDist / 1.5) * 0.45, 0.24, 0.76);
      const cx = fretCenterX(originX, fbW, fretCount, note.fret);
      const cy = stringY(originY, fbH, stringCount, note.stringIdx);
      const radius = active ? 11 : 8;

      g.save();
      g.globalAlpha = alpha;
      g.fillStyle = active ? "rgba(62,230,168,0.22)" : "rgba(126,192,255,0.14)";
      g.beginPath();
      g.arc(cx, cy, radius + 8, 0, Math.PI * 2);
      g.fill();

      g.fillStyle = active ? "#3ee6a8" : "#7ec0ff";
      g.beginPath();
      g.arc(cx, cy, radius, 0, Math.PI * 2);
      g.fill();

      g.fillStyle = "rgba(0,0,0,0.72)";
      g.font = "700 10px system-ui";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(String(note.fret), cx, cy);
      g.restore();
    }

    g.fillStyle = fg;
    g.font = "12px system-ui";
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    const activeCount = visibleNotes.filter((n) => n.t_on <= frame.state.t + 0.03 && n.t_off >= frame.state.t - 0.03).length;
    g.fillText(
      `Fretboard  notes=${this.notes.length}  active=${activeCount}  t=${frame.state.t.toFixed(2)}s  dpr=${this.dpr.toFixed(2)}`,
      12,
      18,
    );

    if (this.notes.length === 0) {
      g.fillStyle = "rgba(255,255,255,0.78)";
      g.font = "700 14px system-ui";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("No fretted note metadata available", frame.width * 0.5, frame.height * 0.5);
    }
  }

  dispose(): void {
    this.notes = [];
  }
}

export function createVisualizer(): Visualizer {
  return new FretboardVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;
