/**
 * Audio output panel — host + device picker UI for the Rust native audio
 * engine.
 *
 * Owns the two `<select>`s, four buttons, list/refresh/apply state machine,
 * and the two cached arrays of host/device descriptors. Status messages
 * are forwarded to a caller-provided sink so this module has no
 * dependency on the rest of the audio status pipeline.
 *
 * Extracted from main.ts as part of Phase 2 decomposition.
 */

import {
  NativeAudioTimebase,
  type NativeAudioDeviceInfo,
  type NativeAudioDeviceSelection,
  type NativeAudioHostInfo,
  type NativeAudioHostSelection,
} from "./nativeAudioTimebase";

export type AudioOutputPanelDeps = {
  nativeTimebase: NativeAudioTimebase | null;
  /** Whether the Tauri runtime is present (no audio enumeration in plain browser). */
  haveTauri: () => boolean;
  /** Status sink — typically the host's setAudioStatus. */
  setAudioStatus: (msg: string) => void;
  /** Host-side HTML escape; injected to avoid duplicating the implementation. */
  escapeHtml: (s: string) => string;
};

export type AudioOutputPanelHandle = {
  /** Refresh both the host and device lists (re-fetches from the timebase). */
  refreshAll: () => Promise<void>;
  /** Disable every control (used when the audio backend is offline). */
  disableAll: () => void;
};

function sameOutputHostSelection(
  a: NativeAudioHostSelection | null | undefined,
  b: NativeAudioHostSelection | null | undefined,
): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.id === b.id;
}

function sameOutputDeviceSelection(
  a: NativeAudioDeviceSelection | null | undefined,
  b: NativeAudioDeviceSelection | null | undefined,
): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.name === b.name && a.channels === b.channels && a.sample_rate_hz === b.sample_rate_hz;
}

function formatOutputDeviceLabel(d: NativeAudioDeviceSelection): string {
  const srKhz = (d.sample_rate_hz / 1000).toFixed(1);
  return `${d.name} (${d.channels}ch, ${srKhz}kHz)`;
}

export function initAudioOutputPanel(deps: AudioOutputPanelDeps): AudioOutputPanelHandle {
  const hostSelect = document.getElementById("audioOutputHost") as HTMLSelectElement | null;
  const hostRefreshBtn = document.getElementById("audioOutputHostRefresh") as HTMLButtonElement | null;
  const hostApplyBtn = document.getElementById("audioOutputHostApply") as HTMLButtonElement | null;
  const deviceSelect = document.getElementById("audioOutputDevice") as HTMLSelectElement | null;
  const deviceRefreshBtn = document.getElementById("audioOutputDeviceRefresh") as HTMLButtonElement | null;
  const deviceApplyBtn = document.getElementById("audioOutputDeviceApply") as HTMLButtonElement | null;
  if (!hostSelect || !hostRefreshBtn || !hostApplyBtn || !deviceSelect || !deviceRefreshBtn || !deviceApplyBtn) {
    throw new Error("initAudioOutputPanel: required DOM controls missing");
  }

  let audioOutputHosts: NativeAudioHostInfo[] = [];
  let audioOutputDevices: NativeAudioDeviceInfo[] = [];

  async function refreshHosts(): Promise<void> {
    if (!deps.nativeTimebase || !deps.haveTauri()) {
      hostSelect!.innerHTML = `<option value="">System default</option>`;
      hostSelect!.disabled = true;
      hostRefreshBtn!.disabled = true;
      hostApplyBtn!.disabled = true;
      return;
    }

    hostRefreshBtn!.disabled = true;
    try {
      const [hosts, selected] = await Promise.all([
        deps.nativeTimebase.listOutputHosts(),
        deps.nativeTimebase.getSelectedOutputHost(),
      ]);
      audioOutputHosts = hosts;

      const options = [
        `<option value="">System default</option>`,
        ...audioOutputHosts.map((h, idx) => {
          const defaultTag = h.is_default ? " [default]" : "";
          return `<option value="${idx}">${deps.escapeHtml(h.name + defaultTag)}</option>`;
        }),
      ];
      hostSelect!.innerHTML = options.join("\n");

      const selectedIdx = audioOutputHosts.findIndex((h) => sameOutputHostSelection(h, selected));
      hostSelect!.value = selectedIdx >= 0 ? String(selectedIdx) : "";
      hostSelect!.disabled = false;
      hostApplyBtn!.disabled = false;
    } catch (e) {
      audioOutputHosts = [];
      hostSelect!.innerHTML = `<option value="">System default</option>`;
      hostSelect!.value = "";
      hostSelect!.disabled = true;
      hostApplyBtn!.disabled = true;
      deps.setAudioStatus(`output host refresh failed: ${String(e)}`);
    } finally {
      hostRefreshBtn!.disabled = false;
    }
  }

  async function applyHostSelection(): Promise<void> {
    if (!deps.nativeTimebase) return;
    const raw = hostSelect!.value.trim();
    const idx = raw === "" ? Number.NaN : Number(raw);
    const selected =
      Number.isFinite(idx) && idx >= 0 && idx < audioOutputHosts.length
        ? audioOutputHosts[idx]
        : null;
    const label = selected ? selected.name : "System default";

    hostApplyBtn!.disabled = true;
    hostRefreshBtn!.disabled = true;
    hostSelect!.disabled = true;
    deps.setAudioStatus(`switching output host to ${label}...`);

    try {
      await deps.nativeTimebase.setOutputHost(selected);
      await refreshHosts();
      await refreshDevices();
      const latencySec = deps.nativeTimebase.getOutputLatencySec?.();
      const latencyMsg =
        typeof latencySec === "number" && Number.isFinite(latencySec)
          ? ` (est latency ${(latencySec * 1000).toFixed(1)}ms)`
          : "";
      deps.setAudioStatus(`output host set: ${label}${latencyMsg}`);
    } catch (e) {
      deps.setAudioStatus(`output host switch failed: ${String(e)}`);
      await refreshHosts();
    }
  }

  async function refreshDevices(): Promise<void> {
    if (!deps.nativeTimebase || !deps.haveTauri()) {
      deviceSelect!.innerHTML = `<option value="">System default</option>`;
      deviceSelect!.disabled = true;
      deviceRefreshBtn!.disabled = true;
      deviceApplyBtn!.disabled = true;
      return;
    }

    deviceRefreshBtn!.disabled = true;
    try {
      const [devices, selected] = await Promise.all([
        deps.nativeTimebase.listOutputDevices(),
        deps.nativeTimebase.getSelectedOutputDevice(),
      ]);
      audioOutputDevices = devices;

      const options = [
        `<option value="">System default</option>`,
        ...audioOutputDevices.map((d, idx) => {
          const label = formatOutputDeviceLabel(d);
          const defaultTag = d.is_default ? " [default]" : "";
          return `<option value="${idx}">${deps.escapeHtml(label + defaultTag)}</option>`;
        }),
      ];
      deviceSelect!.innerHTML = options.join("\n");

      const selectedIdx = audioOutputDevices.findIndex((d) =>
        sameOutputDeviceSelection(d, selected),
      );
      deviceSelect!.value = selectedIdx >= 0 ? String(selectedIdx) : "";
      deviceSelect!.disabled = false;
      deviceApplyBtn!.disabled = false;
    } catch (e) {
      audioOutputDevices = [];
      deviceSelect!.innerHTML = `<option value="">System default</option>`;
      deviceSelect!.value = "";
      deviceSelect!.disabled = true;
      deviceApplyBtn!.disabled = true;
      deps.setAudioStatus(`output device refresh failed: ${String(e)}`);
    } finally {
      deviceRefreshBtn!.disabled = false;
    }
  }

  async function applyDeviceSelection(): Promise<void> {
    if (!deps.nativeTimebase) return;
    const raw = deviceSelect!.value.trim();
    const idx = raw === "" ? Number.NaN : Number(raw);
    const selected =
      Number.isFinite(idx) && idx >= 0 && idx < audioOutputDevices.length
        ? audioOutputDevices[idx]
        : null;
    const label = selected ? formatOutputDeviceLabel(selected) : "System default";

    deviceApplyBtn!.disabled = true;
    deviceRefreshBtn!.disabled = true;
    deviceSelect!.disabled = true;
    deps.setAudioStatus(`switching output device to ${label}...`);

    try {
      await deps.nativeTimebase.setOutputDevice(selected);
      const latencySec = deps.nativeTimebase.getOutputLatencySec?.();
      const latencyMsg =
        typeof latencySec === "number" && Number.isFinite(latencySec)
          ? ` (est latency ${(latencySec * 1000).toFixed(1)}ms)`
          : "";
      deps.setAudioStatus(`output device set: ${label}${latencyMsg} (saved preference)`);
    } catch (e) {
      deps.setAudioStatus(`output device switch failed: ${String(e)}`);
    } finally {
      await refreshDevices();
    }
  }

  function disableAll(): void {
    hostSelect!.disabled = true;
    hostRefreshBtn!.disabled = true;
    hostApplyBtn!.disabled = true;
    deviceSelect!.disabled = true;
    deviceRefreshBtn!.disabled = true;
    deviceApplyBtn!.disabled = true;
  }

  // Wire the click handlers
  hostRefreshBtn.addEventListener("click", () => {
    void refreshHosts();
  });
  hostApplyBtn.addEventListener("click", () => {
    void applyHostSelection();
  });
  deviceRefreshBtn.addEventListener("click", () => {
    void refreshDevices();
  });
  deviceApplyBtn.addEventListener("click", () => {
    void applyDeviceSelection();
  });

  return {
    refreshAll: async () => {
      await refreshHosts();
      await refreshDevices();
    },
    disableAll,
  };
}
