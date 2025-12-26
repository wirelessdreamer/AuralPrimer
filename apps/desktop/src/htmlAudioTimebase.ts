import { clampLoop } from "./audioBackend";
import type { TransportTimebase } from "./audioBackend";

/**
 * TransportTimebase implemented with an HTMLAudioElement.
 *
 * This keeps the existing MVP behavior working, while letting the transport clock
 * depend on an interface instead of directly on the DOM element.
 */
export class HtmlAudioTimebase implements TransportTimebase {
  private objectUrl: string | null = null;
  private loop: { t0: number; t1: number } | undefined;
  private playbackRate = 1;

  constructor(private readonly audio: HTMLAudioElement) {}

  async load(source: { blob: Blob; mime: string }): Promise<void> {
    // Reset state first.
    this.stop();
    this.clearObjectUrl();

    this.objectUrl = URL.createObjectURL(source.blob);
    this.audio.src = this.objectUrl;
    this.audio.load();

    // Wait for metadata so duration/currentTime behave.
    await new Promise<void>((resolve, reject) => {
      const onLoaded = () => {
        cleanup();
        resolve();
      };
      const onError = () => {
        cleanup();
        reject(new Error("audio load error"));
      };
      const cleanup = () => {
        this.audio.removeEventListener("loadedmetadata", onLoaded);
        this.audio.removeEventListener("error", onError);
      };

      this.audio.addEventListener("loadedmetadata", onLoaded);
      this.audio.addEventListener("error", onError);
    });

    // Re-apply current settings that can reset on src/load.
    this.audio.playbackRate = this.playbackRate;

    this.applyLoopToElement();
  }

  async play(): Promise<void> {
    await this.audio.play();
  }

  pause(): void {
    this.audio.pause();
  }

  stop(): void {
    this.audio.pause();
    this.audio.currentTime = 0;
  }

  seek(tSec: number): void {
    this.audio.currentTime = Math.max(0, tSec);
  }

  setLoop(loop?: { t0: number; t1: number }): void {
    this.loop = loop ? clampLoop(loop) : undefined;
    this.applyLoopToElement();
  }

  setPlaybackRate(rate: number): void {
    const r = Number.isFinite(rate) && rate > 0 ? rate : 1;
    this.playbackRate = r;
    this.audio.playbackRate = r;
  }

  getPlaybackRate(): number {
    return this.playbackRate;
  }

  getDurationSec(): number | null {
    const d = this.audio.duration;
    return Number.isFinite(d) && d > 0 ? d : null;
  }

  getCurrentTimeSec(): number {
    return this.audio.currentTime;
  }

  getIsPlaying(): boolean {
    return !this.audio.paused;
  }

  dispose(): void {
    this.stop();
    this.clearObjectUrl();
  }

  private clearObjectUrl() {
    if (this.objectUrl) {
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }
  }

  private applyLoopToElement() {
    // Basic loop enforcement via `timeupdate` callback.
    // Note: HTMLAudio has built-in .loop boolean but not region loop.
    // We keep this lightweight; higher precision looping lives in WebAudio.
    this.audio.ontimeupdate = null;

    const loop = this.loop;
    if (!loop) return;

    this.audio.ontimeupdate = () => {
      if (!this.loop) return;
      if (this.audio.currentTime >= loop.t1) {
        this.audio.currentTime = loop.t0;
      }
    };
  }
}
