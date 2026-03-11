// @vitest-environment jsdom

describe("NativeAudioTimebase", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
    vi.doUnmock("@tauri-apps/api/core");
  });

  it("loadFromSongPack applies playback rate + loop and exposes duration", async () => {
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      if (cmd === "native_audio_load_songpack_audio") {
        expect(payload?.containerPath).toBe("C:/songs/demo.songpack");
        return { mime: "audio/ogg", duration_sec: 42.5 };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    tb.setPlaybackRate(0.8);
    tb.setLoop({ t0: 2, t1: 6 });

    const out = await tb.loadFromSongPack("C:/songs/demo.songpack");
    expect(out).toEqual({ mime: "audio/ogg", durationSec: 42.5 });
    expect(tb.getDurationSec()).toBeCloseTo(42.5, 6);

    expect(invoke).toHaveBeenCalledWith("native_audio_set_playback_rate", { rate: 0.8 });
    expect(invoke).toHaveBeenCalledWith("native_audio_set_loop", { t0: 2, t1: 6 });
  });

  it("setOutputDevice reloads audio and restores playhead/play state", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/mpeg", duration_sec: 10 };
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: true,
          t_sec: 7.25,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 256,
          callback_count: 100,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    tb.setLoop({ t0: 1, t1: 4 });
    tb.setPlaybackRate(0.9);
    await tb.loadFromSongPack("C:/songs/x.songpack");

    calls.length = 0;
    await tb.setOutputDevice({ name: "USB DAC", channels: 2, sample_rate_hz: 48_000 });

    expect(calls.map((c) => c.cmd)).toEqual([
      "native_audio_get_state",
      "native_audio_set_output_device_and_persist",
      "native_audio_load_songpack_audio",
      "native_audio_set_playback_rate",
      "native_audio_set_loop",
      "native_audio_seek",
      "native_audio_play"
    ]);

    expect(calls[1]?.payload).toEqual({
      outputDevice: { name: "USB DAC", channels: 2, sample_rate_hz: 48_000 }
    });
    expect(calls[5]?.payload).toEqual({ tSec: 7.25 });
  });

  it("setOutputHost reinitializes and restores transport", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/ogg", duration_sec: 12 };
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 1.5,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 256,
          callback_count: 1,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/z.songpack");

    calls.length = 0;
    await tb.setOutputHost({ id: "asio" });
    expect(calls.map((c) => c.cmd)).toEqual([
      "native_audio_get_state",
      "native_audio_set_output_host_and_persist",
      "native_audio_load_songpack_audio",
      "native_audio_set_playback_rate",
      "native_audio_set_loop",
      "native_audio_seek"
    ]);
    expect(calls[1]?.payload).toEqual({ outputHost: { id: "asio" } });
  });

  it("polls native state and derives output latency", async () => {
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: true,
          t_sec: 3.5,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: 50,
          callback_overrun_count: 1
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();

    expect(tb.getCurrentTimeSec()).toBe(0);
    await Promise.resolve();

    expect(tb.getIsPlaying()).toBe(true);
    expect(tb.getCurrentTimeSec()).toBeCloseTo(3.5, 6);
    expect(tb.getOutputLatencySec()).toBeCloseTo(240 / 48_000, 6);
  });

  it("play waits for callback activity before reporting playing", async () => {
    let statePolls = 0;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: statePolls >= 2,
          t_sec: statePolls >= 2 ? 0.02 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: statePolls >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.play();

    expect(tb.getIsPlaying()).toBe(true);
    expect(statePolls).toBeGreaterThanOrEqual(2);
  });

  it("play extends startup wait for very large output buffers", async () => {
    vi.useFakeTimers();
    let statePolls = 0;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: statePolls >= 130,
          t_sec: statePolls >= 130 ? 0.02 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          // ~4.8s buffer at 48kHz: forces adaptive wait beyond default 3s window.
          output_buffer_frames: 230_400,
          callback_count: statePolls,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(4_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
    expect(statePolls).toBeGreaterThanOrEqual(130);
  });

  it("play keeps base wait when output buffer metadata is invalid", async () => {
    vi.useFakeTimers();
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 0,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 1024,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play().catch((e) => e);
    await vi.advanceTimersByTimeAsync(4_000);
    const err = await playPromise;
    expect(String(err)).toContain("output callback inactive");
  });

  it("play keeps base wait when output buffer frame count is zero", async () => {
    vi.useFakeTimers();
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 0,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play().catch((e) => e);
    await vi.advanceTimersByTimeAsync(4_000);
    const err = await playPromise;
    expect(String(err)).toContain("output callback inactive");
  });

  it("play keeps base wait when computed buffer duration is non-finite", async () => {
    vi.useFakeTimers();
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: Number.MIN_VALUE,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: Number.MAX_VALUE,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play().catch((e) => e);
    await vi.advanceTimersByTimeAsync(4_000);
    const err = await playPromise;
    expect(String(err)).toContain("output callback inactive");
  });

  it("play waits longer on an active stream before forcing route recovery", async () => {
    vi.useFakeTimers();
    let statePolls = 0;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: statePolls >= 180,
          t_sec: statePolls >= 180 ? 0.05 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 0,
          callback_count: statePolls >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(6_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
    expect(statePolls).toBeGreaterThanOrEqual(180);
  });

  it("play keeps waiting when callbacks are already active but sparse", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    let statePolls = 0;
    const invoke = vi.fn(async (cmd: string) => {
      calls.push(cmd);
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        const started = statePolls >= 230;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 44_100,
          channels: 2,
          output_device: { name: "USB DAC", channels: 2, sample_rate_hz: 44_100 },
          is_playing: started,
          t_sec: started ? 0.12 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          // Sparse-callback scenario where audio is loaded but play-state telemetry lags.
          has_audio: true,
          output_buffer_frames: 0,
          callback_count: started ? 2 : 1,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
    expect(calls).not.toContain("native_audio_set_output_device");
    expect(statePolls).toBeGreaterThanOrEqual(230);
  });

  it("play skips extra sparse-stream wait when callback is active but audio is still unloaded", async () => {
    vi.useFakeTimers();
    let statePolls = 0;
    let firstRecoveryPollCount = -1;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_set_output_device") {
        if (firstRecoveryPollCount < 0) {
          firstRecoveryPollCount = statePolls;
        }
        return null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 44_100,
          channels: 2,
          output_device: { name: "OUT (2- DUO-CAPTURE EX)", channels: 2, sample_rate_hz: 44_100 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: false,
          output_buffer_frames: 0,
          callback_count: 1,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/duo-capture-stalled.songpack");

    const playPromise = tb.play().catch((e) => e);
    await vi.advanceTimersByTimeAsync(8_000);
    const err = await playPromise;

    // Recovery should begin immediately after base wait, not after extended sparse-callback delay.
    expect(firstRecoveryPollCount).toBeGreaterThan(0);
    expect(firstRecoveryPollCount).toBeLessThan(180);
    expect(String(err)).toContain("callbacks=1");
    expect(String(err)).toContain("has_audio=false");
    expect(String(err)).toContain("output_buffer_frames=0");
    expect(String(err)).toContain("sample_rate_hz=44100");
  });

  it("play falls through active-stream extra wait into recovery when playback still never starts", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    let statePolls = 0;
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push(cmd);
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_set_output_device") {
        return payload?.outputDevice ?? null;
      }
      if (cmd === "native_audio_get_state") {
        statePolls += 1;
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Broken DAC", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 0,
          callback_count: statePolls >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/extra-wait-fallthrough.songpack");

    const playPromise = tb.play().catch((e) => e);
    await vi.advanceTimersByTimeAsync(25_000);
    const err = await playPromise;
    expect(String(err)).toContain("output callback inactive");
    expect(calls).toContain("native_audio_set_output_device");
    expect(statePolls).toBeGreaterThan(120);
  });

  it("play throws when playback never reaches running state even with callbacks", async () => {
    vi.useFakeTimers();
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: 50,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();

    const playPromise = tb.play();
    const handled = playPromise.catch((e) => e);
    await vi.advanceTimersByTimeAsync(25_000);
    const err = await handled;
    expect(String(err)).toContain("output callback inactive");
    expect(tb.getIsPlaying()).toBe(false);
  });

  it("play recovery starts with system default when no preferred device is saved", async () => {
    vi.useFakeTimers();
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    let activeDevice = "Broken DAC";
    let playCount = 0;
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        playCount += 1;
        return null;
      }
      if (cmd === "native_audio_set_output_device") {
        const next = payload?.outputDevice as { name?: string } | null | undefined;
        activeDevice = next?.name ?? "USB DAC";
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: activeDevice, channels: 2, sample_rate_hz: 48_000 },
          is_playing: activeDevice === "USB DAC" && playCount >= 2,
          t_sec: activeDevice === "USB DAC" && playCount >= 2 ? 0.02 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: activeDevice === "USB DAC" && playCount >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/recover.songpack");

    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(playPromise).resolves.toBeUndefined();

    expect(tb.getIsPlaying()).toBe(true);
    expect(playCount).toBeGreaterThanOrEqual(2);
    expect(
      calls
        .filter((c) => c.cmd === "native_audio_set_output_device")
        .map((c) => (c.payload?.outputDevice as { name?: string } | null | undefined)?.name ?? "(default)")
    ).toEqual(["(default)"]);
    expect(calls.some((c) => c.cmd === "native_audio_list_output_devices")).toBe(false);
  });

  it("play recovery handles missing current output_device state", async () => {
    vi.useFakeTimers();
    let recovered = false;
    let playCount = 0;
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        playCount += 1;
        return null;
      }
      if (cmd === "native_audio_set_output_device") {
        recovered = payload?.outputDevice == null;
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: null,
          is_playing: recovered && playCount >= 2,
          t_sec: recovered && playCount >= 2 ? 0.02 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: recovered && playCount >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/recover-null-device.songpack");

    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
  });

  it("play recovery tries saved preferred device first, then falls back to system default", async () => {
    vi.useFakeTimers();
    let usingDefault = false;
    let playCount = 0;
    const setDeviceAttempts: string[] = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        playCount += 1;
        return null;
      }
      if (cmd === "native_audio_get_selected_output_device") {
        return { name: "Broken DAC", channels: 2, sample_rate_hz: 48_000 };
      }
      if (cmd === "native_audio_set_output_device") {
        const name = (payload?.outputDevice as { name?: string } | null | undefined)?.name ?? "(default)";
        setDeviceAttempts.push(name);
        if (name === "Broken DAC") {
          throw new Error("native audio startup failed: build_output_stream(f32): unsupported");
        }
        if (name === "(default)") {
          usingDefault = true;
        }
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: {
            name: usingDefault ? "System Default" : "Broken DAC",
            channels: 2,
            sample_rate_hz: 48_000
          },
          is_playing: usingDefault && playCount >= 2,
          t_sec: usingDefault && playCount >= 2 ? 0.03 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: usingDefault && playCount >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/recover-device-error.songpack");

    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
    expect(setDeviceAttempts).toEqual(["Broken DAC", "(default)"]);
  });

  it("play reports host/device after recovery attempts fail", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const invoke = vi.fn(async (cmd: string) => {
      calls.push(cmd);
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Broken DAC", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/recover-fail.songpack");

    const playPromise = tb.play();
    const handled = playPromise.catch((e) => e);
    await vi.advanceTimersByTimeAsync(12_000);
    const err = await handled;
    expect(String(err)).toContain("output callback inactive");
    expect(String(err)).toContain("host=wasapi");
    expect(String(err)).toContain("device=Broken DAC");
    expect(String(err)).toContain("callbacks=0");
    expect(String(err)).toContain("has_audio=true");
    expect(calls).toContain("native_audio_set_output_device");
  });

  it("play skips recovery device scan when recovery pre-state cannot be read", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const invoke = vi.fn(async (cmd: string) => {
      calls.push(cmd);
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_get_state") {
        throw new Error("state unavailable");
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/state-unavailable.songpack");

    const playPromise = tb.play();
    const handled = playPromise.catch((e) => e);
    await vi.advanceTimersByTimeAsync(4_000);
    const err = await handled;
    expect(String(err)).toContain("output callback inactive");
    expect(String(err)).toContain("host=unknown");
    expect(String(err)).toContain("device=unknown");
    expect(String(err)).toContain("callbacks=0");
    expect(String(err)).toContain("has_audio=false");
    expect(calls).not.toContain("native_audio_list_output_devices");
    expect(calls).not.toContain("native_audio_set_output_device");
  });

  it("play recovery falls back to system default when preferred-device lookup fails", async () => {
    vi.useFakeTimers();
    const setOutputDevicePayloads: Array<unknown> = [];
    let playCount = 0;
    let recovered = false;
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_play") {
        playCount += 1;
        return null;
      }
      if (cmd === "native_audio_get_selected_output_device") {
        throw new Error("settings unavailable");
      }
      if (cmd === "native_audio_set_output_device") {
        setOutputDevicePayloads.push(payload?.outputDevice ?? null);
        recovered = payload?.outputDevice == null;
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: {
            name: recovered ? "System Default" : "Broken DAC",
            channels: 2,
            sample_rate_hz: 48_000
          },
          is_playing: recovered && playCount >= 2,
          t_sec: recovered && playCount >= 2 ? 0.02 : 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: recovered && playCount >= 2 ? 1 : 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/preference-read-fail.songpack");

    const playPromise = tb.play();
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(playPromise).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
    expect(setOutputDevicePayloads).toEqual([null]);
  });

  it("play recovery is bounded to system default + restore when no preferred device exists", async () => {
    vi.useFakeTimers();
    const setOutputDevicePayloads: Array<unknown> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/wav", duration_sec: 30 };
      }
      if (cmd === "native_audio_set_output_device") {
        setOutputDevicePayloads.push(payload?.outputDevice ?? null);
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Broken DAC", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/retry-cap.songpack");

    const playPromise = tb.play();
    const handled = playPromise.catch((e) => e);
    await vi.advanceTimersByTimeAsync(12_000);
    const err = await handled;
    expect(String(err)).toContain("output callback inactive");
    expect(setOutputDevicePayloads).toHaveLength(2);
    expect(setOutputDevicePayloads[0]).toBeNull();
    expect((setOutputDevicePayloads[1] as { name?: string } | null | undefined)?.name).toBe("Broken DAC");
  });

  it("play succeeds when state reports playing even if callback_count is zero", async () => {
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: true,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 240,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await expect(tb.play()).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
  });

  it("play tolerates transient native state read failures before start", async () => {
    let polls = 0;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_play") {
        return null;
      }
      if (cmd === "native_audio_get_state") {
        polls += 1;
        if (polls === 1) {
          throw new Error("temporary invoke failure");
        }
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: true,
          t_sec: 0.01,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 128,
          callback_count: 1,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await expect(tb.play()).resolves.toBeUndefined();
    expect(tb.getIsPlaying()).toBe(true);
  });

  it("load from blob uses bytes path and sentinel duration fallback", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    tb.setPlaybackRate(1.25);
    tb.setLoop({ t0: 0.5, t1: 2.5 });

    const src = {
      blob: {
        arrayBuffer: async () => Uint8Array.from([1, 2, 3, 4]).buffer
      } as unknown as Blob,
      mime: "audio/wav"
    };
    await tb.load(src);

    expect(calls.map((c) => c.cmd)).toContain("native_audio_load_audio_bytes");
    expect(calls.some((c) => c.cmd === "native_audio_set_playback_rate" && c.payload?.rate === 1.25)).toBe(true);
    expect(calls.some((c) => c.cmd === "native_audio_set_loop" && c.payload?.t0 === 0.5 && c.payload?.t1 === 2.5)).toBe(
      true
    );
    expect(tb.getDurationSec()).toBe(24 * 60 * 60);
  });

  it("setOutputHost before first load only persists selection", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: false,
          output_buffer_frames: null,
          callback_count: 0,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.setOutputHost({ id: "asio" });

    expect(calls.map((c) => c.cmd)).toEqual(["native_audio_get_state", "native_audio_set_output_host_and_persist"]);
  });

  it("setOutputDevice tolerates state read failure with no cached audio to restore", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_get_state") {
        throw new Error("state unavailable");
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    // Force the rare branch where audio was marked initialized but no source is cached.
    (tb as unknown as { initialized: boolean; lastLoadedSongPackPath: string | null; lastLoadedAudio: unknown | null })
      .initialized = true;
    (tb as unknown as { lastLoadedSongPackPath: string | null }).lastLoadedSongPackPath = null;
    (tb as unknown as { lastLoadedAudio: unknown | null }).lastLoadedAudio = null;

    await tb.setOutputDevice({ name: "Built-in", channels: 2, sample_rate_hz: 48_000 });
    expect(calls.map((c) => c.cmd)).toEqual(["native_audio_get_state", "native_audio_set_output_device_and_persist"]);
  });

  it("setOutputHost after blob load reloads bytes and re-applies runtime settings", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 0,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 256,
          callback_count: 10,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    tb.setPlaybackRate(0.9);
    tb.setLoop({ t0: 1, t1: 4 });
    await tb.load({
      blob: {
        arrayBuffer: async () => Uint8Array.from([9, 8, 7]).buffer
      } as unknown as Blob,
      mime: "audio/wav"
    });

    calls.length = 0;
    await tb.setOutputHost({ id: "asio" });
    expect(calls.map((c) => c.cmd)).toEqual([
      "native_audio_get_state",
      "native_audio_set_output_host_and_persist",
      "native_audio_load_audio_bytes",
      "native_audio_set_playback_rate",
      "native_audio_set_loop"
    ]);
  });

  it("host/device listing + getters delegate and control methods update local state", async () => {
    const calls: Array<{ cmd: string; payload?: Record<string, unknown> }> = [];
    const invoke = vi.fn(async (cmd: string, payload?: Record<string, unknown>) => {
      calls.push({ cmd, payload });
      if (cmd === "native_audio_list_output_hosts") {
        return [{ id: "wasapi", name: "WASAPI", is_default: true }];
      }
      if (cmd === "native_audio_get_selected_output_host") {
        return { id: "wasapi" };
      }
      if (cmd === "native_audio_list_output_devices") {
        return [{ name: "Built-in", channels: 2, sample_rate_hz: 48_000, is_default: true }];
      }
      if (cmd === "native_audio_get_selected_output_device") {
        return { name: "Built-in", channels: 2, sample_rate_hz: 48_000 };
      }
      if (cmd === "native_audio_get_state") {
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: true,
          t_sec: 1.23,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: 480,
          callback_count: 12,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase({ sampleRateHz: 48_000 });

    expect(await tb.listOutputHosts()).toEqual([{ id: "wasapi", name: "WASAPI", is_default: true }]);
    expect(await tb.getSelectedOutputHost()).toEqual({ id: "wasapi" });
    expect(await tb.listOutputDevices()).toEqual([
      { name: "Built-in", channels: 2, sample_rate_hz: 48_000, is_default: true }
    ]);
    expect(await tb.getSelectedOutputDevice()).toEqual({ name: "Built-in", channels: 2, sample_rate_hz: 48_000 });

    tb.setPlaybackRate(Number.NaN);
    expect(tb.getPlaybackRate()).toBe(1);
    tb.setPlaybackRate(1.5);
    expect(tb.getPlaybackRate()).toBe(1.5);
    tb.setLoop({ t0: 3, t1: 1 });
    tb.setLoop(undefined);

    tb.seek(5.5);
    expect(tb.getCurrentTimeSec()).toBe(5.5);
    await Promise.resolve();
    expect(tb.getCurrentTimeSec()).toBeCloseTo(1.23, 6);
    await Promise.resolve();
    expect(tb.getIsPlaying()).toBe(true);
    expect(tb.getOutputLatencySec()).toBeCloseTo(480 / 48_000, 6);

    tb.pause();
    expect(tb.getIsPlaying()).toBe(false);
    tb.stop();
    expect(tb.getCurrentTimeSec()).toBe(0);
    tb.dispose();
    expect(calls.some((c) => c.cmd === "native_audio_pause")).toBe(true);
    expect(calls.filter((c) => c.cmd === "native_audio_stop").length).toBeGreaterThanOrEqual(2);
  });

  it("getCurrentTimeSec keeps last value when polling fails", async () => {
    let failPoll = false;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_get_state") {
        if (failPoll) throw new Error("poll failed");
        return {
          output_host: { id: "wasapi" },
          sample_rate_hz: 48_000,
          channels: 2,
          output_device: { name: "Built-in", channels: 2, sample_rate_hz: 48_000 },
          is_playing: false,
          t_sec: 2.5,
          playback_rate: 1,
          loop_t0_sec: null,
          loop_t1_sec: null,
          has_audio: true,
          output_buffer_frames: null,
          callback_count: 2,
          callback_overrun_count: 0
        };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();

    expect(tb.getCurrentTimeSec()).toBe(0);
    await Promise.resolve();
    expect(tb.getCurrentTimeSec()).toBeCloseTo(2.5, 6);

    failPoll = true;
    expect(tb.getCurrentTimeSec()).toBeCloseTo(2.5, 6);
    await Promise.resolve();
    expect(tb.getCurrentTimeSec()).toBeCloseTo(2.5, 6);
    expect(tb.getOutputLatencySec()).toBeUndefined();
  });

  it("loadFromSongPack falls back to sentinel when duration cache is cleared during apply", async () => {
    let tbRef: { loadedDurationSec: number | null } | null = null;
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/ogg", duration_sec: 11 };
      }
      if (cmd === "native_audio_set_playback_rate" && tbRef) {
        tbRef.loadedDurationSec = null;
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    tbRef = tb as unknown as { loadedDurationSec: number | null };

    const out = await tb.loadFromSongPack("C:/songs/fallback.songpack");
    expect(out).toEqual({ mime: "audio/ogg", durationSec: 24 * 60 * 60 });
  });

  it("loadFromSongPack uses sentinel when decoder reports invalid duration", async () => {
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/ogg", duration_sec: Number.NaN };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const out = await tb.loadFromSongPack("C:/songs/invalid-duration.songpack");
    expect(out).toEqual({ mime: "audio/ogg", durationSec: 24 * 60 * 60 });
  });

  it("loadFromSongPack uses sentinel when decoder omits duration field", async () => {
    const invoke = vi.fn(async (cmd: string) => {
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/ogg" };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    const out = await tb.loadFromSongPack("C:/songs/missing-duration.songpack");
    expect(out).toEqual({ mime: "audio/ogg", durationSec: 24 * 60 * 60 });
  });

  it("setOutputDevice reloads from songpack when state snapshot is unavailable", async () => {
    const calls: string[] = [];
    const invoke = vi.fn(async (cmd: string) => {
      calls.push(cmd);
      if (cmd === "native_audio_get_state") {
        throw new Error("state unavailable");
      }
      if (cmd === "native_audio_load_songpack_audio") {
        return { mime: "audio/ogg", duration_sec: 12 };
      }
      return null;
    });
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    await tb.loadFromSongPack("C:/songs/snapshot-missing.songpack");

    calls.length = 0;
    await tb.setOutputDevice({ name: "Built-in", channels: 2, sample_rate_hz: 48_000 });
    expect(calls).toEqual([
      "native_audio_get_state",
      "native_audio_set_output_device_and_persist",
      "native_audio_load_songpack_audio",
      "native_audio_set_playback_rate",
      "native_audio_set_loop"
    ]);
  });

  it("getOutputLatencySec uses configured sampleRate fallback when native state has not populated it yet", async () => {
    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase({ sampleRateHz: 44_100 });
    (tb as unknown as { _lastOutputBufferFrames: number | null })._lastOutputBufferFrames = 441;
    expect(tb.getOutputLatencySec()).toBeCloseTo(0.01, 6);
  });

  it("getOutputLatencySec returns undefined when sample rate is unavailable even with buffered frames", async () => {
    const { NativeAudioTimebase } = await import("../src/nativeAudioTimebase");
    const tb = new NativeAudioTimebase();
    (tb as unknown as { _lastOutputBufferFrames: number | null })._lastOutputBufferFrames = 256;
    expect(tb.getOutputLatencySec()).toBeUndefined();
  });
});
