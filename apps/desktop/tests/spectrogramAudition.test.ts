// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { SpectrogramEditor } from "../src/spectrogramEditor";

function fixture() {
  const note = { t_on: 1, t_off: 2, pitch: 60, velocity: 90 };
  const stage = document.createElement("div");
  Object.defineProperties(stage, { clientWidth: { value: 1000 }, clientHeight: { value: 480 } });
  stage.getBoundingClientRect = () => ({ left: 0, top: 0 } as DOMRect);
  stage.setPointerCapture = vi.fn();
  stage.releasePointerCapture = vi.fn();
  const editor = Object.assign(Object.create(SpectrogramEditor.prototype), {
    geom: { fmin_midi: 48, n_bins: 48, n_frames: 100, frames_per_sec: 10, duration_sec: 10 },
    view: { originX: 0, originY: 0, spanX: 100, spanY: 48 },
    overlay: { width: 1000, height: 480 }, stage,
    notes: [{ ...note }], selected: null, lanes: null, auditionReference: null,
    drag: "none", dragNoteIndex: -1, dragOrig: null, dragUndo: null,
    undoStack: [], redoStack: [], gridTimes: [],
    opts: { onNoteAuditioned: vi.fn(), onNotesChanged: vi.fn(), onNoteAdded: vi.fn() },
    requestRender: vi.fn(),
  });
  return { editor, note };
}

function pointer(clientX: number, clientY: number, modifiers = {}) {
  return { clientX, clientY, button: 0, pointerId: 1, shiftKey: false, ctrlKey: false,
    altKey: false, preventDefault: vi.fn(), ...modifiers };
}

describe("Shift-gated pointer edits", () => {
  it("auditions empty pitch rows without adding notes, dirtying, or changing undo/redo", () => {
    const { editor, note } = fixture();
    editor.redoStack.push([note]);
    editor.onPointerDown(pointer(350, 345));
    editor.onPointerMove(pointer(400, 335));
    editor.onPointerUp(pointer(400, 335));
    expect(editor.opts.onNoteAuditioned).toHaveBeenCalledWith(expect.objectContaining({ pitch: 61 }));
    expect(editor.notes).toEqual([note]);
    expect(editor.canUndo()).toBe(false);
    expect(editor.canRedo()).toBe(true);
    expect(editor.opts.onNotesChanged).not.toHaveBeenCalled();
    expect(editor.opts.onNoteAdded).not.toHaveBeenCalled();
  });

  it.each([100, 150, 200])("plain clicks on note body/edges at x=%s cannot drag or resize", (x) => {
    const { editor, note } = fixture();
    for (let i = 0; i < 2; i++) {
      editor.onPointerDown(pointer(x, 355));
      editor.onPointerMove(pointer(x + 50, 335, { shiftKey: true }));
      editor.onPointerUp(pointer(x + 50, 335));
    }
    expect(editor.selected).toBe(0);
    expect(editor.opts.onNoteAuditioned).toHaveBeenCalledTimes(2);
    expect(editor.notes).toEqual([note]);
    expect(editor.canUndo()).toBe(false);
    expect(editor.opts.onNotesChanged).not.toHaveBeenCalled();
  });

  it("Shift-click places a note, with Ctrl still bypassing quantization", () => {
    const { editor } = fixture();
    editor.gridTimes = [0, 1, 2, 3, 4];
    editor.onPointerDown(pointer(350, 345, { shiftKey: true }));
    expect(editor.notes[1].t_on).toBe(3);
    editor.onPointerDown(pointer(450, 335, { shiftKey: true, ctrlKey: true }));
    expect(editor.notes[2].t_on).toBe(4.25);
    expect(editor.opts.onNoteAdded).toHaveBeenCalledTimes(2);
    expect(editor.undoStack).toHaveLength(2);
  });

  it.each([
    [150, 250, 345, { t_on: 2, t_off: 3, pitch: 61 }],
    [100, 80, 355, { t_on: 0.8, t_off: 2, pitch: 60 }],
    [200, 250, 355, { t_on: 1, t_off: 2.5, pitch: 60 }],
  ])("requires Shift to remain held while moving/resizing from x=%s", (start, end, y, expected) => {
    const { editor, note } = fixture();
    editor.onPointerDown(pointer(start, 355, { shiftKey: true }));
    editor.onPointerMove(pointer(end, y));
    expect(editor.notes).toEqual([note]);
    expect(editor.canUndo()).toBe(false);
    editor.onPointerMove(pointer(end, y, { shiftKey: true }));
    editor.onPointerUp(pointer(end, y));
    expect(editor.notes[0]).toMatchObject(expected);
    expect(editor.undoStack).toHaveLength(1);
    expect(editor.opts.onNotesChanged).toHaveBeenCalledTimes(1);
    editor.undo();
    expect(editor.notes).toEqual([note]);
  });

  it.each([{ altKey: true }, { button: 1 }, { button: 2 }])("keeps pan gestures safe over notes: %o", (modifiers) => {
    const { editor, note } = fixture();
    editor.onPointerDown(pointer(150, 355, modifiers));
    expect(editor.drag).toBe("pan");
    expect(editor.notes).toEqual([note]);
    expect(editor.canUndo()).toBe(false);
    expect(editor.opts.onNoteAuditioned).not.toHaveBeenCalled();
  });
});

describe("harmonic guides", () => {
  it("draws only visible overtones without mutating notes or adding undo history", () => {
    const { editor, note } = fixture();
    const ctx = { save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(), beginPath: vi.fn(),
      moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fillText: vi.fn(), fillRect: vi.fn(),
      measureText: () => ({ width: 100 }) };
    editor.harmonicGuides = true;
    editor.selected = 0;
    editor.drawHarmonicGuides(ctx, 1000, 480);
    const labels = ctx.fillText.mock.calls.map((args) => args[0]);
    expect(labels).toContain("2×");
    expect(labels).toContain("7×");
    expect(labels).not.toContain("8×"); // Above the spectrogram's highest row.
    expect(labels).toContain("Harmonics of C4 · possible overtones, not detected notes");
    expect(editor.notes).toEqual([note]);
    expect(editor.canUndo()).toBe(false);
    ctx.save.mockClear();
    editor.lanes = ["Kick", "Snare"];
    editor.drawHarmonicGuides(ctx, 1000, 480);
    expect(ctx.save).not.toHaveBeenCalled();
    editor.lanes = null;
    editor.harmonicGuides = false;
    editor.drawHarmonicGuides(ctx, 1000, 480);
    expect(ctx.save).not.toHaveBeenCalled();
  });
});

describe("existing-note audition", () => {
  it("auditions repeat clicks without editing the note or creating an undo step", () => {
    const note = { t_on: 1, t_off: 2, pitch: 60, velocity: 90 };
    const audition = vi.fn();
    // Exercise pointer release independently of the WebGL renderer.
    const editor = Object.assign(Object.create(SpectrogramEditor.prototype), {
      notes: [note], opts: { onNoteAuditioned: audition },
      stage: { releasePointerCapture: vi.fn() }, emitChanged: vi.fn(),
    });
    for (let i = 0; i < 2; i++) {
      Object.assign(editor, { drag: "move", dragNoteIndex: 0, dragOrig: { ...note }, dragUndo: [{ ...note }] });
      editor.onPointerUp({ pointerId: 1 });
    }
    expect(audition).toHaveBeenCalledTimes(2);
    expect(audition).toHaveBeenLastCalledWith(note);
    expect(editor.emitChanged).not.toHaveBeenCalled();
    expect(editor.notes).toEqual([note]);
  });
});
