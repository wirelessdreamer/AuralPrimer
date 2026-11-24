import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import type { RefineAuditionHandle } from "./refineAudition";

/** Cleanup playback shares the audition graph, so both ears use one output. */
export class RefineSourceAudio {
  private readonly audio = new Audio();
  private generation = 0;
  private playGeneration = 0;

  constructor(private readonly audition: RefineAuditionHandle) {
    this.audio.preload = "auto";
    this.audio.crossOrigin = "anonymous";
    this.audio.preservesPitch = true;
  }

  async loadPackAudio(containerPath: string, role: string | null): Promise<void> {
    const generation = ++this.generation;
    this.pause();
    const prepared = await invoke<{ path: string; roles: string[] }>("prepare_refine_audio", { containerPath, role });
    if (generation !== this.generation) throw new Error("Audio load superseded");
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timeout);
        this.audio.removeEventListener("loadedmetadata", ready);
        this.audio.removeEventListener("error", failed);
      };
      const ready = () => { cleanup(); resolve(); };
      const failed = () => { cleanup(); reject(new Error("Could not decode source audio")); };
      const timeout = window.setTimeout(failed, 20_000);
      this.audio.addEventListener("loadedmetadata", ready);
      this.audio.addEventListener("error", failed);
      this.audio.src = convertFileSrc(prepared.path);
      this.audio.load();
    });
    if (generation !== this.generation) throw new Error("Audio load superseded");
  }

  async play(): Promise<void> {
    const generation = ++this.playGeneration;
    this.audition.connectSource(this.audio);
    await this.audition.resume();
    if (generation !== this.playGeneration) return;
    await this.audio.play();
  }

  pause(): void { this.playGeneration += 1; this.audio.pause(); }
  seek(t: number): void { this.audio.currentTime = Math.max(0, t); }
  setPlaybackRate(rate: number): void {
    this.audio.preservesPitch = true;
    this.audio.playbackRate = Math.max(0.25, Math.min(2, rate));
  }
  getCurrentTimeSec(): number { return this.audio.currentTime; }
  getIsPlaying(): boolean { return !this.audio.paused && !this.audio.ended; }
  hasEnded(): boolean { return this.audio.ended; }
  getOutputLatencySec(): number { return this.audition.getOutputLatencySec(); }
}
