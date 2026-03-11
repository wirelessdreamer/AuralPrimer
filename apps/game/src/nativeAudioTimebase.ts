import type { TransportTimebase } from "./audioBackend";
import { clampLoop } from "./audioBackend";
import { invoke } from "@tauri-apps/api/core";

export type NativeAudioDeviceSelection = {
  name: string;
  channels: number;
  sample_rate_hz: number;
};

export type NativeAudioDeviceInfo = NativeAudioDeviceSelection & {
  is_default: boolean;
};

export type NativeAudioHostSelection = {
  id: string;
};

export type NativeAudioHostInfo = {
  id: string;
  name: string;
  is_default: boolean;
};

type NativeAudioState = {
  output_host: NativeAudioHostSelection;
  sample_rate_hz: number;
  channels: number;
  output_device: NativeAudioDeviceSelection;
  is_playing: boolean;
  t_sec: number;
  playback_rate: number;
  loop_t0_sec: number | null;
  loop_t1_sec: number | null;
  has_audio: boolean;
  output_buffer_frames: number | null;
  callback_count: number;
  callback_overrun_count: number;
};

type LoadedSongPackAudioInfo = {
  mime: string;
  duration_sec: number;
};

const LONG_DURATION_SENTINEL_SEC = 24 * 60 * 60;
const PLAY_START_MAX_POLLS = 120;
const PLAY_START_POLL_MS = 25;
const PLAY_START_RECOVERY_POLLS = 80;
const PLAY_START_ADAPTIVE_MAX_WAIT_SEC = 20;
const PLAY_START_ACTIVE_STREAM_EXTRA_POLLS = 480;

/**
 * TransportTimebase backed by the Rust native audio engine.
 *
 * Notes:
 * - Decode path supports MP3/OGG/WAV through Rust (`symphonia`).
 * - We poll `native_audio_get_state()` to keep host transport synced.
 */
export class NativeAudioTimebase implements TransportTimebase {
  private loop: { t0: number; t1: number } | undefined;
  private playbackRate = 1;

  private loadedDurationSec: number | null = null;
  private initialized = false;

  private lastLoadedSongPackPath: string | null = null;
  private lastLoadedAudio: { mime: string; bytes: number[] } | null = null;

  private _lastT = 0;
  private _lastIsPlaying = false;
  private _lastSampleRateHz = 0;
  private _lastOutputBufferFrames: number | null = null;

  constructor(private readonly opts: { sampleRateHz?: number; channels?: number } = {}) {}

  private async loadAudioBytesIntoNative(mime: string, bytes: number[]): Promise<void> {
    await invoke("native_audio_load_audio_bytes", { mime, bytes });
    this.initialized = true;
    // Native decode path does not currently return decoded duration.
    this.loadedDurationSec = LONG_DURATION_SENTINEL_SEC;
  }

  private async loadSongPackAudioIntoNative(containerPath: string): Promise<LoadedSongPackAudioInfo> {
    const info = await invoke<LoadedSongPackAudioInfo>("native_audio_load_songpack_audio", {
      containerPath
    });
    this.initialized = true;
    const durationSec = Number(info.duration_sec ?? 0);
    this.loadedDurationSec = Number.isFinite(durationSec) && durationSec > 0 ? durationSec : LONG_DURATION_SENTINEL_SEC;
    return info;
  }

  private async applyRuntimeSettings(): Promise<void> {
    await invoke("native_audio_set_playback_rate", { rate: this.playbackRate });
    await invoke("native_audio_set_loop", {
      t0: this.loop ? this.loop.t0 : null,
      t1: this.loop ? this.loop.t1 : null
    });
  }

  private consumeNativeState(s: NativeAudioState): void {
    this._lastT = s.t_sec;
    this._lastIsPlaying = s.is_playing;
    this._lastSampleRateHz = s.sample_rate_hz;
    this._lastOutputBufferFrames = s.output_buffer_frames;
  }

  private async readNativeStateSafe(): Promise<NativeAudioState | null> {
    try {
      return await invoke<NativeAudioState>("native_audio_get_state");
    } catch {
      return null;
    }
  }

  private adaptivePlaybackStartPollBudget(basePolls: number, s: NativeAudioState): number {
    const frames = Number(s.output_buffer_frames);
    const sampleRate = Number(s.sample_rate_hz);
    if (!(frames > 0 && sampleRate > 0)) {
      return basePolls;
    }

    const bufferSec = frames / sampleRate;
    if (!Number.isFinite(bufferSec) || bufferSec <= 0) {
      return basePolls;
    }

    const targetSec = Math.min(PLAY_START_ADAPTIVE_MAX_WAIT_SEC, Math.max(3, bufferSec * 4 + 0.5));
    const targetPolls = Math.ceil((targetSec * 1000) / PLAY_START_POLL_MS);
    return Math.max(basePolls, targetPolls);
  }

  private async waitForNativePlaybackStart(maxPolls = PLAY_START_MAX_POLLS): Promise<boolean> {
    let pollBudget = maxPolls;
    for (let i = 0; i < pollBudget; i += 1) {
      const s = await this.readNativeStateSafe();
      if (s) {
        this.consumeNativeState(s);
        pollBudget = this.adaptivePlaybackStartPollBudget(pollBudget, s);
        if (s.is_playing) {
          return true;
        }
      }
      if (i + 1 < pollBudget) {
        await new Promise<void>((resolve) => {
          setTimeout(resolve, PLAY_START_POLL_MS);
        });
      }
    }
    return false;
  }

  private async tryRecoverPlaybackStart(): Promise<boolean> {
    if (!this.initialized) {
      return false;
    }

    const before = await this.readNativeStateSafe();
    if (!before) {
      return false;
    }

    const currentDevice = before.output_device ?? null;
    let preferredDevice: NativeAudioDeviceSelection | null = null;
    try {
      preferredDevice = await this.getSelectedOutputDevice();
    } catch {
      preferredDevice = null;
    }

    const candidateDevices: Array<NativeAudioDeviceSelection | null> = [];
    if (preferredDevice) {
      // Explicit preference is treated as first-class startup target.
      candidateDevices.push(preferredDevice);
      // Then fall back to system default.
      candidateDevices.push(null);
    } else {
      // No preference: always try system default first.
      candidateDevices.push(null);
    }

    for (const outputDevice of candidateDevices) {
      try {
        await invoke("native_audio_set_output_device", { outputDevice });
        await this.restoreAudioAfterRoutingChange(before);
        await invoke("native_audio_play");
        const started = await this.waitForNativePlaybackStart(PLAY_START_RECOVERY_POLLS);
        if (started) {
          return true;
        }
      } catch {
        // Try next output device candidate.
      }
    }

    // Best effort: restore the original routed device selection.
    try {
      await invoke("native_audio_set_output_device", { outputDevice: currentDevice });
      await this.restoreAudioAfterRoutingChange(before);
    } catch {
      // Ignore restoration failures.
    }

    return false;
  }

  private async restoreAudioAfterRoutingChange(before: NativeAudioState | null): Promise<void> {
    // If we haven't loaded audio yet, routing selection is persisted and applies on first load.
    if (!this.initialized) {
      return;
    }

    if (this.lastLoadedSongPackPath) {
      await this.loadSongPackAudioIntoNative(this.lastLoadedSongPackPath);
    } else if (this.lastLoadedAudio) {
      await this.loadAudioBytesIntoNative(this.lastLoadedAudio.mime, this.lastLoadedAudio.bytes);
    } else {
      return;
    }

    await this.applyRuntimeSettings();

    const seekT = Number(before?.t_sec ?? 0);
    if (Number.isFinite(seekT) && seekT > 0) {
      await invoke("native_audio_seek", { tSec: seekT });
      this._lastT = seekT;
    } else {
      this._lastT = 0;
    }

    if (before?.is_playing) {
      await invoke("native_audio_play");
      this._lastIsPlaying = true;
    } else {
      this._lastIsPlaying = false;
    }
  }

  async listOutputHosts(): Promise<NativeAudioHostInfo[]> {
    return invoke<NativeAudioHostInfo[]>("native_audio_list_output_hosts");
  }

  async getSelectedOutputHost(): Promise<NativeAudioHostSelection | null> {
    return invoke<NativeAudioHostSelection | null>("native_audio_get_selected_output_host");
  }

  async setOutputHost(outputHost: NativeAudioHostSelection | null): Promise<void> {
    const before = await this.readNativeStateSafe();
    await invoke("native_audio_set_output_host_and_persist", {
      outputHost
    });
    await this.restoreAudioAfterRoutingChange(before);
  }

  async listOutputDevices(): Promise<NativeAudioDeviceInfo[]> {
    return invoke<NativeAudioDeviceInfo[]>("native_audio_list_output_devices");
  }

  async getSelectedOutputDevice(): Promise<NativeAudioDeviceSelection | null> {
    return invoke<NativeAudioDeviceSelection | null>("native_audio_get_selected_output_device");
  }

  async setOutputDevice(outputDevice: NativeAudioDeviceSelection | null): Promise<void> {
    const before = await this.readNativeStateSafe();

    await invoke("native_audio_set_output_device_and_persist", {
      outputDevice
    });
    await this.restoreAudioAfterRoutingChange(before);
  }

  async load(source: { blob: Blob; mime: string }): Promise<void> {
    const ab = await source.blob.arrayBuffer();
    const bytes = Array.from(new Uint8Array(ab));

    this.lastLoadedSongPackPath = null;
    this.lastLoadedAudio = { mime: source.mime, bytes };

    await this.loadAudioBytesIntoNative(source.mime, bytes);
    await this.applyRuntimeSettings();
  }

  async loadFromSongPack(containerPath: string): Promise<{ mime: string; durationSec: number } | void> {
    this.lastLoadedSongPackPath = containerPath;
    this.lastLoadedAudio = null;

    const info = await this.loadSongPackAudioIntoNative(containerPath);
    await this.applyRuntimeSettings();

    return { mime: info.mime, durationSec: this.loadedDurationSec ?? LONG_DURATION_SENTINEL_SEC };
  }

  async play(): Promise<void> {
    const beforePlay = await this.readNativeStateSafe();
    const callbackBaseline = beforePlay?.callback_count ?? 0;
    await invoke("native_audio_play");
    if (await this.waitForNativePlaybackStart()) {
      this._lastIsPlaying = true;
      return;
    }

    const afterInitialWait = await this.readNativeStateSafe();
    const callbackCountAfterInitialWait = afterInitialWait?.callback_count ?? 0;
    const hasAudioAfterInitialWait = afterInitialWait?.has_audio ?? false;
    const outputBufferFramesAfterInitialWait = Number(afterInitialWait?.output_buffer_frames ?? 0);
    const streamLikelyStalled =
      callbackCountAfterInitialWait > 0 && !hasAudioAfterInitialWait && !(outputBufferFramesAfterInitialWait > 0);
    // Some WASAPI devices report an active stream but dispatch callbacks sparsely.
    // If we have seen any callback at all, give the stream additional time before route recovery.
    if ((callbackCountAfterInitialWait > callbackBaseline || callbackCountAfterInitialWait > 0) && !streamLikelyStalled) {
      if (await this.waitForNativePlaybackStart(PLAY_START_ACTIVE_STREAM_EXTRA_POLLS)) {
        this._lastIsPlaying = true;
        return;
      }
    }

    if (await this.tryRecoverPlaybackStart()) {
      this._lastIsPlaying = true;
      return;
    }

    this._lastIsPlaying = false;
    const s = await this.readNativeStateSafe();
    const host = s?.output_host?.id ?? "unknown";
    const device = s?.output_device?.name ?? "unknown";
    const callbacks = s?.callback_count ?? 0;
    const hasAudio = s?.has_audio ?? false;
    const outbuf = s?.output_buffer_frames ?? 0;
    const sampleRate = s?.sample_rate_hz ?? 0;
    throw new Error(
      `native playback did not start (output callback inactive; host=${host}; device=${device}; callbacks=${callbacks}; has_audio=${hasAudio}; output_buffer_frames=${outbuf}; sample_rate_hz=${sampleRate})`
    );
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
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    invoke<NativeAudioState>("native_audio_get_state")
      .then((s) => this.consumeNativeState(s))
      .catch(() => {
        // keep last-known state on transient invoke failures
      });
    return this._lastT;
  }

  getIsPlaying(): boolean {
    return this._lastIsPlaying;
  }

  getOutputLatencySec(): number | undefined {
    const sampleRate = this._lastSampleRateHz || this.opts.sampleRateHz || 0;
    if (!sampleRate || this._lastOutputBufferFrames == null) {
      return undefined;
    }
    return this._lastOutputBufferFrames / sampleRate;
  }

  dispose(): void {
    this.stop();
  }
}
