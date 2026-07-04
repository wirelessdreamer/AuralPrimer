/**
 * SpectrogramEditor — self-contained, framework-agnostic WebGL2 spectrogram
 * editor for AuralPrimer Studio's "guided edit" transcription-cleanup surface.
 *
 * Responsibilities:
 *  - Render a CQT spectrogram (multi-tile, 16-bit RG-packed magnitude PNGs) on
 *    a WebGL2 canvas, reconstructing dB in a fragment shader and mapping the
 *    scalar through a swappable 1D colormap LUT. Live uniforms (gain / contrast
 *    / dbFloor) are pure-render — no texture reupload.
 *  - Draw a translucent note overlay on a stacked Canvas-2D layer aligned by the
 *    exact same view transform (time x pitch).
 *  - Provide zoom/pan, hit-testing, drag-move (snap pitch to semitone),
 *    edge-resize, click-to-add, delete-to-remove, selection highlight.
 *  - Build a small DOM control bar (gain / contrast / dbFloor sliders, colormap
 *    dropdown, zoom reset) and a setTime() playhead.
 *
 * Host wiring (the host persists edits):
 *   const ed = new SpectrogramEditor(container, { onNotesChanged });
 *   await ed.load(geometry, tileImages, notes);
 *   ed.setTime(playheadSeconds);
 *
 * No React/Vue/3rd-party deps — raw WebGL2 + DOM only. Coordinate convention:
 * row 0 = lowest pitch (MIDI = fmin_midi) sits at the BOTTOM of the view.
 */

import {
  frameToNorm,
  laneBandRows,
  laneMarkerXSpan,
  noteBodyXSpan,
  secToFrame,
} from "./spectrogramGeometry";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Geometry sidecar (spectrogram.json) — see aural/spectrogram/<role>/. */
export interface SpectrogramTile {
  /** Tile PNG filename (relative to the spectrogram dir). */
  file: string;
  /** Inclusive global column index of the first frame in this tile. */
  col_start: number;
  /** Exclusive global column index just past the last frame in this tile. */
  col_end: number;
}

export interface SpectrogramGeometry {
  fmin_midi: number; // 24 — MIDI of row 0
  bins_per_octave: number; // 12
  bins_per_semitone: number; // 1
  n_bins: number; // 84 — texture height
  n_frames: number; // total columns across all tiles
  frames_per_sec: number; // columns per second
  duration_sec: number;
  db_floor: number; // -80
  db_ceil: number; // 0
  bit_depth: number; // 16
  packing: string; // "rg16"
  row_0_is: string; // "lowest_pitch"
  tile_width: number; // 4096
  tiles: SpectrogramTile[];
}

export interface SpectroNote {
  t_on: number; // seconds
  t_off: number; // seconds
  pitch: number; // MIDI
  velocity?: number; // 0..127 (optional)
}

export type ColormapName = "inferno" | "viridis" | "grayscale";

export interface SpectrogramEditorOptions {
  /** Fired after any edit (move/resize/add/delete). Host persists. */
  onNotesChanged?: (notes: SpectroNote[]) => void;
  /** Fired when the user adds a note by clicking empty space. */
  onNoteAdded?: (note: SpectroNote, notes: SpectroNote[]) => void;
  /**
   * Fired when a note is placed or finished being edited (add, or the end of a
   * move/resize drag) — lets the host audition the note's pitch so the user
   * hears what they're putting down. NOT fired during pan/hover.
   */
  onNoteAuditioned?: (note: SpectroNote) => void;
  /** Fired when the selection changes (index into notes, or null). */
  onSelectionChanged?: (index: number | null) => void;
  /**
   * Fired as the cursor moves over the surface with the pitch + time under it
   * (null on leave) — drives a Sonic-Visualiser-style readout in the host.
   */
  onHover?: (info: { time: number; pitch: number } | null) => void;
  /** Initial colormap (default "inferno"). */
  colormap?: ColormapName;
  /** Whether to build the DOM control bar (default true). */
  showControls?: boolean;
}

// ---------------------------------------------------------------------------
// Shaders
// ---------------------------------------------------------------------------

const SPECTRO_VS = /* glsl */ `#version 300 es
precision highp float;
// Unit quad [0,1]x[0,1]; instance-less, one draw per tile.
layout(location = 0) in vec2 a_unit;
// Per-tile placement in *content* space:
//   content X in [0, nFrames], content Y in [0, nBins] (row 0 at bottom).
uniform vec2 u_tileOriginContent;  // (colStart, 0)
uniform vec2 u_tileSizeContent;    // (tileCols, nBins)
// View transform: content -> clip. We map a visible content window to [-1,1].
uniform vec2 u_viewOrigin;         // content coord at bottom-left of viewport
uniform vec2 u_viewSpan;           // content units spanned by the viewport
out vec2 v_tex; // 0..1 across this tile's texture (origin bottom-left)
void main() {
  vec2 content = u_tileOriginContent + a_unit * u_tileSizeContent;
  vec2 ndc = (content - u_viewOrigin) / u_viewSpan; // 0..1
  gl_Position = vec4(ndc * 2.0 - 1.0, 0.0, 1.0);
  // Texture: x along time. The CQT PNG stores the lowest pitch in its TOP row;
  // uploaded UNFLIPPED, that top row sits at v=0, so a_unit.y==0 (content
  // bottom, bin 0) samples the lowest pitch -> matches the note overlay.
  v_tex = a_unit;
}
`;

const SPECTRO_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 v_tex;
out vec4 fragColor;
uniform sampler2D u_tile;      // RG-packed 16-bit magnitude (RGB texture)
uniform sampler2D u_lut;       // 1D colormap (256x1 RGBA)
uniform float u_dbFloor;       // geometry db_floor (e.g. -80)
uniform float u_dbCeil;        // geometry db_ceil  (e.g. 0)
uniform float u_userFloor;     // live threshold (dB) clamp
uniform float u_gain;          // multiply normalized scalar
uniform float u_contrast;      // contrast around 0.5
void main() {
  vec3 rgb = texture(u_tile, v_tex).rgb; // 0..1 floats of the byte values
  // Reconstruct 16-bit level: R high byte, G low byte (bytes are 0..255).
  float hi = floor(rgb.r * 255.0 + 0.5);
  float lo = floor(rgb.g * 255.0 + 0.5);
  float level16 = hi * 256.0 + lo;          // 0..65535
  float db = u_dbFloor + (level16 / 65535.0) * (u_dbCeil - u_dbFloor);
  // Apply live threshold then normalize floor..ceil -> 0..1.
  float floorDb = max(u_dbFloor, u_userFloor);
  float s = (db - floorDb) / max(1e-4, (u_dbCeil - floorDb));
  s = clamp(s, 0.0, 1.0);
  // gain + contrast (pivot 0.5).
  s = clamp(s * u_gain, 0.0, 1.0);
  s = clamp((s - 0.5) * u_contrast + 0.5, 0.0, 1.0);
  vec3 col = texture(u_lut, vec2(s, 0.5)).rgb;
  fragColor = vec4(col, 1.0);
}
`;

// ---------------------------------------------------------------------------
// Colormap LUTs (256 entries, sparse control points linearly interpolated)
// ---------------------------------------------------------------------------

type RGB = [number, number, number];

function lerpRGB(a: RGB, b: RGB, t: number): RGB {
  return [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ];
}

function buildLut(stops: RGB[]): Uint8Array {
  const n = 256;
  const out = new Uint8Array(n * 4);
  const seg = stops.length - 1;
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * seg;
    const lo = Math.min(seg, Math.floor(x));
    const hi = Math.min(seg, lo + 1);
    const f = x - lo;
    const c = lerpRGB(stops[lo]!, stops[hi]!, f);
    out[i * 4 + 0] = Math.round(c[0]);
    out[i * 4 + 1] = Math.round(c[1]);
    out[i * 4 + 2] = Math.round(c[2]);
    out[i * 4 + 3] = 255;
  }
  return out;
}

// Approximations of the matplotlib maps; good enough for a transcription UI.
const COLORMAP_STOPS: Record<ColormapName, RGB[]> = {
  inferno: [
    [0, 0, 4],
    [40, 11, 84],
    [101, 21, 110],
    [159, 42, 99],
    [212, 72, 66],
    [245, 125, 21],
    [250, 193, 39],
    [252, 255, 164],
  ],
  viridis: [
    [68, 1, 84],
    [72, 40, 120],
    [62, 74, 137],
    [49, 104, 142],
    [38, 130, 142],
    [31, 158, 137],
    [53, 183, 121],
    [109, 205, 89],
    [180, 222, 44],
    [253, 231, 37],
  ],
  grayscale: [
    [0, 0, 0],
    [255, 255, 255],
  ],
};

// ---------------------------------------------------------------------------
// View transform — independent time(x) and pitch(y) zoom + pan.
// Content space: X in [0, nFrames] (frames), Y in [0, nBins] (rows).
// ---------------------------------------------------------------------------

interface View {
  // Bottom-left content coordinate currently visible.
  originX: number; // frames
  originY: number; // rows
  // Content units spanned by the viewport.
  spanX: number; // frames across the width
  spanY: number; // rows across the height
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

type DragMode = "none" | "move" | "resize-l" | "resize-r" | "pan";

const EDGE_HIT_PX = 6; // px from a note edge that counts as a resize grab
const MIN_NOTE_SEC = 0.02;
// Lane-mode (drum) hits are INSTANTS: a fixed-width marker CENTERED on the
// onset, so it stays glued to the transient at any zoom. A left-anchored,
// time-spanning box would balloon to ~0.8s of visual width when zoomed out
// and read as drift. CSS px (scaled by dpr at draw time).
const DRUM_MARK_CSS_PX = 5;

export class SpectrogramEditor {
  private readonly container: HTMLElement;
  private readonly opts: SpectrogramEditorOptions;

  // DOM
  private readonly root: HTMLDivElement;
  private readonly stage: HTMLDivElement;
  private readonly glCanvas: HTMLCanvasElement;
  private readonly overlay: HTMLCanvasElement;
  private controlBar: HTMLDivElement | null = null;

  // WebGL
  private gl: WebGL2RenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private quadVao: WebGLVertexArrayObject | null = null;
  private lutTex: WebGLTexture | null = null;
  private tileTextures: WebGLTexture[] = [];
  private uniforms: Record<string, WebGLUniformLocation | null> = {};

  // Data
  private geom: SpectrogramGeometry | null = null;
  private notes: SpectroNote[] = [];
  private selected: number | null = null;
  // Lane mode (drum cleanup): when set, notes are HITS whose `pitch` is a lane
  // INDEX (bottom lane 0) rather than a MIDI pitch. Lanes divide the content
  // height into equal bands; resize is disabled (hits are instants).
  private lanes: string[] | null = null;

  // View + render state
  private view: View = { originX: 0, originY: 0, spanX: 1, spanY: 1 };
  private colormap: ColormapName;
  private gain = 1.0;
  // Defaults tuned for a clean, Sonic-Visualiser-like view: clip the reverb/
  // noise floor (~-50 dB below peak) to black and lift contrast a touch, so
  // notes read as distinct lines instead of a dense wash. Users can drag the
  // Floor dB / Contrast sliders from here.
  private contrast = 1.3;
  private userFloorDb = -50;
  private playheadSec: number | null = null;

  // Undo/redo history of note-set snapshots (note edits only — view/zoom is
  // not undoable). Capped so a long session can't grow unbounded.
  private undoStack: SpectroNote[][] = [];
  private redoStack: SpectroNote[][] = [];
  private static readonly MAX_HISTORY = 200;
  // Set once we've installed the app-wide middle-click autoscroll suppressor.
  private static autoscrollSuppressed = false;

  // Interaction state
  private drag: DragMode = "none";
  private dragNoteIndex = -1;
  private dragStartContent = { x: 0, y: 0 };
  private dragOrig: SpectroNote | null = null;
  private panStartView: View | null = null;
  private rafPending = false;

  // 2D overlay context
  private ovCtx: CanvasRenderingContext2D | null = null;

  // bound listeners (for clean removal)
  private readonly onWheelBound: (e: WheelEvent) => void;
  private readonly onPointerDownBound: (e: PointerEvent) => void;
  private readonly onPointerMoveBound: (e: PointerEvent) => void;
  private readonly onPointerUpBound: (e: PointerEvent) => void;
  private readonly onKeyDownBound: (e: KeyboardEvent) => void;
  private readonly onResizeBound: () => void;
  private resizeObserver: ResizeObserver | null = null;

  constructor(container: HTMLElement, opts: SpectrogramEditorOptions = {}) {
    this.container = container;
    this.opts = opts;
    this.colormap = opts.colormap ?? "inferno";

    // ---- DOM scaffold ----
    this.root = document.createElement("div");
    this.root.className = "spectro-editor";
    this.root.style.cssText =
      "position:relative;display:flex;flex-direction:column;width:100%;height:100%;min-height:0;background:#0a0a0c;color:#ddd;font:12px/1.4 system-ui,sans-serif;";

    this.stage = document.createElement("div");
    this.stage.className = "spectro-stage";
    this.stage.style.cssText =
      "position:relative;flex:1 1 auto;min-height:0;overflow:hidden;touch-action:none;cursor:crosshair;";

    this.glCanvas = document.createElement("canvas");
    this.glCanvas.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;display:block;";

    this.overlay = document.createElement("canvas");
    this.overlay.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;";

    this.stage.appendChild(this.glCanvas);
    this.stage.appendChild(this.overlay);

    if (opts.showControls !== false) {
      this.controlBar = this.buildControls();
      this.root.appendChild(this.controlBar);
    }
    this.root.appendChild(this.stage);
    this.container.appendChild(this.root);

    // ---- bind listeners ----
    this.onWheelBound = (e) => this.onWheel(e);
    this.onPointerDownBound = (e) => this.onPointerDown(e);
    this.onPointerMoveBound = (e) => this.onPointerMove(e);
    this.onPointerUpBound = (e) => this.onPointerUp(e);
    this.onKeyDownBound = (e) => this.onKeyDown(e);
    this.onResizeBound = () => this.resize();

    this.stage.addEventListener("wheel", this.onWheelBound, { passive: false });
    this.stage.addEventListener("pointerdown", this.onPointerDownBound);
    window.addEventListener("pointermove", this.onPointerMoveBound);
    window.addEventListener("pointerup", this.onPointerUpBound);
    // Make the stage focusable so Delete works without a global handler.
    this.stage.tabIndex = 0;
    this.stage.addEventListener("keydown", this.onKeyDownBound);
    this.stage.addEventListener("pointerleave", () => this.opts.onHover?.(null));
    // Middle/right-drag pans (handled in onPointerDown). Right-click's context
    // menu is suppressed on the stage; middle-click autoscroll has to be killed
    // at the document level WITH capture, because the scrollable ancestor
    // (.appMain, overflow:auto) starts autoscroll before a stage-level handler
    // runs. Scoped to spectro stages so other middle-clicks are unaffected.
    this.stage.addEventListener("contextmenu", (e) => e.preventDefault());
    if (!SpectrogramEditor.autoscrollSuppressed) {
      document.addEventListener(
        "mousedown",
        (e) => {
          const me = e as MouseEvent;
          if (me.button === 1 && (me.target as HTMLElement | null)?.closest?.(".spectro-stage")) {
            e.preventDefault();
          }
        },
        { capture: true },
      );
      SpectrogramEditor.autoscrollSuppressed = true;
    }

    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(this.onResizeBound);
      this.resizeObserver.observe(this.stage);
    } else {
      window.addEventListener("resize", this.onResizeBound);
    }

    this.initGL();
    this.ovCtx = this.overlay.getContext("2d");
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  /**
   * Load geometry + decoded tile images + initial notes. `tileImages` may be
   * an array of already-decoded HTMLImageElements (parallel to geom.tiles), or
   * an array of URLs/objects to fetch & decode. Order MUST match geom.tiles.
   */
  async load(
    geometry: SpectrogramGeometry,
    tileImages: Array<HTMLImageElement | ImageBitmap | string>,
    notes: SpectroNote[] = [],
  ): Promise<void> {
    this.geom = geometry;
    this.notes = notes.map((n) => ({ ...n }));
    this.selected = null;
    // A fresh song starts with an empty history.
    this.undoStack = [];
    this.redoStack = [];

    const images = await Promise.all(
      tileImages.map((t) => this.toImageSource(t)),
    );
    this.uploadTiles(images);

    // Default view: whole song, all pitch rows.
    this.view = {
      originX: 0,
      originY: 0,
      spanX: Math.max(1, geometry.n_frames),
      spanY: Math.max(1, geometry.n_bins),
    };
    this.resize();
    this.requestRender();
  }

  /** Replace the note set (host-driven, e.g. applying a candidate). Undoable. */
  setNotes(notes: SpectroNote[]): void {
    this.pushUndo();
    this.notes = notes.map((n) => ({ ...n }));
    if (this.selected != null && this.selected >= this.notes.length) {
      this.setSelection(null);
    }
    this.requestRender();
  }

  /**
   * Toggle lane mode (drum cleanup). `lanes` is the bottom-to-top lane order;
   * while set, notes are hits whose `pitch` is a lane index and resize is
   * disabled. Pass null to return to pitch (melodic) mode. Call BEFORE load()/
   * setNotes() for the mode's content — it clears undo history since the two
   * modes' note coordinates aren't interchangeable.
   */
  setLaneMode(lanes: string[] | null): void {
    this.lanes = lanes && lanes.length ? [...lanes] : null;
    this.undoStack = [];
    this.redoStack = [];
    this.setSelection(null);
    this.requestRender();
  }

  /** Push the current note set onto the undo stack (call BEFORE a mutation). */
  private pushUndo(): void {
    this.undoStack.push(this.notes.map((n) => ({ ...n })));
    if (this.undoStack.length > SpectrogramEditor.MAX_HISTORY) this.undoStack.shift();
    this.redoStack = [];
  }

  // Pre-drag snapshot, committed on the FIRST move of a drag so a click that
  // only selects (no movement) doesn't create an empty undo entry.
  private dragUndo: SpectroNote[] | null = null;
  private commitDragUndo(): void {
    if (!this.dragUndo) return;
    this.undoStack.push(this.dragUndo);
    if (this.undoStack.length > SpectrogramEditor.MAX_HISTORY) this.undoStack.shift();
    this.redoStack = [];
    this.dragUndo = null;
  }

  /** Whether undo / redo are currently available (for host button state). */
  canUndo(): boolean { return this.undoStack.length > 0; }
  canRedo(): boolean { return this.redoStack.length > 0; }

  /** Restore the previous note set. No-op when the history is empty. */
  undo(): void {
    const prev = this.undoStack.pop();
    if (!prev) return;
    this.redoStack.push(this.notes.map((n) => ({ ...n })));
    this.notes = prev;
    this.setSelection(null);
    this.requestRender();
    this.emitChanged();
  }

  /** Re-apply an undone change. No-op when there's nothing to redo. */
  redo(): void {
    const next = this.redoStack.pop();
    if (!next) return;
    this.undoStack.push(this.notes.map((n) => ({ ...n })));
    this.notes = next;
    this.setSelection(null);
    this.requestRender();
    this.emitChanged();
  }

  /** Snapshot of the current notes (defensive copy). */
  getNotes(): SpectroNote[] {
    return this.notes.map((n) => ({ ...n }));
  }

  /** Draw a vertical playhead at time `t` seconds (null clears it). */
  setTime(t: number | null): void {
    this.playheadSec = t;
    this.requestRender();
  }

  /** Switch the active colormap (pure render — re-uploads only the LUT). */
  setColormap(name: ColormapName): void {
    this.colormap = name;
    this.uploadLut();
    this.requestRender();
  }

  /** Recompute canvas backing sizes from the stage box (DPR-aware). */
  resize(): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(this.stage.clientWidth));
    const h = Math.max(1, Math.floor(this.stage.clientHeight));
    for (const cv of [this.glCanvas, this.overlay]) {
      cv.width = Math.floor(w * dpr);
      cv.height = Math.floor(h * dpr);
    }
    if (this.gl) this.gl.viewport(0, 0, this.glCanvas.width, this.glCanvas.height);
    this.clampView();
    this.requestRender();
  }

  /** Tear down GL resources + listeners + DOM. */
  dispose(): void {
    this.stage.removeEventListener("wheel", this.onWheelBound);
    this.stage.removeEventListener("pointerdown", this.onPointerDownBound);
    window.removeEventListener("pointermove", this.onPointerMoveBound);
    window.removeEventListener("pointerup", this.onPointerUpBound);
    this.stage.removeEventListener("keydown", this.onKeyDownBound);
    if (this.resizeObserver) this.resizeObserver.disconnect();
    else window.removeEventListener("resize", this.onResizeBound);

    const gl = this.gl;
    if (gl) {
      for (const t of this.tileTextures) gl.deleteTexture(t);
      if (this.lutTex) gl.deleteTexture(this.lutTex);
      if (this.quadVao) gl.deleteVertexArray(this.quadVao);
      if (this.program) gl.deleteProgram(this.program);
    }
    this.tileTextures = [];
    this.gl = null;
    this.root.remove();
  }

  // -------------------------------------------------------------------------
  // WebGL setup
  // -------------------------------------------------------------------------

  private initGL(): void {
    const gl = this.glCanvas.getContext("webgl2", {
      antialias: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
    });
    if (!gl) {
      this.showGlError("WebGL2 is not available in this environment.");
      return;
    }
    this.gl = gl;

    const program = this.linkProgram(SPECTRO_VS, SPECTRO_FS);
    if (!program) return;
    this.program = program;

    // Cache uniform locations.
    for (const name of [
      "u_tileOriginContent",
      "u_tileSizeContent",
      "u_viewOrigin",
      "u_viewSpan",
      "u_tile",
      "u_lut",
      "u_dbFloor",
      "u_dbCeil",
      "u_userFloor",
      "u_gain",
      "u_contrast",
    ]) {
      this.uniforms[name] = gl.getUniformLocation(program, name);
    }

    // Unit-quad VAO (two triangles).
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    // prettier-ignore
    const verts = new Float32Array([
      0, 0,  1, 0,  0, 1,
      0, 1,  1, 0,  1, 1,
    ]);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
    this.quadVao = vao;

    this.uploadLut();
  }

  private linkProgram(vsSrc: string, fsSrc: string): WebGLProgram | null {
    const gl = this.gl!;
    const vs = this.compile(gl.VERTEX_SHADER, vsSrc);
    const fs = this.compile(gl.FRAGMENT_SHADER, fsSrc);
    if (!vs || !fs) return null;
    const prog = gl.createProgram()!;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      this.showGlError("Shader link failed: " + gl.getProgramInfoLog(prog));
      return null;
    }
    return prog;
  }

  private compile(type: number, src: string): WebGLShader | null {
    const gl = this.gl!;
    const sh = gl.createShader(type)!;
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      this.showGlError("Shader compile failed: " + gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  private uploadLut(): void {
    const gl = this.gl;
    if (!gl) return;
    const data = buildLut(COLORMAP_STOPS[this.colormap]);
    if (!this.lutTex) this.lutTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, data,
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  private uploadTiles(images: TexImageSource[]): void {
    const gl = this.gl;
    if (!gl) return;
    for (const t of this.tileTextures) gl.deleteTexture(t);
    this.tileTextures = [];
    // The CQT PNG stores the LOWEST pitch in its top row. An unflipped upload
    // maps the image's top row to texture v=0, and the shader maps v=0 to the
    // content bottom (bin 0) — so lowest pitch lands at the bottom of the view,
    // matching the note overlay (row 0 = lowest, at the bottom). FLIP_Y=true
    // mirrored the spectrogram vertically against the notes (the placement bug).
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    for (const img of images) {
      const tex = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(
        gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img,
      );
      // NEAREST so the RG byte values are not blended (blending would corrupt
      // the 16-bit reconstruction). Spectro lines are still readable.
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.tileTextures.push(tex);
    }
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  }

  private async toImageSource(
    t: HTMLImageElement | ImageBitmap | string,
  ): Promise<TexImageSource> {
    if (typeof t === "string") {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.src = t;
      await img.decode().catch(() => {
        /* fall through; texImage2D will throw if truly broken */
      });
      return img;
    }
    return t;
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------

  private requestRender(): void {
    if (this.rafPending) return;
    this.rafPending = true;
    requestAnimationFrame(() => {
      this.rafPending = false;
      this.renderGL();
      this.renderOverlay();
    });
  }

  private renderGL(): void {
    const gl = this.gl;
    const geom = this.geom;
    if (!gl || !this.program || !geom) return;

    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.glCanvas.width, this.glCanvas.height);
    gl.clearColor(0.03, 0.03, 0.045, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);

    if (this.tileTextures.length === 0) return;

    gl.useProgram(this.program);
    gl.bindVertexArray(this.quadVao);

    // LUT on unit 1.
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.uniform1i(this.uniforms["u_lut"]!, 1);

    gl.uniform2f(this.uniforms["u_viewOrigin"]!, this.view.originX, this.view.originY);
    gl.uniform2f(this.uniforms["u_viewSpan"]!, this.view.spanX, this.view.spanY);
    gl.uniform1f(this.uniforms["u_dbFloor"]!, geom.db_floor);
    gl.uniform1f(this.uniforms["u_dbCeil"]!, geom.db_ceil);
    gl.uniform1f(this.uniforms["u_userFloor"]!, this.userFloorDb);
    gl.uniform1f(this.uniforms["u_gain"]!, this.gain);
    gl.uniform1f(this.uniforms["u_contrast"]!, this.contrast);

    const viewLeft = this.view.originX;
    const viewRight = this.view.originX + this.view.spanX;

    for (let i = 0; i < geom.tiles.length; i++) {
      const tile = geom.tiles[i]!;
      const tex = this.tileTextures[i];
      if (!tex) continue;
      // Cull tiles fully outside the visible time window.
      if (tile.col_end < viewLeft || tile.col_start > viewRight) continue;

      const tileCols = tile.col_end - tile.col_start;
      gl.uniform2f(this.uniforms["u_tileOriginContent"]!, tile.col_start, 0);
      gl.uniform2f(this.uniforms["u_tileSizeContent"]!, tileCols, geom.n_bins);

      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1i(this.uniforms["u_tile"]!, 0);

      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    gl.bindVertexArray(null);
  }

  private renderOverlay(): void {
    const ctx = this.ovCtx;
    const geom = this.geom;
    if (!ctx || !geom) return;
    const W = this.overlay.width;
    const H = this.overlay.height;
    ctx.clearRect(0, 0, W, H);

    // Lane guides + labels (drum mode): a line per band boundary and the lane
    // name pinned at the left edge, so hits always read against their voice.
    if (this.lanes) {
      const rpl = this.rowsPerLane();
      ctx.strokeStyle = "rgba(255,255,255,0.14)";
      ctx.lineWidth = 1;
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.font = `${Math.round(11 * this.dpr())}px system-ui, sans-serif`;
      ctx.textBaseline = "middle";
      for (let i = 0; i <= this.lanes.length; i++) {
        const y = this.rowToPx(i * rpl);
        if (y >= -1 && y <= H + 1) {
          ctx.beginPath();
          ctx.moveTo(0, y + 0.5);
          ctx.lineTo(W, y + 0.5);
          ctx.stroke();
        }
        if (i < this.lanes.length) {
          const yMid = this.rowToPx((i + 0.5) * rpl);
          if (yMid >= 0 && yMid <= H) ctx.fillText(this.lanes[i]!, 6 * this.dpr(), yMid);
        }
      }
    }

    // Note rectangles. Lane mode draws a fixed-width marker centered on the
    // onset (see noteXSpan) filling its lane band with a slight inset.
    for (let i = 0; i < this.notes.length; i++) {
      const n = this.notes[i]!;
      const span = this.noteXSpan(n);
      const band = this.noteRowBand(n);
      const yBottom = this.rowToPx(band.rLo);
      const yTop = this.rowToPx(band.rHi);
      const x = span.xLeft;
      const w = Math.max(1, span.xRight - span.xLeft);
      let y = Math.min(yTop, yBottom);
      let h = Math.max(1, Math.abs(yBottom - yTop));
      if (this.lanes && h > 6) {
        const inset = h * 0.18;
        y += inset;
        h -= inset * 2;
      }

      const isSel = i === this.selected;
      ctx.fillStyle = isSel ? "rgba(0,229,255,0.28)" : "rgba(0,229,255,0.12)";
      ctx.fillRect(x, y, w, h);
      ctx.lineWidth = isSel ? 2 : 1;
      ctx.strokeStyle = isSel ? "rgba(120,245,255,0.95)" : "rgba(0,229,255,0.75)";
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);

      if (isSel && !this.lanes) {
        // Resize handles (pitch mode only — hits are instants).
        ctx.fillStyle = "rgba(120,245,255,0.95)";
        const hh = Math.min(h, 14 * this.dpr());
        ctx.fillRect(x, y + (h - hh) / 2, 2 * this.dpr(), hh);
        ctx.fillRect(x + w - 2 * this.dpr(), y + (h - hh) / 2, 2 * this.dpr(), hh);
      }
    }

    // Playhead.
    if (this.playheadSec != null && Number.isFinite(this.playheadSec)) {
      const px = this.timeToPx(this.playheadSec);
      if (px >= 0 && px <= W) {
        ctx.strokeStyle = "rgba(255,238,120,0.9)";
        ctx.lineWidth = 1.5 * this.dpr();
        ctx.beginPath();
        ctx.moveTo(px + 0.5, 0);
        ctx.lineTo(px + 0.5, H);
        ctx.stroke();
      }
    }
  }

  // -------------------------------------------------------------------------
  // Coordinate mapping (content <-> device pixels on the overlay canvas)
  // Device pixels: origin top-left, y down. Content y: row 0 at bottom.
  // -------------------------------------------------------------------------

  private dpr(): number {
    return this.overlay.width / Math.max(1, this.stage.clientWidth);
  }

  private timeToFrame(t: number): number {
    return secToFrame(t, this.geom?.frames_per_sec ?? 1);
  }
  private frameToTime(f: number): number {
    return f / (this.geom?.frames_per_sec ?? 1);
  }

  /** Content X (frames) -> device px X on overlay. */
  private frameToPx(frame: number): number {
    return frameToNorm(frame, this.view.originX, this.view.spanX) * this.overlay.width;
  }
  private timeToPx(t: number): number {
    return this.frameToPx(this.timeToFrame(t));
  }
  /** Content row (0 = bottom) -> device px Y on overlay (y down). */
  private rowToPx(row: number): number {
    const ny = (row - this.view.originY) / this.view.spanY; // 0..1 from bottom
    return (1 - ny) * this.overlay.height;
  }

  /** Content rows per lane band (lane mode divides n_bins evenly). */
  private rowsPerLane(): number {
    const lanes = this.lanes;
    if (!lanes || !this.geom) return 1;
    return laneBandRows(this.geom.n_bins, lanes.length);
  }

  /**
   * The content-row band a note occupies: its lane's band in lane mode, its
   * semitone row otherwise. Shared by draw + hit-test so they always agree.
   */
  private noteRowBand(n: SpectroNote): { rLo: number; rHi: number } {
    if (this.lanes) {
      const rpl = this.rowsPerLane();
      return { rLo: n.pitch * rpl, rHi: (n.pitch + 1) * rpl };
    }
    const row = n.pitch - (this.geom?.fmin_midi ?? 0);
    return { rLo: row, rHi: row + 1 };
  }

  /**
   * A note's device-px horizontal span. Lane mode: a fixed-width marker
   * CENTERED on the onset (an instant). Pitch mode: t_on..t_off (a real
   * duration), min 1px. Shared by draw + hit-test so they always agree.
   */
  private noteXSpan(n: SpectroNote): { xLeft: number; xRight: number } {
    if (this.lanes) {
      return laneMarkerXSpan(this.timeToPx(n.t_on), DRUM_MARK_CSS_PX, this.dpr());
    }
    return noteBodyXSpan(this.timeToPx(n.t_on), this.timeToPx(n.t_off));
  }

  /** Device px (relative to stage, CSS px) -> content {frame, row}. */
  private pxToContent(cssX: number, cssY: number): { frame: number; row: number } {
    const nx = cssX / Math.max(1, this.stage.clientWidth);
    const ny = 1 - cssY / Math.max(1, this.stage.clientHeight); // flip y
    return {
      frame: this.view.originX + nx * this.view.spanX,
      row: this.view.originY + ny * this.view.spanY,
    };
  }

  // -------------------------------------------------------------------------
  // Zoom / pan
  // -------------------------------------------------------------------------

  private onWheel(e: WheelEvent): void {
    if (!this.geom) return;
    e.preventDefault();
    const rect = this.stage.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;
    const anchor = this.pxToContent(cssX, cssY);

    const factor = Math.exp(-e.deltaY * 0.0015); // wheel up => zoom in
    const zoomX = !e.shiftKey; // Shift => constrain to Y (pitch) only
    const zoomY = !e.ctrlKey; //  Ctrl => constrain to X (time) only

    if (zoomX) {
      const newSpan = this.view.spanX / factor;
      // Keep the anchor frame fixed under the cursor.
      this.view.spanX = newSpan;
      this.view.originX = anchor.frame - (cssX / Math.max(1, this.stage.clientWidth)) * newSpan;
    }
    if (zoomY) {
      const newSpan = this.view.spanY / factor;
      const ny = 1 - cssY / Math.max(1, this.stage.clientHeight);
      this.view.spanY = newSpan;
      this.view.originY = anchor.row - ny * newSpan;
    }
    this.clampView();
    this.requestRender();
  }

  private clampView(): void {
    const geom = this.geom;
    if (!geom) return;
    const maxX = Math.max(1, geom.n_frames);
    const maxY = Math.max(1, geom.n_bins);
    // Clamp spans (min 8 frames / 2 rows of zoom-in, max = full content).
    this.view.spanX = Math.min(maxX, Math.max(8, this.view.spanX));
    this.view.spanY = Math.min(maxY, Math.max(2, this.view.spanY));
    // Clamp origin so the view stays inside content.
    this.view.originX = Math.min(maxX - this.view.spanX, Math.max(0, this.view.originX));
    this.view.originY = Math.min(maxY - this.view.spanY, Math.max(0, this.view.originY));
  }

  /** Reset zoom/pan to show the entire song + full pitch range. */
  resetView(): void {
    const geom = this.geom;
    if (!geom) return;
    this.view = {
      originX: 0,
      originY: 0,
      spanX: Math.max(1, geom.n_frames),
      spanY: Math.max(1, geom.n_bins),
    };
    this.requestRender();
  }

  /** Total content duration in seconds (0 if not loaded). */
  getDurationSec(): number {
    return this.geom?.duration_sec ?? 0;
  }

  /** The currently-visible time window in seconds. */
  getViewTimeWindow(): { start: number; end: number } {
    return {
      start: this.frameToTime(this.view.originX),
      end: this.frameToTime(this.view.originX + this.view.spanX),
    };
  }

  /**
   * Set the visible time window in seconds (leaves the pitch view untouched).
   * Used by section-jump navigation in the host.
   */
  setViewTimeWindow(startSec: number, endSec: number): void {
    if (!this.geom) return;
    const f0 = this.timeToFrame(startSec);
    const f1 = this.timeToFrame(endSec);
    this.view.spanX = Math.max(8, f1 - f0);
    this.view.originX = f0;
    this.clampView();
    this.requestRender();
  }

  /**
   * Scroll horizontally so time `t` (seconds) stays visible — used to make
   * the view follow the playhead during transport. `lead` (0..1) is how far
   * from the left edge to re-anchor when the playhead scrolls off-screen.
   */
  scrollTimeIntoView(t: number, lead = 0.15): void {
    if (!this.geom) return;
    const f = this.timeToFrame(t);
    const left = this.view.originX;
    const right = this.view.originX + this.view.spanX;
    if (f < left || f > right) {
      this.view.originX = f - this.view.spanX * lead;
      this.clampView();
      this.requestRender();
    }
  }

  // -------------------------------------------------------------------------
  // Pointer interactions: select / move / resize / add / pan
  // -------------------------------------------------------------------------

  private onPointerDown(e: PointerEvent): void {
    if (!this.geom) return;
    this.stage.focus();
    const rect = this.stage.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;

    // Middle button or right button => pan.
    if (e.button === 1 || e.button === 2) {
      e.preventDefault();
      this.drag = "pan";
      this.dragStartContent = { x: cssX, y: cssY };
      this.panStartView = { ...this.view };
      this.stage.setPointerCapture(e.pointerId);
      return;
    }
    if (e.button !== 0) return;

    const hit = this.hitTest(cssX, cssY);
    if (hit) {
      this.setSelection(hit.index);
      this.dragNoteIndex = hit.index;
      this.dragOrig = { ...this.notes[hit.index]! };
      // Snapshot the pre-drag state; committed to undo on the first move.
      this.dragUndo = this.notes.map((n) => ({ ...n }));
      const c = this.pxToContent(cssX, cssY);
      this.dragStartContent = { x: c.frame, y: c.row };
      this.drag = hit.edge === "l" ? "resize-l" : hit.edge === "r" ? "resize-r" : "move";
      this.stage.setPointerCapture(e.pointerId);
      this.requestRender();
      return;
    }

    // Empty space: with Alt => pan, otherwise add a note.
    if (e.altKey) {
      this.drag = "pan";
      this.dragStartContent = { x: cssX, y: cssY };
      this.panStartView = { ...this.view };
      this.stage.setPointerCapture(e.pointerId);
      return;
    }
    this.addNoteAt(cssX, cssY);
  }

  private onPointerMove(e: PointerEvent): void {
    if (this.drag === "none" || !this.geom) {
      this.updateCursor(e);
      return;
    }
    const rect = this.stage.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;

    if (this.drag === "pan" && this.panStartView) {
      const dxFrames = ((cssX - this.dragStartContent.x) / Math.max(1, this.stage.clientWidth)) * this.panStartView.spanX;
      const dyRows = ((cssY - this.dragStartContent.y) / Math.max(1, this.stage.clientHeight)) * this.panStartView.spanY;
      this.view.originX = this.panStartView.originX - dxFrames;
      this.view.originY = this.panStartView.originY + dyRows; // y is flipped
      this.clampView();
      this.requestRender();
      return;
    }

    const orig = this.dragOrig;
    if (!orig || this.dragNoteIndex < 0) return;
    const c = this.pxToContent(cssX, cssY);
    const note = this.notes[this.dragNoteIndex]!;
    // First actual movement of this drag -> bank the pre-drag state for undo.
    this.commitDragUndo();

    if (this.drag === "move") {
      const dFrame = c.frame - this.dragStartContent.x;
      const dT = this.frameToTime(dFrame);
      const dur = orig.t_off - orig.t_on;
      let newOn = orig.t_on + dT;
      const maxOn = (this.geom.duration_sec || Infinity) - dur;
      newOn = Math.max(0, Math.min(maxOn, newOn));
      note.t_on = newOn;
      note.t_off = newOn + dur;
      if (this.lanes) {
        // Vertical drag snaps whole lane bands; pitch is the lane index.
        const dLane = Math.round((c.row - this.dragStartContent.y) / this.rowsPerLane());
        note.pitch = Math.max(0, Math.min(this.lanes.length - 1, orig.pitch + dLane));
      } else {
        const dRow = Math.round(c.row - this.dragStartContent.y); // snap pitch
        const newPitch = orig.pitch + dRow;
        note.pitch = Math.max(
          this.geom.fmin_midi,
          Math.min(this.geom.fmin_midi + this.geom.n_bins - 1, newPitch),
        );
      }
    } else if (this.drag === "resize-l") {
      const t = Math.max(0, Math.min(note.t_off - MIN_NOTE_SEC, this.frameToTime(c.frame)));
      note.t_on = t;
    } else if (this.drag === "resize-r") {
      const upper = this.geom.duration_sec || this.frameToTime(c.frame);
      const t = Math.max(note.t_on + MIN_NOTE_SEC, Math.min(upper, this.frameToTime(c.frame)));
      note.t_off = t;
    }
    this.requestRender();
  }

  private onPointerUp(e: PointerEvent): void {
    if (this.drag === "none") return;
    const wasEdit = this.drag === "move" || this.drag === "resize-l" || this.drag === "resize-r";
    const editedIdx = this.dragNoteIndex;
    const moved = this.dragUndo === null && wasEdit; // commitDragUndo cleared it on the first real move
    try {
      this.stage.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer may not be captured */
    }
    this.drag = "none";
    this.dragNoteIndex = -1;
    this.dragOrig = null;
    this.panStartView = null;
    this.dragUndo = null; // discard the snapshot if the drag never moved
    if (wasEdit) {
      this.emitChanged();
      // Audition the edited note (only when it actually moved/resized, not a
      // bare click that selected it).
      const edited = moved && editedIdx >= 0 ? this.notes[editedIdx] : undefined;
      if (edited) this.opts.onNoteAuditioned?.(edited);
    }
  }

  private onKeyDown(e: KeyboardEvent): void {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) this.redo();
      else this.undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
      e.preventDefault();
      this.redo();
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && this.selected != null) {
      e.preventDefault();
      this.pushUndo();
      this.notes.splice(this.selected, 1);
      this.setSelection(null);
      this.emitChanged();
      this.requestRender();
    } else if (e.key === "Escape") {
      this.setSelection(null);
      this.requestRender();
    } else if (e.key === "0" || e.key.toLowerCase() === "f") {
      this.resetView();
    }
  }

  private updateCursor(e: PointerEvent): void {
    if (!this.geom) return;
    const rect = this.stage.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;
    const hit = this.hitTest(cssX, cssY);
    let cursor = "crosshair";
    if (hit) cursor = hit.edge ? "ew-resize" : "move";
    this.stage.style.cursor = cursor;
    // Emit pitch + time under the cursor for the host's readout. In lane mode
    // `pitch` carries the LANE INDEX; the host maps it to the lane name.
    if (this.opts.onHover) {
      const c = this.pxToContent(cssX, cssY);
      const pitch = this.lanes
        ? Math.max(0, Math.min(this.lanes.length - 1, Math.floor(c.row / this.rowsPerLane())))
        : Math.round(this.geom.fmin_midi + Math.floor(c.row));
      this.opts.onHover({ time: this.frameToTime(c.frame), pitch });
    }
  }

  // -------------------------------------------------------------------------
  // Hit-testing & note creation
  // -------------------------------------------------------------------------

  private hitTest(
    cssX: number,
    cssY: number,
  ): { index: number; edge: "l" | "r" | null } | null {
    const geom = this.geom;
    if (!geom) return null;
    const dpr = this.dpr();
    const pxX = cssX * dpr;
    const pxY = cssY * dpr;
    // Iterate last-drawn first so newer/selected notes win.
    for (let i = this.notes.length - 1; i >= 0; i--) {
      const n = this.notes[i]!;
      const span = this.noteXSpan(n);
      const band = this.noteRowBand(n);
      const yB = this.rowToPx(band.rLo);
      const yT = this.rowToPx(band.rHi);
      const left = span.xLeft;
      const right = span.xRight;
      const top = Math.min(yT, yB);
      const bot = Math.max(yT, yB);
      if (pxX < left - EDGE_HIT_PX * dpr || pxX > right + EDGE_HIT_PX * dpr) continue;
      if (pxY < top || pxY > bot) continue;
      // Lane mode: hits are instants — no resize edges, body hits only.
      if (!this.lanes) {
        if (Math.abs(pxX - left) <= EDGE_HIT_PX * dpr) return { index: i, edge: "l" };
        if (Math.abs(pxX - right) <= EDGE_HIT_PX * dpr) return { index: i, edge: "r" };
      }
      if (pxX >= left - EDGE_HIT_PX * dpr && pxX <= right + EDGE_HIT_PX * dpr) {
        return { index: i, edge: null };
      }
    }
    return null;
  }

  private addNoteAt(cssX: number, cssY: number): void {
    const geom = this.geom;
    if (!geom) return;
    const c = this.pxToContent(cssX, cssY);
    let pitch: number;
    let dur: number;
    let velocity: number;
    if (this.lanes) {
      // Lane mode: pitch = lane index under the cursor; hits are instants with
      // a fixed display length, at the transcriber's velocity convention.
      pitch = Math.max(0, Math.min(this.lanes.length - 1, Math.floor(c.row / this.rowsPerLane())));
      dur = 0.08;
      velocity = 127;
    } else {
      pitch = Math.max(
        geom.fmin_midi,
        Math.min(geom.fmin_midi + geom.n_bins - 1, geom.fmin_midi + Math.floor(c.row)),
      );
      // Default new-note length: ~1/8 of the visible window, clamped sane.
      const visSec = this.frameToTime(this.view.spanX);
      dur = Math.min(Math.max(visSec * 0.08, 0.08), 0.5);
      velocity = 100;
    }
    const tCenter = this.frameToTime(c.frame);
    const tOn = Math.max(0, tCenter - dur / 2);
    const tOff = tOn + dur;
    const note: SpectroNote = { t_on: tOn, t_off: tOff, pitch, velocity };
    this.pushUndo();
    this.notes.push(note);
    this.setSelection(this.notes.length - 1);
    this.opts.onNoteAdded?.(note, this.getNotes());
    this.opts.onNoteAuditioned?.(note);
    this.emitChanged();
    this.requestRender();
  }

  private setSelection(index: number | null): void {
    if (this.selected === index) return;
    this.selected = index;
    this.opts.onSelectionChanged?.(index);
  }

  private emitChanged(): void {
    this.opts.onNotesChanged?.(this.getNotes());
  }

  // -------------------------------------------------------------------------
  // Control bar
  // -------------------------------------------------------------------------

  private buildControls(): HTMLDivElement {
    const bar = document.createElement("div");
    bar.className = "spectro-controls";
    bar.style.cssText =
      "display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:6px 10px;background:#15151b;border-bottom:1px solid #26262f;";

    const mkSlider = (
      label: string,
      min: number,
      max: number,
      step: number,
      value: number,
      onInput: (v: number) => void,
    ): HTMLLabelElement => {
      const wrap = document.createElement("label");
      wrap.style.cssText = "display:inline-flex;align-items:center;gap:6px;white-space:nowrap;";
      const span = document.createElement("span");
      span.textContent = label;
      span.style.opacity = "0.8";
      const input = document.createElement("input");
      input.type = "range";
      input.min = String(min);
      input.max = String(max);
      input.step = String(step);
      input.value = String(value);
      input.style.width = "110px";
      const val = document.createElement("span");
      val.textContent = value.toFixed(2);
      val.style.cssText = "min-width:42px;text-align:right;font-variant-numeric:tabular-nums;opacity:0.7;";
      input.addEventListener("input", () => {
        const v = Number(input.value);
        val.textContent = v.toFixed(2);
        onInput(v);
      });
      wrap.append(span, input, val);
      return wrap;
    };

    bar.appendChild(
      mkSlider("Gain", 0.2, 4, 0.01, this.gain, (v) => {
        this.gain = v;
        this.requestRender();
      }),
    );
    bar.appendChild(
      mkSlider("Contrast", 0.2, 4, 0.01, this.contrast, (v) => {
        this.contrast = v;
        this.requestRender();
      }),
    );
    bar.appendChild(
      mkSlider("Floor dB", -80, -5, 1, this.userFloorDb, (v) => {
        this.userFloorDb = v;
        this.requestRender();
      }),
    );

    // Colormap dropdown.
    const cmWrap = document.createElement("label");
    cmWrap.style.cssText = "display:inline-flex;align-items:center;gap:6px;";
    const cmSpan = document.createElement("span");
    cmSpan.textContent = "Colormap";
    cmSpan.style.opacity = "0.8";
    const cmSel = document.createElement("select");
    cmSel.style.cssText = "background:#0d0d12;color:#ddd;border:1px solid #333;border-radius:4px;padding:2px 4px;";
    for (const name of ["inferno", "viridis", "grayscale"] as ColormapName[]) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === this.colormap) opt.selected = true;
      cmSel.appendChild(opt);
    }
    cmSel.addEventListener("change", () => this.setColormap(cmSel.value as ColormapName));
    cmWrap.append(cmSpan, cmSel);
    bar.appendChild(cmWrap);

    // Zoom reset.
    const resetBtn = document.createElement("button");
    resetBtn.textContent = "Reset zoom";
    resetBtn.style.cssText =
      "background:#222230;color:#ddd;border:1px solid #383848;border-radius:4px;padding:3px 10px;cursor:pointer;";
    resetBtn.addEventListener("click", () => this.resetView());
    bar.appendChild(resetBtn);

    return bar;
  }

  private showGlError(msg: string): void {
    // eslint-disable-next-line no-console
    console.error("[SpectrogramEditor] " + msg);
    const el = document.createElement("div");
    el.style.cssText =
      "position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#f88;padding:16px;text-align:center;";
    el.textContent = msg;
    this.stage.appendChild(el);
  }
}

export default SpectrogramEditor;
