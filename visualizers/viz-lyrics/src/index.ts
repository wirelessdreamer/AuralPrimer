import type { FrameContext, Visualizer, VisualizerModule, VizInitContext, TransportState } from "@auralprimer/viz-sdk";

type LyricsFile = {
  format: string;
  granularity?: "syllable" | "word" | string;
  job_id?: string;
  lines: Array<{
    start: number;
    end: number;
    text: string;
    chunks?: Array<{ start: number; end: number; text: string; char_start: number; char_end: number }>;
  }>;
};

function isLyricsFile(v: unknown): v is LyricsFile {
  if (!v || typeof v !== "object") return false;
  const o = v as any;
  return typeof o.format === "string" && Array.isArray(o.lines);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function findActiveLineIndex(lines: LyricsFile["lines"], t: number): number {
  // Prefer the first line whose [start,end] window contains t.
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (t >= l.start && t <= l.end) return i;
  }
  // Otherwise find last line that started.
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (t >= lines[i].start) idx = i;
  }
  return idx;
}

function computeHighlightCharIndex(line: LyricsFile["lines"][number], t: number): number {
  const txt = line.text ?? "";
  const chunks = line.chunks ?? [];
  if (!chunks.length) {
    // Line-level fallback: sweep based on [start,end]
    const dur = Math.max(0.001, line.end - line.start);
    const p = clamp((t - line.start) / dur, 0, 1);
    return Math.round(p * txt.length);
  }

  // Find active chunk; if between chunks, use the last chunk whose end <= t.
  let idx = -1;
  for (let i = 0; i < chunks.length; i++) {
    const c = chunks[i];
    if (t >= c.start && t <= c.end) {
      // partial progress through this chunk
      const dur = Math.max(0.001, c.end - c.start);
      const p = clamp((t - c.start) / dur, 0, 1);
      const span = Math.max(0, c.char_end - c.char_start);
      return c.char_start + Math.round(p * span);
    }
    if (t >= c.end) idx = i;
  }
  if (idx >= 0) return chunks[idx].char_end;
  return 0;
}

class LyricsVisualizer implements Visualizer {
  private ctx2d!: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private lastState: TransportState | null = null;

  private lyrics: LyricsFile | null = null;

  async init(ctx: VizInitContext): Promise<void> {
    this.ctx2d = ctx.ctx2d;
    if (isLyricsFile(ctx.song?.lyrics)) {
      this.lyrics = ctx.song!.lyrics;
    } else {
      this.lyrics = null;
    }
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
    const t = frame.state.t;

    // Background
    g.clearRect(0, 0, frame.width, frame.height);
    g.fillStyle = "#0b0e14";
    g.fillRect(0, 0, frame.width, frame.height);

    const padX = 24;
    const baselineY = Math.floor(frame.height * 0.62);

    // HUD
    g.fillStyle = "rgba(255,255,255,0.7)";
    g.font = "12px system-ui";
    g.fillText(`viz-lyrics · t=${t.toFixed(2)}s`, 12, 18);

    if (!this.lyrics || !this.lyrics.lines.length) {
      g.fillStyle = "rgba(255,255,255,0.8)";
      g.font = "16px system-ui";
      g.fillText("No lyrics loaded (features/lyrics.json missing)", padX, baselineY);
      return;
    }

    const idx = findActiveLineIndex(this.lyrics.lines, t);
    if (idx < 0) {
      g.fillStyle = "rgba(255,255,255,0.6)";
      g.font = "22px system-ui";
      g.fillText("(lyrics not started)", padX, baselineY);
      return;
    }

    const line = this.lyrics.lines[idx];
    const text = line.text ?? "";
    const splitAt = computeHighlightCharIndex(line, t);

    // Set font size based on canvas height.
    const fontPx = Math.max(20, Math.floor(frame.height * 0.18));
    g.font = `700 ${fontPx}px system-ui`;
    g.textBaseline = "alphabetic";

    // Outline + shadow for readability
    g.lineWidth = 6;
    g.strokeStyle = "rgba(0,0,0,0.75)";
    g.strokeText(text, padX, baselineY);

    // Base (unhighlighted)
    g.fillStyle = "rgba(255,255,255,0.9)";
    g.fillText(text, padX, baselineY);

    // Highlighted overlay (clipped)
    const highlightedText = text.slice(0, splitAt);
    if (highlightedText.length) {
      const metrics = g.measureText(highlightedText);
      const w = metrics.width;

      g.save();
      g.beginPath();
      // Clip to highlighted width.
      g.rect(padX - 4, baselineY - fontPx, w + 8, fontPx + 16);
      g.clip();

      g.fillStyle = "#ffd200";
      g.fillText(text, padX, baselineY);
      g.restore();
    }

    // Next line preview (if any)
    const next = this.lyrics.lines[idx + 1];
    if (next?.text) {
      g.font = `500 ${Math.max(14, Math.floor(fontPx * 0.6))}px system-ui`;
      g.fillStyle = "rgba(255,255,255,0.45)";
      g.fillText(next.text, padX, baselineY + Math.floor(fontPx * 0.85));
    }
  }

  dispose(): void {
    // nothing
  }
}

export function createVisualizer(): Visualizer {
  return new LyricsVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;

