import type { Visualizer, VisualizerModule, VizInitContext, FrameContext, TransportState } from "@auralprimer/viz-sdk";
import { SheetMusicRenderer, type MelodicNote, type MelodicTrackSelection } from "@auralprimer/viz-tab";

// Sheet-music visualizer plugin. Wraps the standard SheetMusicRenderer (which
// owns its own canvas) by hosting it on a detached offscreen container and
// blitting its canvas onto the host's frame canvas each frame.

class SheetMusicVisualizer implements Visualizer {
  private container: HTMLElement | null = null;
  private renderer: SheetMusicRenderer | null = null;
  private lastState: TransportState | null = null;

  async init(ctx: VizInitContext): Promise<void> {
    const raw = ctx.song?.notes ?? [];
    // Melodic only — drop drums (channel 9).
    const notes: MelodicNote[] = raw
      .filter((n) => n.channel !== 9 && Number.isFinite(n.pitch))
      .map((n) => ({
        t_on: n.t_on,
        t_off: typeof n.t_off === "number" && n.t_off > n.t_on ? n.t_off : n.t_on + 0.12,
        pitch: n.pitch,
        velocity: typeof n.velocity === "number" ? n.velocity : 100,
      }))
      .sort((a, b) => a.t_on - b.t_on);

    // Detached host for the renderer's own canvas (never added to the DOM;
    // we size it manually via resize() and blit it onto the frame canvas).
    this.container = document.createElement("div");
    this.renderer = new SheetMusicRenderer(this.container);

    const track: MelodicTrackSelection = {
      role: "keys",
      trackName: "Sheet",
      channel: 0,
      notes,
    };
    this.renderer.setTrack(notes.length > 0 ? track : null);
  }

  onResize(_width: number, _height: number, _dpr: number): void {
    // Sizing is driven per-frame from FrameContext in render().
  }

  update(_dt: number, state: TransportState): void {
    this.lastState = state;
  }

  render(frame: FrameContext): void {
    if (!this.renderer) return;
    this.renderer.resize(frame.width, frame.height);
    this.renderer.render(frame.state.t, {
      bpm: frame.state.bpm,
      timeSignature: frame.state.timeSignature,
      scrollSpeedMultiplier: frame.state.scrollSpeedMultiplier,
    });
    frame.ctx2d.clearRect(0, 0, frame.width, frame.height);
    frame.ctx2d.drawImage(this.renderer.getCanvas(), 0, 0, frame.width, frame.height);
    void this.lastState;
  }

  dispose(): void {
    this.renderer?.dispose();
    this.renderer = null;
    this.container = null;
  }
}

export function createVisualizer(): Visualizer {
  return new SheetMusicVisualizer();
}

const mod: VisualizerModule = { createVisualizer };
export default mod;
