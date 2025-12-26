// @vitest-environment jsdom
import { installModelPackFromUrl } from "../src/models/modelManager";

describe("modelManager", () => {
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

    vi.doUnmock("@tauri-apps/api/core");
  });
});
