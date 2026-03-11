// @vitest-environment jsdom
import { ingestImport } from "../src/ingestClient";

describe("ingestClient", () => {
  afterEach(() => {
    vi.doUnmock("@tauri-apps/api/core");
  });

  it("forwards transcription args to ingest_import command", async () => {
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: vi.fn(async (cmd: string, payload: any) => {
        expect(cmd).toBe("ingest_import");
        expect(payload.req.source_path).toBe("C:/music/input.wav");
        expect(payload.req.subcommand).toBe("import-dir");
        expect(payload.req.drum_filter).toBe("combined_filter");
        expect(payload.req.melodic_method).toBe("basic_pitch");
        expect(payload.req.shifts).toBe(2);
        expect(payload.req.multi_filter).toBe(true);
        return {
          ok: true,
          exit_code: 0,
          command: ["aural_ingest"],
          stdout: "",
          stderr: ""
        };
      })
    }));

    const res = await ingestImport({
      source_path: "  C:/music/input.wav  ",
      subcommand: "import-dir",
      drum_filter: "combined_filter",
      melodic_method: "basic_pitch",
      shifts: 2,
      multi_filter: true
    });

    expect(res.ok).toBe(true);
    expect(res.exit_code).toBe(0);
  });

  it("rejects empty source_path", async () => {
    await expect(ingestImport({ source_path: "   " })).rejects.toThrow("missing source_path");
  });
});
