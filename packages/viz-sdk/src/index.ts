export type TransportState = {
  t: number; // seconds (song time)
  isPlaying: boolean;
  /** Playback speed multiplier (tempo slowdown). 1.0 = normal. */
  playbackRate: number;
  /** Quarter-notes per minute (song tempo). */
  bpm: number;
  timeSignature: [number, number];
  loop?: { t0: number; t1: number };
};

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
