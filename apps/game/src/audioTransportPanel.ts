/**
 * Audio transport panel — owns the row of playback control buttons
 * (Load / Play / Pause / Stop / Seek) and the loop controls
 * (Set / Clear + the two t0/t1 inputs). Also owns the two status preformat
 * elements (#audioStatus + #vizStatus) and exposes setAudioStatus /
 * setVizStatus on the handle so host code can continue to update them
 * via thin wrappers.
 *
 * Extracted from main.ts as Phase 2.Q.
 */

import type { TransportController } from "./transportController";
import type { MidiPanelHandle } from "./midiPanel";
import type { PauseMenuHandle } from "./pauseMenu";
import type { ConsoleBridge } from "./consoleBridge";

export type AudioTransportPanelDeps = {
  transportController: TransportController;
  midiPanel: MidiPanelHandle;
  pauseMenu: PauseMenuHandle;
  consoleBridge: ConsoleBridge;
  /** Called when the user clicks Load. Host's loadAudioFromSelectedSongPack. */
  onLoadAudio: () => Promise<void>;
  /** Called when the user clicks Stop. Host's stopAudio. */
  onStopAudio: () => void;
  /** Called after every transportController mutation so the host cache stays fresh. */
  refreshCachedTransport: () => void;
};

export type AudioTransportPanelHandle = {
  setAudioStatus: (msg: string) => void;
  setVizStatus: (msg: string) => void;
  /** Current viz status text. The pause menu uses it for "(not running)" check. */
  getVizStatus: () => string;
  /** Load button element — exposed because host code gates its disabled state. */
  loadBtn: HTMLButtonElement;
  /** Play/pause/stop/seek button elements — exposed for host gating. */
  playBtn: HTMLButtonElement;
  pauseBtn: HTMLButtonElement;
  stopBtn: HTMLButtonElement;
  seekGoBtn: HTMLButtonElement;
  loopSetBtn: HTMLButtonElement;
  loopClearBtn: HTMLButtonElement;
};

export function initAudioTransportPanel(deps: AudioTransportPanelDeps): AudioTransportPanelHandle {
  const loadBtn = document.getElementById("audioLoad") as HTMLButtonElement | null;
  const playBtn = document.getElementById("audioPlay") as HTMLButtonElement | null;
  const pauseBtn = document.getElementById("audioPause") as HTMLButtonElement | null;
  const stopBtn = document.getElementById("audioStop") as HTMLButtonElement | null;
  const seekInput = document.getElementById("audioSeek") as HTMLInputElement | null;
  const seekGoBtn = document.getElementById("audioSeekGo") as HTMLButtonElement | null;
  const statusEl = document.getElementById("audioStatus") as HTMLPreElement | null;
  const vizStatusEl = document.getElementById("vizStatus") as HTMLPreElement | null;
  const loopT0Input = document.getElementById("loopT0") as HTMLInputElement | null;
  const loopT1Input = document.getElementById("loopT1") as HTMLInputElement | null;
  const loopSetBtn = document.getElementById("loopSet") as HTMLButtonElement | null;
  const loopClearBtn = document.getElementById("loopClear") as HTMLButtonElement | null;
  if (
    !loadBtn || !playBtn || !pauseBtn || !stopBtn ||
    !seekInput || !seekGoBtn || !statusEl || !vizStatusEl ||
    !loopT0Input || !loopT1Input || !loopSetBtn || !loopClearBtn
  ) {
    throw new Error("initAudioTransportPanel: required DOM (#audio* / #loop* / #vizStatus) missing");
  }

  function setAudioStatus(msg: string): void {
    statusEl!.textContent = msg;
    deps.consoleBridge.log("play", msg);
  }

  function setVizStatus(msg: string): void {
    vizStatusEl!.textContent = msg;
    deps.consoleBridge.log("debugging", `viz status -> ${msg}`);
  }

  loadBtn.addEventListener("click", () => {
    void deps.onLoadAudio().catch((e) => setAudioStatus(String(e)));
  });

  playBtn.addEventListener("click", () => {
    deps.consoleBridge.log("play", "play requested");
    void deps.transportController.play()
      .then(() => {
        deps.consoleBridge.log("play", "play started");
        return deps.midiPanel.outStartOrContinue();
      })
      .catch((e) => setAudioStatus(String(e)));
  });

  pauseBtn.addEventListener("click", () => {
    deps.transportController.pause();
    deps.refreshCachedTransport();
    void deps.midiPanel.outStop();
    setAudioStatus("paused");
  });

  stopBtn.addEventListener("click", () => {
    deps.pauseMenu.close({ restoreFocus: false });
    deps.onStopAudio();
    void deps.midiPanel.outStop();
    void deps.midiPanel.outSeek(0);
    setAudioStatus("stopped");
  });

  seekGoBtn.addEventListener("click", () => {
    const t = Number(seekInput!.value);
    if (!Number.isFinite(t)) {
      deps.consoleBridge.warn("play", "seek ignored: invalid value", { value: seekInput!.value });
      return;
    }
    deps.transportController.seek(t);
    void deps.midiPanel.outSeek(t);
    setAudioStatus(`seek: ${t.toFixed(2)}s`);
  });

  loopSetBtn.addEventListener("click", () => {
    const t0 = Number(loopT0Input!.value);
    const t1 = Number(loopT1Input!.value);
    if (!Number.isFinite(t0) || !Number.isFinite(t1)) return;

    deps.transportController.setLoop({ t0, t1 });
    deps.refreshCachedTransport();
    setAudioStatus(`loop set: ${t0}..${t1}`);
  });

  loopClearBtn.addEventListener("click", () => {
    deps.transportController.setLoop(undefined);
    deps.refreshCachedTransport();
    setAudioStatus("loop cleared");
  });

  return {
    setAudioStatus,
    setVizStatus,
    getVizStatus: () => vizStatusEl!.textContent ?? "",
    loadBtn,
    playBtn,
    pauseBtn,
    stopBtn,
    seekGoBtn,
    loopSetBtn,
    loopClearBtn,
  };
}
