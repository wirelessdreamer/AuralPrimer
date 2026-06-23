// @vitest-environment jsdom
import { loadAuralSongAudioIntoTransport, type AuralSongAudioLoadTransport } from "../src/auralSongAudioLoader";
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
    (base as TransportTimebase & { loadFromAuralSong: (path: string) => Promise<void> }).loadFromAuralSong = async () =>
      Promise.resolve();
  }
  return base;
}

function makeTransport(): AuralSongAudioLoadTransport & {
  loadAudioFromAuralSong: ReturnType<typeof vi.fn>;
  loadAudio: ReturnType<typeof vi.fn>;
  setPlaybackRate: ReturnType<typeof vi.fn>;
} {
  return {
    loadAudioFromAuralSong: vi.fn(async () => Promise.resolve()),
    loadAudio: vi.fn(async () => Promise.resolve()),
    setPlaybackRate: vi.fn(() => {})
  };
}

describe("auralsongAudioLoader", () => {
  it("uses direct load when timebase supports loadFromAuralSong", async () => {
    const transport = makeTransport();
    const readAuralSongAudio = vi.fn(async () => ({ mime: "audio/wav", bytes: [1, 2, 3] }));

    const result = await loadAuralSongAudioIntoTransport({
      containerPath: "C:/songs/demo.auralsong",
      timebase: makeTimebase(true),
      transport,
      playbackRate: 1.25,
      readAuralSongAudio
    });

    expect(result.mode).toBe("direct");
    expect(transport.loadAudioFromAuralSong).toHaveBeenCalledWith("C:/songs/demo.auralsong");
    expect(transport.loadAudio).not.toHaveBeenCalled();
    expect(readAuralSongAudio).not.toHaveBeenCalled();
    expect(transport.setPlaybackRate).toHaveBeenCalledWith(1.25);
  });

  it("uses blob load when timebase lacks direct support, even if transport exposes loadAudioFromAuralSong", async () => {
    const transport = makeTransport();
    transport.loadAudioFromAuralSong.mockImplementation(async () => {
      throw new Error("timebase does not support loadFromAuralSong()");
    });
    const readAuralSongAudio = vi.fn(async () => ({ mime: "audio/ogg", bytes: [7, 8, 9, 10] }));

    const result = await loadAuralSongAudioIntoTransport({
      containerPath: "C:/songs/fallback.auralsong",
      timebase: makeTimebase(false),
      transport,
      playbackRate: 0.9,
      readAuralSongAudio
    });

    expect(result.mode).toBe("blob");
    if (result.mode === "blob") {
      expect(result.byteLength).toBe(4);
      expect(result.mime).toBe("audio/ogg");
    }
    expect(transport.loadAudioFromAuralSong).not.toHaveBeenCalled();
    expect(readAuralSongAudio).toHaveBeenCalledWith("C:/songs/fallback.auralsong");
    expect(transport.loadAudio).toHaveBeenCalledTimes(1);
    expect(transport.setPlaybackRate).toHaveBeenCalledWith(0.9);
  });
});

