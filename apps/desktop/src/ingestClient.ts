export type IngestSubcommand = "import" | "import-dir";

export type IngestImportRequest = {
  source_path: string;
  out_auralsong_path?: string;
  subcommand?: IngestSubcommand;
  profile?: string;
  config?: string;
  title?: string;
  artist?: string;
  duration_sec?: number;
  drum_filter?: string;
  melodic_method?: string;
  shifts?: number;
  multi_filter?: boolean;
  ingest_binary_path?: string;
};

export type IngestImportResult = {
  ok: boolean;
  exit_code: number;
  command: string[];
  stdout: string;
  stderr: string;
  /**
   * AuralSong-relative paths of any source MIDI files preserved into
   * `features/midi/` after the sidecar finished. Populated when the source
   * is a folder that contains user-supplied gameplay MIDI (Suno gameplay
   * exports being the canonical case) so the Refine workspace can render
   * them as a guide layer alongside the sidecar's per-instrument
   * transcription candidates. Empty for single-file imports and folders
   * without MIDI. `#[serde(default)]` on the Rust side keeps older results
   * (without this field) parseable, so the runtime fallback to `[]` is the
   * source of truth for absent values.
   */
  preserved_reference_midis?: string[];
};

async function getInvoke() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke;
}

export async function ingestImport(req: IngestImportRequest): Promise<IngestImportResult> {
  const sourcePath = (req.source_path ?? "").trim();
  if (!sourcePath) throw new Error("missing source_path");

  const invoke = await getInvoke();
  return invoke<IngestImportResult>("ingest_import", {
    req: {
      ...req,
      source_path: sourcePath
    }
  });
}
