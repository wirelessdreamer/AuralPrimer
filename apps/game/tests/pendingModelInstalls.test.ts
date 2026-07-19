import { describe, it, expect, vi } from "vitest";
import {
  computeMissingModelPacks,
  initPendingModelInstallBanner,
} from "../src/pendingModelInstalls";

const preferred = [
  { id: "demucs_6", version: "0.0.0" },
  { id: "basic-transcription", version: "0.0.0" },
];

describe("computeMissingModelPacks", () => {
  it("returns preferred packs with no installed match", () => {
    const missing = computeMissingModelPacks(
      [{ id: "demucs_6", version: "0.0.0", root_dir: "", manifest_path: "", ok: true }],
      preferred,
    );
    expect(missing.map((p) => p.id)).toEqual(["basic-transcription"]);
  });

  it("returns empty when all preferred packs are installed", () => {
    const installed = preferred.map((p) => ({
      ...p,
      root_dir: "",
      manifest_path: "",
      ok: true,
    }));
    expect(computeMissingModelPacks(installed, preferred)).toEqual([]);
  });

  it("treats an installed-but-not-ok pack as missing", () => {
    const missing = computeMissingModelPacks(
      [{ id: "demucs_6", version: "0.0.0", root_dir: "", manifest_path: "", ok: false }],
      preferred,
    );
    expect(missing.map((p) => p.id)).toContain("demucs_6");
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

describe("initPendingModelInstallBanner", () => {
  it("renders an attention banner listing missing packs", async () => {
    const { container } = fakeContainer();
    const missing = await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      listInstalled: async () => [],
      preferred,
      storage: memStorage(),
    });
    expect(missing.map((p) => p.id)).toEqual(["demucs_6", "basic-transcription"]);
    expect(container.hidden).toBe(false);
    expect(container.innerHTML).toContain("2 model packs not installed");
    expect(container.innerHTML).toContain("Set up models");
  });

  it("stays hidden when nothing is missing", async () => {
    const { container } = fakeContainer();
    const installed = preferred.map((p) => ({ ...p, root_dir: "", manifest_path: "", ok: true }));
    await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      listInstalled: async () => installed,
      preferred,
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
      listInstalled: async () => [],
      preferred,
      storage: memStorage(),
    });
    handlers["modelInstallSetup"]?.();
    expect(onOpenModels).toHaveBeenCalledTimes(1);
  });

  it("stays dismissed for the same missing set, re-shows when the set changes", async () => {
    const storage = memStorage();
    // First launch: render + dismiss.
    const first = fakeContainer();
    await initPendingModelInstallBanner({
      container: first.container,
      onOpenModels: () => {},
      listInstalled: async () => [],
      preferred,
      storage,
    });
    first.handlers["modelInstallDismiss"]?.();

    // Second launch, same missing set -> stays hidden.
    const second = fakeContainer();
    await initPendingModelInstallBanner({
      container: second.container,
      onOpenModels: () => {},
      listInstalled: async () => [],
      preferred,
      storage,
    });
    expect(second.container.hidden).toBe(true);

    // Set changes (one now installed) -> banner returns.
    const third = fakeContainer();
    await initPendingModelInstallBanner({
      container: third.container,
      onOpenModels: () => {},
      listInstalled: async () => [
        { id: "demucs_6", version: "0.0.0", root_dir: "", manifest_path: "", ok: true },
      ],
      preferred,
      storage,
    });
    expect(third.container.hidden).toBe(false);
    expect(third.container.innerHTML).toContain("basic-transcription");
  });

  it("stays silent if the install status can't be read", async () => {
    const { container } = fakeContainer();
    await initPendingModelInstallBanner({
      container,
      onOpenModels: () => {},
      listInstalled: async () => {
        throw new Error("no tauri");
      },
      preferred,
      storage: memStorage(),
    });
    expect(container.hidden).toBe(true);
  });
});
