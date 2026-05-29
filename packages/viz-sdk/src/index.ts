export * from "./draw";

export type TransportState = {
  t: number; // seconds (song time)
  isPlaying: boolean;
  /** Playback speed multiplier (tempo slowdown). 1.0 = normal. */
  playbackRate: number;
  /** Quarter-notes per minute (song tempo). */
  bpm: number;
  timeSignature: [number, number];
  loop?: { t0: number; t1: number };
  /**
   * Visual scroll-speed multiplier applied uniformly across all instruments.
   * 1.0 = each visualizer's built-in default. Higher values stretch notes
   * further apart on screen (more spacing, easier to read at fast tempos);
   * lower values compress them. Tempo-locking is preserved: notes still hit
   * at the correct beat times -- the multiplier only changes how much
   * horizontal/vertical pixel real estate each second of song time occupies.
   *
   * Visualizers that scroll along time (viz-drum-highway, viz-beats,
   * viz-nashville) multiply their `pxPerSecond` / `scrollPxPerSec` by this
   * value. Visualizers that aren't time-scrolling (viz-fretboard,
   * viz-lyrics) ignore it.
   *
   * Optional + defaulted to 1.0 by consumers so existing hosts that don't
   * yet set it keep working unchanged.
   */
  scrollSpeedMultiplier?: number;
};

/** Coerce an arbitrary scroll-speed input into the safe range visualizers
 * use. Centralized so every viz applies the same clamp and zero-/NaN-guard. */
export const SCROLL_SPEED_MIN = 0.3;
export const SCROLL_SPEED_MAX = 3.0;
export function clampScrollSpeedMultiplier(value: number | undefined | null): number {
  if (value === undefined || value === null || !Number.isFinite(value) || value <= 0) {
    return 1.0;
  }
  return Math.min(SCROLL_SPEED_MAX, Math.max(SCROLL_SPEED_MIN, value));
}

export type FrameContext = {
  canvas: HTMLCanvasElement;
  ctx2d: CanvasRenderingContext2D;
  width: number;
  height: number;
  dpr: number;
  state: TransportState;
};

export type VizInitContext = {
  canvas: HTMLCanvasElement;
  ctx2d: CanvasRenderingContext2D;
  // Song access + host services will grow here later.
  song?: {
    /** Optional karaoke/lyrics timing data loaded from features/lyrics.json */
    lyrics?: unknown;

    /** Optional parsed charts from charts/*.json (key: relPath like charts/easy.json) */
    charts?: Record<string, unknown>;

    /** Optional raw MIDI bytes from features/notes.mid (if present) */
    notesMidiBytes?: Uint8Array;

    /** Optional parsed note events (host-provided) */
    notes?: Array<{
      t_on: number;
      t_off?: number;
      pitch: number;
      velocity?: number;
      channel?: number;
      trackName?: string;
    }>;
  };

  /** Multi-player lane configuration (host-provided) */
  players?: Array<{
    id: string;
    name: string;
    instrument?: string;
    color?: string;
  }>;
};

export interface Visualizer {
  init(ctx: VizInitContext): Promise<void>;
  onResize(width: number, height: number, dpr: number): void;
  update(dt: number, state: TransportState): void;
  render(frame: FrameContext): void;
  dispose(): void;
}

export type VisualizerModule = {
  createVisualizer(): Visualizer;
};
