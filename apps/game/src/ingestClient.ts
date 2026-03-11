export type IngestSubcommand = "import" | "import-dir" | "import-dtx";

export type IngestImportRequest = {
  source_path: string;
  out_songpack_path?: string;
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
