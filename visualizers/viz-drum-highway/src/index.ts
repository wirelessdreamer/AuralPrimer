import type { FrameContext, TransportState, Visualizer, VisualizerModule, VizInitContext } from "@auralprimer/viz-sdk";

type SongNote = {
  t_on: number;
  t_off?: number;
  pitch: number;
  velocity?: number;
  channel?: number;
  trackName?: string;
};

type PadLaneId = "snare" | "hat" | "crash" | "ride" | "tom1" | "tom2" | "tom3";
type LaneId = "kick" | PadLaneId;

type LaneNote = {
  t: number;
  lane: LaneId;
  velocity: number;
  pitch: number;
};

const PAD_LANE_ORDER: PadLaneId[] = ["snare", "hat", "crash", "ride", "tom1", "tom2", "tom3"];

const PAD_LANE_LABELS: Record<PadLaneId, string> = {
  snare: "SNARE",
  hat: "HI-HAT",
  crash: "CRASH",
  ride: "RIDE",
  tom1: "TOM1",
  tom2: "TOM2",
  tom3: "TOM3"
};

const PAD_LANE_COLORS: Record<PadLaneId, string> = {
  snare: "#f05f7f",
  hat: "#5ed7f0",
  crash: "#8af07b",
  ride: "#77c5ff",
  tom1: "#a490ff",
  tom2: "#ffb85c",
  tom3: "#ff7f50"
};

const PAD_LANE_BG: Record<PadLaneId, string> = {
  snare: "rgba(240,95,127,0.08)",
  hat: "rgba(94,215,240,0.08)",
  crash: "rgba(138,240,123,0.08)",
  ride: "rgba(119,197,255,0.08)",
  tom1: "rgba(164,144,255,0.08)",
  tom2: "rgba(255,184,92,0.08)",
  tom3: "rgba(255,127,80,0.08)"
};

const KICK_COLOR = "#e6f069";
const KICK_BG = "rgba(230,240,105,0.08)";

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function toFinite(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function snap(v: number): number {
  return Math.round(v) + 0.5;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function roundRectPath(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = clamp(r, 0, Math.min(w, h) * 0.5);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.lineTo(x + w - rr, y);
  g.quadraticCurveTo(x + w, y, x + w, y + rr);
  g.lineTo(x + w, y + h - rr);
  g.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  g.lineTo(x + rr, y + h);
  g.quadraticCurveTo(x, y + h, x, y + h - rr);
  g.lineTo(x, y + rr);
  g.quadraticCurveTo(x, y, x + rr, y);
  g.closePath();
}

function midiToLane(pitch: number): LaneId | null {
  if (pitch === 35 || pitch === 36) return "kick";
  if (pitch === 37 || pitch === 38 || pitch === 39 || pitch === 40) return "snare";
  if (pitch === 42 || pitch === 44 || pitch === 46) return "hat";
  if (pitch === 49 || pitch === 52 || pitch === 55 || pitch === 57) return "crash";
  if (pitch === 51 || pitch === 53 || pitch === 59) return "ride";
  if (pitch === 48 || pitch === 50) return "tom1";
  if (pitch === 45 || pitch === 47) return "tom2";
  if (pitch === 41 || pitch === 43) return "tom3";
  return null;
}

function parseSongNotes(raw: unknown): SongNote[] {
  if (!Array.isArray(raw)) return [];
  const out: SongNote[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const n = item as Partial<SongNote>;
    const tOn = toFinite(n.t_on, Number.NaN);
    const pitch = toFinite(n.pitch, Number.NaN);
    if (!Number.isFinite(tOn) || !Number.isFinite(pitch)) continue;
    out.push({
      t_on: tOn,
      t_off: Number.isFinite(n.t_off as number) ? (n.t_off as number) : undefined,
      pitch,
      velocity: Number.isFinite(n.velocity as number) ? (n.velocity as number) : undefined,
      channel: Number.isFinite(n.channel as number) ? (n.channel as number) : undefined,
      trackName: typeof n.trackName === "string" ? n.trackName : undefined
    });
  }
  out.sort((a, b) => a.t_on - b.t_on);
  return out;
}

class DrumHighwayVisualizer implements Visualizer {
  private notes: LaneNote[] = [];
  private dpr = 1;

  async init(ctx: VizInitContext): Promise<void> {
    const parsed = parseSongNotes(ctx.song?.notes);
    this.notes = parsed
      .map((n) => {
        const lane = midiToLane(n.pitch);
        if (!lane) return null;
        return {
          t: n.t_on,
          lane,
          velocity: clamp(toFinite(n.velocity, 96), 1, 127),
          pitch: n.pitch
        } as LaneNote;
      })
      .filter((n): n is LaneNote => n !== null)
      .sort((a, b) => a.t - b.t || a.pitch - b.pitch);
  }

  onResize(_width: number, _height: number, dpr: number): void {
    this.dpr = dpr;
  }

  update(_dt: number, _state: TransportState): void {
    // No stateful animation outside transport time.
  }

  render(frame: FrameContext): void {
    const g = frame.ctx2d;
    const w = frame.width;
    const h = frame.height;
    const state = frame.state;

    g.clearRect(0, 0, w, h);

    const bgGrad = g.createLinearGradient(0, 0, 0, h);
    bgGrad.addColorStop(0, "#040811");
    bgGrad.addColorStop(1, "#0b1321");
    g.fillStyle = bgGrad;
    g.fillRect(0, 0, w, h);

    const glow = g.createRadialGradient(w * 0.5, h * 0.82, 24, w * 0.5, h * 0.82, h * 0.7);
    glow.addColorStop(0, "rgba(84,140,255,0.09)");
    glow.addColorStop(1, "rgba(84,140,255,0)");
    g.fillStyle = glow;
    g.fillRect(0, 0, w, h);

    const padX = Math.max(28, Math.round(w * 0.06));
    const topY = 34;
    const bottomY = h - 24;
    const laneCount = PAD_LANE_ORDER.length;
    const laneGap = 10;
    const kickLaneH = clamp(Math.round(h * 0.034), 18, 26);
    const padTravelTopY = topY + 18;
    const hitY = bottomY - 74;
    const padBottomY = bottomY - 34;
    const kickLaneY = hitY - kickLaneH * 0.5;
    const laneAreaW = w - padX * 2;
    const laneW = (laneAreaW - laneGap * (laneCount - 1)) / laneCount;
    const scrollPxPerSec = clamp(Math.round(h * 0.36), 190, 320);
    const lookAheadSec = Math.max(1.6, (hitY - padTravelTopY) / scrollPxPerSec);
    const lookBehindSec = 0.22;

    const laneBounds = (laneIdx: number): { x: number; w: number; center: number } => {
      const x = padX + laneIdx * (laneW + laneGap);
      return { x, w: laneW, center: x + laneW * 0.5 };
    };

    const timelineY = (dt: number): number => snap(hitY - dt * scrollPxPerSec);

    const outerX = padX - 10;
    const outerY = topY - 8;
    const outerW = w - outerX * 2;
    const outerH = bottomY - topY + 16;

    g.fillStyle = "#09111d";
    roundRectPath(g, outerX, outerY, outerW, outerH, 16);
    g.fill();
    g.strokeStyle = "rgba(84,133,190,0.24)";
    g.lineWidth = 1.5;
    g.stroke();

    const bpm = Math.max(1, Number.isFinite(state.bpm) ? state.bpm : 120);
    const beatSec = 60 / bpm;
    const measureBeats = Math.max(1, Array.isArray(state.timeSignature) ? state.timeSignature[0] || 4 : 4);
    const subdivisionSec = beatSec * 0.5;
    const lanePulse: Record<LaneId, number> = {
      kick: 0,
      snare: 0,
      hat: 0,
      crash: 0,
      ride: 0,
      tom1: 0,
      tom2: 0,
      tom3: 0
    };

    for (const n of this.notes) {
      const dt = n.t - state.t;
      if (Math.abs(dt) > 0.16) continue;
      const intensity = clamp(1 - Math.abs(dt) / 0.16, 0, 1) * lerp(0.72, 1, n.velocity / 127);
      lanePulse[n.lane] = Math.max(lanePulse[n.lane], intensity);
    }

    for (let i = 0; i < laneCount; i += 1) {
      const lane = PAD_LANE_ORDER[i];
      const geom = laneBounds(i);
      const laneGrad = g.createLinearGradient(0, padTravelTopY, 0, padBottomY);
      laneGrad.addColorStop(0, PAD_LANE_BG[lane].replace("0.08", "0.07"));
      laneGrad.addColorStop(0.78, PAD_LANE_BG[lane].replace("0.08", "0.11"));
      laneGrad.addColorStop(1, PAD_LANE_BG[lane].replace("0.08", "0.16"));
      g.fillStyle = laneGrad;
      roundRectPath(g, geom.x, padTravelTopY, geom.w, padBottomY - padTravelTopY, 10);
      g.fill();

      g.strokeStyle = "rgba(255,255,255,0.1)";
      g.lineWidth = 1;
      roundRectPath(g, snap(geom.x), snap(padTravelTopY), geom.w, padBottomY - padTravelTopY, 10);
      g.stroke();
    }

    g.fillStyle = KICK_BG;
    roundRectPath(g, padX, kickLaneY, laneAreaW, kickLaneH, 8);
    g.fill();
    g.strokeStyle = "rgba(255,255,255,0.1)";
    g.lineWidth = 1;
    roundRectPath(g, snap(padX), snap(kickLaneY), laneAreaW, kickLaneH, 8);
    g.stroke();

    const firstSubdivision = Math.floor((state.t - lookBehindSec) / subdivisionSec) - 1;
    const lastSubdivision = Math.ceil((state.t + lookAheadSec) / subdivisionSec) + 1;
    for (let si = firstSubdivision; si <= lastSubdivision; si += 1) {
      const lineT = si * subdivisionSec;
      const dt = lineT - state.t;
      if (dt < -lookBehindSec || dt > lookAheadSec) continue;
      const y = timelineY(dt);
      if (y < padTravelTopY || y > bottomY) continue;
      const isBeat = si % 2 === 0;
      const beatIndex = Math.round(si / 2);
      const isMeasure = isBeat && ((beatIndex % measureBeats + measureBeats) % measureBeats === 0);

      g.strokeStyle = isMeasure
        ? "rgba(214,240,255,0.36)"
        : isBeat
          ? "rgba(170,214,255,0.17)"
          : "rgba(170,214,255,0.07)";
      g.lineWidth = isMeasure ? 2.5 : isBeat ? 1.4 : 1;
      g.beginPath();
      g.moveTo(snap(padX - 8), y);
      g.lineTo(snap(padX + laneAreaW + 8), y);
      g.stroke();

      if (isMeasure) {
        g.fillStyle = "rgba(223,237,255,0.72)";
        g.font = "700 10px monospace";
        g.textAlign = "left";
        g.textBaseline = "bottom";
        g.fillText(`${Math.floor(beatIndex / measureBeats) + 1}`, padX - 24, y - 3);
      }
    }

    const hitBandY = hitY - 24;
    const hitBandH = 34;
    const hitBandGrad = g.createLinearGradient(0, hitBandY, 0, hitBandY + hitBandH);
    hitBandGrad.addColorStop(0, "rgba(214,234,255,0.03)");
    hitBandGrad.addColorStop(0.45, "rgba(214,234,255,0.12)");
    hitBandGrad.addColorStop(1, "rgba(214,234,255,0.04)");
    g.fillStyle = hitBandGrad;
    roundRectPath(g, padX - 6, hitBandY, laneAreaW + 12, hitBandH, 12);
    g.fill();

    g.strokeStyle = "rgba(244,250,255,0.88)";
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(padX - 6, snap(hitY));
    g.lineTo(w - padX + 6, snap(hitY));
    g.stroke();

    g.strokeStyle = "rgba(231,241,105,0.88)";
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(padX + 2, snap(hitY));
    g.lineTo(padX + laneAreaW - 2, snap(hitY));
    g.stroke();

    g.fillStyle = "rgba(223,237,255,0.72)";
    g.font = "700 10px monospace";
    g.textAlign = "right";
    g.textBaseline = "bottom";
    g.fillText("PLAY HERE", padX + laneAreaW, hitBandY - 5);

    g.fillStyle = "rgba(223,237,255,0.88)";
    g.font = "700 10px monospace";
    g.textAlign = "left";
    g.textBaseline = "middle";
    g.fillText("KICK", padX + 10, hitY - 14);

    for (const n of this.notes) {
      const dt = n.t - state.t;
      if (dt < -lookBehindSec || dt > lookAheadSec + 0.2) continue;
      const progress = dt >= 0 ? clamp(1 - dt / lookAheadSec, 0, 1) : 1;
      const noteAlpha = dt >= 0 ? 0.54 + progress * 0.38 : 0.92 - clamp(-dt / lookBehindSec, 0, 1) * 0.34;

      if (n.lane === "kick") {
        const y = timelineY(dt);
        const noteH = clamp(10 + (n.velocity / 127) * 4, 10, 15);
        const inset = 10;
        const noteY = y - noteH * 0.5;
        g.save();
        g.globalAlpha = noteAlpha;
        g.fillStyle = "rgba(255,255,255,0.22)";
        roundRectPath(g, padX + inset, noteY - 1, laneAreaW - inset * 2, noteH + 2, noteH * 0.45);
        g.fill();
        g.fillStyle = KICK_COLOR;
        roundRectPath(g, padX + inset + 2, noteY, laneAreaW - inset * 2 - 4, noteH, noteH * 0.45);
        g.fill();
        g.strokeStyle = "rgba(16,21,29,0.95)";
        g.lineWidth = 1.4;
        g.stroke();
        g.restore();
        continue;
      }

      const laneIdx = PAD_LANE_ORDER.indexOf(n.lane);
      if (laneIdx < 0) continue;

      const y = timelineY(dt);
      const laneGeom = laneBounds(laneIdx);
      const noteW = laneGeom.w * 0.76;
      const noteH = clamp(14 + (n.velocity / 127) * 4, 14, 18);
      const noteX = laneGeom.center - noteW * 0.5;
      const noteY = y - noteH * 0.5;

      g.save();
      g.globalAlpha = noteAlpha;
      g.fillStyle = "rgba(255,255,255,0.18)";
      roundRectPath(g, noteX, noteY - 1, noteW, noteH + 2, noteH * 0.45);
      g.fill();
      g.fillStyle = PAD_LANE_COLORS[n.lane];
      roundRectPath(g, noteX + 1, noteY, noteW - 2, noteH, noteH * 0.45);
      g.fill();

      g.fillStyle = "rgba(255,255,255,0.2)";
      roundRectPath(g, noteX + 4, noteY + 2, noteW - 8, Math.max(3, noteH * 0.22), Math.max(2, noteH * 0.11));
      g.fill();

      g.strokeStyle = "rgba(16,21,29,0.95)";
      g.lineWidth = 1.35;
      g.stroke();
      g.restore();
    }

    for (let i = 0; i < laneCount; i += 1) {
      const lane = PAD_LANE_ORDER[i];
      const bottomGeom = laneBounds(i);
      const pulse = lanePulse[lane];
      const receptorX = bottomGeom.x + 3;
      const receptorW = bottomGeom.w - 6;
      const receptorY = hitY - 11;
      const receptorH = 22;

      g.fillStyle = "#0d1d30";
      roundRectPath(g, receptorX, receptorY, receptorW, receptorH, 8);
      g.fill();

      if (pulse > 0) {
        g.fillStyle = PAD_LANE_COLORS[lane].replace("#", "");
        const [r, gg, b] = [
          parseInt(g.fillStyle.slice(0, 2), 16),
          parseInt(g.fillStyle.slice(2, 4), 16),
          parseInt(g.fillStyle.slice(4, 6), 16)
        ];
        g.fillStyle = `rgba(${r},${gg},${b},${0.12 + pulse * 0.22})`;
        roundRectPath(g, receptorX - 2, receptorY - 2, receptorW + 4, receptorH + 4, 10);
        g.fill();
      }

      g.strokeStyle = "rgba(245,250,255,0.72)";
      g.lineWidth = 1.2;
      roundRectPath(g, receptorX, receptorY, receptorW, receptorH, 8);
      g.stroke();

      g.strokeStyle = PAD_LANE_COLORS[lane];
      g.lineWidth = 2.4;
      roundRectPath(g, receptorX, receptorY, receptorW, receptorH, 8);
      g.stroke();

      g.fillStyle = "rgba(223,237,255,0.7)";
      g.font = "700 9px monospace";
      g.textAlign = "center";
      g.textBaseline = "top";
      g.fillText(PAD_LANE_LABELS[lane], bottomGeom.center, receptorY + receptorH + 10);
    }

    g.fillStyle = "#0d1d30";
    roundRectPath(g, padX + 4, hitY - 7, laneAreaW - 8, 14, 7);
    g.fill();

    if (lanePulse.kick > 0) {
      g.fillStyle = `rgba(230,240,105,${0.12 + lanePulse.kick * 0.2})`;
      roundRectPath(g, padX + 2, hitY - 9, laneAreaW - 4, 18, 9);
      g.fill();
    }

    g.strokeStyle = "rgba(245,250,255,0.72)";
    g.lineWidth = 1.2;
    roundRectPath(g, padX + 4, hitY - 7, laneAreaW - 8, 14, 7);
    g.stroke();

    g.strokeStyle = KICK_COLOR;
    g.lineWidth = 2.5;
    roundRectPath(g, padX + 4, hitY - 7, laneAreaW - 8, 14, 7);
    g.stroke();

    g.fillStyle = "rgba(223,237,255,0.82)";
    g.font = "11px monospace";
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillText(`Drum Highway  bpm=${bpm.toFixed(1)}  t=${state.t.toFixed(2)}s`, 12, 18);

    if (this.notes.length === 0) {
      g.fillStyle = "rgba(255,255,255,0.8)";
      g.font = "700 16px monospace";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("No MIDI drum notes available for this song", w * 0.5, h * 0.5);
    }
  }

  dispose(): void {
    // No persistent resources.
  }
}

export function createVisualizer(): Visualizer {
  return new DrumHighwayVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;
