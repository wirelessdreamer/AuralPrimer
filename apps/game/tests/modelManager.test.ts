// @vitest-environment jsdom
import { installModelPackFromPath, installModelPackFromUrl, listInstalledModelPacks } from "../src/models/modelManager";

describe("modelManager", () => {
  afterEach(() => {
    vi.doUnmock("@tauri-apps/api/core");
  });

  it("installModelPackFromUrl downloads and invokes installer", async () => {
    // Mock fetch
    const buf = new Uint8Array([1, 2, 3]).buffer;
    (globalThis as any).fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      arrayBuffer: async () => buf,
    }));

    // Mock tauri invoke
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string, payload: any) => {
        expect(cmd).toBe("install_modelpack_zip_bytes");
        expect(payload.zip_bytes).toEqual([1, 2, 3]);
        expect(payload.expected_zip_sha256).toBe("deadbeef");
      }),
    }));

    await installModelPackFromUrl({ id: "x", version: "1", url: "https://example.com/x.zip", sha256: "deadbeef" });
  });

  it("installModelPackFromUrl fails when preferred pack has no URL", async () => {
    await expect(installModelPackFromUrl({ id: "x", version: "1", url: "" })).rejects.toThrow("has no url configured");
  });

  it("installModelPackFromUrl propagates download errors", async () => {
    (globalThis as any).fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      statusText: "Not Found",
    }));

    await expect(
      installModelPackFromUrl({ id: "x", version: "1", url: "https://example.com/x.zip" })
    ).rejects.toThrow("download failed: 404 Not Found");
  });

  it("listInstalledModelPacks invokes backend command", async () => {
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string) => {
        expect(cmd).toBe("list_installed_modelpacks");
        return [{ id: "m", version: "1", root_dir: "r", manifest_path: "m", ok: true }];
      }),
    }));

    const packs = await listInstalledModelPacks();
    expect(packs).toHaveLength(1);
    expect(packs[0].id).toBe("m");
  });

  it("installModelPackFromPath validates path and invokes backend command", async () => {
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string, payload: any) => {
        expect(cmd).toBe("install_modelpack_from_path");
        expect(payload.path).toBe("C:/x/model.zip");
      }),
    }));

    await installModelPackFromPath(" C:/x/model.zip ");
    await expect(installModelPackFromPath("   ")).rejects.toThrow("missing path");
  });
});
