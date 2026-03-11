// @vitest-environment jsdom
import { loadSongPackAudioIntoTransport, type SongPackAudioLoadTransport } from "../src/songpackAudioLoader";
import type { TransportTimebase } from "../src/audioBackend";

function makeTimebase(withDirectLoad: boolean): TransportTimebase {
  const base: TransportTimebase = {
    async load() {},
    async play() {},
    pause() {},
    stop() {},
    seek() {},
    setLoop() {},
    setPlaybackRate() {},
    getPlaybackRate() {
      return 1;
    },
    getDurationSec() {
      return 1;
    },
    getCurrentTimeSec() {
      return 0;
    },
    getIsPlaying() {
      return false;
    },
    dispose() {}
  };
  if (withDirectLoad) {
    (base as TransportTimebase & { loadFromSongPack: (path: string) => Promise<void> }).loadFromSongPack = async () =>
      Promise.resolve();
  }
  return base;
}

function makeTransport(): SongPackAudioLoadTransport & {
  loadAudioFromSongPack: ReturnType<typeof vi.fn>;
  loadAudio: ReturnType<typeof vi.fn>;
  setPlaybackRate: ReturnType<typeof vi.fn>;
} {
  return {
    loadAudioFromSongPack: vi.fn(async () => Promise.resolve()),
    loadAudio: vi.fn(async () => Promise.resolve()),
    setPlaybackRate: vi.fn(() => {})
  };
}

describe("songpackAudioLoader", () => {
  it("uses direct load when timebase supports loadFromSongPack", async () => {
    const transport = makeTransport();
    const readSongPackAudio = vi.fn(async () => ({ mime: "audio/wav", bytes: [1, 2, 3] }));

    const result = await loadSongPackAudioIntoTransport({
      containerPath: "C:/songs/demo.songpack",
      timebase: makeTimebase(true),
      transport,
      playbackRate: 1.25,
      readSongPackAudio
    });

    expect(result.mode).toBe("direct");
    expect(transport.loadAudioFromSongPack).toHaveBeenCalledWith("C:/songs/demo.songpack");
    expect(transport.loadAudio).not.toHaveBeenCalled();
    expect(readSongPackAudio).not.toHaveBeenCalled();
    expect(transport.setPlaybackRate).toHaveBeenCalledWith(1.25);
  });

  it("uses blob load when timebase lacks direct support, even if transport exposes loadAudioFromSongPack", async () => {
    const transport = makeTransport();
    transport.loadAudioFromSongPack.mockImplementation(async () => {
      throw new Error("timebase does not support loadFromSongPack()");
    });
    const readSongPackAudio = vi.fn(async () => ({ mime: "audio/ogg", bytes: [7, 8, 9, 10] }));

    const result = await loadSongPackAudioIntoTransport({
      containerPath: "C:/songs/fallback.songpack",
      timebase: makeTimebase(false),
      transport,
      playbackRate: 0.9,
      readSongPackAudio
    });

    expect(result.mode).toBe("blob");
    if (result.mode === "blob") {
      expect(result.byteLength).toBe(4);
      expect(result.mime).toBe("audio/ogg");
    }
    expect(transport.loadAudioFromSongPack).not.toHaveBeenCalled();
    expect(readSongPackAudio).toHaveBeenCalledWith("C:/songs/fallback.songpack");
    expect(transport.loadAudio).toHaveBeenCalledTimes(1);
    expect(transport.setPlaybackRate).toHaveBeenCalledWith(0.9);
  });
});

