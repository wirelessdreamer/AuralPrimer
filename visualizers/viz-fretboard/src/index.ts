import type { FrameContext, TransportState, Visualizer, VisualizerModule, VizInitContext } from "@auralprimer/viz-sdk";

type Point = { x: number; y: number };

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function fretX(originX: number, w: number, fretCount: number, fret: number): number {
  const usableW = w;
  const t = clamp(fret / Math.max(1, fretCount), 0, 1);
  return originX + usableW * t;
}

function stringY(originY: number, h: number, stringCount: number, stringIdx: number): number {
  const t = clamp(stringIdx / Math.max(1, stringCount - 1), 0, 1);
  return originY + h * t;
}

function pickPlaceholderNote(state: TransportState): { stringIdx: number; fret: number } {
  // Deterministic placeholder “note cursor” using time.
  // Cycles frets 0..12 and strings 0..5 on bar boundaries.
  const bpm = state.bpm || 120;
  const [beatsPerBar] = state.timeSignature || [4, 4];
  const secPerBeat = 60 / Math.max(1e-6, bpm);
  const secPerBar = secPerBeat * Math.max(1, beatsPerBar);

  const bar = Math.floor(state.t / secPerBar);
  const withinBar = (state.t - bar * secPerBar) / secPerBar;

  const fret = Math.floor(withinBar * 13); // 0..12
  const stringIdx = ((bar % 6) + 6) % 6;
  return { stringIdx, fret };
}

class FretboardVisualizer implements Visualizer {
  private ctx2d!: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr = 1;

  async init(ctx: VizInitContext): Promise<void> {
    this.ctx2d = ctx.ctx2d;
  }

  onResize(width: number, height: number, dpr: number): void {
    this.w = width;
    this.h = height;
    this.dpr = dpr;
  }

  update(_dt: number, _state: TransportState): void {
    // nothing yet
  }

  render(frame: FrameContext): void {
    const g = frame.ctx2d;
    const bg = "#0f1218";
    const fg = "rgba(255,255,255,0.8)";

    g.clearRect(0, 0, frame.width, frame.height);
    g.fillStyle = bg;
    g.fillRect(0, 0, frame.width, frame.height);

    // Geometry
    const pad = 18;
    const originX = pad;
    const originY = 42;
    const fbW = frame.width - pad * 2;
    const fbH = frame.height - originY - pad;

    const stringCount = 6;
    const fretCount = 12;

    // Board background
    g.fillStyle = "rgba(255,255,255,0.04)";
    g.fillRect(originX, originY, fbW, fbH);
    g.strokeStyle = "rgba(255,255,255,0.12)";
    g.lineWidth = 1;
    g.strokeRect(originX, originY, fbW, fbH);

    // Frets
    for (let f = 0; f <= fretCount; f++) {
      const x = fretX(originX, fbW, fretCount, f);
      g.strokeStyle = f === 0 ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.16)";
      g.lineWidth = f === 0 ? 3 : 1;
      g.beginPath();
      g.moveTo(x, originY);
      g.lineTo(x, originY + fbH);
      g.stroke();

      if (f > 0) {
        g.fillStyle = "rgba(255,255,255,0.35)";
        g.font = "11px system-ui";
        g.textAlign = "center";
        g.textBaseline = "top";
        g.fillText(String(f), x + (fretX(originX, fbW, fretCount, f + 1) - x) / 2, originY + fbH + 2);
      }
    }

    // Strings
    for (let s = 0; s < stringCount; s++) {
      const y = stringY(originY, fbH, stringCount, s);
      const thickness = lerp(1.2, 2.6, s / (stringCount - 1));
      g.strokeStyle = "rgba(255,255,255,0.22)";
      g.lineWidth = thickness;
      g.beginPath();
      g.moveTo(originX, y);
      g.lineTo(originX + fbW, y);
      g.stroke();
    }

    // Placeholder note cursor
    const note = pickPlaceholderNote(frame.state);
    const fx0 = fretX(originX, fbW, fretCount, note.fret);
    const fx1 = fretX(originX, fbW, fretCount, note.fret + 1);
    const cy = stringY(originY, fbH, stringCount, note.stringIdx);
    const cx = (fx0 + fx1) / 2;

    g.fillStyle = "#3ee6a8";
    g.beginPath();
    g.arc(cx, cy, 8, 0, Math.PI * 2);
    g.fill();

    g.fillStyle = "rgba(0,0,0,0.65)";
    g.font = "700 10px system-ui";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(String(note.fret), cx, cy);

    // HUD
    g.fillStyle = fg;
    g.font = "12px system-ui";
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillText(
      `Fretboard (placeholder) · bpm=${frame.state.bpm.toFixed(1)} · t=${frame.state.t.toFixed(2)}s · dpr=${this.dpr.toFixed(2)}`,
      12,
      18
    );
  }

  dispose(): void {
    // nothing
  }
}

export function createVisualizer(): Visualizer {
  return new FretboardVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;

