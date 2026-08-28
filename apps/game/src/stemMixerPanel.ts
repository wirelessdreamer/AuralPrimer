/**
 * Per-stem mixer for the Band Setup rail. Renders one row per loaded stem
 * (label + volume fader + Mute + Solo) and folds mute/solo + fader into a
 * single linear gain per track, pushed to the native engine via
 * `native_audio_set_track_gain`. Session-only — no persistence, and the engine
 * resets every track to unity on each song load, so the panel starts flat.
 */
import { invoke } from "@tauri-apps/api/core";

const ROLE_LABELS: Record<string, string> = {
  bass: "Bass",
  drums: "Drums",
  vocals: "Vocals",
  guitar: "Guitar",
  keys: "Keys",
  other: "Other",
};

export type StemMixerHandle = {
  /** Rebuild for a new song's stem roles (empty array hides the panel). */
  setRoles: (roles: string[]) => void;
  /**
   * Scale every track at once, 0..1.5. The engine has no master gain, so the
   * song fader folds in here alongside mute, solo and the per-stem faders and
   * is pushed as one number per track -- which also keeps the per-stem balance
   * intact while the whole song gets quieter.
   */
  setMaster: (gain: number) => void;
};

export function initStemMixerPanel(container: HTMLElement): StemMixerHandle {
  let roles: string[] = [];
  let gains: number[] = [];
  let muted: boolean[] = [];
  let soloed: boolean[] = [];
  let master = 1;

  // Solo overrides: if any track is soloed, only soloed tracks are audible (at
  // their fader gain); otherwise a track is audible unless muted.
  function effectiveGain(i: number): number {
    const own = soloed.some((s) => s)
      ? (soloed[i] ? (gains[i] ?? 1) : 0)
      : (muted[i] ? 0 : (gains[i] ?? 1));
    return own * master;
  }

  function pushAll(): void {
    for (let i = 0; i < roles.length; i++) {
      void invoke("native_audio_set_track_gain", { index: i, gain: effectiveGain(i) }).catch(() => {});
    }
  }

  function render(): void {
    if (roles.length === 0) {
      container.innerHTML = "";
      container.style.display = "none";
      return;
    }
    container.style.display = "";
    const soloActive = soloed.some((s) => s);
    container.innerHTML =
      `<div class="mixerTitle">Mixer</div>` +
      roles
        .map((r, i) => {
          const label = ROLE_LABELS[r] ?? r;
          const dim = soloActive && !soloed[i] ? " isDimmed" : "";
          return `<div class="mixerRow${dim}">
            <span class="mixerLabel">${label}</span>
            <input class="mixerFader" type="range" min="0" max="150" step="1" value="${Math.round((gains[i] ?? 1) * 100)}" data-i="${i}" />
            <button class="mixerBtn mixerMute${muted[i] ? " isOn" : ""}" data-i="${i}" data-act="mute" type="button" title="Mute">M</button>
            <button class="mixerBtn mixerSolo${soloed[i] ? " isOn" : ""}" data-i="${i}" data-act="solo" type="button" title="Solo">S</button>
          </div>`;
        })
        .join("");

    for (const fader of Array.from(container.querySelectorAll<HTMLInputElement>(".mixerFader"))) {
      fader.addEventListener("input", () => {
        const i = Number(fader.dataset.i);
        gains[i] = Math.max(0, Math.min(1.5, Number(fader.value) / 100));
        // A fader move only changes this track unless solo is gating it.
        if (soloed.some((s) => s)) pushAll();
        else void invoke("native_audio_set_track_gain", { index: i, gain: effectiveGain(i) }).catch(() => {});
      });
    }
    for (const btn of Array.from(container.querySelectorAll<HTMLButtonElement>(".mixerBtn"))) {
      btn.addEventListener("click", () => {
        const i = Number(btn.dataset.i);
        if (btn.dataset.act === "mute") muted[i] = !muted[i];
        else soloed[i] = !soloed[i];
        render(); // reflect button + dim states
        pushAll();
      });
    }
  }

  function setRoles(next: string[]): void {
    roles = Array.isArray(next) ? next.slice() : [];
    gains = roles.map(() => 1);
    muted = roles.map(() => false);
    soloed = roles.map(() => false);
    render();
    // Engine already resets to unity on load_stems, so no push needed here.
  }

  return {
    setRoles,
    setMaster(gain: number) {
      const next = Math.max(0, Math.min(1.5, gain));
      if (next === master) return;
      master = next;
      // Pushed even with no roles loaded: setRoles pushes again on the next
      // song, so a fader moved before a song is chosen still applies to it.
      pushAll();
    },
  };
}
