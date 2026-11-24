// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ piano: [] as any[], guitar: [] as any[], drums: [] as any[] }));
vi.mock("@auralprimer/viz-tab", () => {
  const renderer = (container: HTMLElement, view?: string) => {
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);
    return { setTrack: vi.fn(), render: vi.fn(), dispose: vi.fn(() => canvas.remove()), view };
  };
  return {
    TabRenderer: vi.fn(function (container: HTMLElement) { const r = renderer(container); mocks.piano.push(r); return r; }),
    GuitarRenderer: vi.fn(function (container: HTMLElement, view: string) { const r = renderer(container, view); mocks.guitar.push(r); return r; }),
    isFrettedRole: (role: string) => ["bass", "lead_guitar", "rhythm_guitar"].includes(role),
  };
});
vi.mock("@auralprimer/viz-drum-highway", () => ({
  createVisualizer: () => {
    const r = { init: vi.fn(() => Promise.resolve()), onResize: vi.fn(), update: vi.fn(), render: vi.fn(), dispose: vi.fn() };
    mocks.drums.push(r); return r;
  },
}));
import { RefineGamePreview } from "../src/refineGamePreview";

let resized: () => void;
let redraw: FrameRequestCallback;
let observed: Element[];
beforeEach(() => {
  vi.restoreAllMocks();
  mocks.piano.length = mocks.guitar.length = mocks.drums.length = 0;
  observed = [];
  vi.stubGlobal("ResizeObserver", class { constructor(cb: () => void) { resized = cb; } observe(el: Element) { observed.push(el); } unobserve() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", vi.fn((cb: FrameRequestCallback) => { redraw = cb; return 1; }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ setTransform: vi.fn() } as unknown as CanvasRenderingContext2D);
});
const notes = [{ t_on: 2, t_off: 3, pitch: 60, velocity: 90 }];

describe("shared game preview", () => {
  it("shows current unsaved notes and follows only the host clock", () => {
    const container = document.createElement("div");
    const preview = new RefineGamePreview(container);
    preview.setNotes(notes, "keys");
    expect(mocks.piano).toHaveLength(0); // Hidden mode does no rendering work.
    preview.setTime(2.25, false, 0.5);
    preview.setVisible(true);
    const piano = mocks.piano[0];
    expect(piano.setTrack).toHaveBeenLastCalledWith(expect.objectContaining({ role: "keys", notes }));
    expect(piano.render).toHaveBeenLastCalledWith(2.25, expect.any(Object));
    preview.setTime(2.75, true, 0.5);
    expect(piano.render).toHaveBeenLastCalledWith(2.75, expect.any(Object));
    const edited = [{ ...notes[0], pitch: 64 }];
    preview.setNotes(edited, "keys");
    expect(piano.setTrack).toHaveBeenLastCalledWith(expect.objectContaining({ notes: edited }));
    expect(mocks.piano).toHaveLength(1);
    expect(requestAnimationFrame).not.toHaveBeenCalled(); // No independent transport/animation clock.
    preview.setVisible(false);
    expect(piano.dispose).toHaveBeenCalledOnce();
    expect(container.children).toHaveLength(0);
    preview.setVisible(true);
    expect(container.children).toHaveLength(1);
    expect(mocks.piano[1].render).toHaveBeenLastCalledWith(2.75, expect.any(Object));
    preview.dispose();
  });

  it("redraws a paused preview after canvas resize and uses the shared beat grid", () => {
    const preview = new RefineGamePreview(document.createElement("div"));
    preview.setNotes(notes, "keys");
    preview.setBeatGrid([0, 0.5, 1], [0], [3, 4]);
    preview.setTime(0.75, false, 1);
    preview.setVisible(true);
    resized();
    resized();
    expect(requestAnimationFrame).toHaveBeenCalledOnce();
    redraw(1);
    expect(mocks.piano[0].render).toHaveBeenLastCalledWith(0.75, {
      bpm: 120, timeSignature: [3, 4], liveInputNotes: [], beats: [
        { t: 0, down: true, m: 1 }, { t: 0.5, down: false, m: 1 }, { t: 1, down: false, m: 1 },
      ],
    });
    preview.dispose();
  });

  it("uses game fretted views for guitar/bass and converts drum lane indices to GM pitches", async () => {
    const container = document.createElement("div");
    const preview = new RefineGamePreview(container);
    preview.setNotes(notes, "keys");
    preview.setVisible(true);
    preview.setNotes(notes, "guitar");
    expect(mocks.piano[0].dispose).toHaveBeenCalledOnce();
    expect(mocks.guitar[0].view).toBe("tab");
    expect(mocks.guitar[0].setTrack).toHaveBeenLastCalledWith(expect.objectContaining({ role: "lead_guitar" }));
    preview.setNotes([{ t_on: 1, t_off: 1.08, pitch: 0 }, { t_on: 2, t_off: 2.08, pitch: 1 }], "drums", ["kick", "hihat_closed"]);
    await Promise.resolve();
    const drum = mocks.drums[0];
    expect(mocks.guitar[0].dispose).toHaveBeenCalledOnce();
    expect(drum.init.mock.calls[0][0].song.notes).toEqual([
      { t_on: 1, t_off: 1.08, pitch: 36, channel: 9, trackName: "Drums" },
      { t_on: 2, t_off: 2.08, pitch: 42, channel: 9, trackName: "Drums" },
    ]);
    preview.setTime(2.5, true, 0.75);
    expect(drum.render).toHaveBeenLastCalledWith(expect.objectContaining({ state: expect.objectContaining({ t: 2.5, isPlaying: true, playbackRate: 0.75 }) }));
    expect(container.children).toHaveLength(1);
    preview.clear();
    expect(drum.dispose).toHaveBeenCalledOnce();
    expect(container.children).toHaveLength(0);
    preview.dispose();
  });

  it("redraws live keys while paused without replacing notes or moving the playhead", () => {
    const preview = new RefineGamePreview(document.createElement("div"));
    preview.setNotes(notes, "keys");
    preview.setTime(4, false, 1);
    preview.setVisible(true);
    const piano = mocks.piano[0];
    piano.setTrack.mockClear();
    const live = [{ pitch: 60, velocity: 90, channel: 0, heldBySustain: false }];
    preview.setLiveInputNotes(live);
    expect(piano.render).toHaveBeenLastCalledWith(4, expect.objectContaining({ liveInputNotes: live }));
    preview.setLiveInputNotes([]);
    expect(piano.render).toHaveBeenLastCalledWith(4, expect.objectContaining({ liveInputNotes: [] }));
    expect(piano.setTrack).not.toHaveBeenCalled();
    expect(requestAnimationFrame).not.toHaveBeenCalled();
    preview.dispose();
  });

  it("redraws a new instrument's canvas while paused even when the pane size stays the same", () => {
    const container = document.createElement("div");
    const preview = new RefineGamePreview(container);
    preview.setNotes(notes, "keys");
    preview.setTime(4, false, 1);
    preview.setVisible(true);
    preview.setNotes(notes, "bass");
    expect(observed.at(-1)).toBe(container.querySelector("canvas"));
    const guitar = mocks.guitar[0];
    guitar.render.mockClear(); // Initial canvas resize clears its first frame.
    resized();
    redraw(1);
    expect(guitar.render).toHaveBeenLastCalledWith(4, expect.any(Object));
    preview.dispose();
  });
});
