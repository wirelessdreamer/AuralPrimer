/** The game's own instrument renderers, driven by Cleanup's edited notes and
 * audible playhead. This surface has no audio engine or independent clock. */
import { GuitarRenderer, TabRenderer, isFrettedRole, type InstrumentRole, type MelodicTrackSelection,
  type GridBeat, type PianoLiveInputNote } from "@auralprimer/viz-tab";
import { createVisualizer as createDrumHighway } from "@auralprimer/viz-drum-highway";
import type { TransportState, Visualizer } from "@auralprimer/viz-sdk";
import type { SpectroNote } from "./spectrogramEditor";
import { laneToMidi } from "./drumTabChart";

export class RefineGamePreview {
  private visible = false;
  private instrument = "keys";
  private notes: SpectroNote[] = [];
  private lanes: string[] = [];
  private liveNotes: PianoLiveInputNote[] = [];
  private melodic: TabRenderer | GuitarRenderer | null = null;
  private drum: Visualizer | null = null;
  private drumCanvas: HTMLCanvasElement | null = null;
  private beats: GridBeat[] = [];
  private timeSignature: [number, number] = [4, 4];
  private state: TransportState = { t: 0, isPlaying: false, playbackRate: 1, bpm: 120, timeSignature: [4, 4] };
  private readonly resizeObserver: ResizeObserver;
  private resizeFrame: number | null = null;

  constructor(private readonly container: HTMLElement) {
    // Render after the game renderer's own resize observer has resized its
    // canvas. This also redraws paused previews after moving the divider.
    this.resizeObserver = new ResizeObserver(() => {
      if (!this.visible || this.resizeFrame !== null) return;
      this.resizeFrame = requestAnimationFrame(() => { this.resizeFrame = null; this.render(); });
    });
    this.resizeObserver.observe(container);
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
    if (visible) { this.updateRenderer(); this.render(); }
    else this.disposeRenderer();
  }

  setNotes(notes: SpectroNote[], instrument: string, lanes: string[] = []): void {
    if (this.instrument !== instrument) this.disposeRenderer();
    this.instrument = instrument;
    this.notes = notes.map((note) => ({ ...note }));
    this.lanes = [...lanes];
    if (this.visible) { this.updateRenderer(); this.render(); }
  }

  setBeatGrid(times: number[], downbeats: number[], timeSignature: [number, number]): void {
    const down = new Set(downbeats);
    let measure = 0;
    this.beats = times.map((t) => { if (down.has(t)) measure++; return { t, down: down.has(t), m: measure }; });
    this.timeSignature = timeSignature;
    this.render();
  }

  setTime(t: number, isPlaying: boolean, playbackRate: number): void {
    this.state = { ...this.state, t, isPlaying, playbackRate };
    this.render();
  }

  setLiveInputNotes(notes: PianoLiveInputNote[]): void {
    this.liveNotes = notes.map((note) => ({ ...note }));
    this.render();
  }

  clear(): void {
    this.notes = [];
    this.beats = [];
    this.timeSignature = [4, 4];
    this.disposeRenderer();
  }

  dispose(): void {
    this.visible = false;
    this.disposeRenderer();
    this.resizeObserver.disconnect();
    if (this.resizeFrame !== null) cancelAnimationFrame(this.resizeFrame);
  }

  private updateRenderer(): void {
    if (this.instrument === "drums") {
      if (!this.drum) {
        this.drumCanvas = document.createElement("canvas");
        this.container.appendChild(this.drumCanvas);
        this.resizeObserver.observe(this.drumCanvas);
        this.drum = createDrumHighway();
      }
      const notes = this.notes.filter((n) => this.lanes[n.pitch]).map((n) => ({
        ...n, pitch: laneToMidi(this.lanes[n.pitch]!), channel: 9, trackName: "Drums",
      }));
      const canvas = this.drumCanvas!;
      void this.drum.init({ canvas, ctx2d: canvas.getContext("2d")!, song: { notes } }).then(() => this.render());
    } else {
      const role: InstrumentRole = this.instrument === "guitar" ? "lead_guitar" : this.instrument as InstrumentRole;
      if (!this.melodic) {
        this.melodic = isFrettedRole(role) ? new GuitarRenderer(this.container, "tab") : new TabRenderer(this.container);
        // A new game's canvas gets its own initial resize even when the pane
        // size is unchanged (e.g. switching instruments while paused).
        this.resizeObserver.observe(this.container.querySelector("canvas")!);
      }
      const track: MelodicTrackSelection = {
        role, trackName: this.instrument, channel: 0,
        notes: this.notes.map((n) => ({ ...n, velocity: n.velocity ?? 100 })).sort((a, b) => a.t_on - b.t_on),
      };
      this.melodic.setTrack(track);
    }
  }

  private render(): void {
    if (!this.visible) return;
    const next = this.beats.findIndex((b) => b.t > this.state.t);
    const index = next < 0 ? this.beats.length - 2 : Math.max(0, next - 1);
    const interval = this.beats[index + 1]?.t - this.beats[index]?.t;
    const bpm = interval > 0 ? 60 / interval : 120;
    const state = { ...this.state, bpm, timeSignature: this.timeSignature };
    if (this.melodic) this.melodic.render(state.t, { bpm, timeSignature: this.timeSignature, beats: this.beats, liveInputNotes: this.liveNotes });
    if (this.drum && this.drumCanvas) {
      const canvas = this.drumCanvas;
      const width = Math.max(1, this.container.clientWidth);
      const height = Math.max(1, this.container.clientHeight);
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const pixelW = Math.round(width * dpr), pixelH = Math.round(height * dpr);
      if (canvas.width !== pixelW || canvas.height !== pixelH) { canvas.width = pixelW; canvas.height = pixelH; }
      const ctx2d = canvas.getContext("2d")!;
      ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.drum.onResize(width, height, dpr);
      this.drum.update(0, state);
      this.drum.render({ canvas, ctx2d, width, height, dpr, state });
    }
  }

  private disposeRenderer(): void {
    const canvas = this.container.querySelector("canvas");
    if (canvas) this.resizeObserver.unobserve(canvas);
    this.melodic?.dispose();
    this.melodic = null;
    this.drum?.dispose();
    this.drum = null;
    this.drumCanvas?.remove();
    this.drumCanvas = null;
  }
}
