/**
 * Guitar Hero / Rock Band kits arrive as gamepads, and the one thing that is
 * not negotiable is that a hit fires once. These pads rattle, the kick pedal
 * rests pressed on some kits, and a poll that mistook either for a strike
 * would play a drum roll nobody performed.
 */
import { describe, it, expect, vi } from "vitest";
import { initDrumPadInput, type DrumPadMap } from "../src/drumPadInput";

/** A fake gamepad whose button values we drive by hand. */
function fakePads(values: number[][]): void {
  vi.stubGlobal("navigator", {
    getGamepads: () =>
      values.map((buttons, index) => ({
        index,
        buttons: buttons.map((value) => ({ value, pressed: value > 0.5 })),
      })),
  });
}

describe("initDrumPadInput", () => {
  it("fires once on a strike, not once per frame while held", () => {
    fakePads([[0, 0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "snare" });

    expect(pad.poll()).toEqual([]);
    fakePads([[1, 0]]);
    expect(pad.poll()).toHaveLength(1); // the strike
    expect(pad.poll()).toHaveLength(0); // still held: not a second hit
    fakePads([[0, 0]]);
    expect(pad.poll()).toHaveLength(0); // released
    fakePads([[1, 0]]);
    expect(pad.poll()).toHaveLength(1); // struck again
  });

  it("ignores a pad that rattles below the threshold", () => {
    fakePads([[0.1]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "snare" });
    pad.poll();
    fakePads([[0.3]]); // still under PRESS_THRESHOLD
    expect(pad.poll()).toEqual([]);
    fakePads([[0.9]]);
    expect(pad.poll()).toHaveLength(1);
  });

  it("takes velocity from the button's force where the kit reports it", () => {
    fakePads([[0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "snare" });
    pad.poll();
    fakePads([[0.5]]);
    const [hit] = pad.poll();
    expect(hit.velocity).toBe(Math.round(0.5 * 127));
  });

  it("gives a digital button a full-force velocity", () => {
    // Rock Band 1 pads have no velocity at all; they report 1 or 0.
    fakePads([[0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "kick" });
    pad.poll();
    fakePads([[1]]);
    expect(pad.poll()[0].velocity).toBe(127);
  });

  it("binds a pad by hitting it, and does not sound that hit", () => {
    // Otherwise naming a drum plays it, which is startling and teaches the
    // player that binding is a thing that makes noise.
    fakePads([[0, 0]]);
    const onMapChanged = vi.fn();
    const pad = initDrumPadInput(onMapChanged);
    pad.learn("crash");
    expect(pad.learning()).toBe("crash");

    pad.poll();
    fakePads([[0, 1]]);
    expect(pad.poll()).toEqual([]); // consumed by the binding
    expect(pad.getMap()).toEqual({ 1: "crash" });
    expect(pad.learning()).toBeNull();
    expect(onMapChanged).toHaveBeenCalledWith({ 1: "crash" });

    fakePads([[0, 0]]);
    pad.poll();
    fakePads([[0, 1]]);
    expect(pad.poll()).toEqual([{ lane: "crash", velocity: 127 }]);
  });

  it("rebinding a pad replaces its lane rather than adding one", () => {
    fakePads([[0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "snare" });
    pad.learn("tom_high");
    pad.poll();
    fakePads([[1]]);
    pad.poll();
    expect(pad.getMap()).toEqual({ 0: "tom_high" });
  });

  it("ignores buttons nobody bound", () => {
    fakePads([[0, 0, 0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 1: "snare" } as DrumPadMap);
    pad.poll();
    fakePads([[1, 0, 1]]);
    expect(pad.poll()).toEqual([]);
  });

  it("survives having no gamepad, and says so", () => {
    vi.stubGlobal("navigator", {});
    const pad = initDrumPadInput(() => {});
    expect(pad.connected()).toBe(false);
    expect(pad.poll()).toEqual([]);
  });

  it("reads several pads struck in the same frame", () => {
    // A flam, or a crash landing with the kick.
    fakePads([[0, 0]]);
    const pad = initDrumPadInput(() => {});
    pad.setMap({ 0: "kick", 1: "crash" });
    pad.poll();
    fakePads([[1, 1]]);
    expect(pad.poll().map((h) => h.lane).sort()).toEqual(["crash", "kick"]);
  });
});
