// @vitest-environment jsdom
/**
 * Execution tests for the Configure -> MIDI -> Transport control panel.
 *
 * The learn flow is the point: click Learn, press a button, capture whatever
 * arrives. The cases that matter are the ones a real controller produces --
 * a release arriving right after the press, a button already bound to another
 * action, and traffic arriving when nothing is being learned.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { initMidiTransportPanel, type MidiTransportPanelHandle } from "../src/midiTransportPanel";
import { defaultBindings, type TransportBindings } from "../src/midiTransportBindings";
import type { MidiInputMessageEvent } from "../src/midiInput";

let handle: MidiTransportPanelHandle | null = null;

function stageDom(): void {
  document.body.innerHTML =
    '<div id="midiTransportRows"></div><pre id="midiTransportStatus"></pre>';
}

function setup(initial: TransportBindings = defaultBindings()) {
  let bindings = initial;
  const setBindings = vi.fn((next: TransportBindings) => {
    bindings = next;
  });
  handle = initMidiTransportPanel({ getBindings: () => bindings, setBindings });
  return { setBindings, current: () => bindings };
}

function send(over: Partial<MidiInputMessageEvent>): void {
  window.dispatchEvent(
    new CustomEvent("auralprimer:midi-input", {
      detail: {
        timestamp_us: 0,
        message_type: "control_change",
        status: 0xb0,
        channel: 0,
        data1: 31,
        data2: 127,
        bytes: [],
        ...over,
      },
    }),
  );
}

const rows = () => Array.from(document.querySelectorAll(".midiTransportRow"));
const statusText = () => document.getElementById("midiTransportStatus")!.textContent ?? "";

/** The Learn button on the Nth row (order matches TRANSPORT_ACTIONS). */
function learnBtn(index: number): HTMLButtonElement {
  return rows()[index].querySelectorAll("button")[0] as HTMLButtonElement;
}
function clearBtn(index: number): HTMLButtonElement {
  return rows()[index].querySelectorAll("button")[1] as HTMLButtonElement;
}
function valueText(index: number): string {
  return rows()[index].querySelector(".midiTransportValue")!.textContent ?? "";
}

describe("midiTransportPanel", () => {
  beforeEach(stageDom);
  afterEach(() => {
    handle?.dispose();
    handle = null;
  });

  it("throws when its DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() =>
      initMidiTransportPanel({ getBindings: defaultBindings, setBindings: vi.fn() }),
    ).toThrow(/required DOM/);
  });

  it("renders a row per action showing the current binding", () => {
    setup();
    expect(rows()).toHaveLength(6);
    expect(valueText(0)).toBe("CC 31 (any ch)");
  });

  it("offers wait mode as a learnable action, unassigned by default", () => {
    const h = setup();
    expect(valueText(5)).toBe("unassigned");

    learnBtn(5).click();
    send({ data1: 50, data2: 127, channel: 1 });
    expect(h.current().waitMode).toEqual({ kind: "cc", number: 50, channel: 1 });
  });

  it("is not learning until asked", () => {
    setup();
    expect(handle!.isLearning()).toBe(false);
  });

  it("captures the next press into the row being learned", () => {
    const h = setup();
    learnBtn(4).click(); // play
    expect(handle!.isLearning()).toBe(true);

    send({ message_type: "note_on", data1: 36, data2: 100, channel: 9 });

    expect(handle!.isLearning()).toBe(false);
    expect(h.current().play).toEqual({ kind: "note", number: 36, channel: 9 });
    expect(h.setBindings).toHaveBeenCalledTimes(1);
    expect(valueText(4)).toBe("Note 36 (ch 10)");
  });

  it("ignores the release that follows the captured press", () => {
    const h = setup();
    learnBtn(4).click();
    send({ data1: 44, data2: 127 }); // press -> captured
    send({ data1: 44, data2: 0 }); // release -> must not re-capture
    expect(h.setBindings).toHaveBeenCalledTimes(1);
    expect(h.current().play).toEqual({ kind: "cc", number: 44, channel: 0 });
  });

  it("waits through non-button traffic while learning", () => {
    const h = setup();
    learnBtn(4).click();
    send({ message_type: "pitch_bend" });
    expect(handle!.isLearning()).toBe(true);
    expect(h.setBindings).not.toHaveBeenCalled();
  });

  it("steals a button already bound to another action", () => {
    const h = setup();
    // Learn Play onto CC 31, which the defaults give to Start-over.
    learnBtn(4).click();
    send({ data1: 31, data2: 127 });
    expect(h.current().play).toEqual({ kind: "cc", number: 31, channel: 0 });
    expect(h.current().restart).toBeNull();
  });

  it("cancels learning when Learn is clicked again", () => {
    const h = setup();
    learnBtn(0).click();
    expect(learnBtn(0).textContent).toBe("Cancel");
    learnBtn(0).click();
    expect(handle!.isLearning()).toBe(false);
    send({ data1: 44, data2: 127 });
    expect(h.setBindings).not.toHaveBeenCalled();
  });

  it("cancels learning on Escape", () => {
    setup();
    learnBtn(0).click();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(handle!.isLearning()).toBe(false);
  });

  it("clears a binding", () => {
    const h = setup();
    clearBtn(0).click();
    expect(h.current().restart).toBeNull();
    expect(valueText(0)).toBe("unassigned");
  });

  it("shows incoming traffic even when not learning, as a liveness check", () => {
    setup();
    send({ data1: 77, data2: 12 });
    expect(statusText()).toContain("CC 77 = 12");
  });

  it("reports nothing received before any traffic", () => {
    setup();
    expect(statusText()).toContain("no MIDI received yet");
  });

  it("annotates each message with the action it drives and the edge it reads as", () => {
    // The whole point of the monitor: a controller that repeats its press value
    // on release shows two PRESS lines, which is why momentary hold cannot work
    // for it. That has to be visible as a sequence, not one overwritten line.
    setup();
    send({ data1: 31, data2: 127 });
    send({ data1: 31, data2: 0 });
    const text = statusText();
    expect(text).toContain("Start song over: PRESS");
    expect(text).toContain("Start song over: RELEASE");
  });

  it("marks traffic that matches no binding", () => {
    setup();
    send({ data1: 77, data2: 127 });
    expect(statusText()).toContain("(unbound)");
  });

  it("keeps a rolling window rather than one line", () => {
    setup();
    for (let i = 0; i < 4; i += 1) send({ data1: 31, data2: i % 2 === 0 ? 127 : 0 });
    expect(statusText().split(String.fromCharCode(10)).length).toBeGreaterThan(1);
  });

  it("prompts for the specific button while learning", () => {
    setup();
    learnBtn(1).click(); // rewind
    expect(statusText()).toContain("Rewind");
  });

  it("stops listening after dispose", () => {
    const h = setup();
    learnBtn(0).click();
    handle!.dispose();
    handle = null;
    send({ data1: 44, data2: 127 });
    expect(h.setBindings).not.toHaveBeenCalled();
  });
});
