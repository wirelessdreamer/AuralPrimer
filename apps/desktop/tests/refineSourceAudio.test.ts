// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
import { RefineSourceAudio } from "../src/refineSourceAudio";
import type { RefineAuditionHandle } from "../src/refineAudition";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), convertFileSrc: (p: string) => p }));
let source: { pause: ReturnType<typeof vi.fn>; play: ReturnType<typeof vi.fn>; preload: string; crossOrigin: string; preservesPitch: boolean };
beforeEach(() => {
  vi.stubGlobal("Audio", class {
    pause = vi.fn(); play = vi.fn(() => Promise.resolve()); preload = ""; crossOrigin = ""; preservesPitch = false;
    constructor() { source = this; }
  });
});

it("cancels an initial play if pause arrives while the shared audio context is resuming", async () => {
  let finishResume!: () => void;
  const graph = { connectSource: vi.fn(), resume: () => new Promise<void>(resolve => { finishResume = resolve; }) } as unknown as RefineAuditionHandle;
  const transport = new RefineSourceAudio(graph);
  const pending = transport.play();
  transport.pause();
  finishResume();
  await pending;
  expect(source.play).not.toHaveBeenCalled();
  expect(source.preservesPitch).toBe(true);
  expect(source.crossOrigin).toBe("anonymous");
});
