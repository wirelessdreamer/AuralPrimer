import type { TransportTimebase } from "./audioBackend";

export type AuralSongAudioBlob = {
  mime: string;
  bytes: number[];
};

export type LoadedAudioSource = {
  blob: Blob;
  mime: string;
};

export type AuralSongAudioLoadTransport = {
  loadAudioFromAuralSong(containerPath: string): Promise<void>;
  loadAudio(source: LoadedAudioSource): Promise<void>;
  setPlaybackRate(rate: number): void;
};

export type AuralSongAudioLoadResult =
  | { mode: "direct" }
  | { mode: "blob"; loadedAudio: LoadedAudioSource; byteLength: number; mime: string };

export async function loadAuralSongAudioIntoTransport(opts: {
  containerPath: string;
  timebase: TransportTimebase;
  transport: AuralSongAudioLoadTransport;
  playbackRate: number;
  readAuralSongAudio: (containerPath: string) => Promise<AuralSongAudioBlob>;
}): Promise<AuralSongAudioLoadResult> {
  if (typeof opts.timebase.loadFromAuralSong === "function") {
    await opts.transport.loadAudioFromAuralSong(opts.containerPath);
    opts.transport.setPlaybackRate(opts.playbackRate);
    return { mode: "direct" };
  }

  const raw = await opts.readAuralSongAudio(opts.containerPath);
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

