import type { Visualizer, VisualizerModule, VizInitContext, FrameContext, TransportState } from "@auralprimer/viz-sdk";

// NOTE: The host does not yet provide chord/key data to plugins.
// This visualizer renders a *placeholder* Nashville lane driven purely by the transport clock.

type Roman = "I" | "ii" | "iii" | "IV" | "V" | "vi";

function defaultRomanForBar(barIdx: number): Roman {
  // A simple, familiar loop.
  const loop: Roman[] = ["I", "IV", "V", "vi"];
  return loop[Math.abs(barIdx) % loop.length] as Roman;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

class NashvilleVisualizer implements Visualizer {
  private ctx2d!: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private lastState: TransportState | null = null;

  async init(ctx: VizInitContext): Promise<void> {
    this.ctx2d = ctx.ctx2d;
  }

  onResize(width: number, height: number, dpr: number): void {
    this.w = width;
    this.h = height;
    this.dpr = dpr;
  }

  update(_dt: number, state: TransportState): void {
    this.lastState = state;
  }

  render(frame: FrameContext): void {
    const g = frame.ctx2d;

    // Background
    g.clearRect(0, 0, frame.width, frame.height);
    g.fillStyle = "#0f1218";
    g.fillRect(0, 0, frame.width, frame.height);

    // Layout
    const originX = 24;
    const laneY = Math.floor(frame.height * 0.5);
    const laneH = clamp(Math.floor(frame.height * 0.35), 48, 120);
    const laneTop = laneY - Math.floor(laneH / 2);
    const laneBottom = laneY + Math.floor(laneH / 2);

    // Lane
    g.fillStyle = "rgba(255,255,255,0.04)";
    g.fillRect(originX, laneTop, frame.width - originX, laneH);
    g.strokeStyle = "rgba(255,255,255,0.12)";
    g.lineWidth = 1;
    g.strokeRect(originX, laneTop, frame.width - originX, laneH);

    const bpm = frame.state.bpm || 120;
    const [beatsPerBar] = frame.state.timeSignature || [4, 4];
    const secPerBeat = 60 / Math.max(1e-6, bpm);
    const secPerBar = secPerBeat * Math.max(1, beatsPerBar);

    // View: show ~8 bars ahead (clamped by canvas width).
    const pxPerSecond = 140;
    const windowSec = (frame.width - originX) / pxPerSecond;
    const t = frame.state.t;
    const t0 = t;
    const t1 = t + windowSec;

    const firstBar = Math.floor(t0 / secPerBar);
    const lastBar = Math.ceil(t1 / secPerBar);

    // Vertical bar lines + roman numerals.
    for (let bar = firstBar; bar <= lastBar; bar++) {
      const bt = bar * secPerBar;
      const x = originX + (bt - t) * pxPerSecond;
      const isDownbeatNow = t >= bt && t < bt + 0.1;
      const roman = defaultRomanForBar(bar);

      g.strokeStyle = "rgba(255,255,255,0.18)";
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(x, laneTop);
      g.lineTo(x, laneBottom);
      g.stroke();

      // Label (centered in bar)
      const cx = x + (secPerBar * pxPerSecond) / 2;
      g.font = "600 18px system-ui";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillStyle = isDownbeatNow ? "#ffd166" : "rgba(255,255,255,0.85)";
      g.fillText(roman, cx, laneY);

      // Bar index (small)
      g.font = "12px system-ui";
      g.fillStyle = "rgba(255,255,255,0.5)";
      g.fillText(String(bar), cx, laneBottom - 12);
    }

    // Playhead
    g.strokeStyle = "#3ee6a8";
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(originX, 0);
    g.lineTo(originX, frame.height);
    g.stroke();

    // HUD
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillStyle = "rgba(255,255,255,0.75)";
    g.font = "12px system-ui";
    g.fillText(
      `Nashville (placeholder) · bpm=${bpm.toFixed(1)} · timeSig=${beatsPerBar}/4 · t=${t.toFixed(2)}s · dpr=${this.dpr.toFixed(2)}`,
      12,
      18
    );

    if (this.lastState?.loop) {
      g.fillStyle = "rgba(255,255,255,0.55)";
      g.fillText(`loop: ${this.lastState.loop.t0.toFixed(2)}..${this.lastState.loop.t1.toFixed(2)}`, 12, 34);
    }
  }

  dispose(): void {
    // nothing
  }
}

export function createVisualizer(): Visualizer {
  return new NashvilleVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;

