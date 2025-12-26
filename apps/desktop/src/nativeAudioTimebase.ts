import type { TransportTimebase } from "./audioBackend";
import { clampLoop } from "./audioBackend";
import { invoke } from "@tauri-apps/api/core";

type NativeAudioState = {
  sample_rate_hz: number;
  channels: number;
  is_playing: boolean;
  t_sec: number;
  playback_rate: number;
  loop_t0_sec: number | null;
  loop_t1_sec: number | null;
  has_audio: boolean;
};

type LoadedSongPackAudioInfo = {
  mime: string;
  duration_sec: number;
};

/**
 * TransportTimebase backed by the Phase 1 Rust native audio engine.
 *
 * Notes:
 * - Phase 1.5 supports MP3/OGG/WAV decode via Rust (symphonia).
 * - We keep this implementation simple: polling `native_audio_get_state()` for time.
 */
export class NativeAudioTimebase implements TransportTimebase {
  private loop: { t0: number; t1: number } | undefined;
  private playbackRate = 1;

  private loadedDurationSec: number | null = null;
  private initialized = false;

  constructor(private readonly opts: { sampleRateHz?: number; channels?: number } = {}) {}

  async load(source: { blob: Blob; mime: string }): Promise<void> {
    // Convert Blob -> bytes.
    const ab = await source.blob.arrayBuffer();
    const bytes = Array.from(new Uint8Array(ab));

    // Phase 1.5: decode and (re)init native engine inside Rust.
    await invoke("native_audio_load_audio_bytes", { mime: source.mime, bytes });
    this.initialized = true;

    // We don't currently expose duration from Rust; however, returning null causes
    // TransportController to treat this as "no audio" and fall back to a simulated
    // clock. For native playback we want the controller to treat the native engine
    // as authoritative, so we return a non-null sentinel.
    this.loadedDurationSec = 24 * 60 * 60; // 24h sentinel (best-effort)

    // Reset loop/playbackRate each load.
    await invoke("native_audio_set_playback_rate", { rate: this.playbackRate });
    await invoke("native_audio_set_loop", {
      t0: this.loop ? this.loop.t0 : null,
      t1: this.loop ? this.loop.t1 : null
    });
  }

  async loadFromSongPack(containerPath: string): Promise<{ mime: string; durationSec: number } | void> {
    const info = await invoke<LoadedSongPackAudioInfo>("native_audio_load_songpack_audio", {
      containerPath
    });

    this.initialized = true;
    const durationSec = Number(info.duration_sec ?? 0);
    this.loadedDurationSec = Number.isFinite(durationSec) && durationSec > 0 ? durationSec : 24 * 60 * 60;

    // Reset loop/playbackRate each load.
    await invoke("native_audio_set_playback_rate", { rate: this.playbackRate });
    await invoke("native_audio_set_loop", {
      t0: this.loop ? this.loop.t0 : null,
      t1: this.loop ? this.loop.t1 : null
    });

    return { mime: info.mime, durationSec: this.loadedDurationSec };
  }

  async play(): Promise<void> {
    await invoke("native_audio_play");
    this._lastIsPlaying = true;
  }

  pause(): void {
    void invoke("native_audio_pause");
    this._lastIsPlaying = false;
  }

  stop(): void {
    void invoke("native_audio_stop");
    this._lastIsPlaying = false;
    this._lastT = 0;
  }

  seek(tSec: number): void {
    void invoke("native_audio_seek", { tSec });
    this._lastT = tSec;
  }

  setLoop(loop?: { t0: number; t1: number }): void {
    this.loop = loop ? clampLoop(loop) : undefined;
    void invoke("native_audio_set_loop", {
      t0: this.loop ? this.loop.t0 : null,
      t1: this.loop ? this.loop.t1 : null
    });
  }

  setPlaybackRate(rate: number): void {
    const r = Number.isFinite(rate) && rate > 0 ? rate : 1;
    this.playbackRate = r;
    void invoke("native_audio_set_playback_rate", { rate: r });
  }

  getPlaybackRate(): number {
    return this.playbackRate;
  }

  getDurationSec(): number | null {
    return this.loadedDurationSec;
  }

  getCurrentTimeSec(): number {
    // Phase 1: best-effort synchronous-ish polling.
    // Caller should tolerate small jitter.
    // NOTE: invoke is async; we return last-known value and refresh opportunistically.
    // For simplicity, we block transport precision to next frame.
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    invoke<NativeAudioState>("native_audio_get_state").then((s) => {
      this._lastT = s.t_sec;
      this._lastIsPlaying = s.is_playing;
    });
    return this._lastT;
  }
  private _lastT = 0;
  private _lastIsPlaying = false;

  getIsPlaying(): boolean {
    // Keep in sync via polling in getCurrentTimeSec.
    return this._lastIsPlaying;
  }

  dispose(): void {
    // Phase 1: no explicit shutdown. Engine stays alive for the app lifetime.
    // We just stop.
    this.stop();
  }
}
