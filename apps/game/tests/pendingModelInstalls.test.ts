import { describe, it, expect, vi } from "vitest";
import {
  computeMissingRequirements,
  initPendingModelInstallBanner,
  type RuntimeCheckPayload,
} from "../src/pendingModelInstalls";

describe("computeMissingRequirements", () => {
  it("reports a required asset that is not ok", () => {
    const payload: RuntimeCheckPayload = {
      assets: { basic_pitch_model: { ok: false, required: true, error: "not found" } },
    };
    expect(computeMissingRequirements(payload)).toEqual([
      { id: "basic_pitch_model", label: "Basic pitch model", detail: "not found" },
    ]);
  });

  it("IGNORES optional assets/dependencies that are not ok", () => {
    // The bug this whole design exists to avoid: nagging about things that are
    // deliberately not installed (an optional backend) or resolved elsewhere.
    const payload: RuntimeCheckPayload = {
      assets: { demucs_modelpack: { ok: false, required: false } },
      dependencies: {
        tensorflow: { ok: false, required: false, missing_behavior: "ONNX remains usable" },
      },
    };
    expect(computeMissingRequirements(payload)).toEqual([]);
  });

  it("walks nested asset groups like mt3_checkpoints", () => {
    const payload: RuntimeCheckPayload = {
      assets: {
        mt3_checkpoints: {
          mr_mt3_drums: { ok: true, required: true },
          yourmt3_drums: { ok: false, required: true, error: "checkpoint missing" },
        },
      },
    };
    const missing = computeMissingRequirements(payload);
    expect(missing).toHaveLength(1);
    expect(missing[0].id).toBe("mt3_checkpoints.yourmt3_drums");
    expect(missing[0].detail).toBe("checkpoint missing");
  });

  it("reports a required dependency with its missing_behavior as detail", () => {
    const payload: RuntimeCheckPayload = {
      dependencies: {
        basic_pitch: { ok: false, required: true, missing_behavior: "auto profiles fall back" },
      },
    };
    expect(computeMissingRequirements(payload)[0]).toMatchObject({
      id: "basic_pitch",
      detail: "auto profiles fall back",
    });
  });

  it("returns nothing for a healthy real-world payload", () => {
    // Trimmed from an actual `runtime-check` on a working install: every
    // required item ok, TensorFlow absent but optional. Must be silent.
    const payload: RuntimeCheckPayload = {
      ok: true,
      assets: {
        basic_pitch_model: { ok: true, required: true },
        ffmpeg: { ok: true, required: false },
        demucs_modelpack: { ok: true, required: false },
        mt3_checkpoints: {
          mr_mt3_drums: { ok: true, required: true },
          yourmt3_drums: { ok: true, required: true },
        },
      },
      dependencies: {
        basic_pitch: { ok: true, required: true },
        onnxruntime: { ok: true, required: false },
        tensorflow: { ok: false, required: false, missing_behavior: "ONNX remains usable" },
        torch: { ok: true, required: false },
      },
    };
    expect(computeMissingRequirements(payload)).toEqual([]);
  });

  it("tolerates missing/empty payloads", () => {
    expect(computeMissingRequirements(undefined)).toEqual([]);
    expect(computeMissingRequirements(null)).toEqual([]);
    expect(computeMissingRequirements({})).toEqual([]);
  });
});

function fakeContainer() {
  const handlers: Record<string, () => void> = {};
  const container = {
    hidden: false,
    innerHTML: "",
    querySelector(sel: string) {
      const cls = sel.replace(".", "");
      return { addEventListener: (_e: string, h: () => void) => (handlers[cls] = h) };
    },
  };
  return { container: container as unknown as HTMLElement, handlers };
}

function memStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  };
}

const missingPayload: RuntimeCheckPayload = {
  assets: { basic_pitch_model: { ok: false, required: true, error: "not found" } },
};

describe("initPendingModelInstallBanner", () => {
  it("renders an attention banner naming the missing requirement", async () => {
    const { container } = fakeContainer();
    const missing = await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => ({ ok: true, payload: missingPayload }),
      storage: memStorage(),
    });
    expect(missing).toHaveLength(1);
    expect(container.hidden).toBe(false);
    expect(container.innerHTML).toContain("1 required model missing");
    expect(container.innerHTML).toContain("Basic pitch model");
    expect(container.innerHTML).toContain("Set up models");
  });

  it("stays hidden when everything required is present", async () => {
    const { container } = fakeContainer();
    await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => ({ ok: true, payload: { assets: {}, dependencies: {} } }),
      storage: memStorage(),
    });
    expect(container.hidden).toBe(true);
    expect(container.innerHTML).toBe("");
  });

  it("opens the Models UI when 'Set up models' is clicked", async () => {
    const { container, handlers } = fakeContainer();
    const onOpenModels = vi.fn();
    await initPendingModelInstallBanner({
      container,
      onOpenModels,
      fetchRuntimeCheck: async () => ({ ok: true, payload: missingPayload }),
      storage: memStorage(),
    });
    handlers["modelInstallSetup"]?.();
    expect(onOpenModels).toHaveBeenCalledTimes(1);
  });

  it("stays dismissed for the same set, returns when the set changes", async () => {
    const storage = memStorage();
    const first = fakeContainer();
    await initPendingModelInstallBanner({
      container: first.container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => ({ ok: true, payload: missingPayload }),
      storage,
    });
    first.handlers["modelInstallDismiss"]?.();

    const second = fakeContainer();
    await initPendingModelInstallBanner({
      container: second.container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => ({ ok: true, payload: missingPayload }),
      storage,
    });
    expect(second.container.hidden).toBe(true);

    const third = fakeContainer();
    await initPendingModelInstallBanner({
      container: third.container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => ({
        ok: true,
        payload: {
          assets: {
            basic_pitch_model: { ok: false, required: true },
            mt3_checkpoints: { mr_mt3_drums: { ok: false, required: true } },
          },
        },
      }),
      storage,
    });
    expect(third.container.hidden).toBe(false);
  });

  it("stays silent when the runtime check can't be run", async () => {
    const { container } = fakeContainer();
    await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      fetchRuntimeCheck: async () => {
        throw new Error("no tauri");
      },
      storage: memStorage(),
    });
    expect(container.hidden).toBe(true);
  });
});
