// @vitest-environment jsdom
/**
 * Execution tests for the audio output panel (host + device picker for the
 * Rust native audio engine). Exercises initAudioOutputPanel(deps) wiring:
 * refresh/apply state machines for both hosts and devices, the no-Tauri /
 * no-timebase disabled paths, error branches, and the returned handle.
 *
 * The panel depends only on a `nativeTimebase` object (injected), so we feed
 * it a hand-rolled mock rather than the real Tauri invoke — deterministic,
 * no network, no real Tauri runtime.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { initAudioOutputPanel, type AudioOutputPanelDeps } from "../src/audioOutputPanel";
import type {
  NativeAudioDeviceInfo,
  NativeAudioHostInfo,
} from "../src/nativeAudioTimebase";

function stageDom(): void {
  document.body.innerHTML =
    '<select id="audioOutputHost"></select>' +
    '<button id="audioOutputHostRefresh"></button>' +
    '<button id="audioOutputHostApply"></button>' +
    '<select id="audioOutputDevice"></select>' +
    '<button id="audioOutputDeviceRefresh"></button>' +
    '<button id="audioOutputDeviceApply"></button>';
}

const flush = async () => {
  // Let any queued microtasks/promise chains settle. A macrotask turn drains
  // the deep await chains (apply -> setX -> refreshHosts -> refreshDevices).
  for (let i = 0; i < 4; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
};

const hostSelect = () => document.getElementById("audioOutputHost") as HTMLSelectElement;
const hostRefreshBtn = () =>
  document.getElementById("audioOutputHostRefresh") as HTMLButtonElement;
const hostApplyBtn = () =>
  document.getElementById("audioOutputHostApply") as HTMLButtonElement;
const deviceSelect = () =>
  document.getElementById("audioOutputDevice") as HTMLSelectElement;
const deviceRefreshBtn = () =>
  document.getElementById("audioOutputDeviceRefresh") as HTMLButtonElement;
const deviceApplyBtn = () =>
  document.getElementById("audioOutputDeviceApply") as HTMLButtonElement;

const HOSTS: NativeAudioHostInfo[] = [
  { id: "wasapi", name: "WASAPI", is_default: true },
  { id: "asio", name: "ASIO", is_default: false },
];

const DEVICES: NativeAudioDeviceInfo[] = [
  { name: "Speakers", channels: 2, sample_rate_hz: 48000, is_default: true },
  { name: "Headphones", channels: 2, sample_rate_hz: 44100, is_default: false },
];

type TimebaseMock = {
  listOutputHosts: ReturnType<typeof vi.fn>;
  getSelectedOutputHost: ReturnType<typeof vi.fn>;
  setOutputHost: ReturnType<typeof vi.fn>;
  listOutputDevices: ReturnType<typeof vi.fn>;
  getSelectedOutputDevice: ReturnType<typeof vi.fn>;
  setOutputDevice: ReturnType<typeof vi.fn>;
  getOutputLatencySec: ReturnType<typeof vi.fn>;
};

function makeTimebase(): TimebaseMock {
  return {
    listOutputHosts: vi.fn().mockResolvedValue(HOSTS),
    getSelectedOutputHost: vi.fn().mockResolvedValue({ id: "asio" }),
    setOutputHost: vi.fn().mockResolvedValue(undefined),
    listOutputDevices: vi.fn().mockResolvedValue(DEVICES),
    getSelectedOutputDevice: vi
      .fn()
      .mockResolvedValue({ name: "Headphones", channels: 2, sample_rate_hz: 44100 }),
    setOutputDevice: vi.fn().mockResolvedValue(undefined),
    getOutputLatencySec: vi.fn().mockReturnValue(0.0123),
  };
}

function makeDeps(over: Partial<AudioOutputPanelDeps> = {}): {
  deps: AudioOutputPanelDeps;
  tb: TimebaseMock;
  setAudioStatus: ReturnType<typeof vi.fn>;
} {
  const tb = makeTimebase();
  const setAudioStatus = vi.fn();
  const deps: AudioOutputPanelDeps = {
    nativeTimebase: tb as unknown as AudioOutputPanelDeps["nativeTimebase"],
    haveTauri: () => true,
    setAudioStatus,
    escapeHtml: (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    ...over,
  };
  return { deps, tb, setAudioStatus };
}

describe("audioOutputPanel", () => {
  beforeEach(() => {
    stageDom();
  });

  it("throws when required DOM controls are missing", () => {
    document.body.innerHTML = "";
    const { deps } = makeDeps();
    expect(() => initAudioOutputPanel(deps)).toThrow(/required DOM controls missing/);
  });

  it("refreshAll populates both selects and selects the current host/device", async () => {
    const { deps, tb } = makeDeps();
    const handle = initAudioOutputPanel(deps);
    await handle.refreshAll();
    await flush();

    // host: System default + 2 hosts; selected id "asio" -> index 1
    expect(tb.listOutputHosts).toHaveBeenCalled();
    expect(hostSelect().querySelectorAll("option")).toHaveLength(3);
    expect(hostSelect().value).toBe("1");
    expect(hostSelect().disabled).toBe(false);
    expect(hostApplyBtn().disabled).toBe(false);

    // device: System default + 2 devices; selected "Headphones" -> index 1
    expect(deviceSelect().querySelectorAll("option")).toHaveLength(3);
    expect(deviceSelect().value).toBe("1");
    // [default] tag + formatted label present
    expect(deviceSelect().innerHTML).toContain("Speakers (2ch, 48.0kHz) [default]");
    expect(deviceSelect().disabled).toBe(false);
    expect(deviceApplyBtn().disabled).toBe(false);
  });

  it("disables everything when Tauri is absent", async () => {
    const { deps } = makeDeps({ haveTauri: () => false });
    const handle = initAudioOutputPanel(deps);
    await handle.refreshAll();
    await flush();

    expect(hostSelect().disabled).toBe(true);
    expect(hostRefreshBtn().disabled).toBe(true);
    expect(hostApplyBtn().disabled).toBe(true);
    expect(deviceSelect().disabled).toBe(true);
    expect(deviceRefreshBtn().disabled).toBe(true);
    expect(deviceApplyBtn().disabled).toBe(true);
    expect(hostSelect().innerHTML).toContain("System default");
  });

  it("disables everything when nativeTimebase is null", async () => {
    const { deps } = makeDeps({ nativeTimebase: null });
    const handle = initAudioOutputPanel(deps);
    await handle.refreshAll();
    await flush();
    expect(hostSelect().disabled).toBe(true);
    expect(deviceSelect().disabled).toBe(true);
  });

  it("host refresh button re-fetches hosts", async () => {
    const { deps, tb } = makeDeps();
    initAudioOutputPanel(deps);
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(tb.listOutputHosts).toHaveBeenCalledTimes(1);
    expect(hostRefreshBtn().disabled).toBe(false); // re-enabled in finally
  });

  it("device refresh button re-fetches devices", async () => {
    const { deps, tb } = makeDeps();
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(tb.listOutputDevices).toHaveBeenCalledTimes(1);
    expect(deviceRefreshBtn().disabled).toBe(false);
  });

  it("host refresh error path resets to System default and reports status", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    tb.listOutputHosts.mockRejectedValueOnce(new Error("boom"));
    initAudioOutputPanel(deps);
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output host refresh failed"),
    );
    expect(hostSelect().disabled).toBe(true);
    expect(hostSelect().value).toBe("");
  });

  it("device refresh error path resets to System default and reports status", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    tb.listOutputDevices.mockRejectedValueOnce(new Error("nope"));
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output device refresh failed"),
    );
    expect(deviceSelect().disabled).toBe(true);
    expect(deviceSelect().value).toBe("");
  });

  it("applying a selected host calls setOutputHost and reports latency", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    initAudioOutputPanel(deps);
    await deps.nativeTimebase ? null : null;
    // Populate hosts first so the index resolves to a real host.
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    hostSelect().value = "0"; // WASAPI
    hostApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(tb.setOutputHost).toHaveBeenCalledWith(HOSTS[0]);
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("switching output host to WASAPI"),
    );
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output host set: WASAPI (est latency 12.3ms)"),
    );
  });

  it("applying the empty host selection means System default (no latency when not finite)", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    tb.getOutputLatencySec.mockReturnValue(Number.NaN);
    initAudioOutputPanel(deps);
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    hostSelect().value = ""; // System default
    hostApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(tb.setOutputHost).toHaveBeenCalledWith(null);
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output host set: System default"),
    );
    // No "(est latency" suffix when latency is NaN.
    const calls = setAudioStatus.mock.calls.map((c) => String(c[0]));
    expect(calls.some((m) => m.includes("output host set: System default") && !m.includes("est latency"))).toBe(true);
  });

  it("host apply error path reports failure and refreshes", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    tb.setOutputHost.mockRejectedValueOnce(new Error("switch-fail"));
    initAudioOutputPanel(deps);
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    hostSelect().value = "0";
    hostApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output host switch failed"),
    );
  });

  it("host apply is a no-op when nativeTimebase is null", async () => {
    const { deps, tb } = makeDeps();
    const handle = initAudioOutputPanel(deps);
    // Mutate the dep to null after init to hit the early-return guard.
    deps.nativeTimebase = null;
    hostApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(tb.setOutputHost).not.toHaveBeenCalled();
    void handle;
  });

  it("applying a selected device calls setOutputDevice and reports latency + saved", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    deviceSelect().value = "1"; // Headphones
    deviceApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(tb.setOutputDevice).toHaveBeenCalledWith(DEVICES[1]);
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("switching output device to Headphones"),
    );
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("(saved preference)"),
    );
  });

  it("applying empty device selection means System default", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    deviceSelect().value = "";
    deviceApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(tb.setOutputDevice).toHaveBeenCalledWith(null);
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output device set: System default"),
    );
  });

  it("device apply error path reports failure and still refreshes", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    tb.setOutputDevice.mockRejectedValueOnce(new Error("dev-fail"));
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    deviceSelect().value = "0";
    deviceApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();

    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output device switch failed"),
    );
    // finally -> refreshDevices re-fetched
    expect(tb.listOutputDevices).toHaveBeenCalledTimes(2);
  });

  it("device apply is a no-op when nativeTimebase is null", async () => {
    const { deps, tb } = makeDeps();
    initAudioOutputPanel(deps);
    deps.nativeTimebase = null;
    deviceApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(tb.setOutputDevice).not.toHaveBeenCalled();
  });

  it("handles getOutputLatencySec being absent (optional chaining)", async () => {
    const { deps, tb, setAudioStatus } = makeDeps();
    // Remove the optional method entirely.
    delete (tb as Partial<TimebaseMock>).getOutputLatencySec;
    initAudioOutputPanel(deps);
    deviceRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    deviceSelect().value = "0";
    deviceApplyBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(setAudioStatus).toHaveBeenCalledWith(
      expect.stringContaining("output device set: Speakers"),
    );
  });

  it("selectedIdx falls back to empty when no host matches", async () => {
    const { deps, tb } = makeDeps();
    tb.getSelectedOutputHost.mockResolvedValue({ id: "nonexistent" });
    initAudioOutputPanel(deps);
    hostRefreshBtn().dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(hostSelect().value).toBe("");
  });

  it("disableAll() disables every control", () => {
    const { deps } = makeDeps();
    const handle = initAudioOutputPanel(deps);
    handle.disableAll();
    expect(hostSelect().disabled).toBe(true);
    expect(hostRefreshBtn().disabled).toBe(true);
    expect(hostApplyBtn().disabled).toBe(true);
    expect(deviceSelect().disabled).toBe(true);
    expect(deviceRefreshBtn().disabled).toBe(true);
    expect(deviceApplyBtn().disabled).toBe(true);
  });
});
