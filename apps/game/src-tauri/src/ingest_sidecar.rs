use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use tauri::{AppHandle, Emitter, Manager};

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "kebab-case")]
pub enum IngestSubcommand {
    #[default]
    Import,
    ImportDir,
    ImportDtx,
}

impl IngestSubcommand {
    pub fn as_cli_arg(self) -> &'static str {
        match self {
            IngestSubcommand::Import => "import",
            IngestSubcommand::ImportDir => "import-dir",
            IngestSubcommand::ImportDtx => "import-dtx",
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct IngestImportRequest {
    pub source_path: String,

    #[serde(default)]
    pub out_songpack_path: Option<String>,

    #[serde(default)]
    pub subcommand: Option<IngestSubcommand>,

    #[serde(default)]
    pub profile: Option<String>,

    #[serde(default)]
    pub config: Option<String>,

    #[serde(default)]
    pub title: Option<String>,

    #[serde(default)]
    pub artist: Option<String>,

    #[serde(default)]
    pub duration_sec: Option<f64>,

    #[serde(default)]
    pub drum_filter: Option<String>,

    #[serde(default)]
    pub melodic_method: Option<String>,

    #[serde(default)]
    pub shifts: Option<i32>,

    #[serde(default)]
    pub multi_filter: Option<bool>,

    #[serde(default)]
    pub ingest_binary_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct IngestImportResult {
    pub ok: bool,
    pub exit_code: i32,
    pub command: Vec<String>,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct IngestProgressEvent {
    pub stream: String,
    pub line: String,
    pub parsed: Option<serde_json::Value>,
}

fn non_empty_opt(s: &Option<String>) -> Option<String> {
    s.as_ref()
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
}

#[cfg(target_os = "windows")]
const INGEST_BINARY_FILENAME: &str = "aural_ingest.exe";
#[cfg(not(target_os = "windows"))]
const INGEST_BINARY_FILENAME: &str = "aural_ingest";
const INGEST_BINARY_FALLBACK_CMD: &str = "aural_ingest";

fn push_candidate(candidates: &mut Vec<PathBuf>, candidate: PathBuf) {
    if !candidates.iter().any(|p| p == &candidate) {
        candidates.push(candidate);
    }
}

fn append_candidate_dir(candidates: &mut Vec<PathBuf>, dir: &Path) {
    push_candidate(
        candidates,
        dir.join("sidecar").join(INGEST_BINARY_FILENAME),
    );
    push_candidate(candidates, dir.join(INGEST_BINARY_FILENAME));
    push_candidate(
        candidates,
        dir.join("dist")
            .join("sidecar")
            .join(INGEST_BINARY_FILENAME),
    );
}

fn default_ingest_binary_candidates(app: Option<&AppHandle>) -> Vec<PathBuf> {
    let mut candidates: Vec<PathBuf> = vec![];

    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            append_candidate_dir(&mut candidates, exe_dir);
        }
    }

    if let Some(app) = app {
        if let Ok(resource_dir) = app.path().resource_dir() {
            append_candidate_dir(&mut candidates, &resource_dir);
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        append_candidate_dir(&mut candidates, &cwd);
    }

    candidates
}

fn pick_first_existing_binary(candidates: &[PathBuf]) -> Option<String> {
    candidates
        .iter()
        .find(|p| p.is_file())
        .map(|p| p.to_string_lossy().to_string())
}

fn resolve_ingest_binary(req: &IngestImportRequest, app: Option<&AppHandle>) -> (String, Vec<String>) {
    if let Some(explicit_binary) = non_empty_opt(&req.ingest_binary_path) {
        return (explicit_binary, vec![]);
    }

    let candidates = default_ingest_binary_candidates(app);
    let searched = candidates
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect::<Vec<_>>();

    if let Some(found) = pick_first_existing_binary(&candidates) {
        return (found, searched);
    }

    (INGEST_BINARY_FALLBACK_CMD.to_string(), searched)
}

fn emit_progress(app: Option<&AppHandle>, ev: IngestProgressEvent) {
    if let Some(app) = app {
        let _ = app.emit("ingest_import_progress", ev);
    }
}

pub fn build_ingest_args(req: &IngestImportRequest) -> Result<Vec<String>, String> {
    let source_path = req.source_path.trim();
    if source_path.is_empty() {
        return Err("missing source_path".to_string());
    }

    let out_songpack_path = non_empty_opt(&req.out_songpack_path)
        .ok_or_else(|| "missing out_songpack_path".to_string())?;

    let subcommand = req.subcommand.unwrap_or_default().as_cli_arg().to_string();
    let profile = req
        .profile
        .as_ref()
        .map(|x| x.trim())
        .filter(|x| !x.is_empty())
        .unwrap_or("full")
        .to_string();

    let mut args: Vec<String> = vec![
        subcommand,
        source_path.to_string(),
        "--out".to_string(),
        out_songpack_path,
        "--profile".to_string(),
        profile,
    ];

    if let Some(config) = non_empty_opt(&req.config) {
        args.push("--config".to_string());
        args.push(config);
    }
    if let Some(title) = non_empty_opt(&req.title) {
        args.push("--title".to_string());
        args.push(title);
    }
    if let Some(artist) = non_empty_opt(&req.artist) {
        args.push("--artist".to_string());
        args.push(artist);
    }
    if let Some(duration_sec) = req.duration_sec {
        args.push("--duration-sec".to_string());
        args.push(duration_sec.to_string());
    }
    if let Some(drum_filter) = non_empty_opt(&req.drum_filter) {
        args.push("--drum-filter".to_string());
        args.push(drum_filter);
    }
    if let Some(melodic_method) = non_empty_opt(&req.melodic_method) {
        args.push("--melodic-method".to_string());
        args.push(melodic_method);
    }
    if let Some(shifts) = req.shifts {
        args.push("--shifts".to_string());
        args.push(shifts.to_string());
    }
    if req.multi_filter.unwrap_or(false) {
        args.push("--multi-filter".to_string());
    }

    Ok(args)
}

pub fn run_ingest_import(req: IngestImportRequest) -> Result<IngestImportResult, String> {
    run_ingest_import_with_progress(req, None)
}

pub fn run_ingest_import_with_progress(
    req: IngestImportRequest,
    app: Option<&AppHandle>,
) -> Result<IngestImportResult, String> {
    let args = build_ingest_args(&req)?;
    let (binary, searched_paths) = resolve_ingest_binary(&req, app);

    let mut child = Command::new(&binary)
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| {
            if searched_paths.is_empty() {
                format!("failed to start {binary}: {e}")
            } else {
                format!(
                    "failed to start {binary}: {e}; searched default locations: {}",
                    searched_paths.join("; ")
                )
            }
        })?;

    #[derive(Clone, Copy)]
    enum StreamKind {
        Stdout,
        Stderr,
    }
    let (tx, rx) = mpsc::channel::<(StreamKind, String)>();

    if let Some(stdout) = child.stdout.take() {
        let tx_out = tx.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let _ = tx_out.send((StreamKind::Stdout, line));
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let tx_err = tx.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = tx_err.send((StreamKind::Stderr, line));
            }
        });
    }
    drop(tx);

    let mut stdout_lines: Vec<String> = vec![];
    let mut stderr_lines: Vec<String> = vec![];

    for (kind, line) in rx {
        match kind {
            StreamKind::Stdout => {
                let parsed = serde_json::from_str::<serde_json::Value>(&line).ok();
                emit_progress(
                    app,
                    IngestProgressEvent {
                        stream: "stdout".to_string(),
                        line: line.clone(),
                        parsed,
                    },
                );
                stdout_lines.push(line);
            }
            StreamKind::Stderr => {
                emit_progress(
                    app,
                    IngestProgressEvent {
                        stream: "stderr".to_string(),
                        line: line.clone(),
                        parsed: None,
                    },
                );
                stderr_lines.push(line);
            }
        }
    }

    let status = child
        .wait()
        .map_err(|e| format!("failed waiting for {binary}: {e}"))?;
    let exit_code = status.code().unwrap_or(-1);
    let stdout = stdout_lines.join("\n");
    let stderr = stderr_lines.join("\n");

    let mut command = vec![binary];
    command.extend(args);

    Ok(IngestImportResult {
        ok: status.success(),
        exit_code,
        command,
        stdout,
        stderr,
    })
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::{build_ingest_args, IngestImportRequest, IngestSubcommand};

    fn req_base() -> IngestImportRequest {
        IngestImportRequest {
            source_path: "C:/input/song.wav".to_string(),
            out_songpack_path: Some("C:/songs/my.songpack".to_string()),
            ..IngestImportRequest::default()
        }
    }

    fn has_pair(args: &[String], key: &str, value: &str) -> bool {
        args.windows(2).any(|w| w[0] == key && w[1] == value)
    }

    #[test]
    fn build_ingest_args_forwards_transcription_flags() {
        let mut req = req_base();
        req.subcommand = Some(IngestSubcommand::Import);
        req.profile = Some("full".to_string());
        req.config = Some("{\"bpm_hint\":120}".to_string());
        req.title = Some("My Song".to_string());
        req.artist = Some("My Artist".to_string());
        req.duration_sec = Some(42.5);
        req.drum_filter = Some("combined_filter".to_string());
        req.melodic_method = Some("basic_pitch".to_string());
        req.shifts = Some(3);
        req.multi_filter = Some(true);

        let args = build_ingest_args(&req).expect("args");
        assert_eq!(args[0], "import");
        assert_eq!(args[1], "C:/input/song.wav");
        assert!(has_pair(&args, "--out", "C:/songs/my.songpack"));
        assert!(has_pair(&args, "--profile", "full"));
        assert!(has_pair(&args, "--config", "{\"bpm_hint\":120}"));
        assert!(has_pair(&args, "--title", "My Song"));
        assert!(has_pair(&args, "--artist", "My Artist"));
        assert!(has_pair(&args, "--duration-sec", "42.5"));
        assert!(has_pair(&args, "--drum-filter", "combined_filter"));
        assert!(has_pair(&args, "--melodic-method", "basic_pitch"));
        assert!(has_pair(&args, "--shifts", "3"));
        assert!(args.iter().any(|x| x == "--multi-filter"));
    }

    #[test]
    fn build_ingest_args_handles_import_dir_defaults() {
        let mut req = req_base();
        req.subcommand = Some(IngestSubcommand::ImportDir);
        req.profile = None;
        req.multi_filter = Some(false);

        let args = build_ingest_args(&req).expect("args");
        assert_eq!(args[0], "import-dir");
        assert!(has_pair(&args, "--profile", "full"));
        assert!(!args.iter().any(|x| x == "--multi-filter"));
    }

    #[test]
    fn build_ingest_args_requires_paths() {
        let req = IngestImportRequest {
            source_path: "   ".to_string(),
            out_songpack_path: None,
            ..IngestImportRequest::default()
        };
        let err = build_ingest_args(&req).expect_err("expected error");
        assert!(err.contains("missing source_path"));
    }

    #[test]
    fn resolve_ingest_binary_honors_explicit_override() {
        let req = IngestImportRequest {
            ingest_binary_path: Some("C:/tools/custom_ingest.exe".to_string()),
            ..req_base()
        };
        let (binary, searched) = super::resolve_ingest_binary(&req, None);
        assert_eq!(binary, "C:/tools/custom_ingest.exe");
        assert!(searched.is_empty());
    }

    #[test]
    fn pick_first_existing_binary_prefers_earliest_candidate() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("aural_ingest_candidates_{nonce}"));
        fs::create_dir_all(&root).expect("mkdir temp");

        let missing = root.join("missing.exe");
        let first = root.join("first.exe");
        let second = root.join("second.exe");
        fs::write(&first, b"x").expect("write first");
        fs::write(&second, b"x").expect("write second");

        let picked = super::pick_first_existing_binary(&[missing, first.clone(), second])
            .expect("pick existing binary");
        assert_eq!(picked, first.to_string_lossy().to_string());

        let _ = fs::remove_dir_all(root);
    }
}
