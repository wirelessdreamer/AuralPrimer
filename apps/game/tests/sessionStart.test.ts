import { startSelectedSongSessionFlow, type StartSessionDeps } from "../src/sessionStart";

function makeDeps(overrides: Partial<StartSessionDeps> = {}): StartSessionDeps {
  return {
    setPlayStartDisabled: vi.fn(),
    setAudioStatus: vi.fn(),
    setVizStatus: vi.fn(),
    showSongLibraryStep: vi.fn(),
    loadAudioFromSelectedSongPack: vi.fn(async () => Promise.resolve()),
    startVisualizer: vi.fn(async () => Promise.resolve()),
    playTransport: vi.fn(async () => Promise.resolve()),
    startMidiOut: vi.fn(async () => Promise.resolve()),
    isNativePlaybackInactiveError: vi.fn(() => false),
    tryFallbackToHtmlPlayback: vi.fn(async () => false),
    onPrimaryStartError: vi.fn(),
    onFallbackStartError: vi.fn(),
    ...overrides
  };
}

describe("sessionStart", () => {
  it("returns no_song and shows library when nothing is selected", async () => {
    const deps = makeDeps();
    const result = await startSelectedSongSessionFlow(
      { selectedSongPackPath: null, lastLoadedSongPackPath: null, hasVisualizer: false },
      deps
    );

    expect(result).toEqual({ kind: "no_song" });
    expect(deps.setAudioStatus).toHaveBeenCalledWith("Select a song first from the library");
    expect(deps.showSongLibraryStep).toHaveBeenCalledTimes(1);
    expect(deps.setPlayStartDisabled).not.toHaveBeenCalled();
  });

  it("loads audio, starts visualizer, plays transport, and starts midi", async () => {
    const deps = makeDeps();
    const result = await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: null,
        hasVisualizer: false
      },
      deps
    );

    expect(result).toEqual({ kind: "started" });
    expect(deps.setPlayStartDisabled).toHaveBeenNthCalledWith(1, true);
    expect(deps.loadAudioFromSelectedSongPack).toHaveBeenCalledTimes(1);
    expect(deps.startVisualizer).toHaveBeenCalledTimes(1);
    expect(deps.playTransport).toHaveBeenCalledTimes(1);
    expect(deps.startMidiOut).toHaveBeenCalledTimes(1);
    expect(deps.setAudioStatus).toHaveBeenLastCalledWith("playing: C:/songs/a.songpack");
    expect(deps.setPlayStartDisabled).toHaveBeenLastCalledWith(false);
  });

  it("skips load and visualizer when already loaded and viz exists", async () => {
    const deps = makeDeps();
    const result = await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: "C:/songs/a.songpack",
        hasVisualizer: true
      },
      deps
    );

    expect(result).toEqual({ kind: "started" });
    expect(deps.loadAudioFromSelectedSongPack).not.toHaveBeenCalled();
    expect(deps.startVisualizer).not.toHaveBeenCalled();
    expect(deps.setAudioStatus).toHaveBeenCalledWith("audio already loaded: C:/songs/a.songpack");
  });

  it("fails normally when error is not native callback inactive", async () => {
    const deps = makeDeps({
      playTransport: vi.fn(async () => {
        throw new Error("generic play failure");
      }),
      isNativePlaybackInactiveError: vi.fn(() => false)
    });
    const result = await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: "C:/songs/a.songpack",
        hasVisualizer: true
      },
      deps
    );

    expect(result).toEqual({ kind: "failed", error: "Error: generic play failure" });
    expect(deps.onPrimaryStartError).toHaveBeenCalledTimes(1);
    expect(deps.tryFallbackToHtmlPlayback).not.toHaveBeenCalled();
    expect(deps.setVizStatus).toHaveBeenCalledWith("playback start failed: Error: generic play failure");
  });

  it("starts via fallback when native callback inactive and fallback succeeds", async () => {
    const deps = makeDeps({
      playTransport: vi.fn(async () => {
        throw new Error("native playback did not start (output callback inactive; host=wasapi)");
      }),
      isNativePlaybackInactiveError: vi.fn((err: string) => err.includes("output callback inactive")),
      tryFallbackToHtmlPlayback: vi.fn(async () => true)
    });
    const result = await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: "C:/songs/a.songpack",
        hasVisualizer: true
      },
      deps
    );

    expect(result).toEqual({ kind: "fallback_started" });
    expect(deps.tryFallbackToHtmlPlayback).toHaveBeenCalledWith("C:/songs/a.songpack");
    expect(deps.setVizStatus).not.toHaveBeenCalled();
  });

  it("surfaces original error when fallback throws", async () => {
    const deps = makeDeps({
      playTransport: vi.fn(async () => {
        throw new Error("native playback did not start (output callback inactive)");
      }),
      isNativePlaybackInactiveError: vi.fn(() => true),
      tryFallbackToHtmlPlayback: vi.fn(async () => {
        throw new Error("fallback failed");
      })
    });
    const result = await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: "C:/songs/a.songpack",
        hasVisualizer: true
      },
      deps
    );

    expect(result).toEqual({ kind: "failed", error: "Error: native playback did not start (output callback inactive)" });
    expect(deps.onFallbackStartError).toHaveBeenCalledTimes(1);
    expect(deps.setVizStatus).toHaveBeenCalledWith(
      "playback start failed: Error: native playback did not start (output callback inactive)"
    );
  });

  it("always re-enables start button after failure", async () => {
    const deps = makeDeps({
      loadAudioFromSelectedSongPack: vi.fn(async () => {
        throw new Error("load failed");
      })
    });
    await startSelectedSongSessionFlow(
      {
        selectedSongPackPath: "C:/songs/a.songpack",
        lastLoadedSongPackPath: null,
        hasVisualizer: false
      },
      deps
    );

    expect(deps.setPlayStartDisabled).toHaveBeenNthCalledWith(1, true);
    expect(deps.setPlayStartDisabled).toHaveBeenLastCalledWith(false);
  });
});

