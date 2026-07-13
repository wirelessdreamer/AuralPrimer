import { beatGridLines, clampScrollSpeedMultiplier, scrollWindow } from "@auralprimer/viz-sdk";
import type { BeatGridLine, Visualizer, VisualizerModule, VizInitContext, FrameContext, TransportState } from "@auralprimer/viz-sdk";

type SongTimelineBeat = {
  time: number;
  measure?: number;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseTimelineBeats(songTimeline: unknown): SongTimelineBeat[] {
  if (!isObject(songTimeline) || !Array.isArray(songTimeline.beats)) return [];
  return songTimeline.beats
    .filter((beat): beat is SongTimelineBeat => {
      return isObject(beat) && typeof beat.time === "number" && Number.isFinite(beat.time);
    })
    .map((beat) => ({
      time: beat.time,
      measure: typeof beat.measure === "number" && Number.isFinite(beat.measure) ? beat.measure : undefined,
    }))
    .sort((a, b) => a.time - b.time);
}

export function timelineBeatGridLines(songTimeline: unknown, t: number, windowSec: number): BeatGridLine[] {
  const beats = parseTimelineBeats(songTimeline);
  if (beats.length === 0 || !(windowSec > 0)) return [];
  const end = t + windowSec;
  const lines: BeatGridLine[] = [];
  for (let i = 0; i < beats.length; i += 1) {
    const beat = beats[i];
    if (beat.time < t || beat.time > end) continue;
    const prev = i > 0 ? beats[i - 1] : null;
    const hasMeasure = Number.isFinite(beat.measure);
    const isDownbeat = hasMeasure && (!prev || prev.measure !== beat.measure);
    const barIndex = hasMeasure ? Math.max(0, Math.round((beat.measure ?? 1) - 1)) : i;
    lines.push({
      tSec: beat.time,
      x01: (beat.time - t) / windowSec,
      isDownbeat,
      barIndex,
    });
  }
  return lines;
}

class BeatsVisualizer implements Visualizer {
  private ctx2d!: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private lastState: TransportState | null = null;
  private songTimeline: unknown | null = null;

  async init(ctx: VizInitContext): Promise<void> {
    this.ctx2d = ctx.ctx2d;
    this.songTimeline = ctx.song?.songTimeline ?? null;
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

    // Host-provided uniform scroll-speed multiplier (default 1.0). Higher
    // values spread notes further apart on screen; lower values compress
    // them. Tempo-lock is preserved -- only pixel density changes.
    const scrollMul = clampScrollSpeedMultiplier(frame.state.scrollSpeedMultiplier);
    const pxPerSecond = 120 * scrollMul;
    const originX = 20;
    const midY = Math.floor(frame.height * 0.5);

    g.strokeStyle = "rgba(255,255,255,0.15)";
    g.lineWidth = 1;

    const t = frame.state.t;
    const { windowSec } = scrollWindow({
      heightPx: frame.width - originX,
      basePxPerSec: 120,
      scrollMul
    });
    const laneW = frame.width - originX;

    const timelineLines = timelineBeatGridLines(this.songTimeline, t, windowSec);
    const gridLines =
      timelineLines.length > 0
        ? timelineLines
        : beatGridLines({
            t,
            windowSec,
            bpm: frame.state.bpm,
            beatsPerBar: frame.state.timeSignature[0] ?? 4,
          });
    for (const line of gridLines) {
      const x = originX + line.x01 * laneW;
      g.strokeStyle = line.isDownbeat ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.15)";
      g.lineWidth = line.isDownbeat ? 2 : 1;
      g.beginPath();
      g.moveTo(x, midY - 60);
      g.lineTo(x, midY + 60);
      g.stroke();

      g.fillStyle = "rgba(255,255,255,0.6)";
      g.font = "12px system-ui";
      g.fillText(line.isDownbeat ? `m${line.barIndex + 1}` : line.tSec.toFixed(1), x + 4, midY - 70);
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
