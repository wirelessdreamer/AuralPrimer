import type { TransportTimebase } from "./audioBackend";

export type SongPackAudioBlob = {
  mime: string;
  bytes: number[];
};

export type LoadedAudioSource = {
  blob: Blob;
  mime: string;
};

export type SongPackAudioLoadTransport = {
  loadAudioFromSongPack(containerPath: string): Promise<void>;
  loadAudio(source: LoadedAudioSource): Promise<void>;
  setPlaybackRate(rate: number): void;
};

export type SongPackAudioLoadResult =
  | { mode: "direct" }
  | { mode: "blob"; loadedAudio: LoadedAudioSource; byteLength: number; mime: string };

export async function loadSongPackAudioIntoTransport(opts: {
  containerPath: string;
  timebase: TransportTimebase;
  transport: SongPackAudioLoadTransport;
  playbackRate: number;
  readSongPackAudio: (containerPath: string) => Promise<SongPackAudioBlob>;
}): Promise<SongPackAudioLoadResult> {
  if (typeof opts.timebase.loadFromSongPack === "function") {
    await opts.transport.loadAudioFromSongPack(opts.containerPath);
    opts.transport.setPlaybackRate(opts.playbackRate);
    return { mode: "direct" };
  }

  const raw = await opts.readSongPackAudio(opts.containerPath);
  const bytes = new Uint8Array(raw.bytes);
  const loadedAudio: LoadedAudioSource = {
    blob: new Blob([bytes], { type: raw.mime }),
    mime: raw.mime
  };
  await opts.transport.loadAudio(loadedAudio);
  opts.transport.setPlaybackRate(opts.playbackRate);
  return {
    mode: "blob",
    loadedAudio,
    byteLength: bytes.byteLength,
    mime: raw.mime
  };
}

