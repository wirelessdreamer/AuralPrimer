import type { Visualizer, VisualizerModule, VizInitContext, FrameContext, TransportState } from "@auralprimer/viz-sdk";

class BeatsVisualizer implements Visualizer {
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
    g.fillStyle = "#10131a";
    g.fillRect(0, 0, frame.width, frame.height);

    // Beat grid (placeholder: 1 beat per second)
    const secondsPerBeat = 1;
    const pxPerSecond = 120;
    const originX = 20;
    const midY = Math.floor(frame.height * 0.5);

    g.strokeStyle = "rgba(255,255,255,0.15)";
    g.lineWidth = 1;

    const t = frame.state.t;
    const windowSec = (frame.width - originX) / pxPerSecond;
    const t0 = t;
    const t1 = t + windowSec;

    const firstBeat = Math.floor(t0 / secondsPerBeat);
    const lastBeat = Math.ceil(t1 / secondsPerBeat);

    for (let b = firstBeat; b <= lastBeat; b++) {
      const bt = b * secondsPerBeat;
      const x = originX + (bt - t) * pxPerSecond;
      g.beginPath();
      g.moveTo(x, midY - 60);
      g.lineTo(x, midY + 60);
      g.stroke();

      g.fillStyle = "rgba(255,255,255,0.6)";
      g.font = "12px system-ui";
      g.fillText(String(b), x + 4, midY - 70);
    }

    // Playhead
    g.strokeStyle = "#3ee6a8";
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(originX, 0);
    g.lineTo(originX, frame.height);
    g.stroke();

    // HUD
    g.fillStyle = "rgba(255,255,255,0.8)";
    g.font = "12px system-ui";
    g.fillText(`t=${t.toFixed(2)}s rate=${frame.state.playbackRate.toFixed(2)}x dpr=${this.dpr.toFixed(2)}`, 12, 18);

    if (this.lastState && this.lastState.isPlaying) {
      g.fillText("PLAY", 12, 34);
    } else {
      g.fillText("PAUSE", 12, 34);
    }
  }

  dispose(): void {
    // nothing
  }
}

export function createVisualizer(): Visualizer {
  return new BeatsVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;
