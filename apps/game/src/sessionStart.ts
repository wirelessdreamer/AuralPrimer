export type StartSessionContext = {
  selectedSongPackPath: string | null;
  lastLoadedSongPackPath: string | null;
  hasVisualizer: boolean;
};

export type StartSessionDeps = {
  setPlayStartDisabled(disabled: boolean): void;
  setAudioStatus(msg: string): void;
  setVizStatus(msg: string): void;
  showSongLibraryStep(): void;
  loadAudioFromSelectedSongPack(): Promise<void>;
  startVisualizer(): Promise<void>;
  playTransport(): Promise<void>;
  startMidiOut(): Promise<void>;
  isNativePlaybackInactiveError(err: string): boolean;
  tryFallbackToHtmlPlayback(songpackPath: string): Promise<boolean>;
  onPrimaryStartError(err: unknown): void;
  onFallbackStartError(err: unknown): void;
};

export type StartSessionResult =
  | { kind: "no_song" }
  | { kind: "started" }
  | { kind: "fallback_started" }
  | { kind: "failed"; error: string };

export async function startSelectedSongSessionFlow(
  ctx: StartSessionContext,
  deps: StartSessionDeps
): Promise<StartSessionResult> {
  if (!ctx.selectedSongPackPath) {
    deps.setAudioStatus("Select a song first from the library");
    deps.showSongLibraryStep();
    return { kind: "no_song" };
  }

  deps.setPlayStartDisabled(true);
  try {
    deps.setAudioStatus("starting...");
    if (ctx.lastLoadedSongPackPath !== ctx.selectedSongPackPath) {
      await deps.loadAudioFromSelectedSongPack();
    } else {
      deps.setAudioStatus(`audio already loaded: ${ctx.selectedSongPackPath}`);
    }

    if (!ctx.hasVisualizer) {
      await deps.startVisualizer();
    }

    await deps.playTransport();
    await deps.startMidiOut();
    deps.setAudioStatus(`playing: ${ctx.selectedSongPackPath}`);
    return { kind: "started" };
  } catch (e) {
    const err = String(e);
    deps.onPrimaryStartError(e);
    if (ctx.selectedSongPackPath && deps.isNativePlaybackInactiveError(err)) {
      try {
        if (await deps.tryFallbackToHtmlPlayback(ctx.selectedSongPackPath)) {
          return { kind: "fallback_started" };
        }
      } catch (fallbackErr) {
        deps.onFallbackStartError(fallbackErr);
      }
    }
    deps.setAudioStatus(err);
    deps.setVizStatus(`playback start failed: ${err}`);
    return { kind: "failed", error: err };
  } finally {
    deps.setPlayStartDisabled(false);
  }
}

